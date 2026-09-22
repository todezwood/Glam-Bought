"""Strands hooks: memory injection, audit trail, and the spend gate."""
from strands.hooks import (
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from . import config, events, memory

# Human-language labels for the UI's activity card (never show raw tool names)
STEPS = {
    "recall_beauty_memory": ("remember", "Remembering you"),
    "remember_beauty_fact": ("remember", "Noting that down"),
    "search_beauty_web": ("shop", "Shopping the web"),
    "refresh_market": ("shop", "Checking today's prices"),
    "rank_products": ("balance", "Balancing your budget"),
    "create_shopping_plan": ("act", "Preparing your plan"),
}

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
