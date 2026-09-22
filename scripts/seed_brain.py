"""Load the personal sources into the Beauty Brain.  uv run python scripts/seed_brain.py"""
import time
from pathlib import Path

from glam_bought import config, memory

SEED = Path(__file__).resolve().parent.parent / "seed"
SOURCES = [
    (SEED / "profile.md", config.DS_PROFILE, "beauty_notes"),
    (SEED / "product_experiences.md", config.DS_PROFILE, "beauty_notes"),
    *[(p, config.DS_PURCHASES, "receipt") for p in sorted((SEED / "receipts").glob("*.txt"))],
    *[(p, config.DS_PURCHASES, "email") for p in sorted((SEED / "emails").glob("*.txt"))],
]

for path, dataset, source_type in SOURCES:
    provenance = "told_me" if source_type == "beauty_notes" else "observed"
    text = f"[source_type: {source_type}] [source_file: {path.name}] [provenance: {provenance}]\n{path.read_text()}"
    t = time.time()
    memory.remember(text, dataset)
    print(f"remembered {path.name} -> {dataset} ({time.time() - t:.1f}s)")

print(f"\nwaiting for Cognee Cloud to build the graph... ({memory.wait_until_recallable([config.DS_PROFILE, config.DS_PURCHASES]):.0f}s)")
print("\nrecall check:\n", memory.recall_text("Which foundations did the user dislike, and why?", [config.DS_PROFILE, config.DS_PURCHASES])[:1200])
