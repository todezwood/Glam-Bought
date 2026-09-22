"""Google Calendar (primary): read the week ahead, write the pickup after approval."""
import html
import json
from datetime import datetime, timedelta

from strands import tool

from . import config, events, oauth

API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
OWN_PREFIXES = ("Pick up:", "GlamBought")  # our own events, skipped on read
LAST_EVENT: dict | None = None  # set by add_calendar_event; the server returns it with the reply


def _rfc3339(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _err(r) -> str:
    if r.status_code == 401:
        return "token rejected"
    if r.status_code == 403:
        return "Calendar API not enabled for this project, or scope missing"
    return f"{r.status_code}: {r.text[:200]}"


def list_events(time_min: datetime, time_max: datetime, max_results: int = 25) -> list[dict]:
    r = oauth.request("google", "GET", API, params={
        "timeMin": _rfc3339(time_min), "timeMax": _rfc3339(time_max),
        "singleEvents": "true", "orderBy": "startTime", "maxResults": max_results,
    })
    if r.status_code != 200:
        raise RuntimeError(_err(r))
    return r.json().get("items", [])


def insert_event(summary: str, start: datetime, end: datetime, description: str = "", location: str = "") -> dict:
    body = {
        "summary": summary, "description": description, "location": location,
        "start": {"dateTime": _rfc3339(start), "timeZone": config.LOCAL_TZ},
        "end": {"dateTime": _rfc3339(end), "timeZone": config.LOCAL_TZ},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 60}]},
    }
    r = oauth.request("google", "POST", API, json=body)
    if r.status_code != 200:
        raise RuntimeError(_err(r))
    return r.json()


def delete_event(event_id: str) -> None:
    r = oauth.request("google", "DELETE", f"{API}/{event_id}")
    if r.status_code not in (200, 204, 404, 410):
        raise RuntimeError(_err(r))


def parse_local(s: str) -> datetime:
    dt = datetime.fromisoformat(s.strip())
    return dt.replace(tzinfo=config.tz()) if dt.tzinfo is None else dt.astimezone(config.tz())


def _describe(item: dict, now: datetime) -> dict | None:
    title = (item.get("summary") or "").strip()
    if not title or title.startswith(OWN_PREFIXES):
        return None
    start, end = item.get("start") or {}, item.get("end") or {}
    if start.get("date"):
        d = datetime.fromisoformat(start["date"]).date()
        when = f"{d:%A %Y-%m-%d} (all day)"
        days_away = (d - now.date()).days
        start_s, end_s, all_day = start["date"], end.get("date"), True
    else:
        dt = datetime.fromisoformat(start["dateTime"]).astimezone(config.tz())
        when = f"{dt:%A %Y-%m-%d %-I:%M %p}"
        days_away = (dt.date() - now.date()).days
        start_s, end_s, all_day = _rfc3339(dt), end.get("dateTime"), False
    return {"title": title, "when": when, "start": start_s, "end": end_s, "days_away": days_away,
            "all_day": all_day, "location": item.get("location") or "", "url": item.get("htmlLink")}


@tool
def check_calendar(days_ahead: int = 10) -> str:
    """Read the user's upcoming Google Calendar events (primary calendar) to find the occasion
    she is shopping for and the deadline to have products in hand. Only call this for a shopping
    request that mentions an event, a day of the week, "this weekend", or a deadline.

    Args:
        days_ahead: How many days ahead to look.
    """
    if not oauth.configured("google"):
        return "Calendar not connected: GOOGLE_CLIENT_ID is not set. Tell the user in one clause and continue without it."
    now = config.local_now()
    try:
        items = list_events(now, now + timedelta(days=days_ahead))
    except oauth.NotConnected as e:
        return f"Calendar not connected: {e}. Tell the user in one clause and continue without it."
    except Exception as e:
        return f"Calendar unavailable right now: {str(e)[:160]}. Continue without it."
    found = [ev for ev in (_describe(it, now) for it in items) if ev][:12]
    first = next((ev for ev in found if not ev["all_day"]), found[0] if found else None)
    if first:
        away = first["days_away"]
        in_days = "today" if away == 0 else "tomorrow" if away == 1 else f"in {away} days"
        summary = f"Next: {first['title']}, {first['when']} ({in_days})" + (f", {first['location']}" if first["location"] else "") + "."
    else:
        summary = f"No events in the next {days_ahead} days."
    return json.dumps({"source": "Calendar", "today": f"{now:%A %Y-%m-%d}", "timezone": config.LOCAL_TZ,
                       "days_ahead": days_ahead, "events": found, "summary": summary})


@tool
def add_calendar_event(title: str, start: str, duration_minutes: int = 45, location: str = "", notes: str = "") -> str:
    """Put the approved pickup on the user's Google Calendar. ONLY call this after the user has
    approved the plan and only when an occasion was found on the calendar; it is cancelled otherwise.

    Args:
        title: For example "Pick up: Kosas Revealer Concealer at Sephora".
        start: Local start time as ISO 8601 without timezone, for example "2026-09-25T17:30".
        duration_minutes: Length of the hold in minutes.
        location: Store name and address if known.
        notes: The items with prices and product URLs, and the occasion this is for.
    """
    global LAST_EVENT
    title, location, notes = (html.unescape(x) for x in (title, location, notes))  # the model sometimes writes &amp;
    try:
        start_dt = parse_local(start)
    except ValueError:
        return 'start must be local ISO time like "2026-09-25T17:30"'
    if start_dt < config.local_now():
        return "That time is in the past; propose a later slot."
    end_dt = start_dt + timedelta(minutes=int(duration_minutes or 45))
    try:
        ev = insert_event(title, start_dt, end_dt, description=notes, location=location)
    except oauth.NotConnected:
        return "Calendar not connected: the pickup was NOT added. Tell the user to connect Google Calendar on the laptop."
    except Exception as e:
        return f"Calendar write failed: {str(e)[:200]}"
    LAST_EVENT = {"title": title, "when": f"{start_dt:%A %-I:%M %p}", "url": ev.get("htmlLink")}
    events.emit("calendar_write", label=f"{title} · {LAST_EVENT['when']}", url=LAST_EVENT["url"])
    return json.dumps({"status": "event_created", "id": ev.get("id"), "title": title,
                       "start": _rfc3339(start_dt), "end": _rfc3339(end_dt), "url": LAST_EVENT["url"]})
