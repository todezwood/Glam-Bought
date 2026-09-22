"""The action layer. Anything with financial consequences sits behind an approval gate."""
import json
from datetime import datetime
from pathlib import Path

from strands import tool

from . import events

PLANS = Path("plans")


@tool
def create_shopping_plan(items: list[dict], approved: bool, note: str = "") -> str:
    """Create the purchase-ready shopping plan the user approved. ONLY call this after the
    user has explicitly approved the exact items you presented. Never substitute products.

    Args:
        items: Approved items: name, brand, retailer, price, regular_price, currency, url,
            and reason.
        approved: True only if the user explicitly said yes to these exact items.
        note: Optional one-line summary for the user.
    """
    if not approved:
        return "BLOCKED: the user has not approved this plan. Present it and ask first."
    total = round(sum(float(i.get("price") or 0) for i in items), 2)
    regular = round(sum(float(i.get("regular_price") or i.get("price") or 0) for i in items), 2)
    plan = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "items": items, "total": total, "savings": round(regular - total, 2), "note": note,
    }
    PLANS.mkdir(exist_ok=True)
    path = PLANS / f"plan-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(plan, indent=2))
    events.emit("plan_created", plan=plan)
    return json.dumps({"status": "plan_created", "path": str(path), **plan})
