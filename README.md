# GlamBought — your personal beauty agent

*Beauty, done for you.* Built in five hours at the Battle of the Personal Brains hackathon (Bright Data, SF, 2026-09-21).

GlamBought is a personal beauty shopping agent that remembers what works for you, researches the live market, and takes action, with your approval.

> Google knows what's popular. Sephora knows its catalog. A generic LLM knows beauty products. GlamBought knows what *you* like, what's available *right now*, what it costs *right now*, and can do something about it.

## Pipeline: Bright Data → Cognee → Strands, Docker to help

```
personal sources (notes, receipts, order emails)      public web (Bright Data)
            │ cognee.remember                            │ Sephora / Ulta / Olive Young scrapers (Datasets API) → scripts/seed_retail.py
            │                                            │ MCP tools in-turn → MarketMemoryHook → cognee.remember
            ▼                                            ▼
        ┌──────────── Cognee Cloud knowledge graph: beauty_profile · purchases · market ────────────┐
        └───────────────────────────────┬────────────────────────────────────────────▲──────────────┘
                     recall (before every turn)                                       │ new facts
                                        ▼                                             │
        Strands agent ── tools: Bright Data MCP (search_engine, web_data_amazon_*), rank_products (Docker),
                                recall_beauty_memory, remember_beauty_fact, create_shopping_plan (approval-gated)
                                        ▼
        FastAPI + SSE ──► mobile web UI (web/index.html)
```

- **Memory:** every fact carries provenance (`told_me` / `observed` / `inferred`) and every scraped page carries `source_url` + `retrieved_at`.
- **Two ways in from Bright Data:** the agent calls Bright Data's MCP tools live (Amazon, ~10 s each), and Bright Data's out-of-the-box **Sephora scraper** (Datasets API, ~100 s a page) is run ahead of time by `scripts/seed_retail.py` (also Ulta and Olive Young, from Angela's source table), which remembers price, sale price, finish, coverage, fragrance-free and every shade's stock into the `market` dataset. `MemoryHook` recalls the relevant market facts before each turn, so the agent answers with Sephora shade and stock evidence without waiting on a slow scrape.
- **Reasoning across sources:** each recommendation clause is badged **Brain** or **Live web**. Example from a real run: a foundation on sale was excluded because the live ingredient list showed alcohol denat, which the user's profile says to avoid.
- **Action:** `create_shopping_plan` is cancelled by a Strands `SteeringHook` unless the user approved this turn. Approval writes a purchase-ready plan and remembers the purchase.
- **Sandbox:** `optimizer/rank.py` dedupes, computes discounts, eliminates hard-constraint violators and ranks, inside `docker run --network none`.

## Run it

```
cp .env.example .env         # add ANTHROPIC_API_KEY (or Bedrock), COGNEE_SERVICE_URL/API_KEY, BRIGHTDATA_API_TOKEN
uv sync
docker build -t glambought-optimizer optimizer/
uv run python scripts/seed_brain.py         # personal sources → Cognee
uv run python scripts/seed_retail.py sephora   # Bright Data retailer scrapers → Cognee market (also: ulta, oliveyoung; --dry-run to preview)
uv run uvicorn glam_bought.server:app --port 8787
```

Open http://localhost:8787 (phone frame on desktop; full-bleed on a phone). CLI: `uv run python -m glam_bought.agent`.
Smoke tests: `uv run python scripts/smoke.py [strands|cognee|brightdata|docker]`.

## Layout

| Path | What |
|---|---|
| `src/glam_bought/agent.py` | System prompt, tool wiring, CLI |
| `src/glam_bought/hooks.py` | MemoryHook, MarketMemoryHook, AuditHook, SteeringHook |
| `src/glam_bought/memory.py` | Cognee remember / recall tools |
| `src/glam_bought/web.py` | Bright Data MCP client, tool filter, result compaction |
| `src/glam_bought/datasets.py` | Bright Data Datasets API (Sephora / Ulta scrapers): trigger → progress → snapshot, record compaction |
| `src/glam_bought/sandbox.py` | Docker-run ranking tool |
| `src/glam_bought/actions.py` | Approval-gated shopping plan |
| `src/glam_bought/server.py` | FastAPI: `/chat`, `/events` (SSE), `/profile`, `/healthz` |
| `optimizer/` | Deterministic ranker + Dockerfile |
| `web/index.html` | Mobile UI, single file |
| `seed/` | Fictional persona: notes, receipts, order email |

Team: Angela (vision, beauty expertise) and Jarmar (build).
