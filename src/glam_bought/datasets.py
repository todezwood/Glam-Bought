"""Bright Data Datasets API: the "out of the box" retailer scrapers (Sephora, Ulta, Olive Young).

These scrapers are not exposed as MCP tools, and a page takes ~100 s to collect, so they feed the
Beauty Brain's `market` dataset ahead of time (scripts/seed_retail.py) rather than run inside a
live turn. Same API shape Angela's custom scrapers will use: trigger -> progress -> snapshot.
"""
import json
import re
import time
from datetime import datetime, timezone

import requests

from . import config

API = "https://api.brightdata.com/datasets/v3"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"}
# Angela's source table (GlamBought brief, "FOR JARMAR"). All three share one record schema.
RETAILERS = {
    "sephora": {"id": "gd_mloyjmqz1ucoikm4ja", "name": "Sephora", "currency": "USD"},  # gd_lbz49igcthopwaygd is sephora.fr
    "ulta": {"id": "gd_mljet48s1kibed4k2d", "name": "Ulta", "currency": "USD"},
    "oliveyoung": {"id": "gd_mq7s2bye2kjqhn0bl8", "name": "Olive Young Global", "currency": "USD"},
}
AMAZON = {  # Angela's table, Amazon rows: best-sellers discovery and product reviews
    "bestsellers": "gd_l7q7dkf244hwjntr0",  # + type=discover_new&discover_by=best_sellers_url
    "reviews": "gd_le8e811kzy4ggddlq",
}
AMAZON_BESTSELLER_LISTS = {
    "beauty": "https://www.amazon.com/gp/bestsellers/beauty/",
    "skincare": "https://www.amazon.com/gp/bestsellers/beauty/11060451",
    "makeup": "https://www.amazon.com/gp/bestsellers/beauty/11058281",
}
SHADES_MAX = 40
REVIEWS_MAX = 3
EXCLUDE = ("brush", "sponge", "set-", "-set", "mini-", "refill", "primer", "powder", "spray", "kit",
           "conditioner", "shampoo", "hair", "-tool", "applicator", "nipple", "spatula", "blender", "nail",
           "travel-size", "cushion-cream", "inserts", "sample")
# Slug patterns per shelf category; skincare adds body/baby/gift exclusions because Ulta's slugs carry no brand.
CATEGORIES = {
    "base": {"patterns": ["foundation", "skin-tint", "concealer", "tinted-moisturizer", "complexion-stick", "cushion"],
             "exclude": EXCLUDE},
    "skincare": {"patterns": ["moisturizer", "-cream", "serum", "cleanser", "sunscreen", "spf", "toner", "essence",
                              "eye-cream", "ampoule", "gel-cream", "face-mask", "sheet-mask", "barrier"],
                 "exclude": EXCLUDE + ("baby", "body", "hand-", "foot", "gwp", "free-", "scalp", "co-cleanser",
                                       "shave", "beard", "duo", "trio", "candle", "perfume", "fragrance", "deodorant",
                                       "-men-", "lip-", "-lip", "gloss", "patch", "foundation", "concealer", "blush")},
    "suncare": {"patterns": ["sunscreen", "sunblock", "spf-", "-spf", "sun-stick", "after-sun"],  # face AND body
                "exclude": EXCLUDE + ("foundation", "tint", "concealer", "gwp", "free-", "lip-", "-lip", "baby", "-men-",
                                      "self-tan", "bronzer", "tanning")},
}


def _headers() -> dict:
    if not config.BRIGHTDATA_API_TOKEN:
        raise RuntimeError("Set BRIGHTDATA_API_TOKEN in .env")
    return {"Authorization": f"Bearer {config.BRIGHTDATA_API_TOKEN}", "Content-Type": "application/json"}


def trigger(dataset_id: str, inputs: list[dict], limit_per_input: int | None = None, **params) -> str:
    """Start a collection; returns the snapshot id. Extra params: type=discover_new, discover_by=..."""
    body = {"input": inputs, "limit_per_input": limit_per_input} if limit_per_input else inputs
    r = requests.post(f"{API}/trigger", headers=_headers(), json=body, timeout=60,
                      params={"dataset_id": dataset_id, "include_errors": "true", **params})
    r.raise_for_status()
    return r.json()["snapshot_id"]


def progress(snapshot_id: str) -> dict:
    r = requests.get(f"{API}/progress/{snapshot_id}", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def snapshot(snapshot_id: str) -> list[dict]:
    r = requests.get(f"{API}/snapshot/{snapshot_id}", headers=_headers(), params={"format": "json"}, timeout=120)
    r.raise_for_status()
    return r.json()


def collect(dataset_id: str, inputs: list[dict], timeout_s: int = 600, on_progress=None,
            limit_per_input: int | None = None, **params) -> list[dict]:
    """Trigger, poll until ready, return records (error records carry `error` / `error_code`)."""
    sid = trigger(dataset_id, inputs, limit_per_input=limit_per_input, **params)
    t0 = time.time()
    while True:
        p = progress(sid)
        if on_progress:
            on_progress(sid, p)
        if p.get("status") == "ready":
            return snapshot(sid)
        if p.get("status") == "failed":
            raise RuntimeError(f"snapshot {sid} failed: {p}")
        if time.time() - t0 > timeout_s:
            raise TimeoutError(f"snapshot {sid} still {p.get('status')} after {timeout_s}s")
        time.sleep(8)


# --- URL discovery (free: the retailers' own sitemaps) -------------------------------------

def _sitemap_locs(url: str, pattern: str) -> list[str]:
    xml = requests.get(url, headers=UA, timeout=60).text
    return re.findall(rf"<loc>({pattern})</loc>", xml)


def sephora_product_urls() -> list[str]:
    """Every /product/ URL in sephora.com's US product sitemaps (~9.7k; a subset of the catalog)."""
    maps = [m for m in _sitemap_locs("https://www.sephora.com/sitemap.xml", r"[^<]+") if "products-sitemap" in m and "-CA" not in m]
    urls = []
    for m in maps:
        urls += _sitemap_locs(m, r"https://www\.sephora\.com/product/[^<]+")
    return list(dict.fromkeys(urls))


def ulta_product_urls() -> list[str]:
    """Every /p/ URL in ulta.com's product sitemap (sitemap/p.xml, itself possibly an index)."""
    urls = _sitemap_locs("https://www.ulta.com/sitemap/p.xml", r"https://www\.ulta\.com/p/[^<]+")
    if not urls:
        for m in _sitemap_locs("https://www.ulta.com/sitemap/p.xml", r"[^<]+\.xml"):
            urls += _sitemap_locs(m, r"https://www\.ulta\.com/p/[^<]+")
    return list(dict.fromkeys(urls))


def pick_urls(urls: list[str], patterns: list[str], brands: list[str] = (), limit: int = 25,
              exclude: tuple = EXCLUDE) -> list[str]:
    """Product URLs by slug pattern; the given brands come first."""
    brand_hits, other_hits, seen = [], [], set()
    for u in urls:
        path = u.split("?")[0]
        if path in seen:  # Ulta's sitemap lists every SKU of a product; keep the first
            continue
        seen.add(path)
        slug = path.rsplit("/", 1)[-1].lower()
        if not any(p in slug for p in patterns) or any(x in slug for x in exclude):
            continue
        (brand_hits if any(slug.startswith(b.lower() + "-") for b in brands) else other_hits).append(u)
    return (brand_hits + other_hits)[:limit]


# --- Records -> what the Beauty Brain remembers ----------------------------------------------

def dedupe(records: list[dict]) -> list[dict]:
    """The scrapers return one row per SKU, many of them shells (no title/price).
    Keep one row per product (item_id), preferring the row with the most shade data."""
    best: dict[str, dict] = {}
    for r in records:
        if r.get("error"):
            best.setdefault(f"err:{(r.get('input') or {}).get('url')}", r)
            continue
        if not r.get("title") or not r.get("price"):
            continue
        key = (r.get("url") or "").split("?")[0] or r.get("item_id")  # item_id is per SKU on Sephora
        score = sum(len(v.get("variant_options") or []) for v in r.get("variants") or [])
        if key not in best or score > best[key].get("_score", -1):
            best[key] = {**r, "_score": score, "url": (r.get("url") or "").split("?")[0]}
    return list(best.values())


_FACET = re.compile(r"(Coverage|Finish|Formulation|Skin Type|Skin type)[:\s ]+([^\n]+)")


def compact(rec: dict, retailer: str) -> dict:
    """Reduce a retailer record to the facts the ranker and the agent use."""
    meta = RETAILERS[retailer]
    desc = rec.get("description") or ""
    facets = {k.lower().replace(" ", "_"): v.strip()[:80] for k, v in _FACET.findall(desc)}
    shades, in_stock_shades = [], 0
    for v in rec.get("variants") or []:
        for o in v.get("variant_options") or []:
            name = o.get("option_name") or o.get("option_id")
            if name:
                shades.append({"shade": name, "in_stock": bool(o.get("in_stock"))})
                in_stock_shades += bool(o.get("in_stock"))
    low = desc.lower()
    out = {
        "retailer": meta["name"],
        "brand": rec.get("brand"),
        "name": rec.get("title"),
        "url": rec.get("url"),
        "price": rec.get("price"),
        "sale_price": rec.get("sale_price"),
        "currency": meta["currency"],
        "availability": rec.get("availability"),
        "rating": rec.get("star_rating"),
        "reviews_count": rec.get("review_count"),
        **facets,
        "fragrance_free": True if "fragrance-free" in low or "fragrance free" in low else None,
        "shades_total": len(shades),
        "shades_in_stock": in_stock_shades,
        "shades": shades[:SHADES_MAX],
        "ingredients": rec.get("ingredients") or None,
        "description": desc[:1200] or None,
    }
    if isinstance(out["ingredients"], list):
        out["ingredients"] = ", ".join(map(str, out["ingredients"]))[:1500]
    elif isinstance(out["ingredients"], str):
        out["ingredients"] = out["ingredients"][:1500]
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def retailer_reviews(rec: dict, limit: int = REVIEWS_MAX) -> list[dict]:
    """The shopper reviews the retailer scrapers return with each product (Angela's category 4:
    real-world experience). Lowest and highest ratings first so the Brain hears both sides."""
    rows = [r for r in rec.get("reviews") or [] if isinstance(r, dict) and r.get("content")]
    rows.sort(key=lambda r: r.get("rating") or 0)
    picked = (rows[:1] + rows[-limit + 1:]) if len(rows) > limit else rows
    return [{"rating": r.get("rating"), "title": (r.get("title") or "")[:80], "text": r["content"][:280]} for r in picked]


def compact_amazon(rec: dict) -> dict:
    """An Amazon product record (best-sellers discovery) -> the same shape the live MCP tool yields."""
    from . import web

    out = {k: rec.get(k) for k in web.PRODUCT_FIELDS if rec.get(k) not in (None, "", [])}
    out["retailer"] = "Amazon"
    out["name"] = out.pop("title", None)
    out["price"] = out.pop("final_price", None)
    out["url"] = (out.get("url") or (rec.get("input") or {}).get("url") or "").split("?")[0] or None
    if rec.get("bs_rank") or rec.get("root_bs_rank"):
        out["best_seller_rank"] = rec.get("bs_rank") or rec.get("root_bs_rank")
    if rec.get("bs_category"):
        out["best_seller_category"] = str(rec["bs_category"])[:80]
    if isinstance(out.get("ingredients"), list):
        out["ingredients"] = ", ".join(map(str, out["ingredients"]))
    for k in ("description", "ingredients"):
        if isinstance(out.get(k), str):
            out[k] = out[k][:1500]
    if isinstance(out.get("features"), list):
        out["features"] = out["features"][:6]
    if isinstance(out.get("product_details"), list):
        out["product_details"] = [p for p in out["product_details"] if any(
            w in str(p).lower() for w in ("finish", "coverage", "skin", "shade", "fragrance", "size", "ounce", "type"))][:8]
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def compact_amazon_review(rec: dict) -> dict | None:
    """One Amazon review record -> the evidence fields Angela's provenance object asks for."""
    text = rec.get("review_text") or rec.get("review_content") or rec.get("content") or rec.get("text") or ""
    if not text:
        return None
    url = (rec.get("url") or rec.get("product_url") or (rec.get("input") or {}).get("url") or "").split("?")[0]
    verified = rec.get("is_verified") if rec.get("is_verified") is not None else rec.get("verified_purchase")
    return {k: v for k, v in {
        "retailer": "Amazon", "product": (rec.get("product_name") or "")[:120] or None, "url": url or None,
        "rating": rec.get("rating"), "title": (rec.get("review_header") or rec.get("title") or "")[:80] or None,
        "text": text[:320], "verified_purchase": verified, "helpful_votes": rec.get("helpful_count"),
        "reviewed_at": (rec.get("review_posted_date") or rec.get("date") or "")[:10] or None,
        "variant": (rec.get("product_attributes") or rec.get("variant") or "")[:80] or None,
    }.items() if v not in (None, "", [])}


def market_text(compacted: dict, retailer: str, kind: str = "product") -> str:
    """The line Cognee remembers: provenance header + facts."""
    stamp = datetime.now(timezone.utc).isoformat(timespec="minutes")
    evidence = ("[evidence_type: verified_purchase_review]" if compacted.get("verified_purchase")
                else "[evidence_type: retailer_review]") if kind == "review" else "[evidence_type: retailer_listing]"
    return (f"[source: live_web] [scraper: brightdata_{retailer}] {evidence} [source_url: {compacted.get('url')}] "
            f"[retrieved_at: {stamp}]\n{json.dumps(compacted, ensure_ascii=False)}")
