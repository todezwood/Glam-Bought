"""Bright Data: the agent's eyes on the beauty market, via the Bright Data MCP server.

Pipeline: Bright Data (MCP tools) -> Cognee (MarketMemoryHook remembers every scrape) -> Strands.
The agent calls Bright Data's own MCP tools directly; nothing is wrapped.
"""
import json
from datetime import datetime, timezone

from strands.tools.mcp import MCPClient

from . import config

MAX_CHARS = 7000
# The subset of Bright Data MCP tools the agent gets. Amazon's structured scrapers are fast
# (8-13 s) and return price, sale, stock, delivery and the full ingredient list; Sephora and
# Ulta pages render client-side and come back as nav shells or take 100 s.
ALLOWED_TOOLS = ["search_engine", "web_data_amazon_product_search", "web_data_amazon_product"]
PRODUCT_FIELDS = ("title", "brand", "final_price", "initial_price", "discount", "currency", "availability",
                  "is_available", "delivery", "rating", "reviews_count", "ingredients", "description",
                  "features", "product_details", "url")
_client: MCPClient | None = None


def client() -> MCPClient:
    """Started Bright Data MCP client (hosted endpoint; no local npx needed)."""
    global _client
    if _client is None:
        if not config.BRIGHTDATA_API_TOKEN:
            raise RuntimeError("Set BRIGHTDATA_API_TOKEN in .env")
        _client = MCPClient(
            url=f"https://mcp.brightdata.com/mcp?token={config.BRIGHTDATA_API_TOKEN}&pro=1",
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


def body_of(text: str) -> str:
    """Strip Bright Data's untrusted-content envelope down to the payload."""
    if "_BEGIN=====" in text:
        text = text.split("_BEGIN=====", 1)[1].split("=====UNTRUSTED", 1)[0]
    return text.strip()


def compact(tool: str, raw: str) -> str:
    """Reduce a Bright Data result to the product facts the agent needs (and Cognee remembers)."""
    body = body_of(raw)
    if tool == "web_data_amazon_product":
        try:
            d = json.loads(body)
            d = d[0] if isinstance(d, list) else d
        except (ValueError, IndexError):
            return body[:MAX_CHARS]
        out = {k: d.get(k) for k in PRODUCT_FIELDS if d.get(k) not in (None, "", [])}
        out["url"] = out.get("url") or (d.get("input") or {}).get("url")
        for k in ("description", "ingredients"):
            if isinstance(out.get(k), str):
                out[k] = out[k][:1500]
        if out.get("url") and out.get("ingredients"):
            PAGES[_key(out["url"])] = str(out["ingredients"])
        if isinstance(out.get("features"), list):
            out["features"] = out["features"][:6]
        if isinstance(out.get("product_details"), list):
            out["product_details"] = [p for p in out["product_details"] if any(w in str(p).lower() for w in ("finish", "coverage", "skin", "shade", "fragrance", "size", "ounce"))][:8]
        return json.dumps(out, ensure_ascii=False)
    if tool == "web_data_amazon_product_search":
        try:
            items = json.loads(body)
        except ValueError:
            return body[:MAX_CHARS]
        rows = [{"name": i.get("name"), "price": i.get("final_price"), "regular_price": i.get("initial_price") or None,
                 "rating": i.get("rating"), "ratings": i.get("num_ratings"), "delivery": (i.get("delivery") or [None])[0] if isinstance(i.get("delivery"), list) else i.get("delivery"),
                 "sponsored": i.get("sponsored"), "url": i.get("url")}
                for i in items if isinstance(i, dict) and i.get("name")][:20]
        return json.dumps(rows, ensure_ascii=False)
    if tool == "search_engine":
        try:
            d = json.loads(body)
            return json.dumps([{"title": r.get("title"), "link": r.get("link"), "description": r.get("description")} for r in d.get("organic", [])[:10]], ensure_ascii=False)
        except ValueError:
            return body[:MAX_CHARS]
    return trim_product_page(body)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="minutes")


# Ingredient lists by product URL for every page fetched in this process. The ranker fills them
# in, so the model never re-types a 1500-character INCI list into a tool call (that alone was
# ~20 s of output per turn). The Sephora/Ulta scrapers return no ingredient field, so shelf
# candidates rank as ingredients_unverified unless a page was fetched.
PAGES: dict[str, str] = {}


def _key(url: str) -> str:
    return url.split("?")[0].rstrip("/")


def ingredients_for(url: str) -> str | None:
    return PAGES.get(_key(url or ""))


def trim_product_page(markdown: str) -> str:
    """Keep the part of a retailer page that carries product facts; drop nav/footer noise."""
    lines = [ln for ln in markdown.splitlines() if ln.strip() and not ln.lstrip().startswith(("![", "[![", "* [", "- ["))]
    text = "\n".join(lines)
    lower = text.lower()
    head = text[: MAX_CHARS // 2]
    idx = lower.find("ingredients")
    tail = text[max(idx - 200, 0): idx + MAX_CHARS // 2] if idx > len(head) else ""
    return (head + "\n...\n" + tail) if tail else text[:MAX_CHARS]
