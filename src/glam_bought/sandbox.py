"""Docker sandbox: deterministic work the LLM should not do in its head."""
import json
import subprocess

from strands import tool

IMAGE = "glambought-optimizer"


@tool
def rank_products(candidates: list[dict], constraints: dict) -> str:
    """Rank product candidates deterministically inside an isolated Docker sandbox
    (no network). Dedupes the same product across retailers, computes savings and discount %,
    ELIMINATES anything violating a hard constraint, then ranks the rest by soft preferences.
    Always use this instead of doing price math or filtering yourself.

    Args:
        candidates: One dict per retailer offer: name, brand, category, price (number),
            regular_price (number, if on sale), currency, retailer, url, ingredients
            (string or list), finish, coverage, shade, size, in_stock (bool), retrieved_at.
        constraints: budget (number), exclude_ingredients (list, e.g. ["fragrance",
            "denatured alcohol"]), disliked_finishes (list), disliked_products (list of names),
            preferred_finish, preferred_coverage, preferred_retailers (list), liked_brands
            (list), price_sensitivity (VERY_HIGH|HIGH|MEDIUM|LOW|NONE), require_in_stock (bool).
    """
    proc = subprocess.run(
        ["docker", "run", "--rm", "-i", "--network", "none", "--memory", "256m", IMAGE],
        input=json.dumps({"candidates": candidates, "constraints": constraints}),
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return f"sandbox error: {proc.stderr[-500:]}"
    return proc.stdout
