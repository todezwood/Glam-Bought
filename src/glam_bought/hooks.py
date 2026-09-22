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

from . import config, events, memory, web

# Human-language labels for the UI's activity card (never show raw tool names)
STEPS = {
    "recall_beauty_memory": ("remember", "Remembering you"),
    "remember_beauty_fact": ("remember", "Noting that down"),
    "check_wellness": ("context", "Checking your ring"),
    "check_calendar": ("context", "Checking your calendar"),
    "search_engine": ("shop", "Shopping the web"),
    "web_data_amazon_product_search": ("shop", "Shopping the web"),
    "web_data_amazon_product": ("shop", "Checking today's prices"),
    "scrape_as_markdown": ("shop", "Checking today's prices"),
    "rank_products": ("balance", "Balancing your budget"),
    "create_shopping_plan": ("act", "Preparing your plan"),
    "add_calendar_event": ("act", "Adding to your calendar"),
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
            events.emit("market_write", label=url)

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


def _context(kind: str) -> str:
    """The same JSON the check_wellness / check_calendar tools return, fetched ahead of the model."""
    hit = _context_cache.get(kind)
    if hit and time.time() - hit[0] < CONTEXT_TTL[kind]:
        return hit[1]
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


class MemoryHook(HookProvider):
    """Everything the model needs to know, gathered before it speaks, all in parallel: the user's
    profile (cached; refreshed after every memory write), what the market dataset knows about this
    request (Sephora/Ulta shelf seeded from Bright Data, and pages scraped in earlier turns), and
    for a shopping request her ring and her calendar. Each Cognee recall is a ~10-30 s graph query;
    doing this here instead of as tool calls saves the model two or three round trips per turn."""

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
        shopping = wants_market(request)
        events.emit("tool_start", step="remember", label="Remembering you")
        if shopping:
            events.emit("tool_start", step="context", label="Checking your day", tool="context")
        with ThreadPoolExecutor(max_workers=4) as pool:
            profile_f = pool.submit(self._profile) if _profile_cache is None else None
            market_f = pool.submit(self._market, request) if shopping else None
            ring_f = pool.submit(_context, "ring") if shopping else None
            cal_f = pool.submit(_context, "calendar") if shopping else None
            if profile_f:
                _profile_cache = profile_f.result()
            market = market_f.result() if market_f else ""
            ring = ring_f.result() if ring_f else (_context_cache.get("ring") or (0, ""))[1]
            cal = cal_f.result() if cal_f else (_context_cache.get("calendar") or (0, ""))[1]
        recalled = _profile_cache or ""
        events.emit("tool_end", step="remember", label="Remembering you", detail=recalled[:400])
        if shopping:
            events.emit("tool_end", step="context", label="Checking your day", tool="context",
                        detail=" · ".join(s for s in (_summary(ring), _summary(cal)) if s)[:160])
        event.agent.system_prompt = (
            f"{self.base_prompt}\n\n<today>\n{config.today_line()}\n</today>"
            f"\n\n<beauty_brain_recall>\n{recalled}\n</beauty_brain_recall>"
            + (f"\n\n<market_recall>\n{market}\n</market_recall>" if market else "")
            + (f"\n\n<ring>\n{ring}\n</ring>" if ring else "")
            + (f"\n\n<calendar>\n{cal}\n</calendar>" if cal else "")
        )

    def _profile(self) -> str:
        try:
            return memory.recall_text(PROFILE_QUERY, [config.DS_PROFILE, config.DS_PURCHASES])
        except Exception as e:  # memory being down must not take the agent down
            return f"(memory unavailable: {e})"

    def _market(self, request: str) -> str:
        if request not in _market_cache:
            try:
                _market_cache[request] = memory.recall_text(
                    f"Products, prices, finishes and in-stock shades relevant to: {request}", [config.DS_MARKET], top_k=6)
            except Exception:
                _market_cache[request] = ""
        return _market_cache[request]


def last_user_text(messages) -> str:
    for msg in reversed(list(messages or [])):
        if msg.get("role") == "user":
            return " ".join(c.get("text", "") for c in msg.get("content", []) if isinstance(c, dict)).strip()
    return ""


def wants_market(request: str) -> bool:
    """Shopping-shaped requests get a market recall; approvals and short remarks do not."""
    import re

    if len(request) < 15 or re.match(r"^\s*(yes|yep|approve|approved|go ahead|do it|proceed|confirm|looks good)\b", request, re.I):
        return False
    return True


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
