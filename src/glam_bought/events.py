"""Tiny in-process event bus: agent activity -> audit log + UI stream."""
import contextvars
import json
import queue
import time
from pathlib import Path

_subscribers: list[queue.Queue] = []
_AUDIT = Path("logs/audit.jsonl")
SESSION: contextvars.ContextVar[str | None] = contextvars.ContextVar("session", default=None)  # set per turn by the server


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue()
    _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    if q in _subscribers:
        _subscribers.remove(q)


def emit(type_: str, **data) -> None:
    event = {"type": type_, "ts": time.time(), "session": SESSION.get(), **data}
    _AUDIT.parent.mkdir(exist_ok=True)
    with _AUDIT.open("a") as f:
        f.write(json.dumps(event, default=str) + "\n")
    for q in list(_subscribers):
        q.put(event)
