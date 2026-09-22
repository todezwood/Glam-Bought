"""The pre-flight hook, driven per intent with every network call stubbed: which fetches run,
which card rows are announced, what the model is told, what the session remembers."""
import time
from types import SimpleNamespace

import pytest
from strands.agent.state import AgentState

from glam_bought import events, hooks, intent


class FakeAgent:
    def __init__(self, messages=None):
        self.messages = messages or []
        self.state = AgentState()
        self.system_prompt = ""


def run_turn(monkeypatch, message, plan, *, agent=None, approved=False, calls=None):
    """One inject() with stubs; returns (events emitted this turn, agent)."""
    calls = calls if calls is not None else []
    agent = agent or FakeAgent()
    monkeypatch.setattr(intent, "classify", lambda *a, **k: plan)
    monkeypatch.setattr(hooks.memory, "recall_text", lambda q, ds, top_k=12: calls.append(("recall", ds[0])) or f"recall:{ds[0]}")
    monkeypatch.setattr("glam_bought.oura.check_wellness", lambda *a, **k: calls.append(("ring",)) or '{"summary": "Readiness 67"}')
    monkeypatch.setattr("glam_bought.gcal.check_calendar", lambda *a, **k: calls.append(("cal",)) or '{"summary": "Next: Lien"}')
    seen = []
    monkeypatch.setattr(events, "emit", lambda type_, **d: seen.append({"type": type_, **d}))
    ev = SimpleNamespace(agent=agent, messages=[{"role": "user", "content": [{"text": message}]}],
                         invocation_state={"user_approved": approved})
    hooks.MemoryHook("BASE").inject(ev)
    return seen, agent


@pytest.fixture(autouse=True)
def cold_caches(monkeypatch):
    monkeypatch.setattr(hooks, "_profile_cache", None)
    monkeypatch.setattr(hooks, "_market_cache", {})
    monkeypatch.setattr(hooks, "_context_cache", {})


def steps(seen):
    return [s[0] for s in next(e for e in seen if e["type"] == "turn_start")["steps"]]


def rows_started(seen):
    return [e["step"] for e in seen if e["type"] == "tool_start"]


def test_new_request_fetches_everything_and_snapshots(monkeypatch):
    calls = []
    plan = intent.TurnPlan("new_request", "Sunscreen for Switzerland", sources={"profile", "ring", "calendar", "market"})
    seen, agent = run_turn(monkeypatch, "sunscreen for my Switzerland trip", plan, calls=calls)
    assert steps(seen) == ["remember", "context", "shop", "balance"]
    assert rows_started(seen) == ["remember", "context"]
    ctx = next(e for e in seen if e["type"] == "tool_start" and e["step"] == "context")
    assert ctx["detail"] == "Ring · Calendar"
    assert ("ring",) in calls and ("cal",) in calls and ("recall", "market_v2") in calls
    assert "<ring>" in agent.system_prompt and "<calendar>" in agent.system_prompt and "<market_recall>" in agent.system_prompt
    assert "<turn>" not in agent.system_prompt
    snap = agent.state.get("context")
    assert snap["headline"] == "Sunscreen for Switzerland" and "Readiness" in snap["ring"] and snap["market"]


def test_new_request_with_fresh_cache_shows_no_context_row(monkeypatch):
    hooks._context_cache.update({"ring": (time.time(), "cached-ring"), "calendar": (time.time(), "cached-cal")})
    hooks._profile_cache = "Dry, sensitive"
    calls = []
    plan = intent.TurnPlan("new_request", "A Serum", sources={"profile", "ring", "calendar", "market"})
    seen, agent = run_turn(monkeypatch, "find me a hydrating serum please", plan, calls=calls)
    assert steps(seen) == ["remember", "shop", "balance"]  # remember shows, instantly done, with the pills
    assert rows_started(seen) == ["remember"]
    assert ("ring",) not in calls and ("cal",) not in calls
    assert "cached-ring" in agent.system_prompt and "cached-cal" in agent.system_prompt


def test_refine_reuses_snapshot_and_fetches_nothing(monkeypatch):
    calls = []
    agent = FakeAgent()
    hooks._profile_cache = "Dry"
    agent.state.set("context", {"ring": "snap-ring", "calendar": "snap-cal", "market": "snap-market", "at": 1.0})
    plan = intent.TurnPlan("refine", "A Lighter Sunscreen", item="face sunscreen", sources={"profile", "market"})
    seen, agent = run_turn(monkeypatch, "swap it for something lighter", plan, agent=agent, calls=calls)
    assert steps(seen) == ["shop", "balance"]
    assert rows_started(seen) == []
    assert calls == []
    assert "snap-ring" in agent.system_prompt and "snap-cal" in agent.system_prompt and "snap-market" in agent.system_prompt
    assert "<turn>" in agent.system_prompt and "(item: face sunscreen)" in agent.system_prompt


def test_refine_without_snapshot_fetches_honestly(monkeypatch):
    calls = []
    hooks._profile_cache = "Dry"
    plan = intent.TurnPlan("refine", "A Lighter Sunscreen", item="face sunscreen", sources={"profile", "market"})
    seen, agent = run_turn(monkeypatch, "swap it for something lighter", plan, calls=calls)
    assert steps(seen) == ["remember", "context", "shop", "balance"]
    assert ("recall", "market_v2") in calls and ("ring",) in calls and ("cal",) in calls
    assert agent.state.get("context")["market"] == "recall:market_v2"  # narrow recall merged for the next follow-up


def test_approve_fetches_nothing(monkeypatch):
    calls = []
    hooks._profile_cache = "Dry"
    agent = FakeAgent()
    agent.state.set("context", {"ring": "snap-ring", "calendar": "snap-cal", "market": "", "at": 1.0})
    plan = intent.TurnPlan("approve", "Your plan", sources=set(), source="heuristic")
    seen, agent = run_turn(monkeypatch, "yes", plan, agent=agent, approved=True, calls=calls)
    assert steps(seen) == ["plan"] and rows_started(seen) == [] and calls == []
    assert "The user approved the plan" in agent.system_prompt and "snap-ring" in agent.system_prompt


def test_question_is_profile_only(monkeypatch):
    calls = []
    plan = intent.TurnPlan("question", "About Your Sleep", sources={"profile"})
    seen, agent = run_turn(monkeypatch, "how have I been sleeping lately?", plan, calls=calls)
    assert steps(seen) == [] and rows_started(seen) == []
    assert calls == [("recall", "beauty_profile_v3")]
    assert "<ring>" not in agent.system_prompt and "not a shopping request" in agent.system_prompt
