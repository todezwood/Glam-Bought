# Battle of the Personal Brains — Build Brief

**When:** Mon Sep 21, 2026, 4:00–9:00 PM (5 hours total, including demo prep)
**Where:** Bright Data office, SOMA, SF
**Prizes:** $500 / $300 / $200 cash (top 3), plus $1000 Cognee credits and $2000 AWS credits in the pool
**Credits for us:** $50 Cognee Cloud + $50 Bright Data each

## The challenge, in one line

> Build a brain. Build an agent. Then make it do something.

A personal agent that (1) knows your data, (2) pulls live public web data, (3) reasons across both, and (4) **takes a real action**.

## What judges are looking for

Taken from the "Battle" section — these are the implied judging axes:

1. **Memory** — does it remember better? (Cognee is doing real work, not decoration)
2. **Retrieval** — does it find better information? (personal + fresh web data)
3. **Cross-source reasoning** — does it combine personal, company, and public data into a conclusion neither source gives alone?
4. **Useful action** — does it actually do something in the world, not just answer?

Explicit anti-goal: **"This isn't just about building another chatbot."** A chat UI over RAG loses.

Judges are developer advocates from Cognee, AWS, and Bright Data — each will look for their product being used meaningfully.

## The stack (use all of it, visibly)

| Layer | Tech | Role in our build | Must-have? |
|---|---|---|---|
| Brain | **Cognee** | Long-term structured memory / knowledge graph over personal data | Yes — named as the required brain |
| Web | **Bright Data** | Fresh public web data discovery + retrieval | Yes — named as the required web layer |
| Personal data | Local files, Drive, email, receipts, photos, notes, calendar | The "personal" in personal brain | Yes — at least 2 sources |
| Agent harness | **AWS Strands Agents** | Orchestration, tool use, deciding next step | Yes — sponsor + AWS credits prize |
| Sandbox | **Docker Sandboxes** | Secure env for agent-run code / tasks | Strongly advised — easy differentiator, most teams will skip it |

Formula they state: **Data + Memory + Reasoning + Tools + Actions.** Our demo should hit all five, out loud.

## Hard requirements checklist

- [ ] Personal brain built in Cognee from **multiple** sources (not one folder of PDFs)
- [ ] Bright Data call that returns **live** info the brain didn't have
- [ ] Strands agent that chooses tools itself (not a hardcoded pipeline)
- [ ] At least one **real side effect**: calendar event created, email/message sent, report file generated, reminder scheduled
- [ ] Docker sandbox used for something genuine (code exec, file processing)
- [ ] Live demo that works end to end at ~8:30 PM

## Suggested idea space (from the prompt)

Calendar scheduling · notes + timed reminders · meetings → action items → to-do schedule · receipts → expense reports · photos → organize + share · general personal assistant · connect-your-digital-life. "Or build something completely different" — originality is allowed and probably rewarded.

## Rules we set for ourselves

1. **One workflow, done completely.** A single end-to-end loop (ingest → remember → look up web → reason → act) beats three half-features.
2. **The action is the demo.** Design backward from the moment something visibly happens (event appears on calendar, message lands on phone).
3. **Show the reasoning across sources.** The "wow" is a conclusion that needs both personal memory AND fresh web data.
4. **Show memory persisting.** Tell it something early in the demo; have it use that later unprompted.
5. **Pre-load data.** Ingestion into Cognee is slow — seed the brain in hour 1, never live on stage.
6. **Have a fallback.** Record a working run by 8:00 PM in case Wi-Fi or an API dies during the demo.
7. **No sensitive real data on a projector.** Use a curated/sanitized slice of personal data.

## Time budget (5 hours)

| Time | Goal |
|---|---|
| 4:00–4:30 | Lock idea + demo script. Claim credits, get API keys, confirm all 4 SDKs install. |
| 4:30–5:30 | Brain: ingest data into Cognee, verify queries return good answers. In parallel: Bright Data fetch working standalone. |
| 5:30–7:00 | Strands agent with tools: `search_brain`, `search_web`, the action tool(s). Docker sandbox wired in. |
| 7:00–8:00 | End-to-end run. Fix the one path we demo. Cut everything else. |
| 8:00–8:30 | Record backup video. Rehearse 2–3 min pitch. |
| 8:30–9:00 | Demos. |

**Split:** one person owns brain + data (Cognee, ingestion, Bright Data); the other owns agent + actions (Strands, tools, Docker, demo surface). Agree on tool function signatures in the first 30 minutes so the halves plug together.

## Demo script skeleton (2–3 min)

1. The problem, in one sentence, as a personal pain.
2. "Here's what the brain knows" — show sources going in / a graph view.
3. Give the agent one natural-language goal.
4. Watch it: recall from memory → fetch live web data → reason → **act**.
5. Show the side effect landing in the real app.
6. Name the stack: Cognee, Bright Data, Strands, Docker — one clause each.

## Open questions to ask mentors at kickoff

- Is there a formal judging rubric / are all four technologies required or just encouraged?
- Demo length and format (live only, or video allowed)?
- Team size limits; is pre-written code allowed?
- Which LLM provider does Strands default to here — are Bedrock credentials provided?
