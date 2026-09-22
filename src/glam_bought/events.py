"""Tiny in-process event bus: agent activity -> audit log + UI stream."""
import collections
import contextvars
import itertools
import json
import queue
import time
from pathlib import Path

_subscribers: list[queue.Queue] = []
_AUDIT = Path("logs/audit.jsonl")
SESSION: contextvars.ContextVar[str | None] = contextvars.ContextVar("session", default=None)  # set per turn by the server
_seq = itertools.count(1)
_last_seq = 0
RECENT: collections.deque = collections.deque(maxlen=2000)  # the UI polls these; the tunnel buffers SSE


def last_seq() -> int:
    return _last_seq


def since(session: str | None, seq: int) -> list[dict]:
    """Events after `seq` for one session (plus untagged server events), oldest first."""
    return [e for e in list(RECENT) if e["seq"] > seq and e.get("session") in (None, session)]


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue()
    _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    if q in _subscribers:
        _subscribers.remove(q)


def emit(type_: str, **data) -> None:
    global _last_seq
    _last_seq = next(_seq)
    event = {"seq": _last_seq, "type": type_, "ts": time.time(), "session": SESSION.get(), **data}
    RECENT.append(event)
    _AUDIT.parent.mkdir(exist_ok=True)
    with _AUDIT.open("a") as f:
        f.write(json.dumps(event, default=str) + "\n")
    for q in list(_subscribers):
        q.put(event)
