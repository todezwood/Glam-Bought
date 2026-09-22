"""Deterministic product ranking. Runs inside the Docker sandbox.

stdin:  {"candidates": [...], "constraints": {...}}
stdout: {"ranked": [...], "excluded": [...], "stats": {...}}

candidate: {name, brand, category, price, regular_price?, currency?, retailer, url,
            ingredients?: [str] | str, finish?, coverage?, shade?, size?, in_stock?,
            retrieved_at?}
constraints: {budget?, exclude_ingredients?: [str], disliked_finishes?: [str],
              disliked_products?: [str], preferred_finish?, preferred_coverage?,
              preferred_retailers?: [str], liked_brands?: [str],
              price_sensitivity?: "VERY_HIGH"|"HIGH"|"MEDIUM"|"LOW"|"NONE",
              require_in_stock?: bool}
"""
import json
import re
import sys

PRICE_WEIGHT = {"VERY_HIGH": 0.5, "HIGH": 0.35, "MEDIUM": 0.2, "LOW": 0.08, "NONE": 0.0}
INGREDIENT_ALIASES = {
    "fragrance": ["fragrance", "parfum", "perfume", "aroma"],
    "denatured alcohol": ["alcohol denat", "denatured alcohol", "sd alcohol"],
    "essential oils": ["essential oil", "limonene", "linalool", "citronellol", "geraniol"],
}


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def product_key(c):
    name = re.sub(r"\b\d+(\.\d+)?\s*(ml|oz|fl oz|g)\b", "", norm(c.get("name")))
    brand = norm(c.get("brand"))
    return f"{brand}|{name.replace(brand, '').strip()}"


def ingredient_text(c):
    ing = c.get("ingredients") or ""
    return norm(", ".join(ing) if isinstance(ing, list) else ing)


def found_excluded(c, excluded):
    text = ingredient_text(c)
    hits = []
    for term in excluded:
        for alias in INGREDIENT_ALIASES.get(term.lower(), [term]):
            if norm(alias) and norm(alias) in text:
                hits.append(term)
                break
    return hits


def main():
    payload = json.load(sys.stdin)
    cons = payload.get("constraints", {})
    budget = cons.get("budget")
    weight = PRICE_WEIGHT.get(str(cons.get("price_sensitivity", "MEDIUM")).upper(), 0.2)

    # Dedupe: same product at several retailers -> keep the cheapest offer, remember the rest
    best = {}
    for c in payload.get("candidates", []):
        if c.get("price") is None:
            continue
        k = product_key(c)
        c.setdefault("other_offers", [])
        if k not in best:
            best[k] = c
        else:
            keep, drop = (c, best[k]) if c["price"] < best[k]["price"] else (best[k], c)
            keep["other_offers"] = keep.get("other_offers", []) + drop.get("other_offers", []) + [
                {"retailer": drop.get("retailer"), "price": drop["price"], "url": drop.get("url")}
            ]
            best[k] = keep

    ranked, excluded = [], []
    for c in best.values():
        price, regular = c["price"], c.get("regular_price") or c["price"]
        c["savings"] = round(max(regular - price, 0), 2)
        c["discount_pct"] = round(100 * c["savings"] / regular) if regular else 0

        # Hard constraints eliminate
        why = []
        if budget is not None and price > budget:
            why.append(f"over_budget:{price}>{budget}")
        hits = found_excluded(c, cons.get("exclude_ingredients", []))
        if hits:
            why.append("contains:" + ",".join(hits))
        if norm(c.get("finish")) and norm(c.get("finish")) in [norm(f) for f in cons.get("disliked_finishes", [])]:
            why.append(f"disliked_finish:{c.get('finish')}")
        if any(norm(d) and norm(d) in norm(f"{c.get('brand')} {c.get('name')}") for d in cons.get("disliked_products", [])):
            why.append("previously_disliked")
        if cons.get("require_in_stock") and c.get("in_stock") is False:
            why.append("out_of_stock")
        if why:
            excluded.append({"name": c.get("name"), "brand": c.get("brand"), "retailer": c.get("retailer"), "reasons": why})
            continue

        # Soft preferences rank
        score, reasons = 0.5, ["within_budget"] if budget is not None else []
        if not ingredient_text(c):
            score -= 0.1
            reasons.append("ingredients_unverified")
        elif cons.get("exclude_ingredients"):
            score += 0.15
            reasons.append("free_of_excluded_ingredients")
        if norm(cons.get("preferred_finish")) and norm(cons.get("preferred_finish")) in norm(c.get("finish")):
            score += 0.15
            reasons.append("preferred_finish")
        if norm(cons.get("preferred_coverage")) and norm(cons.get("preferred_coverage")) in norm(c.get("coverage")):
            score += 0.1
            reasons.append("preferred_coverage")
        if norm(c.get("brand")) in [norm(b) for b in cons.get("liked_brands", [])]:
            score += 0.12
            reasons.append("user_likes_brand")
        if norm(c.get("retailer")) in [norm(r) for r in cons.get("preferred_retailers", [])]:
            score += 0.06
            reasons.append("preferred_retailer")
        if c["discount_pct"] >= 5:
            score += min(c["discount_pct"], 40) / 200
            reasons.append("currently_discounted")
        if c.get("in_stock"):
            score += 0.05
            reasons.append("in_stock")
        if budget:
            score -= weight * (price / budget)
        c["score"], c["reasons"] = round(score, 3), reasons
        ranked.append(c)

    ranked.sort(key=lambda c: c["score"], reverse=True)
    json.dump({
        "ranked": ranked,
        "excluded": excluded,
        "stats": {"received": len(payload.get("candidates", [])), "unique": len(best),
                  "eliminated": len(excluded), "ranked": len(ranked)},
    }, sys.stdout)


if __name__ == "__main__":
    main()
