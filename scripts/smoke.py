"""Smoke tests. Run one:  uv run python scripts/smoke.py [strands|cognee|brightdata|docker]"""
import sys
import time


def timed(label, fn):
    t = time.time()
    out = fn()
    print(f"\n[{label}] {time.time() - t:.1f}s\n{str(out)[:1500]}")
    return out


def strands():
    from strands import Agent
    from glam_bought import config

    agent = Agent(model=config.get_model(), callback_handler=None)
    timed("strands", lambda: agent("In one sentence: what finish suits dry skin in a foundation?"))


def cognee():
    from glam_bought import memory

    timed("cognee.remember", lambda: memory.remember(
        "[provenance: told_me] Smoke test: the user has dry, sensitive skin and avoids fragrance.", "smoke_test"))
    timed("cognee.recall", lambda: memory.recall_text("What skin type does the user have?", ["smoke_test"]))


def brightdata():
    from glam_bought import web

    print("allowed tools:", [t.tool_name for t in web.tools()])
    timed("search_engine", lambda: web.call("search_engine", {"query": "fragrance-free natural finish foundation dry skin sephora", "engine": "google"}))
    timed("scrape_as_markdown", lambda: web.trim_product_page(web.call(
        "scrape_as_markdown", {"url": "https://www.sephora.com/product/ilia-super-serum-skin-tint-spf-40-P455926"})))


def docker():
    from glam_bought.sandbox import rank_products

    timed("docker", lambda: rank_products(
        candidates=[{"name": "Tint", "brand": "A", "price": 48, "regular_price": 60, "retailer": "Sephora", "finish": "natural", "ingredients": "water, squalane"}],
        constraints={"budget": 65, "exclude_ingredients": ["fragrance"]}))


if __name__ == "__main__":
    {"strands": strands, "cognee": cognee, "brightdata": brightdata, "docker": docker}[sys.argv[1]]()
