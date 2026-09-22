"""Oura ring: the last few nights, as an observation the agent can reason with."""
import json
from datetime import date, datetime, timedelta, timezone

import requests
from strands import tool

from . import config, memory, oauth

PROD = "https://api.ouraring.com/v2/usercollection"
SANDBOX = "https://api.ouraring.com/v2/sandbox/usercollection"
_MARK = config.TOKENS_DIR / "wellness_written.txt"
COLLECTIONS = ("daily_readiness", "daily_sleep", "daily_stress", "sleep")


def fetch(type_: str, start: date, end: date) -> list[dict] | None:
    """One collection. 401 -> NotConnected; any other non-200 (403 = scope not granted or
    subscription lapsed) -> None so the other collections still render."""
    params = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    if config.OURA_USE_SANDBOX:
        r = requests.get(f"{SANDBOX}/{type_}", headers={"Authorization": "Bearer sandbox"},
                         params=params, timeout=15)
    else:
        r = oauth.request("oura", "GET", f"{PROD}/{type_}", params=params)
    if r.status_code == 401:
        raise oauth.NotConnected("token rejected")
    if r.status_code != 200:
        return None
    return r.json().get("data", [])


def _latest(items, key):
    for it in reversed(items or []):
        if it.get(key) is not None:
            return it
    return None


def snapshot(days: int = 4) -> dict:
    end = config.local_now().date()
    start = end - timedelta(days=days - 1)
    raw = {c: fetch(c, start, end) for c in COLLECTIONS}
    unavailable = [c for c, v in raw.items() if v is None]
    if len(unavailable) == len(COLLECTIONS):
        return {"empty": True, "unavailable": unavailable}

    snap = {"source": "Ring", "as_of": end.isoformat(), "days": days,
            "readiness": None, "sleep": None, "stress": None, "readiness_trend": [],
            "unavailable": unavailable, "summary": ""}
    parts = []

    r = _latest(raw["daily_readiness"], "score")
    if r:
        c = r.get("contributors") or {}
        snap["readiness"] = {
            "day": r.get("day"), "score": r.get("score"),
            "temperature_deviation_c": r.get("temperature_deviation"),
            "hrv_balance_score": c.get("hrv_balance"), "sleep_balance_score": c.get("sleep_balance"),
            "recovery_index_score": c.get("recovery_index"), "resting_hr_score": c.get("resting_heart_rate"),
        }
        snap["readiness_trend"] = [it["score"] for it in raw["daily_readiness"] if it.get("score") is not None]
        trend = snap["readiness_trend"]
        line = f"Readiness {r['score']}"
        if len(trend) >= 2 and trend[0] - trend[-1] >= 8:
            line += f", down from {trend[0]} over {len(trend)} days"
        parts.append(line + ".")
        snap["as_of"] = r.get("day") or snap["as_of"]

    ds = _latest(raw["daily_sleep"], "score")
    periods = [p for p in (raw["sleep"] or []) if p.get("total_sleep_duration")]
    long = [p for p in periods if p.get("type") == "long_sleep"] or periods
    sp = long[-1] if long else None
    if ds or sp:
        hours = round((sp.get("total_sleep_duration") or 0) / 3600, 1) if sp else None
        snap["sleep"] = {
            "day": (sp or ds).get("day"), "score": ds.get("score") if ds else None,
            "total_sleep_hours": hours, "efficiency": (sp or {}).get("efficiency"),
            "average_hrv": (sp or {}).get("average_hrv"), "lowest_heart_rate_bpm": (sp or {}).get("lowest_heart_rate"),
        }
        if hours is not None:
            parts.append(f"Slept {hours} h last night" + (f" (sleep score {ds['score']})." if ds else "."))
        elif ds:
            parts.append(f"Sleep score {ds['score']}.")

    st = _latest(raw["daily_stress"], "day_summary")
    if st:
        snap["stress"] = {
            "day": st.get("day"), "day_summary": st.get("day_summary"),
            "stress_minutes": round((st.get("stress_high") or 0) / 60),
            "recovery_minutes": round((st.get("recovery_high") or 0) / 60),
        }
        label = {"stressful": "Yesterday was stressful", "restored": "Yesterday was restorative",
                 "normal": "Stress was normal"}.get(st["day_summary"], f"Stress: {st['day_summary']}")
        parts.append(label + ".")

    td = (snap["readiness"] or {}).get("temperature_deviation_c")
    if td is not None and abs(td) >= 0.2:
        parts.append(f"Temperature {td:+.1f} C from baseline.")
    if unavailable:
        parts.append("(" + ", ".join(unavailable) + " unavailable)")
    snap["summary"] = " ".join(parts)[:160]
    return snap


@tool
def check_wellness(days: int = 4) -> str:
    """Read the user's Oura ring: readiness, sleep and stress for the last few days, as an
    observation about how her body, and so her skin, is doing right now. Only call this for a
    shopping request that mentions an occasion, a deadline, or how she has been feeling.

    Args:
        days: How many days back to look. Today is often missing until the ring syncs.
    """
    if not config.OURA_USE_SANDBOX and not oauth.configured("oura"):
        return "Ring not connected: OURA_CLIENT_ID is not set. Tell the user in one clause and continue without ring data."
    try:
        snap = snapshot(days)
    except oauth.NotConnected as e:
        return f"Ring not connected: {e}. Tell the user in one clause and continue without ring data."
    except Exception as e:  # Unavailable, JSON, anything: never take the turn down
        return f"Ring unavailable right now: {str(e)[:160]}. Continue without it."
    if snap.get("empty"):
        return f"Ring returned no data for the last {days} days (has the Oura app synced?). Continue without it."
    if not config.OURA_USE_SANDBOX:  # fake data must not enter the Brain
        _remember_once(snap)
    return json.dumps(snap)


def _remember_once(snap: dict) -> None:
    """Write today's reading to the Beauty Brain once per day (a Cognee graph rebuild mid-turn hurts)."""
    try:
        if _MARK.exists() and _MARK.read_text().strip() == snap["as_of"]:
            return
        stamp = datetime.now(timezone.utc).isoformat(timespec="minutes")
        memory.remember_in_background(
            f"[provenance: observed] [source: oura_ring] [recorded: {stamp}] Oura ring reading for {snap['as_of']}: {snap['summary']}",
            config.DS_WELLNESS)
        config.TOKENS_DIR.mkdir(exist_ok=True)
        _MARK.write_text(snap["as_of"])
    except Exception:
        pass
