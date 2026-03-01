#!/usr/bin/env python3
"""
preview_recommendations.py — Interactive test harness for the Animo Niche Engine

Mimics the /open-pack flow in the terminal so you can eyeball results
without spinning up the full server.

Usage:
    python preview_recommendations.py
"""

import os
import sys

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyClientCredentials

# Load .env before importing modules that read env vars at import time
load_dotenv()

from discovery import get_niche_tracks        # noqa: E402
from metadata import enrich_track_data         # noqa: E402

# ---------------------------------------------------------------------------
# Shared Spotify client (for the extra fields not in enrich_track_data)
# ---------------------------------------------------------------------------
try:
    _sp = spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=os.getenv("SPOTIFY_CLIENT_ID"),
            client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        )
    )
except Exception as e:
    print(f"[ERROR] Could not initialise Spotify client: {e}")
    print("        Check SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in your .env")
    sys.exit(1)

_W = 72  # total display width
_BAR = "─" * _W


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_spotify_extras(artist: str, title: str) -> dict:
    """
    Return the ``popularity`` score and ``preview_url`` for a track.

    These fields are not part of ``enrich_track_data``'s return value, so we
    do a lightweight search here solely for the test script.

    Raises ``spotipy.exceptions.SpotifyException`` on HTTP 429 so the caller
    can surface the rate-limit message directly.
    """
    try:
        resp = _sp.search(q=f"track:{title} artist:{artist}", type="track", limit=1)
        items = resp.get("tracks", {}).get("items", [])
        if items:
            return {
                "popularity":  items[0].get("popularity"),   # int 0–100
                "preview_url": items[0].get("preview_url"),  # str | None
            }
    except spotipy.exceptions.SpotifyException:
        raise  # let the caller handle 429 vs other errors
    except Exception:
        pass

    return {"popularity": None, "preview_url": None}


def _popularity_label(score: int | None) -> str:
    """Turn a Spotify popularity score into a human-readable label."""
    if score is None:
        return "—"
    if score <= 25:
        label = "very niche"
    elif score <= 45:
        label = "niche"
    elif score <= 65:
        label = "mid"
    else:
        label = "mainstream"
    return f"{score}/100  ({label})"


def _print_track(index: int, track: dict) -> None:
    genres     = ", ".join(track.get("genre_tags") or []) or "—"
    popularity = _popularity_label(track.get("popularity"))
    preview    = track.get("preview_url") or "—"
    yt_url     = f"https://music.youtube.com/watch?v={track['youtube_id']}"

    view_count       = track.get("view_count")
    niche_score      = track.get("niche_score")
    subscriber_count = track.get("subscriber_count")

    view_str  = f"{view_count:,}" if view_count is not None else "—"
    score_str = f"{niche_score}/100" if niche_score is not None else "—"
    sub_str   = f"{subscriber_count:,}" if subscriber_count is not None else "—"

    print(f"\n  {index}.  {track['title']}")
    print(f"       Artist      : {track['artist']}")
    print(f"       YT Views    : {view_str}")
    print(f"       Niche Score : {score_str}  (frontier proximity)")
    print(f"       Subscribers : {sub_str}")
    print(f"       Genres      : {genres}")
    print(f"       Popularity  : {popularity}")
    print(f"       Preview     : {preview}")
    print(f"       YouTube     : {yt_url}")


# ---------------------------------------------------------------------------
# Core test function
# ---------------------------------------------------------------------------

def test_query(song_title: str, artist_name: str, niche_value: float = 0.5) -> None:
    """
    Mimic the /open-pack flow for a single (song, artist) pair.

    The artist is used as the niche-engine seed; the song title provides
    user-facing context only (the engine works at the artist graph level).
    """
    max_views = int(10 ** (3 + niche_value * 5))
    print(f"\n{_BAR}")
    print(f"  Seed artist : {artist_name}")
    print(f"  Inspired by : {song_title}")
    print(f"  Niche value : {niche_value}  →  ceiling = {max_views:,} views")
    print(_BAR)

    # ------------------------------------------------------------------
    # Step 1: Discover niche candidates
    # ------------------------------------------------------------------
    print("  [1/2] Scanning artist graph ...", end="", flush=True)

    try:
        raw_tracks = get_niche_tracks([artist_name], niche_value=niche_value)
    except Exception as e:
        print(f"\n  [ERROR] Discovery failed: {e}")
        return

    if not raw_tracks:
        print()
        print(f"  No niche tracks found for '{artist_name}'.")
        print("  Suggestions:")
        print("    • Try a more niche artist (very popular seeds have shallow graphs)")
        print("    • Check your internet connection")
        return

    print(f" {len(raw_tracks)} track(s) found.")

    # ------------------------------------------------------------------
    # Step 2: Enrich with Spotify metadata + extra fields
    # ------------------------------------------------------------------
    print("  [2/2] Fetching Spotify metadata ...", end="", flush=True)

    results = []
    for track in raw_tracks:
        enriched = enrich_track_data(
            artist_name=track.get("artist", ""),
            song_name=track.get("title", ""),
        )

        try:
            extras = _fetch_spotify_extras(
                artist=track.get("artist", ""),
                title=track.get("title", ""),
            )
        except spotipy.exceptions.SpotifyException as exc:
            if exc.http_status == 429:
                retry = getattr(exc, "headers", {}).get("Retry-After", "a moment")
                print(f"\n  [RATE LIMIT] Spotify throttled this session.")
                print(f"               Wait {retry} second(s) then try again.")
                return
            # Non-429 Spotify error — degrade gracefully
            extras = {"popularity": None, "preview_url": None}

        results.append({**track, **enriched, **extras})

    print(" done.")

    # ------------------------------------------------------------------
    # Step 3: Print results
    # ------------------------------------------------------------------
    print(f"\n  Results  ({len(results)} niche track(s))")
    print("  " + "·" * (_W - 2))

    for i, track in enumerate(results, 1):
        _print_track(i, track)

    print(f"\n{_BAR}\n")


def _parse_niche_value(raw: str) -> float:
    """Parse and clamp a niche_value string to [0.0, 1.0]."""
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        print(f"  Invalid niche value '{raw}' — using default 0.5")
        return 0.5


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n  Animo — Niche Engine Preview")
    print("  (Press Ctrl-C at any time to quit)\n")

    try:
        while True:
            song_title   = input("  Song title       : ").strip()
            artist_name  = input("  Artist name      : ").strip()
            niche_raw    = input("  Niche value [0–1]: ").strip() or "0.5"

            if not artist_name:
                print("  Artist name is required — please try again.\n")
                continue

            test_query(song_title or "—", artist_name, _parse_niche_value(niche_raw))

            again = input("  Try another? [Y/n] : ").strip().lower()
            if again in ("n", "no"):
                print("\n  Goodbye.\n")
                break
            print()

    except KeyboardInterrupt:
        print("\n\n  Interrupted. Goodbye.\n")
