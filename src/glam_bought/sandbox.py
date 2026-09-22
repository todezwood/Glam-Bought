"""Docker sandbox: deterministic work the LLM should not do in its head."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from strands import tool

from . import web

IMAGE = "glambought-optimizer"
RANKER = Path(__file__).resolve().parent.parent.parent / "optimizer" / "rank.py"


def _command() -> list[str]:
    """Docker sandbox on the laptop; in a container (no Docker daemon) the same script runs as a plain subprocess."""
    if shutil.which("docker"):
        return ["docker", "run", "--rm", "-i", "--network", "none", "--memory", "256m", IMAGE]
    return [sys.executable, "-I", str(RANKER)]


@tool
def rank_products(candidates: list[dict], constraints: dict) -> str:
    """Rank product candidates deterministically inside an isolated Docker sandbox
    (no network). Dedupes the same product across retailers, computes savings and discount %,
    ELIMINATES anything violating a hard constraint, then ranks the rest by soft preferences.
    Always use this instead of doing price math or filtering yourself.

    Args:
        candidates: One dict per retailer offer: name, brand, category, price (number),
            regular_price (number, if on sale), currency, retailer, url, finish, coverage,
            shade, size, in_stock (bool), retrieved_at. Do NOT copy ingredient lists: for any
            url whose page you fetched this turn they are filled in automatically. Keep every
            field short.
        constraints: budget (number), exclude_ingredients (list, e.g. ["fragrance",
            "denatured alcohol"]), disliked_finishes (list), disliked_products (list of names),
            preferred_finish, preferred_coverage, preferred_retailers (list), liked_brands
            (list), price_sensitivity (VERY_HIGH|HIGH|MEDIUM|LOW|NONE), require_in_stock (bool).
    """
    for c in candidates:
        if isinstance(c, dict) and not c.get("ingredients"):
            ing = web.ingredients_for(c.get("url") or "")
            if ing:
                c["ingredients"] = ing
    proc = subprocess.run(
        _command(),
        input=json.dumps({"candidates": candidates, "constraints": constraints}),
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return f"sandbox error: {proc.stderr[-500:]}"
    return proc.stdout
