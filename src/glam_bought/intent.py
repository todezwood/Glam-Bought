"""What is this turn about? A fast model call reads the message against the last two turns and
answers with an intent, the context sources it needs, and a headline for the activity card.
It fails open: any error, timeout or unusable answer falls back to a heuristic, so a turn never
waits on the classifier or breaks because of it."""
import json
import re
import sys
from dataclasses import dataclass, field

from . import config

INTENTS = ("new_request", "refine", "approve", "question")
SOURCES_ALLOWED = {
    "new_request": {"profile", "ring", "calendar", "market"},
    "refine": {"profile", "market"},
    "approve": set(),
    "question": {"profile"},
}
CLASSIFIER_TIMEOUT = 3.0
HEADLINE_MAX = 40

APPROVAL = re.compile(r"^\s*(yes|yep|approve|approved|go ahead|do it|proceed|confirm|looks good)\b", re.I)
REFINE_WORDS = re.compile(
    r"\b(swap|instead|change|replace|switch|lighter|cheaper|different|another|other one|remove|drop|add|without|make it)\b", re.I)

SYSTEM = """You route one message in a personal beauty-shopping chat. Reply with a single JSON object and nothing else:
{"intent": "new_request" | "refine" | "approve" | "question",
 "headline": "2-5 words, Title Case, what this turn is about (e.g. 'Sunscreen for Switzerland', 'A Lighter Sunscreen', 'Your Plan', 'About Your Sleep')",
 "item": "for refine only: the product type being changed, e.g. 'body sunscreen'; else empty",
 "sources": ["profile", "ring", "calendar", "market"]}
Definitions:
- new_request: asks to find, compare or buy products; a fresh basket. sources: all four.
- refine: changes, swaps, adds or removes an item in the basket shown in the previous assistant turn, or adjusts its budget, retailer, shade or constraints. sources: ["profile"], plus "market" if the replacement is a type the shelf may hold.
- approve: agrees to the plan as presented. sources: [].
- question: anything else: questions about her profile, past purchases, ingredients, sleep or calendar; statements about herself or a product she used; small talk. sources: ["profile"].
When the message says "it", "that one", "the second one" and a basket is present, it is a refine."""


@dataclass
class TurnPlan:
    intent: str
    headline: str
    item: str = ""
    sources: set[str] = field(default_factory=set)
    source: str = "model"  # "model" or "heuristic": which one decided


def wants_market(request: str) -> bool:
    """Shopping-shaped requests get a market recall; approvals and short remarks do not."""
    return len(request) >= 15 and not APPROVAL.match(request)


def _text(msg) -> str:
    return " ".join(c.get("text", "") for c in msg.get("content", []) if isinstance(c, dict)).strip()


def has_basket(messages) -> bool:
    """Did the last assistant turn present a basket? Then "swap it" has something to swap."""
    for msg in reversed(list(messages or [])):
        if msg.get("role") == "assistant":
            return "```basket" in _text(msg)
    return False


def _basket_names(text: str) -> str:
    m = re.search(r"```basket\s*([\s\S]*?)```", text)
    if not m:
        return ""
    try:
        items = json.loads(m.group(1)).get("items", [])
        return ", ".join(f"{i.get('brand', '')} {i.get('name', '')}".strip() for i in items if isinstance(i, dict))
    except (ValueError, AttributeError):
        return ""


def recent_turns(messages, n: int = 2) -> str:
    """The last n user/assistant exchanges, compact: the basket block becomes its item names."""
    lines = []
    for msg in [m for m in list(messages or []) if m.get("role") in ("user", "assistant")][-2 * n:]:
        text = _text(msg)
        if msg["role"] == "assistant":
            names = _basket_names(text)
            text = re.sub(r"```basket[\s\S]*?```", f"[basket: {names}]" if names else "[basket]", text)
            text = text[:600]
        lines.append(f"{msg['role']}: {text}")
    return "\n".join(lines)


def _first_words(message: str, n: int = 5) -> str:
    words = re.findall(r"\S+", message)[:n]
    return " ".join(words).strip(" .,!?") or "This request"


def heuristic(message: str, approved: bool, basket: bool) -> TurnPlan:
    """No model: the old gate plus 'is there a basket to change'."""
    if approved or APPROVAL.match(message):
        return TurnPlan("approve", "Your plan", sources=set(), source="heuristic")
    if not wants_market(message):
        return TurnPlan("question", _first_words(message), sources={"profile"}, source="heuristic")
    if basket and REFINE_WORDS.search(message):
        return TurnPlan("refine", "A change to your basket", sources={"profile"}, source="heuristic")
    return TurnPlan("new_request", _first_words(message), sources=set(SOURCES_ALLOWED["new_request"]), source="heuristic")


def _parse(text: str, fallback: TurnPlan) -> TurnPlan | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    data = json.loads(m.group(0))
    intent = data.get("intent")
    if intent not in INTENTS:
        return None
    headline = str(data.get("headline") or "").strip()[:HEADLINE_MAX] or fallback.headline
    item = str(data.get("item") or "").strip()[:60] if intent == "refine" else ""
    raw = data.get("sources")
    sources = {s for s in raw if isinstance(s, str)} if isinstance(raw, list) else set(SOURCES_ALLOWED[intent])
    sources &= SOURCES_ALLOWED[intent]
    if intent == "new_request":
        sources = set(SOURCES_ALLOWED[intent])  # a fresh basket always gets her day and the shelf
    elif intent == "question":
        sources.add("profile")  # the recall is always injected; never let the model drop it
    return TurnPlan(intent, headline, item=item, sources=sources, source="model")


def classify(message: str, messages, approved: bool = False) -> TurnPlan:
    """The plan for this turn. Approvals never pay for a model call; everything else does,
    inside a hard time budget, and falls back to the heuristic."""
    basket = has_basket(messages)
    fallback = heuristic(message, approved, basket)
    if approved or fallback.intent == "approve":
        return fallback
    user = (f"<basket_present>{'true' if basket else 'false'}</basket_present>\n"
            f"<previous_turns>\n{recent_turns(messages)}\n</previous_turns>\n<message>\n{message}\n</message>")
    try:
        plan = _parse(config.classifier_call(SYSTEM, user, timeout=CLASSIFIER_TIMEOUT), fallback)
    except Exception as e:  # noqa: BLE001 - the classifier is best-effort by design
        print(f"[intent] classifier failed, using heuristic: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        plan = None
    plan = plan or fallback
    print(f"[intent] {plan.intent} ({plan.source}) '{plan.headline}' sources={sorted(plan.sources)}"
          + (f" item='{plan.item}'" if plan.item else ""), file=sys.stderr)
    return plan


def steps_for(plan: TurnPlan, fetched: dict) -> list[list[str]]:
    """The rows the activity card declares up front, in order. Only steps that will run."""
    if plan.intent == "approve":
        return [["plan", "Preparing your plan"]]
    if plan.intent == "question":
        return []
    steps = []
    if plan.intent == "new_request" or fetched.get("market"):
        steps.append(["remember", "Remembering you"])
    if fetched.get("ring") or fetched.get("calendar"):
        steps.append(["context", "Checking your day"])
    steps += [["shop", "Shopping the web"], ["balance", "Balancing your budget"]]
    return steps
