"""Bright Data Amazon scrapers (Angela's source table) -> Cognee `market` dataset.

    uv run python scripts/seed_amazon.py bestsellers --dry-run              # which best-seller lists
    uv run python scripts/seed_amazon.py bestsellers --limit 30             # top N of each list (one snapshot)
    uv run python scripts/seed_amazon.py reviews --urls URL ... --max 8     # shopper reviews for product pages
    uv run python scripts/seed_amazon.py reviews --from-audit --max 8       # ...for every page a live turn fetched
    uv run python scripts/seed_amazon.py bestsellers --from-cache FILE      # re-remember, no credit

Best-sellers uses the discovery form of the product dataset (type=discover_new, discover_by=best_sellers_url);
reviews take a product URL each. Raw snapshots are kept in cache/amazon/.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from glam_bought import config, datasets, memory


def audit_urls() -> list[str]:
    """Every Amazon product page a live turn wrote into the Brain today (audit `market_write` events)."""
    seen = []
    for line in Path("logs/audit.jsonl").read_text().splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        url = (ev.get("label") or "").split("?")[0] if ev.get("type") == "market_write" else ""
        if "amazon.com" in url and url not in seen:
            seen.append(url)
    return seen


def collect(kind: str, inputs: list[dict], args, **params) -> list[dict]:
    t0 = time.time()
    records = datasets.collect(
        datasets.AMAZON[kind], inputs, timeout_s=args.timeout, limit_per_input=args.limit if kind == "bestsellers" else None,
        on_progress=lambda sid, p: print(f"  {sid} {p.get('status')} records={p.get('records')} errors={p.get('errors')}  {time.time() - t0:.0f}s", file=sys.stderr),
        **params)
    cache = Path("cache") / "amazon"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=1))
    print(f"collected {len(records)} rows in {time.time() - t0:.0f}s -> {path}")
    return records


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=("bestsellers", "reviews"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=30, help="bestsellers: products per list")
    ap.add_argument("--lists", nargs="*", default=sorted(datasets.AMAZON_BESTSELLER_LISTS), help="bestsellers: which lists")
    ap.add_argument("--urls", nargs="*", help="reviews: product URLs")
    ap.add_argument("--from-audit", action="store_true", help="reviews: every Amazon page a live turn fetched")
    ap.add_argument("--max", type=int, default=8, help="reviews: reviews per product")
    ap.add_argument("--from-cache")
    ap.add_argument("--snapshot", help="ingest an already-triggered snapshot id (waits if still running)")
    ap.add_argument("--no-wait", action="store_true")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()

    if args.from_cache:
        records = json.loads(Path(args.from_cache).read_text())
    elif args.snapshot:
        while (p := datasets.progress(args.snapshot)).get("status") not in ("ready", "failed"):
            print(f"  {args.snapshot} {p.get('status')}", file=sys.stderr)
            time.sleep(8)
        records = datasets.snapshot(args.snapshot)
        Path("cache/amazon").mkdir(parents=True, exist_ok=True)
        Path(f"cache/amazon/{args.kind}-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(records, ensure_ascii=False, indent=1))
    elif args.kind == "bestsellers":
        lists = [datasets.AMAZON_BESTSELLER_LISTS[k] for k in args.lists]
        print(f"{len(lists)} best-seller lists, top {args.limit} each:")
        for u in lists:
            print("  ", u)
        if args.dry_run:
            return
        records = collect("bestsellers", [{"category_url": u, "zipcode": ""} for u in lists], args,
                          type="discover_new", discover_by="best_sellers_url")
    else:
        urls = list(args.urls or []) + (audit_urls() if args.from_audit else [])
        urls = list(dict.fromkeys(u.split("?")[0] for u in urls))
        if not urls:
            sys.exit("reviews: pass --urls or --from-audit")
        print(f"{len(urls)} product pages, up to {args.max} reviews each:")
        for u in urls:
            print("  ", u)
        if args.dry_run:
            return
        records = collect("reviews", [{"url": u, "max_reviews": args.max, "variation_specific": False} for u in urls], args)

    ok = skipped = 0
    for rec in records:
        if rec.get("error"):
            skipped += 1
            print(f"  skip {(rec.get('input') or {})}: {rec.get('error_code')}")
            continue
        if args.kind == "bestsellers":
            c = datasets.compact_amazon(rec)
            if not c.get("name") or not c.get("url"):
                skipped += 1
                continue
            memory.remember(datasets.market_text(c, "amazon_bestsellers"), config.DS_MARKET)
            print(f"  remembered #{c.get('best_seller_rank', '?')} {c.get('brand')} | {c.get('name')[:70]} | {c.get('price')} | ingredients={'yes' if c.get('ingredients') else 'no'}")
        else:
            c = datasets.compact_amazon_review(rec)
            if not c:
                skipped += 1
                continue
            memory.remember(datasets.market_text(c, "amazon_reviews", kind="review"), config.DS_MARKET)
            print(f"  remembered {c.get('rating')}★ {'verified ' if c.get('verified_purchase') else ''}| {(c.get('product') or c.get('url') or '')[:50]} | {c.get('title')}")
        ok += 1
    print(f"{ok} {args.kind} rows -> Cognee dataset '{config.DS_MARKET}' ({skipped} skipped)")

    if not args.no_wait and ok:
        print("waiting for Cognee graph build...")
        waited = memory.wait_until_recallable([config.DS_MARKET])
        print(f"recallable after {waited:.0f}s. Recall check:")
        probe = "best-selling moisturizer or serum on Amazon: price, rating" if args.kind == "bestsellers" else "what Amazon shoppers with dry or sensitive skin said about a moisturizer"
        print(memory.recall_text(probe, [config.DS_MARKET], top_k=3)[:1200])


if __name__ == "__main__":
    main()
