# GlamBought — demo runbook (3 minutes)

## Before you walk up
- Laptop: Docker Desktop running, server on `localhost:8787`, cloudflared tunnel running. Check `/healthz` shows all `true`.
- Phone: open the tunnel URL over **cellular**, not venue Wi-Fi. Keep the screen awake.
- Backup: append `?replay=1` to the URL to replay the last good run with no API calls.
- Don't run extra demo turns beforehand: each costs Bright Data credit and ~2.5 min.

## The pitch (Angela speaks, Jarmar drives)

**0:00 Problem (Angela).** "Google knows what's popular. Sephora knows its catalog. A chatbot knows beauty products. None of them know that matte foundation made *my* skin look cakey, that fragrance gives me redness, or what that product costs *right now*. GlamBought does."

**0:20 The brain (Jarmar taps Profile).** "Her Beauty Brain lives in Cognee: beauty notes, Sephora and Ulta receipts, an order email. Three source types, one knowledge graph. Every fact is tagged: told me, noticed, or my guess."

**0:40 The request (tap 'A foundation for dinner').** Read the request aloud. While it runs, narrate the activity card:
- *Remembering you* — "Cognee recall, injected before every turn. She never repeats herself."
- *Shopping the web* — "Bright Data's Amazon scrapers, called by the Strands agent itself: a candidate search, then four live product pages with full ingredient lists. Every scrape is written back into Cognee's market dataset with URL and timestamp."
- *Balancing your budget* — "Deterministic ranking runs in a Docker sandbox with no network. The model never does price math."

**1:40 The result.** Point at one card. "Every claim carries its source. *Brain*: she rated the Kosas concealer 9/10. *Live web*: Sephora lists no fragrance, checked 2 minutes ago." Then the excluded list: "It threw out a foundation that was **on sale** because the live ingredient list showed alcohol denat. A search engine would have led with that discount."

**2:10 The action.** Tap Review → Approve. "Nothing is purchased without a tap: a Strands hook cancels the purchase tool unless this turn was approved." Show "Your plan is ready" with the Buy link, then the "I'll remember that" pill: "The purchase is now in her brain."

**2:40 Memory that survives (if time).** Type: "That vitamin C serum irritated me." Show the pill. "Kill the process, restart it, ask again: it still knows. This is Cognee, not context."

**2:55 Close (Angela).** "Data, memory, live web, reasoning, tools, action. Your brain. Your agent. Beauty, done for you."

## Stack, one line each (for questions)
- **Cognee Cloud** — `remember`/`recall`; datasets `beauty_profile`, `purchases`, `market`; provenance tags on every fact.
- **Bright Data** — hosted MCP server, pro mode; `web_data_amazon_product_search`, `web_data_amazon_product`, `search_engine`.
- **Strands Agents** — one agent, five tools, three hooks: MemoryHook (recall before every turn), MarketMemoryHook (Bright Data → Cognee), SteeringHook (approval gate). Bedrock-ready: one `.env` line.
- **Docker** — `optimizer/rank.py` runs in `python:3.12-alpine`, `--network none`, 256 MB.

## If it breaks
- Turn hangs > 3 min: reload with `?replay=1`.
- Tunnel dead: on the laptop, `cloudflared tunnel --url http://localhost:8787`, share the new URL.
- Server dead: `uv run uvicorn glam_bought.server:app --port 8787`.
