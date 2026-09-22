# GlamBought — demo runbook (3 minutes)

## Before you walk up
- Laptop: Docker Desktop running, server on `localhost:8787`, cloudflared tunnel running. Check `/healthz` shows all `true`.
- Phone: open the tunnel URL over **cellular**, not venue Wi-Fi. Keep the screen awake.
- Backup: append `?replay=1` to the URL to replay the last good run with no API calls.
- The three shopping tiles answer in ~10 s: `cache/tiles.json` holds a recorded run of each (result + activity events), and a tap replays it with the real "checked N min ago" age, then seeds the conversation so Approve and follow-ups run live from it. Re-record before the demo so the age reads minutes, not hours: `uv run python scripts/cache_tiles.py` (~5 min, Bright Data credit). Typed requests run live: everything the agent needs (profile, the Sephora/Ulta shelf, ring, calendar) is fetched in parallel before the model speaks, and base makeup is answered from the shelf with no live web call, so a typed base-makeup or already-researched request takes ~50 s to ~1.5 min; a category the Brain has never seen (fresh Bright Data research) still takes 2–3 min.
- Ring + calendar: `/healthz` shows `oura_connected` and `google_connected` true. Connect both on the laptop at `localhost:8787` (tap Connect Ring / Connect Calendar on the home screen; click through Google's "unverified app" interstitial there, never on stage). If Google was connected more than 7 days ago, `rm tokens/google.json` and reconnect.
- Then: `uv run python scripts/smoke.py oauth` (both refresh), `smoke.py oura` (real ring data), `smoke.py gcal` (events include "Switzerland hiking trip"). Open the Oura app on the phone so last night synced. "Switzerland hiking trip" exists on Angela's Google Calendar (Mon 28 Sep 9 AM to Fri 2 Oct); keep Google Calendar week view open in a laptop tab. After any server restart, tap one tile before walking up so the profile recall is cached.

## The pitch (Angela speaks, Jarmar drives)

**0:00 Problem (Angela).** "Google knows what's popular. Sephora knows its catalog. A chatbot knows beauty products. None of them know that matte foundation made *my* skin look cakey, that fragrance gives me redness, or what that product costs *right now*. GlamBought does."

**0:20 The brain (Jarmar taps Profile).** The sheet opens instantly on Angela's real profile: dry-to-combination skin, her concerns, PDRN as the hero ingredient, how she shops, travel, who she buys for, and which sources are connected (Ring, Calendar, receipts, notes). "Her Beauty Brain lives in Cognee: beauty notes, Sephora and Ulta receipts, an order email, her ring. Scroll to *From the Brain*: that section is a live Cognee recall of what she bought, returned and rated." (It takes ~20 s the first time and is cached after; open Profile once before walking up.)

**0:40 The request (tap 'Switzerland, sun-ready').** "Her calendar already knows she flies to Switzerland Monday for hiking. The agent surfaced the trip; she only adds her budget: $200, splurge on the face sunscreen, save on the body sunscreen." While it runs, narrate the activity card:
- *Remembering you* — "Cognee recall, injected before every turn. She never repeats herself."
- *Checking your day* — "Her ring says readiness 67 and 4.5 hours of sleep; her calendar says the Switzerland hiking trip starts Monday 28 September. Two personal sources the web cannot see. Hiking at elevation means drier air and stronger UV, and the lake swim means reef- and lake-friendly body sunscreen: her Brain knows that from her profile."
- *Remembering you* — also add: "Her brain already holds Sephora's shelf: Bright Data's Sephora scraper filled the market dataset with prices, finishes and which shades are in stock, before we walked up."
- *Shopping the web* — "Bright Data's Amazon scrapers, called by the Strands agent itself: a candidate search, then four live product pages with full ingredient lists. Every scrape is written back into Cognee's market dataset with URL and timestamp."
- *Balancing your budget* — "Deterministic ranking runs in a Docker sandbox with no network. The model never does price math."

**1:40 The result.** Point at the face sunscreen card (the splurge) and the body sunscreen card (the save). "Every claim carries its source. *Brain*: she lives in humid Taiwan and wants light textures, PDRN, functional K-beauty, no fragrance. *Ring*: 4.5 hours of sleep, so hydrating formulas, nothing stripping. *Calendar*: pickup Saturday, two days before she flies. *Live web*: Sephora's price and stock, checked minutes ago." Then the excluded list: "It ruled out the matte finish that went cakey on her before, and anything not reef-safe for the lake."

**2:10 The action.** Tap Review → Approve. "Nothing is purchased without a tap: a Strands hook cancels the purchase tool unless this turn was approved." Show "Your plan is ready" with the Buy link and the "On your calendar" row: tap it (or refresh the laptop's Google Calendar tab; the phone opens whichever Google account it is signed into). "The pickup is on her calendar, Saturday 5 PM, the day before she flies." Then the "I'll remember that" pill: "The purchase is now in her brain."

**2:40 Memory that survives (if time).** Type: "That vitamin C serum irritated me." Show the pill. Or: "How have I been sleeping this week?" answers from Cognee's wellness dataset. "Kill the process, restart it, ask again: it still knows. This is Cognee, not context."

**2:55 Close (Angela).** "Data, memory, live web, reasoning, tools, action. Your brain. Your agent. Beauty, done for you."

## Stack, one line each (for questions)
- **Cognee Cloud** — `remember`/`recall`; datasets `beauty_profile_v3`, `purchases`, `market_v2`, `wellness`; provenance tags on every fact.
- **Bright Data** — two ways in. Live: hosted MCP server, pro mode; `web_data_amazon_product_search`, `web_data_amazon_product`, `search_engine`. Seeded: the out-of-the-box **Sephora and Ulta scrapers** from Angela's source table (Datasets API) collected 32 base-makeup products with every shade's stock into Cognee's market dataset before the demo (Olive Young's scraper errors on every URL, so it is not in); the Strands MemoryHook recalls the relevant ones for each request.
- **Strands Agents** — one agent, eight tools (incl. `check_wellness`, `check_calendar`, approval-gated `add_calendar_event`), three hooks: MemoryHook (recall before every turn), MarketMemoryHook (Bright Data → Cognee), SteeringHook (approval gate). Bedrock-ready: one `.env` line.
- **Docker** — `optimizer/rank.py` runs in `python:3.12-alpine`, `--network none`, 256 MB.

## If it breaks
- A typed turn takes ~1–3 min (see above); the page polls a job, so a reload resumes the same turn. Each device has its own conversation on the server (Angela's tests never queue behind the demo phone), but one device runs one turn at a time: a second request from the same device shows "Finishing another request first." Turn hangs > 5 min: reload with `?replay=1`.
- Clean slate before walking up: `curl -X POST localhost:8787/reset` forgets every device's conversation (the Beauty Brain is untouched).
- Tunnel dead: on the laptop, `cloudflared tunnel --url http://localhost:8787`, share the new URL.
- Server dead: `uv run uvicorn glam_bought.server:app --port 8787`.
- Ring not connected: the agent says so in one clause and continues. Calendar event missing after Approve: type "Please add the Saturday 5 PM pickup to my calendar" and tap Review → Approve again. `?replay=1` replays the cached basket and, on Approve, the cached calendar link.
