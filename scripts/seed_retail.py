"""Bright Data retailer scrapers (Angela's source table) -> Cognee `market` dataset.

    uv run python scripts/seed_retail.py sephora --dry-run          # list the URLs that would be scraped
    uv run python scripts/seed_retail.py sephora --limit 25         # scrape + remember (one snapshot, ~2-4 min)
    uv run python scripts/seed_retail.py ulta --limit 25
    uv run python scripts/seed_retail.py oliveyoung --urls URL ...  # no readable sitemap: pass URLs
    uv run python scripts/seed_retail.py sephora --from-cache FILE  # re-remember a cached snapshot, no credit

Raw snapshots are kept in cache/<retailer>/ so a Cognee re-seed never costs Bright Data credit twice.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from glam_bought import config, datasets, memory

PATTERNS = ["foundation", "skin-tint", "concealer", "tinted-moisturizer", "complexion-stick", "cushion"]
BRANDS = {
    "sephora": ["ilia", "kosas", "rare-beauty", "nars", "armani", "saie", "merit", "tower-28"],
    "ulta": ["nars", "clinique", "lancome", "it-cosmetics", "tarte", "too-faced", "mac", "e.l.f", "nyx"],
    "oliveyoung": [],
}
# Sephora's sitemap is a subset of the catalog; the demo hero is pinned explicitly.
PINNED = {"sephora": ["https://www.sephora.com/product/true-skin-serum-foundation-P429548"]}  # ILIA, found by the agent live
DISCOVER = {"sephora": datasets.sephora_product_urls, "ulta": datasets.ulta_product_urls}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("retailer", choices=sorted(datasets.RETAILERS))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--from-cache")
    ap.add_argument("--snapshot", help="ingest an already-triggered Bright Data snapshot id (waits if still running)")
    ap.add_argument("--urls", nargs="*")
    ap.add_argument("--no-wait", action="store_true", help="skip waiting for the Cognee graph build")
    args = ap.parse_args()
    retailer = args.retailer
    cache = Path("cache") / retailer

    if args.from_cache:
        records = json.loads(Path(args.from_cache).read_text())
    elif args.snapshot:
        while (p := datasets.progress(args.snapshot)).get("status") not in ("ready", "failed"):
            print(f"  {args.snapshot} {p.get('status')}", file=sys.stderr)
            time.sleep(8)
        records = datasets.snapshot(args.snapshot)
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"snapshot-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(records, ensure_ascii=False, indent=1))
        print(f"fetched {len(records)} rows -> {path}")
    else:
        if args.urls:
            urls = args.urls
        elif retailer in DISCOVER:
            found = datasets.pick_urls(DISCOVER[retailer](), PATTERNS, BRANDS[retailer], limit=args.limit)
            urls = PINNED.get(retailer, []) + [u for u in found if u not in PINNED.get(retailer, [])]
        else:
            sys.exit(f"{retailer}: no sitemap discovery; pass --urls")
        print(f"{len(urls)} {retailer} product URLs")
        for u in urls:
            print("  ", u)
        if args.dry_run:
            return
        t0 = time.time()
        records = datasets.collect(
            datasets.RETAILERS[retailer]["id"], [{"url": u} for u in urls],
            on_progress=lambda sid, p: print(f"  {sid} {p.get('status')} records={p.get('records')} errors={p.get('errors')}  {time.time() - t0:.0f}s", file=sys.stderr),
        )
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"snapshot-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(records, ensure_ascii=False, indent=1))
        print(f"collected {len(records)} rows in {time.time() - t0:.0f}s -> {path}")

    ok = 0
    for rec in datasets.dedupe(records):
        if rec.get("error"):
            print(f"  skip {(rec.get('input') or {}).get('url')}: {rec.get('error_code')}")
            continue
        c = datasets.compact(rec, retailer)
        memory.remember(datasets.market_text(c, retailer), config.DS_MARKET)
        ok += 1
        print(f"  remembered {c.get('brand')} | {c.get('name')} | {c.get('sale_price') or c.get('price')} | {c.get('shades_in_stock')}/{c.get('shades_total')} shades in stock")
    print(f"{ok} products -> Cognee dataset '{config.DS_MARKET}'")

    if not args.no_wait and ok:
        print("waiting for Cognee graph build...")
        waited = memory.wait_until_recallable([config.DS_MARKET])
        print(f"recallable after {waited:.0f}s. Recall check:")
        print(memory.recall_text(f"foundation at {datasets.RETAILERS[retailer]['name']}: price, finish, shades in stock", [config.DS_MARKET], top_k=3)[:1200])


if __name__ == "__main__":
    main()
