"""Bright Data -> Cognee: pre-build the market dataset.  uv run python scripts/build_market.py

Discovers product pages with Bright Data search, scrapes each, and remembers the trimmed page
into the `market` dataset with its source URL and timestamp.
"""
import re
import time

from glam_bought import config, memory, web

QUERIES = [
    "site:sephora.com/product natural finish medium coverage foundation dry skin",
    "site:sephora.com/product fragrance free hydrating foundation sensitive skin",
    "site:ulta.com/p natural finish medium coverage foundation dry skin",
    "site:sephora.com/product hydrating serum fragrance free sensitive skin",
]
MAX_PAGES = 16
URL = re.compile(r"https://www\.(?:sephora\.com/product|ulta\.com/p)/[^\s\)\"\]]+")

urls: list[str] = []
for q in QUERIES:
    found = URL.findall(web.call("search_engine", {"query": q, "engine": "google"}))
    for u in found:
        u = u.split("?")[0]
        if u not in urls:
            urls.append(u)
    print(f"{len(found):>3} urls  <- {q}")

for i, url in enumerate(urls[:MAX_PAGES], 1):
    t = time.time()
    try:
        page = web.trim_product_page(web.call("scrape_as_markdown", {"url": url}))
        memory.remember(f"[source: live_web] [source_url: {url}] [retrieved_at: {web._now()}]\n{page}", config.DS_MARKET)
        print(f"[{i}/{min(len(urls), MAX_PAGES)}] {time.time() - t:.0f}s  {url}")
    except Exception as e:
        print(f"[{i}] FAILED {url}: {e}")
