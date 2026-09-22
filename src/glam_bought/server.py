"""FastAPI front door: POST /chat, GET /events (SSE activity), /profile, /healthz."""
import asyncio
import json
import queue
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import config, events, memory
from .agent import ask, build_agent

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "cache" / "last_good.json"

app = FastAPI(title="GlamBought")
_agent = None
_lock = asyncio.Lock()


def agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


class ChatIn(BaseModel):
    message: str
    approved: bool = False
    replay: bool = False


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.post("/chat")
async def chat(body: ChatIn):
    if body.replay and CACHE.exists():
        return JSONResponse(json.loads(CACHE.read_text()))
    async with _lock:  # one turn at a time; the agent holds conversation state
        try:
            reply = await asyncio.to_thread(ask, agent(), body.message, body.approved)
        except Exception as e:
            events.emit("error", label=str(e)[:300])
            return JSONResponse({"reply": "Something went wrong on my side. Please try again.", "error": str(e)[:300]}, status_code=500)
    result = {"reply": reply}
    if "```basket" in reply:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(result))
    events.emit("done")
    return result


@app.post("/reset")
async def reset():
    global _agent
    _agent = None
    return {"ok": True}


@app.get("/events")
async def stream():
    q = events.subscribe()

    async def gen():
        try:
            while True:
                try:
                    event = await asyncio.to_thread(q.get, True, 12)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/profile")
async def profile():
    """What the Beauty Brain knows, grouped by provenance."""
    def q(text):
        try:
            return memory.recall_text(text, [config.DS_PROFILE, config.DS_PURCHASES])
        except Exception as e:
            return f"(memory unavailable: {e})"

    told, observed = await asyncio.gather(
        asyncio.to_thread(q, "List what the user has directly told us about their skin, colouring, ingredients to avoid, preferences and product experiences. Short bullet points."),
        asyncio.to_thread(q, "List what we have observed from receipts and order emails: purchases, repurchases, returns. Short bullet points."),
    )
    return {"told_me": told, "observed": observed}


@app.get("/healthz")
def healthz():
    docker_ok = subprocess.run(["docker", "version"], capture_output=True).returncode == 0
    return {
        "model_provider": config.MODEL_PROVIDER,
        "docker": docker_ok,
        "cognee_configured": bool(config.COGNEE_SERVICE_URL and config.COGNEE_API_KEY),
        "brightdata_configured": bool(config.BRIGHTDATA_API_TOKEN),
    }
