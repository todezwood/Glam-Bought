"""Bright Data's live Amazon MCP tools -> Cognee `market` dataset, for a category the shelf lacks.

    uv run python scripts/seed_amazon_live.py "reef safe mineral body sunscreen SPF 50" --pages 4

One web_data_amazon_product_search, then web_data_amazon_product on the top N results in parallel
(the same calls the agent makes in a live turn, ~100 s), each page remembered with provenance.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from glam_bought import config, memory, web


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword")
    ap.add_argument("--pages", type=int, default=4)
    ap.add_argument("--no-wait", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    rows = json.loads(web.compact("web_data_amazon_product_search",
                                  web.call("web_data_amazon_product_search", {"keyword": args.keyword, "url": "https://www.amazon.com"}, timeout_s=150)))
    rows = [r for r in rows if r.get("url") and str(r.get("sponsored")).lower() not in ("true", "1")]  # the field is a string
    urls = [r["url"].split("?")[0] for r in rows[:args.pages]]
    print(f"search: {len(rows)} results in {time.time() - t0:.0f}s; fetching {len(urls)} pages", file=sys.stderr)
    for u in urls:
        print("  ", u)

    def page(url: str) -> str:
        return web.compact("web_data_amazon_product", web.call("web_data_amazon_product", {"url": url}, timeout_s=180))

    ok = 0
    with ThreadPoolExecutor(max_workers=len(urls) or 1) as pool:
        for url, facts in zip(urls, pool.map(page, urls)):
            if not facts or facts.startswith("Error"):
                print(f"  skip {url}: {facts[:80]}")
                continue
            stamp = web.now()
            memory.remember(f"[source: live_web] [scraper: brightdata_amazon_product] [evidence_type: retailer_listing] "
                            f"[source_url: {url}] [retrieved_at: {stamp}]\n{facts}", config.DS_MARKET)
            try:
                d = json.loads(facts)
                print(f"  remembered {d.get('brand')} | {(d.get('title') or '')[:70]} | {d.get('final_price')} | ingredients={'yes' if d.get('ingredients') else 'no'}")
            except ValueError:
                print(f"  remembered {url}")
            ok += 1
    print(f"{ok} pages -> Cognee dataset '{config.DS_MARKET}' in {time.time() - t0:.0f}s")
    if ok and not args.no_wait:
        print(f"recallable after {memory.wait_until_recallable([config.DS_MARKET]):.0f}s")


if __name__ == "__main__":
    main()
