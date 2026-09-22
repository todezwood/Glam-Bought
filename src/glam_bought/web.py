"""Bright Data: the agent's eyes on the beauty market, via the Bright Data MCP server.

Pipeline: Bright Data (MCP tools) -> Cognee (MarketMemoryHook remembers every scrape) -> Strands.
The agent calls Bright Data's own MCP tools directly; nothing is wrapped.
"""
import re
from datetime import datetime, timezone

from strands.tools.mcp import MCPClient

from . import config

MAX_CHARS = 7000
# The subset of Bright Data MCP tools the agent gets. Add web_data_* dataset tools if a
# retailer we need has one.
ALLOWED_TOOLS = ["search_engine", "scrape_as_markdown", re.compile(r"^web_data_(amazon|walmart)_product$")]
_client: MCPClient | None = None


def client() -> MCPClient:
    """Started Bright Data MCP client (hosted endpoint; no local npx needed)."""
    global _client
    if _client is None:
        if not config.BRIGHTDATA_API_TOKEN:
            raise RuntimeError("Set BRIGHTDATA_API_TOKEN in .env")
        _client = MCPClient(
            url=f"https://mcp.brightdata.com/mcp?token={config.BRIGHTDATA_API_TOKEN}",
            startup_timeout=60,
            tool_filters={"allowed": ALLOWED_TOOLS},
        )
        _client.start()
    return _client


def tools() -> list:
    """Bright Data MCP tools, ready to hand to the Strands Agent."""
    return list(client().list_tools_sync())


def call(name: str, arguments: dict, timeout_s: int = 90) -> str:
    """Direct call (used by the build_market script and smoke tests)."""
    import uuid
    from datetime import timedelta

    result = client().call_tool_sync(
        tool_use_id=str(uuid.uuid4()), name=name, arguments=arguments,
        read_timeout_seconds=timedelta(seconds=timeout_s),
    )
    return result_text(result)


def result_text(result) -> str:
    content = result.get("content", []) if isinstance(result, dict) else getattr(result, "content", [])
    parts = []
    for c in content:
        text = c.get("text") if isinstance(c, dict) else getattr(c, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="minutes")


def trim_product_page(markdown: str) -> str:
    """Keep the part of a retailer page that carries product facts; drop nav/footer noise."""
    lines = [ln for ln in markdown.splitlines() if ln.strip() and not ln.lstrip().startswith(("![", "[![", "* [", "- ["))]
    text = "\n".join(lines)
    lower = text.lower()
    head = text[: MAX_CHARS // 2]
    idx = lower.find("ingredients")
    tail = text[max(idx - 200, 0): idx + MAX_CHARS // 2] if idx > len(head) else ""
    return (head + "\n...\n" + tail) if tail else text[:MAX_CHARS]
