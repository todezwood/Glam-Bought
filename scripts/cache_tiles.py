"""Record the home-screen tiles so a tap answers in seconds: run each tile once on the live server
and save its result plus its activity events to cache/tiles.json (served by server._serve_tile).

    uv run python scripts/cache_tiles.py            # run the three shopping tiles, ~5 min, costs Bright Data credit
    uv run python scripts/cache_tiles.py --only 1   # re-record tile 1 only; tiles 2 and 3 keep their recordings
    uv run python scripts/cache_tiles.py --collect JOBS.json   # gather runs already started ({name: {job, message}})
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost:8787"
OUT = ROOT / "cache" / "tiles.json"
AUDIT = ROOT / "logs" / "audit.jsonl"


def api(path, body=None):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode() if body else None,
                                 {"Content-Type": "application/json"}, method="POST" if body else "GET")
    return json.load(urllib.request.urlopen(req))


def tile_messages():
    html = (ROOT / "web" / "index.html").read_text()
    cases = re.search(r"const CASES = \[(.*?)\n\];", html, re.S).group(1)
    return re.findall(r'\n   "(.*?)"\]', cases)[:3]  # the fourth tile is a memory statement: fast already


def main():
    if "--collect" in sys.argv:
        jobs = json.loads(Path(sys.argv[sys.argv.index("--collect") + 1]).read_text())
    else:
        # --only N re-records one tile and keeps the others (the server would replay the old
        # recording, so that tile is dropped from tiles.json first; the previous file is kept aside).
        only = int(sys.argv[sys.argv.index("--only") + 1]) if "--only" in sys.argv else None
        kept = {}
        if OUT.exists():
            kept = json.loads(OUT.read_text())
            OUT.replace(OUT.with_name("tiles.previous.json"))
            print("moved the old tiles to cache/tiles.previous.json")
        messages = tile_messages()
        if only:
            kept.pop(messages[only - 1].strip(), None)
            OUT.write_text(json.dumps(kept))  # the other tiles keep answering while this one records
        jobs = {}
        for i, m in enumerate(messages):
            if only and i + 1 != only:
                continue
            sid = f"tile{i + 1}"
            jobs[sid] = {"job": api("/chat", {"message": m, "session": sid})["job"], "message": m, "start": time.time()}
            print("started", sid, m[:60])
    tiles = json.loads(OUT.read_text()) if OUT.exists() else {}
    for sid, j in jobs.items():
        while (status := api(f"/chat/{j['job']}"))["status"] != "done":
            time.sleep(5)
        result = status["result"]
        if "```basket" not in result.get("reply", ""):
            print("SKIP", sid, "no basket:", result.get("reply", "")[:120])
            continue
        evs = [e for e in map(json.loads, AUDIT.read_text().splitlines())
               if e.get("session") in (sid, sid + "b") and e["ts"] >= j.get("start", 0) and e["type"] in ("turn_start", "tool_start", "tool_end", "market_write")]
        tiles[j["message"].strip()] = {"result": result, "events": evs, "ts": evs[-1]["ts"] if evs else time.time()}
        print("cached", sid, len(evs), "events")
    OUT.write_text(json.dumps(tiles))
    print("wrote", OUT, len(tiles), "tiles")


if __name__ == "__main__":
    main()
