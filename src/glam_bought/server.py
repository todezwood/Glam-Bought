"""FastAPI front door: POST /chat, GET /events (SSE activity), /profile, /healthz."""
import asyncio
import json
import queue
import re
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from . import config, events, gcal, memory, oauth
from .agent import ask, build_agent

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "cache" / "last_good.json"
CACHE_APPROVED = ROOT / "cache" / "last_approved.json"
TILES = ROOT / "cache" / "tiles.json"  # scripts/cache_tiles.py: recorded runs of the home-screen tiles
TILE_SECONDS = 9  # a recorded run is replayed over this long so the activity card still tells the story

app = FastAPI(title="GlamBought")
_sessions: dict[str, dict] = {}  # one agent (conversation) and one lock per browser session
_jobs: dict[str, dict] = {}


def session(sid: str) -> dict:
    if sid not in _sessions:
        _sessions[sid] = {"agent": None, "lock": asyncio.Lock()}
    return _sessions[sid]


def agent(sid: str):
    s = session(sid)
    if s["agent"] is None:
        s["agent"] = build_agent()
    return s["agent"]


class ChatIn(BaseModel):
    message: str
    approved: bool = False
    replay: bool = False
    session: str = "default"


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html", headers={"Cache-Control": "no-cache"})  # a stale page cannot talk to a new server


@app.post("/chat")
async def chat(body: ChatIn):
    """A turn takes 2-3 minutes, longer than the tunnel's 100 s response limit and longer than a
    reload-happy user waits: start it as a job and return at once; the UI polls GET /chat/{job}."""
    if body.replay:
        path = CACHE_APPROVED if body.approved and CACHE_APPROVED.exists() else CACHE
        if path.exists():
            return JSONResponse(json.loads(path.read_text()))
    lock = session(body.session)["lock"]
    if lock.locked():  # a reload re-sent the same request: hand back the turn already running
        for job_id, job in reversed(list(_jobs.items())):
            if (job["status"] == "running" and job["session"] == body.session
                    and job["message"] == body.message and job["approved"] == body.approved):
                return {"job": job_id, "queued": True}
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {"status": "running", "queued": lock.locked(), "ts": time.time(),
                     "session": body.session, "message": body.message, "approved": body.approved}
    asyncio.create_task(_run(job_id, body))
    return {"job": job_id, "queued": _jobs[job_id]["queued"]}


@app.get("/chat/{job_id}")
async def chat_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    return {"status": job["status"], "queued": job.get("queued", False), "result": job.get("result")}


def _tile(body: ChatIn) -> dict | None:
    """The recorded run for a home-screen tile, if this request is one (and not an approval)."""
    if body.approved or not TILES.exists():
        return None
    return json.loads(TILES.read_text()).get(body.message.strip())


async def _serve_tile(job_id: str, body: ChatIn, tile: dict) -> None:
    """Answer a tile in seconds: replay its recorded activity, compressed, then its result with the
    real 'checked N min ago' age, and seed the conversation so Approve and follow-ups continue from it."""
    evs = tile.get("events", [])
    span = max(evs[-1]["ts"] - evs[0]["ts"], 1.0) if evs else 1.0
    prev = evs[0]["ts"] if evs else 0
    for ev in evs:
        await asyncio.sleep((ev["ts"] - prev) / span * TILE_SECONDS)
        prev = ev["ts"]
        events.emit(ev["type"], **{k: v for k, v in ev.items() if k not in ("type", "ts", "session")})
    age = max(1, int((time.time() - tile["ts"]) / 60))
    result = dict(tile["result"])
    result["reply"] = re.sub(r'"checked": "[^"]*"', f'"checked": "checked {age} min ago"', result["reply"])
    events.emit("done")
    _jobs[job_id].update(status="done", result=result)
    ag = await asyncio.to_thread(agent, body.session)
    ag.messages.extend([{"role": "user", "content": [{"text": body.message}]},
                        {"role": "assistant", "content": [{"text": result["reply"]}]}])


async def _run(job_id: str, body: ChatIn) -> None:
    events.SESSION.set(body.session)  # tags this turn's activity events; to_thread carries the context
    async with session(body.session)["lock"]:  # one turn at a time per session; the agent holds conversation state
        _jobs[job_id]["queued"] = False
        gcal.LAST_EVENT = None
        tile = _tile(body)
        if tile:
            await _serve_tile(job_id, body, tile)
            return
        try:
            reply = await asyncio.to_thread(ask, agent(body.session), body.message, body.approved)
        except Exception as e:
            events.emit("error", label=str(e)[:300])
            _jobs[job_id].update(status="done", result={"reply": "Something went wrong on my side. Please try again.", "error": str(e)[:300]})
            return
    result = {"reply": reply}
    if gcal.LAST_EVENT:
        result["calendar"] = gcal.LAST_EVENT
    if "```basket" in reply:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(result))
    if body.approved and gcal.LAST_EVENT:
        CACHE_APPROVED.parent.mkdir(exist_ok=True)
        CACHE_APPROVED.write_text(json.dumps(result))
    events.emit("done")
    _jobs[job_id].update(status="done", result=result)
    for old in [k for k, j in _jobs.items() if time.time() - j["ts"] > 3600]:
        _jobs.pop(old, None)


@app.post("/reset")
async def reset(session: str = ""):
    """Forget the conversation (not the Beauty Brain) for one session, or for all of them."""
    for sid in [session] if session else list(_sessions):
        if sid in _sessions and not _sessions[sid]["lock"].locked():
            _sessions[sid]["agent"] = None
    return {"ok": True}


@app.get("/events")
async def stream(session: str = ""):
    """Activity stream; with ?session= only that session's turns (plus untagged server events)."""
    q = events.subscribe()

    async def gen():
        try:
            while True:
                try:
                    event = await asyncio.to_thread(q.get, True, 12)
                    if session and event.get("session") not in (None, session):
                        continue
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


BRAIN_QUERY = (
    "In short markdown bullet points under bold headings: what has the user bought, repurchased or "
    "returned (with dates and retailers); which products she rated well or badly and why; and what her "
    "ring has shown about her sleep and readiness recently. Only facts that are in memory; leave out any "
    "heading that has nothing recorded."
)
_brain_cache: dict = {"at": 0.0, "text": ""}


@app.get("/profile")
def profile():
    """The structured profile (seed/profile.json) plus which sources feed the Brain. Instant."""
    try:
        prof = json.loads((config.SEED_DIR / "profile.json").read_text())
    except Exception as e:
        return JSONResponse({"error": f"profile file unavailable: {e}"}, status_code=500)
    seed = config.SEED_DIR
    mark = config.TOKENS_DIR / "wellness_written.txt"
    sources = {
        "ring": oauth.connected("oura") or config.OURA_USE_SANDBOX,
        "calendar": oauth.connected("google"),
        "notes": len(list(seed.glob("*.md"))),
        "receipts": len(list((seed / "receipts").glob("*.txt"))) + len(list((seed / "emails").glob("*.txt"))),
        "ring_as_of": mark.read_text().strip() if mark.exists() else "",
    }
    return {"profile": prof, "sources": sources}


@app.get("/profile/brain")
async def profile_brain(fresh: int = 0):
    """What the Beauty Brain (Cognee) has recorded: purchases, reactions, ring readings. ~30 s, cached."""
    if not fresh and _brain_cache["text"] and time.time() - _brain_cache["at"] < 600:
        return {"brain": _brain_cache["text"], "cached": True}

    def q():
        sets = [config.DS_PROFILE, config.DS_PURCHASES, config.DS_WELLNESS]
        try:
            text = memory.recall_text(BRAIN_QUERY, sets)
        except Exception:  # a dataset still building on Cognee Cloud: fall back to the core two
            text = memory.recall_text(BRAIN_QUERY, sets[:2])
        return re.sub(r"\s*\[dataset: [^\]]+\]", "", text).strip()

    try:
        text = await asyncio.to_thread(q)
    except Exception as e:
        return JSONResponse({"error": f"memory unavailable: {e}"}, status_code=503)
    _brain_cache.update(at=time.time(), text=text)
    return {"brain": text, "cached": False}


_LEGAL_CSS = "<style>body{font:16px/1.6 system-ui,sans-serif;max-width:640px;margin:48px auto;padding:0 20px;color:#1E1C26}h1{font-weight:500}</style>"


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return _LEGAL_CSS + """<h1>GlamBought privacy policy</h1>
<p>GlamBought is a personal beauty assistant built for the Battle of the Personal Brains hackathon (September 2026).
It runs on the operator's own laptop and serves a single user.</p>
<p><strong>What we access.</strong> With your consent, GlamBought reads daily readiness, sleep and stress summaries from
your Oura ring, and reads and creates events on your Google Calendar. It also stores the beauty preferences, purchases and
product reactions you tell it about.</p>
<p><strong>How it is used.</strong> This data is used only to personalise product recommendations and to add pickup
reminders to your calendar. It is stored in a private memory (Cognee) belonging to the operator. It is never sold,
shared with advertisers, or used for any other purpose.</p>
<p><strong>Retention and deletion.</strong> Access tokens are kept on the operator's machine and can be revoked at any
time from your Oura or Google account settings. Email <a href="mailto:jarmarzledesma@gmail.com">jarmarzledesma@gmail.com</a>
to have any stored data deleted.</p>"""


@app.get("/terms", response_class=HTMLResponse)
def terms():
    return _LEGAL_CSS + """<h1>GlamBought terms of service</h1>
<p>GlamBought is an experimental, non-commercial hackathon project provided as is, without warranty of any kind.</p>
<p>Product recommendations are suggestions, not medical or dermatological advice. Ring data is treated as a general
wellness observation, never a diagnosis. Prices and availability come from public web pages and may be out of date.</p>
<p>Nothing is purchased on your behalf. Any calendar event is created only after you explicitly approve it in the app.</p>
<p>Questions: <a href="mailto:jarmarzledesma@gmail.com">jarmarzledesma@gmail.com</a>.</p>"""


@app.get("/healthz")
def healthz():
    try:
        docker_ok = subprocess.run(["docker", "version"], capture_output=True).returncode == 0
    except FileNotFoundError:  # inside the Lightsail container: the ranker runs as a plain subprocess instead
        docker_ok = False
    return {
        "model_provider": config.MODEL_PROVIDER,
        "docker": docker_ok,
        "cognee_configured": bool(config.COGNEE_SERVICE_URL and config.COGNEE_API_KEY),
        "brightdata_configured": bool(config.BRIGHTDATA_API_TOKEN),
        "oura_configured": oauth.configured("oura") or config.OURA_USE_SANDBOX,
        "oura_connected": oauth.connected("oura") or config.OURA_USE_SANDBOX,
        "google_configured": oauth.configured("google"),
        "google_connected": oauth.connected("google"),
        "oauth_base_url": config.OAUTH_BASE_URL,
    }


@app.get("/connect/{provider}")
def connect(provider: str):
    """Start the OAuth flow. Open this on the laptop: the redirect URI is OAUTH_BASE_URL."""
    if provider not in oauth.PROVIDERS:
        return JSONResponse({"error": "unknown provider"}, status_code=404)
    if not oauth.configured(provider):
        return JSONResponse({"error": f"set {provider.upper()}_CLIENT_ID and {provider.upper()}_CLIENT_SECRET in .env"}, status_code=400)
    return RedirectResponse(oauth.authorize_url(provider), status_code=302)


@app.get("/oauth/{provider}/callback")
def oauth_callback(provider: str, code: str = "", state: str = "", error: str = ""):
    if provider not in oauth.PROVIDERS:
        return JSONResponse({"error": "unknown provider"}, status_code=404)
    if error or not code:
        events.emit("error", label=f"{provider} oauth: {error or 'no code'}")
        return RedirectResponse(f"/?connect_error={provider}", status_code=303)
    try:
        oauth.exchange_code(provider, code, state)
    except Exception as e:
        events.emit("error", label=f"{provider} oauth: {str(e)[:200]}")
        return RedirectResponse(f"/?connect_error={provider}", status_code=303)
    events.emit("connected", provider=provider)
    return RedirectResponse("/", status_code=303)
