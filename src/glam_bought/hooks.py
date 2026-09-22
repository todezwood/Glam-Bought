"""Strands hooks: memory injection, audit trail, and the spend gate."""
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
    "search_engine": ("shop", "Shopping the web"),
    "scrape_as_markdown": ("shop", "Checking today's prices"),
    "rank_products": ("balance", "Balancing your budget"),
    "create_shopping_plan": ("act", "Preparing your plan"),
}
SCRAPE_TOOLS = {"scrape_as_markdown"}


class MarketMemoryHook(HookProvider):
    """Bright Data -> Cognee. Every page the agent scrapes is remembered into the market
    dataset with its source URL and timestamp, and trimmed before it reaches the model."""

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(AfterToolCallEvent, self.after_scrape)

    def after_scrape(self, event: AfterToolCallEvent) -> None:
        name = event.tool_use.get("name", "")
        if (name not in SCRAPE_TOOLS and not name.startswith("web_data_")) or event.exception:
            return
        url = (event.tool_use.get("input") or {}).get("url", "")
        page = web.trim_product_page(web.result_text(event.result))
        if not page:
            return
        retrieved_at = web.now()
        stamped = f"source_url: {url}\nretrieved_at: {retrieved_at}\n\n{page}"
        event.result["content"] = [{"text": stamped}]  # trimmed + stamped for the model
        memory.remember_in_background(f"[source: live_web] [source_url: {url}] [retrieved_at: {retrieved_at}]\n{page}", config.DS_MARKET)
        events.emit("market_write", label=url)

PROFILE_QUERY = (
    "Summarise this user's beauty profile: skin type, sensitivities, colour analysis, "
    "ingredients to avoid, products they liked or disliked and why, budget style, preferred retailers."
)


class MemoryHook(HookProvider):
    """Memory, injected before every turn."""

    def __init__(self, base_prompt: str):
        self.base_prompt = base_prompt

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeInvocationEvent, self.inject)

    def inject(self, event: BeforeInvocationEvent) -> None:
        events.emit("tool_start", step="remember", label="Remembering you")
        try:
            recalled = memory.recall_text(PROFILE_QUERY, [config.DS_PROFILE, config.DS_PURCHASES])
        except Exception as e:  # memory being down must not take the agent down
            recalled = f"(memory unavailable: {e})"
        events.emit("tool_end", step="remember", label="Remembering you", detail=recalled[:400])
        event.agent.system_prompt = (
            f"{self.base_prompt}\n\n<beauty_brain_recall>\n{recalled}\n</beauty_brain_recall>"
        )


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
        events.emit("tool_end", step=step, label=label, tool=name, error=str(event.exception or ""))


class SteeringHook(HookProvider):
    """Spend gate: purchase actions are cancelled unless the user approved this turn."""

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self.gate)

    def gate(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use.get("name") != "create_shopping_plan":
            return
        if not event.invocation_state.get("user_approved", False):
            events.emit("blocked", label="Waiting for your approval")
            event.cancel_tool = (
                "Blocked by approval gate: the user has not approved a plan in this message. "
                "Present the plan and ask them to approve it."
            )
