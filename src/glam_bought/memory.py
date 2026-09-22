"""Beauty Brain: Cognee-backed memory (remember / recall)."""
import asyncio
import json
import threading
from datetime import datetime, timezone

import cognee
from strands import tool

from . import config, events

_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()
_connected = False


def run(coro, timeout=180):
    """Run a Cognee coroutine from sync code (tools run in worker threads)."""
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout)


async def _connect():
    global _connected
    if not _connected and config.COGNEE_SERVICE_URL:
        result = cognee.serve(url=config.COGNEE_SERVICE_URL, api_key=config.COGNEE_API_KEY)
        if asyncio.iscoroutine(result):
            await result
    _connected = True


async def aremember(text: str, dataset: str):
    await _connect()
    return await cognee.remember(text, dataset_name=dataset)


async def arecall(query: str, datasets=None, top_k: int = 12):
    await _connect()
    return await cognee.recall(query, datasets=datasets or config.ALL_DATASETS, top_k=top_k)


def remember(text: str, dataset: str):
    return run(aremember(text, dataset))


def remember_in_background(text: str, dataset: str):
    asyncio.run_coroutine_threadsafe(aremember(text, dataset), _loop)


def recall_text(query: str, datasets=None, top_k: int = 12, retries: int = 2) -> str:
    """Recall, tolerating the short window while Cognee Cloud is still building the graph (409)."""
    import time

    for attempt in range(retries + 1):
        try:
            return _stringify(run(arecall(query, datasets, top_k)))
        except RuntimeError as e:
            if "409" in str(e) and attempt < retries:
                time.sleep(5)
                continue
            raise


def wait_until_recallable(datasets, timeout: int = 300) -> float:
    """Block until the datasets answer a recall (graph build finished). Returns seconds waited."""
    import time

    t = time.time()
    while True:
        try:
            run(arecall("what is here?", datasets, 3))
            return time.time() - t
        except RuntimeError as e:
            if "409" not in str(e) or time.time() - t > timeout:
                raise
            time.sleep(10)


def _stringify(results) -> str:
    if isinstance(results, str):
        return results
    out = []
    for r in results if isinstance(results, (list, tuple)) else [results]:
        if hasattr(r, "model_dump"):
            r = r.model_dump()
        if isinstance(r, dict) and r.get("text"):
            out.append(r["text"] + (f"  [dataset: {r['dataset_name']}]" if r.get("dataset_name") else ""))
        elif isinstance(r, str):
            out.append(r)
        else:
            out.append(json.dumps(r, default=str))
    return "\n".join(out)[:6000]


@tool
def recall_beauty_memory(query: str) -> str:
    """Search the user's Beauty Brain (Cognee knowledge graph) for anything relevant:
    skin profile, colour analysis, sensitivities, liked/disliked products, past purchases,
    past reactions, retailer and budget preferences, and previously researched market products.

    Args:
        query: A natural-language question, e.g. "foundations the user disliked and why".
    """
    try:
        return recall_text(query) or "Nothing found in memory."
    except Exception:  # e.g. a dataset still building on Cognee Cloud: fall back to the core three
        try:
            return recall_text(query, [config.DS_PROFILE, config.DS_PURCHASES, config.DS_MARKET]) or "Nothing found in memory."
        except Exception as e:
            return f"Memory unavailable: {e}"


@tool
def remember_beauty_fact(fact: str, provenance: str, dataset: str = "beauty_profile") -> str:
    """Save a new fact to the user's Beauty Brain so it shapes every future recommendation.

    Args:
        fact: One clear sentence, e.g. "The vitamin C serum from Brand X stung and caused redness."
        provenance: "told_me" (user said it), "observed" (seen in purchases/behaviour),
            or "inferred" (your own conclusion). Never present an inference as a fact.
        dataset: "beauty_profile" for preferences/reactions, "purchases" for purchase events.
    """
    if provenance not in ("told_me", "observed", "inferred"):
        return "provenance must be told_me, observed, or inferred"
    stamp = datetime.now(timezone.utc).isoformat(timespec="minutes")
    dataset = {"beauty_profile": config.DS_PROFILE, "purchases": config.DS_PURCHASES}.get(dataset, dataset)
    remember(f"[provenance: {provenance}] [recorded: {stamp}] {fact}", dataset)
    events.emit("memory_write", label=fact, provenance=provenance)
    return f"Remembered ({provenance}): {fact}"
