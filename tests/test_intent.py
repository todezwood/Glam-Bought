"""Pure tests for the turn router and the freshness helpers. No network."""
import json
import time

import pytest

from glam_bought import config, hooks, intent


def basket_messages():
    return [{"role": "user", "content": [{"text": "I need a face sunscreen for my trip"}]},
            {"role": "assistant", "content": [{"text": "Here you go.\n```basket\n"
              + json.dumps({"items": [{"brand": "Beauty of Joseon", "name": "Relief Sun"}]}) + "\n```"}]}]


# ---- heuristic -----------------------------------------------------------------------------

def test_heuristic_approve():
    assert intent.heuristic("yes please", False, True).intent == "approve"
    assert intent.heuristic("anything at all here", True, False).intent == "approve"


def test_heuristic_refine_needs_basket():
    msg = "swap the sunscreen for something lighter"
    assert intent.heuristic(msg, False, True).intent == "refine"
    assert intent.heuristic(msg, False, False).intent == "new_request"


def test_heuristic_question():
    p = intent.heuristic("thanks", False, False)
    assert p.intent == "question" and p.sources == {"profile"}


def test_has_basket_and_recent_turns():
    msgs = basket_messages()
    assert intent.has_basket(msgs)
    assert not intent.has_basket(msgs[:1])
    turns = intent.recent_turns(msgs)
    assert "[basket: Beauty of Joseon Relief Sun]" in turns and "```" not in turns


# ---- classify: fail-open -------------------------------------------------------------------

@pytest.mark.parametrize("bad", [RuntimeError("boom"), "no json here", '{"intent": "nonsense"}', "{not json"])
def test_classify_falls_back(monkeypatch, bad):
    def fake(system, user, timeout=3.0, max_tokens=200):
        if isinstance(bad, Exception):
            raise bad
        return bad
    monkeypatch.setattr(config, "classifier_call", fake)
    p = intent.classify("find me a hydrating serum under $40", [], approved=False)
    assert p.intent == "new_request" and p.source == "heuristic"


def test_classify_skips_model_when_approved(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "classifier_call", lambda *a, **k: calls.append(1) or "{}")
    p = intent.classify("go ahead", basket_messages(), approved=True)
    assert p.intent == "approve" and p.sources == set() and not calls


def test_classify_happy_path_clips_sources(monkeypatch):
    monkeypatch.setattr(config, "classifier_call", lambda *a, **k:
                        'Sure: {"intent": "refine", "headline": "A Lighter Sunscreen", "item": "face sunscreen", '
                        '"sources": ["profile", "ring", "calendar", "market"]}')
    p = intent.classify("make it lighter", basket_messages(), approved=False)
    assert p.intent == "refine" and p.source == "model"
    assert p.headline == "A Lighter Sunscreen" and p.item == "face sunscreen"
    assert p.sources == {"profile", "market"}  # ring/calendar are never fetched on a refine


def test_classify_new_request_always_gets_every_source(monkeypatch):
    monkeypatch.setattr(config, "classifier_call", lambda *a, **k:
                        '{"intent": "new_request", "headline": "Sunscreen for Switzerland", "sources": ["market"]}')
    p = intent.classify("sunscreen for Switzerland please", [], approved=False)
    assert p.sources == {"profile", "ring", "calendar", "market"}


# ---- freshness + steps ---------------------------------------------------------------------

def test_context_fresh(monkeypatch):
    monkeypatch.setitem(hooks._context_cache, "calendar", (time.time() - 200, "old"))
    assert not hooks.context_fresh("calendar")
    assert hooks.cached_context("calendar") == "old"
    monkeypatch.setitem(hooks._context_cache, "calendar", (time.time() - 30, "new"))
    assert hooks.context_fresh("calendar")
    monkeypatch.delitem(hooks._context_cache, "ring", raising=False)
    assert not hooks.context_fresh("ring") and hooks.cached_context("ring") == ""


def test_steps_for():
    new = intent.TurnPlan("new_request", "x")
    assert [s[0] for s in intent.steps_for(new, {})] == ["remember", "shop", "balance"]
    assert [s[0] for s in intent.steps_for(new, {"ring": True})] == ["remember", "context", "shop", "balance"]
    ref = intent.TurnPlan("refine", "x")
    assert [s[0] for s in intent.steps_for(ref, {})] == ["shop", "balance"]
    assert [s[0] for s in intent.steps_for(ref, {"market": True})] == ["remember", "shop", "balance"]
    assert [s[0] for s in intent.steps_for(intent.TurnPlan("approve", "x"), {})] == ["plan"]
    assert intent.steps_for(intent.TurnPlan("question", "x"), {}) == []
