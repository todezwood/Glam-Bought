"""Bright Data: the agent's eyes on the beauty market.

Pipeline: Bright Data -> Cognee -> Strands. Everything scraped here is handed to the
agent AND remembered into the `market` dataset with its source URL and timestamp.
"""
import uuid
from datetime import datetime, timedelta, timezone

from strands import tool
from strands.tools.mcp import MCPClient

from . import config, memory

MAX_CHARS = 7000
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
        )
        _client.start()
    return _client


def call(name: str, arguments: dict, timeout_s: int = 90) -> str:
    result = client().call_tool_sync(
        tool_use_id=str(uuid.uuid4()), name=name, arguments=arguments,
        read_timeout_seconds=timedelta(seconds=timeout_s),
    )
    parts = [c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)]
    return "\n".join(parts)


def _now() -> str:
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


@tool
def search_beauty_web(query: str) -> str:
    """Search the live public web (via Bright Data) for beauty products, retailers, sales,
    reviews and ingredient information. Returns search results with titles, URLs, snippets.
    Use specific queries, e.g. "fragrance-free natural finish foundation dry skin sephora".

    Args:
        query: The search query.
    """
    return call("search_engine", {"query": query, "engine": "google"})[:MAX_CHARS]


@tool
def refresh_market(url: str) -> str:
    """Fetch a live product or retailer page (via Bright Data) to get CURRENT price, sale
    price, stock, size, shade range, finish and the full ingredient list. The observation is
    also saved to the Beauty Brain's market dataset with its source URL and timestamp.
    Prices and availability are time-sensitive observations, not permanent facts.

    Args:
        url: Full https URL of the product page.
    """
    page = trim_product_page(call("scrape_as_markdown", {"url": url}))
    retrieved_at = _now()
    memory.remember_in_background(
        f"[source: live_web] [source_url: {url}] [retrieved_at: {retrieved_at}]\n{page}", config.DS_MARKET
    )
    return f"source_url: {url}\nretrieved_at: {retrieved_at}\n\n{page}"
