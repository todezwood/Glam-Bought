"""Strands hooks: memory injection, audit trail, and the spend gate."""
import json
import time

from strands.hooks import (
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from . import config, events, intent, memory, web

# Human-language labels for the UI's activity card (never show raw tool names)
STEPS = {
    "recall_beauty_memory": ("remember", "Remembering you"),
    "remember_beauty_fact": ("note", "Noting that down"),
    "check_wellness": ("context", "Checking your ring"),
    "check_calendar": ("context", "Checking your calendar"),
    "search_engine": ("shop", "Shopping the web"),
    "web_data_amazon_product_search": ("shop", "Shopping the web"),
    "web_data_amazon_product": ("shop", "Checking today's prices"),
    "rank_products": ("balance", "Balancing your budget"),
    "create_shopping_plan": ("plan", "Preparing your plan"),
    "add_calendar_event": ("calendar", "Adding to your calendar"),
}
CONTEXT_TOOLS = {"check_wellness", "check_calendar"}
GATED_TOOLS = {"create_shopping_plan", "add_calendar_event"}
WEB_TOOLS = {"search_engine", "web_data_amazon_product_search", "web_data_amazon_product", "scrape_as_markdown"}
PAGE_TOOLS = {"web_data_amazon_product", "scrape_as_markdown"}


class MarketMemoryHook(HookProvider):
    """Bright Data -> Cognee. Every Bright Data result is compacted before it reaches the
    model; every product page is remembered into the market dataset with its source URL
    and timestamp."""

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(AfterToolCallEvent, self.after_web)

    def after_web(self, event: AfterToolCallEvent) -> None:
        name = event.tool_use.get("name", "")
        if name not in WEB_TOOLS or event.exception:
            return
        facts = web.compact(name, web.result_text(event.result))
        if not facts:
            return
        url = (event.tool_use.get("input") or {}).get("url", "")
        retrieved_at = web.now()
        event.result["content"] = [{"text": f"retrieved_at: {retrieved_at}\n{facts}"}]
        if name in PAGE_TOOLS:
            memory.remember_in_background(
                f"[source: live_web] [source_url: {url}] [retrieved_at: {retrieved_at}]\n{facts}", config.DS_MARKET)
            events.emit("market_write", step="shop", label=url)

PROFILE_QUERY = (
    "Summarise this user's beauty profile: skin type and skin concerns, climate, sun protection needs, "
    "hero ingredients she wants (such as PDRN), ingredients or product types to avoid, products she liked "
    "or disliked and why, budget style and value philosophy, travel habits, and who she shops for."
)


# Process-wide caches, shared by every browser session's agent: a new device does not pay the
# 15 s profile recall again, and the ring/calendar readings are re-read only when stale.
_profile_cache: str | None = None
_market_cache: dict[str, str] = {}
_context_cache: dict[str, tuple[float, str]] = {}
CONTEXT_TTL = {"ring": 600, "calendar": 120}


def context_fresh(kind: str) -> bool:
    """Is the cached ring/calendar reading still inside its TTL? Never fetches."""
    hit = _context_cache.get(kind)
    return bool(hit) and time.time() - hit[0] < CONTEXT_TTL[kind]


def cached_context(kind: str) -> str:
    """Whatever reading the process holds, however old ("" if none). Never fetches."""
    hit = _context_cache.get(kind)
    return hit[1] if hit else ""


def _context(kind: str) -> str:
    """The same JSON the check_wellness / check_calendar tools return, fetched ahead of the model."""
    if context_fresh(kind):
        return cached_context(kind)
    from . import gcal, oura

    text = oura.check_wellness() if kind == "ring" else gcal.check_calendar()
    _context_cache[kind] = (time.time(), text)
    return text


def _summary(text: str) -> str:
    try:
        parsed = json.loads(text)
        return (parsed.get("summary") if isinstance(parsed, dict) else None) or text[:80]
    except ValueError:
        return text[:80]


# What the hook tells the model about the shape of this turn, appended after the context blocks.
TURN_NOTES = {
    "refine": (
        "This message adjusts the basket you presented in your previous reply{item}. Change only "
        "what the user asked; keep every other item exactly as presented (same product, price, url, "
        "evidence). The <ring> and <calendar> blocks are the same readings you already reasoned with: "
        "do not call check_wellness or check_calendar. Research live only for the replacement, call "
        "rank_products once with the full new set, and re-emit the complete basket block."),
    "approve": "The user approved the plan. Act now (rule 8); do not re-research or re-rank.",
    "question": (
        "This is a question or a statement, not a shopping request. Answer from the recall; no basket "
        "block. If she tells you something about herself or a product, remember it."),
}


class MemoryHook(HookProvider):
    """Everything the model needs to know, gathered before it speaks, and only what this turn
    needs: a fast classifier reads the message (new request, a change to the basket, an approval,
    a question), and the hook fetches the profile, the market shelf, the ring and the calendar
    accordingly. A new request writes a context snapshot into the session; follow-ups reuse it
    verbatim, so the model keeps reasoning with the same readings without re-fetching them.
    Each Cognee recall is a ~10-30 s graph query; doing this here instead of as tool calls saves
    the model two or three round trips per turn."""

    def __init__(self, base_prompt: str):
        self.base_prompt = base_prompt

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeInvocationEvent, self.inject)
        registry.add_callback(AfterToolCallEvent, self.invalidate)

    def invalidate(self, event: AfterToolCallEvent) -> None:
        global _profile_cache
        if event.tool_use.get("name") == "remember_beauty_fact" and not event.exception:
            _profile_cache = None

    def inject(self, event: BeforeInvocationEvent) -> None:
        global _profile_cache
        from concurrent.futures import ThreadPoolExecutor

        # Strands fires this before the new turn's messages join agent.messages: read them from the
        # event, else the hook would see the previous request (or nothing on a fresh session).
        request = last_user_text(getattr(event, "messages", None) or event.agent.messages)
        approved = bool((getattr(event, "invocation_state", None) or {}).get("user_approved"))
        snap = event.agent.state.get("context") or {}

        with ThreadPoolExecutor(max_workers=4) as pool:
            # The profile is always injected; on a cold cache its recall overlaps the classifier.
            profile_f = pool.submit(self._profile) if _profile_cache is None else None
            plan = intent.classify(request, event.agent.messages, approved)

            # Decide, without any I/O, what this turn really has to fetch.
            fetch = {"profile": profile_f is not None, "ring": False, "calendar": False, "market": False}
            ring = cal = market = ""
            if plan.intent == "new_request":
                fetch["ring"] = "ring" in plan.sources and not context_fresh("ring")
                fetch["calendar"] = "calendar" in plan.sources and not context_fresh("calendar")
                fetch["market"] = "market" in plan.sources
                ring = cached_context("ring") if "ring" in plan.sources else ""
                cal = cached_context("calendar") if "calendar" in plan.sources else ""
            elif plan.intent in ("refine", "approve"):
                ring, cal, market = snap.get("ring", ""), snap.get("calendar", ""), snap.get("market", "")
                if plan.intent == "refine":
                    if not ring:  # no snapshot (first turn after a reload): use the cache, or fetch honestly
                        ring, fetch["ring"] = cached_context("ring"), not context_fresh("ring")
                    if not cal:
                        cal, fetch["calendar"] = cached_context("calendar"), not context_fresh("calendar")
                    fetch["market"] = "market" in plan.sources and bool(plan.item) and not market

            events.emit("turn_start", intent=plan.intent, headline=plan.headline, source=plan.source,
                        steps=intent.steps_for(plan, fetch))

            market_key = plan.item if plan.intent == "refine" else request
            market_f = pool.submit(self._market, market_key, 4 if plan.intent == "refine" else 6) if fetch["market"] else None
            ring_f = pool.submit(_context, "ring") if fetch["ring"] else None
            cal_f = pool.submit(_context, "calendar") if fetch["calendar"] else None

            show_remember = plan.intent == "new_request" or fetch["market"]
            if show_remember:
                events.emit("tool_start", step="remember", label="Remembering you")
            if ring_f or cal_f:
                what = " · ".join(s for s, f in (("Ring", ring_f), ("Calendar", cal_f)) if f)
                events.emit("tool_start", step="context", label="Checking your day", tool="context", detail=what)

            if profile_f:
                _profile_cache = profile_f.result()
            if market_f:
                market = market_f.result()
            if ring_f:
                ring = ring_f.result()
            if cal_f:
                cal = cal_f.result()
        recalled = _profile_cache or ""
        if show_remember:  # instant when cached: the row completes at once with the memory pills
            events.emit("tool_end", step="remember", label="Remembering you", detail=recalled[:400])
        if ring_f or cal_f:
            events.emit("tool_end", step="context", label="Checking your day", tool="context",
                        detail=" · ".join(s for s, f in ((_summary(ring), ring_f), (_summary(cal), cal_f)) if f and s)[:160])

        note = TURN_NOTES.get(plan.intent, "")
        if plan.intent == "refine":
            note = note.format(item=f" (item: {plan.item})" if plan.item else "")
        event.agent.system_prompt = (
            f"{self.base_prompt}\n\n<today>\n{config.today_line()}\n</today>"
            f"\n\n<beauty_brain_recall>\n{recalled}\n</beauty_brain_recall>"
            + (f"\n\n<market_recall>\n{market}\n</market_recall>" if market else "")
            + (f"\n\n<ring>\n{ring}\n</ring>" if ring else "")
            + (f"\n\n<calendar>\n{cal}\n</calendar>" if cal else "")
            + (f"\n\n<turn>\n{note}\n</turn>" if note else "")
        )

        if plan.intent == "new_request":
            event.agent.state.set("context", {"request": request, "headline": plan.headline, "intent": plan.intent,
                                              "market": market, "ring": ring, "calendar": cal, "at": time.time()})
        elif plan.intent == "refine" and fetch["market"] and market:
            event.agent.state.set("context", {**snap, "market": market})

    def _profile(self) -> str:
        try:
            return memory.recall_text(PROFILE_QUERY, [config.DS_PROFILE, config.DS_PURCHASES])
        except Exception as e:  # memory being down must not take the agent down
            return f"(memory unavailable: {e})"

    @staticmethod
    def warm_profile() -> None:
        """Fill the profile cache ahead of a live turn (the server does this while a tile replays)."""
        global _profile_cache
        if _profile_cache is None:
            _profile_cache = memory.recall_text(PROFILE_QUERY, [config.DS_PROFILE, config.DS_PURCHASES])

    def _market(self, request: str, top_k: int = 6) -> str:
        if request not in _market_cache:
            try:
                _market_cache[request] = memory.recall_text(
                    f"Products, prices, finishes and in-stock shades relevant to: {request}", [config.DS_MARKET], top_k=top_k)
            except Exception:
                _market_cache[request] = ""
        return _market_cache[request]


def last_user_text(messages) -> str:
    for msg in reversed(list(messages or [])):
        if msg.get("role") == "user":
            return " ".join(c.get("text", "") for c in msg.get("content", []) if isinstance(c, dict)).strip()
    return ""


wants_market = intent.wants_market  # kept for callers that import it from here


class AuditHook(HookProvider):
    """Every tool call is logged and streamed to the UI."""

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before)
        registry.add_callback(AfterToolCallEvent, self.after)

    def before(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use.get("name", "")
        step, label = STEPS.get(name, ("shop", "Working"))
        events.emit("tool_start", step=step, label=label, tool=name, input=event.tool_use.get("input"))

    def after(self, event: AfterToolCallEvent) -> None:
        name = event.tool_use.get("name", "")
        step, label = STEPS.get(name, ("shop", "Working"))
        detail = None
        if name in CONTEXT_TOOLS:  # the UI shows the one-line summary under "Checking your day"
            text = web.result_text(event.result)
            try:
                parsed = json.loads(text)
                detail = parsed.get("summary") if isinstance(parsed, dict) else None
            except ValueError:
                detail = None
            detail = (detail or text)[:160]
        error = str(event.exception or getattr(event, "cancel_message", None) or "")
        events.emit("tool_end", step=step, label=label, tool=name, detail=detail, error=error)


class SteeringHook(HookProvider):
    """Spend gate: purchase and calendar actions are cancelled unless the user approved this turn."""

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self.gate)

    def gate(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use.get("name") not in GATED_TOOLS:
            return
        if not event.invocation_state.get("user_approved", False):
            events.emit("blocked", label="Waiting for your approval")
            event.cancel_tool = (
                "Blocked by approval gate: the user has not approved in this message. Present the "
                "plan (and the proposed pickup time, if there is an occasion) and ask them to approve."
            )
