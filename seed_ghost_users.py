#!/usr/bin/env python3
"""
Animo Ghost Seeder
==================
Populates the Supabase database with "ghost user" tracks and comments so that
Vibe Hubs look active when the app first loads.

Usage:
    pip install requests
    python seed_ghost_users.py

Requirements:
    - The FastAPI backend must be running at http://localhost:8000
    - SUPABASE_URL and SUPABASE_KEY must be set in animo/.env (or local.settings.json)
"""

import json
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Install requests first:  pip install requests")
    sys.exit(1)

API_BASE = "http://localhost:8000"

# ---------------------------------------------------------------------------
# Ghost community comments — realistic, evocative, non-spammy
# ---------------------------------------------------------------------------
GHOST_COMMENTS = [
    "This changed everything for me. Pure signal.",
    "On repeat all week. Obsessed.",
    "Found this at 3am. Haven't stopped since.",
    "The algorithm would never surface this. Animo delivered.",
    "Sent this to 5 friends immediately.",
    "This artist is going places. Calling it now.",
    "The depth score doesn't lie — this is rare.",
    "Exactly my frequency. How did Animo know?",
    "Finally, something that hits different.",
    "This is what the niche window was made for.",
    "I've listened to this 40 times and I'm still finding new things.",
    "The kind of track that gets better every single listen.",
    "Goosebumps. Every time.",
    "This unlocked something in me I didn't know was locked.",
    "Not just a song — a whole mood.",
]

# ---------------------------------------------------------------------------
# Seed packs — diverse styles for good genre coverage
# ---------------------------------------------------------------------------
SEED_PACKS = [
    {
        "label": "Electronic Underground",
        "seed_artists": ["Aphex Twin", "Four Tet", "Burial"],
        "niche_value": 0.3,
    },
    {
        "label": "Hyperpop / Alt-Digital",
        "seed_artists": ["PinkPantheress", "Arca", "100 gecs"],
        "niche_value": 0.45,
    },
    {
        "label": "Hip-Hop / Beat Diggers",
        "seed_artists": ["MF DOOM", "Madlib", "J Dilla"],
        "niche_value": 0.35,
    },
    {
        "label": "Indie / Post-Punk",
        "seed_artists": ["Parquet Courts", "Ought", "Pile"],
        "niche_value": 0.4,
    },
    {
        "label": "Soul / R&B Edges",
        "seed_artists": ["serpentwithfeet", "Sampha", "Lianne La Havas"],
        "niche_value": 0.5,
    },
]


def open_pack(seed_artists: list[str], niche_value: float) -> list[dict]:
    """Call POST /open-pack and return a list of track dicts (or [] on failure)."""
    try:
        resp = requests.post(
            f"{API_BASE}/open-pack",
            json={"seed_artists": seed_artists, "niche_value": niche_value},
            timeout=180,
        )
        if resp.status_code == 200:
            return resp.json()
        print(f"  ✗ /open-pack returned {resp.status_code}: {resp.text[:200]}")
    except requests.exceptions.ConnectionError:
        print(f"  ✗ Could not connect to {API_BASE} — is the backend running?")
    except requests.exceptions.Timeout:
        print("  ✗ Request timed out (180 s) — discovery may need more time")
    return []


def add_comment(track_id: str, text: str) -> bool:
    """Call POST /comment. Returns True on success."""
    try:
        resp = requests.post(
            f"{API_BASE}/comment",
            json={"track_id": track_id, "text": text},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception:
        return False


def check_health() -> bool:
    """Quick health check before starting the seed run."""
    try:
        resp = requests.get(f"{API_BASE}/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def main() -> None:
    print("🌱  Animo Ghost Seeder")
    print("=" * 50)

    if not check_health():
        print(f"\n✗ Backend not reachable at {API_BASE}")
        print("  Start the server with:  uvicorn main:app --reload")
        sys.exit(1)

    print(f"✓  Backend is up at {API_BASE}\n")

    all_track_ids: list[str] = []

    for pack in SEED_PACKS:
        label = pack["label"]
        seeds = pack["seed_artists"]
        niche = pack["niche_value"]
        print(f"📦  Opening pack: {label}")
        print(f"    Seeds: {seeds}  |  niche_value={niche}")

        tracks = open_pack(seeds, niche)

        if not tracks:
            print("    No tracks returned — skipping.\n")
            time.sleep(2)
            continue

        print(f"    ✓ {len(tracks)} track(s) saved:")
        for t in tracks:
            tid = t.get("id", "?")
            short_id = tid[:8] + "..." if len(tid) > 8 else tid
            print(f"      · [{short_id}] {t.get('artist')} — {t.get('title')}")
            all_track_ids.append(tid)

        print()
        time.sleep(3)  # be polite to the APIs

    if not all_track_ids:
        print("⚠️   No tracks were saved.  Seeding aborted.")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # Add ghost comments to every discovered track
    # -------------------------------------------------------------------------
    print(f"\n💬  Adding ghost comments to {len(all_track_ids)} track(s) …")
    total_ok = 0
    total_fail = 0

    for track_id in all_track_ids:
        short_id = track_id[:8] + "..."
        for comment_text in GHOST_COMMENTS:
            ok = add_comment(track_id, comment_text)
            if ok:
                total_ok += 1
            else:
                total_fail += 1
            time.sleep(0.1)  # avoid hammering the endpoint

        print(f"    ✓ {len(GHOST_COMMENTS)} comments → track {short_id}")

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    print("\n" + "=" * 50)
    print("✅  Seeding complete!")
    print(f"    Tracks seeded  : {len(all_track_ids)}")
    print(f"    Comments added : {total_ok}")
    if total_fail:
        print(f"    Failed comments: {total_fail}  (check server logs)")


if __name__ == "__main__":
    main()
