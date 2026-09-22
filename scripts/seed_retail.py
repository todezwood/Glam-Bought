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

BRANDS = {
    "base": {
        "sephora": ["ilia", "kosas", "rare-beauty", "nars", "armani", "saie", "merit", "tower-28"],
        "ulta": ["nars", "clinique", "lancome", "it-cosmetics", "tarte", "too-faced", "mac", "e.l.f", "nyx"],
    },
    "suncare": {  # the Switzerland-trip demo: a face sunscreen to splurge on, a reef/lake-friendly body sunscreen to save on
        "sephora": ["supergoop", "la-roche-posay", "tatcha", "laneige", "beauty-of-joseon", "shiseido", "coola", "sun-bum", "kiehls"],
        "ulta": ["sun-bum", "supergoop", "la-roche-posay", "neutrogena", "coola", "eltamd", "blue-lizard", "thinksport",
                 "cerave", "coppertone", "hawaiian-tropic", "babo", "bare-republic", "raw-elements"],
    },
    "skincare": {  # Angela's brief: French pharmacy, K-beauty and derm brands a North American shopper can buy
        "sephora": ["la-roche-posay", "tatcha", "laneige", "beauty-of-joseon", "drunk-elephant", "kiehls", "supergoop",
                    "glow-recipe", "first-aid-beauty", "paulas-choice", "sunday-riley", "skinceuticals", "cosrx", "medicube"],
        "ulta": ["la-roche-posay", "cerave", "vanicream", "cosrx", "anua", "beauty-of-joseon", "laneige", "supergoop",
                 "neutrogena", "olay", "kiehls", "clinique", "peach-lily", "mario-badescu"],
    },
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
    ap.add_argument("--category", choices=sorted(datasets.CATEGORIES), default="base", help="which shelf to discover")
    ap.add_argument("--reviews-only", action="store_true",
                    help="remember only the shopper reviews of a cached snapshot (products already in the Brain)")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds to wait for the Bright Data snapshot")
    ap.add_argument("--all-variations", action="store_true",
                    help="ask the scraper for every SKU (Angela's Sephora example); plain URLs often return shells")
    ap.add_argument("--shells-of", help="retry the URLs a cached snapshot returned without title/price")
    args = ap.parse_args()
    retailer = args.retailer
    cache = Path("cache") / retailer
    cat = datasets.CATEGORIES[args.category]

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
        elif args.shells_of:
            urls = list(dict.fromkeys((r.get("input") or {}).get("url") for r in json.loads(Path(args.shells_of).read_text())
                                      if not r.get("error") and not (r.get("title") and r.get("price"))))
        elif retailer in DISCOVER:
            pinned = PINNED.get(retailer, []) if args.category == "base" else []
            found = datasets.pick_urls(DISCOVER[retailer](), cat["patterns"], BRANDS[args.category].get(retailer, []),
                                       limit=args.limit, exclude=cat["exclude"])
            urls = pinned + [u for u in found if u not in pinned]
        else:
            sys.exit(f"{retailer}: no sitemap discovery; pass --urls")
        print(f"{len(urls)} {retailer} product URLs")
        for u in urls:
            print("  ", u)
        if args.dry_run:
            return
        t0 = time.time()
        records = datasets.collect(
            datasets.RETAILERS[retailer]["id"],
            [{"url": u, **({"all_variations": True} if args.all_variations else {})} for u in urls], timeout_s=args.timeout,
            on_progress=lambda sid, p: print(f"  {sid} {p.get('status')} records={p.get('records')} errors={p.get('errors')}  {time.time() - t0:.0f}s", file=sys.stderr),
        )
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"snapshot-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(records, ensure_ascii=False, indent=1))
        print(f"collected {len(records)} rows in {time.time() - t0:.0f}s -> {path}")

    ok = reviews = 0
    for rec in datasets.dedupe(records):
        if rec.get("error"):
            print(f"  skip {(rec.get('input') or {}).get('url')}: {rec.get('error_code')}")
            continue
        c = datasets.compact(rec, retailer)
        if not args.reviews_only:
            memory.remember(datasets.market_text(c, retailer), config.DS_MARKET)
            ok += 1
            print(f"  remembered {c.get('brand')} | {c.get('name')} | {c.get('sale_price') or c.get('price')} | {c.get('shades_in_stock')}/{c.get('shades_total')} shades in stock")
        # The scrapers ship shopper reviews with each product: remembered as separate evidence lines
        # so a recall can cite "a Sephora shopper with combination skin found it oily" with provenance.
        for r in datasets.retailer_reviews(rec):
            memory.remember(datasets.market_text(
                {"retailer": c["retailer"], "product": c.get("name"), "brand": c.get("brand"), "url": c.get("url"), **r},
                f"{retailer}_reviews", kind="review"), config.DS_MARKET)
            reviews += 1
    print(f"{ok} products + {reviews} shopper reviews -> Cognee dataset '{config.DS_MARKET}'")
    ok = ok or reviews

    if not args.no_wait and ok:
        print("waiting for Cognee graph build...")
        waited = memory.wait_until_recallable([config.DS_MARKET])
        print(f"recallable after {waited:.0f}s. Recall check:")
        probe = "foundation: price, finish, shades in stock" if args.category == "base" else "fragrance-free moisturizer or sunscreen: price, what shoppers said"
        print(memory.recall_text(f"{probe} at {datasets.RETAILERS[retailer]['name']}", [config.DS_MARKET], top_k=3)[:1200])


if __name__ == "__main__":
    main()
