"""
discovery.py — Animo Niche Engine

Expands a list of seed artists two degrees through YouTube Music's related-artist
graph, filters out anyone appearing in the global charts, and returns up to five
tracks from the remaining niche pool.
"""

import logging
from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)

# Single shared session — YTMusic() is lightweight (no auth required for public data)
_ytmusic = YTMusic()

MAX_TRACKS = 5


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _resolve_browse_id(artist_name: str) -> str | None:
    """Search for an artist by name and return the first result's browseId."""
    try:
        results = _ytmusic.search(artist_name, filter="artists")
        if not results:
            logger.warning("No search results for artist '%s'", artist_name)
            return None
        return results[0].get("browseId")
    except Exception:
        logger.warning("Search failed for artist '%s'", artist_name, exc_info=True)
        return None


def _get_related_artists(browse_id: str) -> list[dict]:
    """
    Fetch an artist's page and return the list of related artist dicts.

    Each dict contains at minimum: browseId, title.
    Returns an empty list on any failure or missing section.
    """
    try:
        artist_data = _ytmusic.get_artist(browse_id)
        return artist_data.get("related", {}).get("results", [])
    except Exception:
        logger.warning(
            "Could not fetch related artists for browseId '%s'", browse_id, exc_info=True
        )
        return []


def _get_chart_artist_names() -> set[str]:
    """
    Pull the global (ZZ) charts and return a lower-cased set of artist names.

    The `artists` value can be a list or a dict with an `items` key depending
    on the country, so both shapes are handled defensively.
    """
    try:
        charts = _ytmusic.get_charts(country="ZZ")
        artists_section = charts.get("artists", [])

        # Normalise: some country responses wrap items in a dict
        if isinstance(artists_section, dict):
            items = artists_section.get("items", [])
        else:
            items = artists_section

        return {item.get("title", "").lower() for item in items if item.get("title")}
    except Exception:
        logger.warning(
            "Could not fetch global charts — skipping chart filter", exc_info=True
        )
        return set()


def _pick_track(browse_id: str) -> dict | None:
    """
    Re-fetch an artist page and return one playable track dict, or None.

    Return shape: {"artist": str, "title": str, "youtube_id": str}
    """
    try:
        artist_data = _ytmusic.get_artist(browse_id)
        artist_name = artist_data.get("name", "Unknown Artist")
        songs = artist_data.get("songs", {}).get("results", [])

        for song in songs:
            video_id = song.get("videoId")
            title = song.get("title")
            if video_id and title:
                return {
                    "artist": artist_name,
                    "title": title,
                    "youtube_id": video_id,
                }

        logger.warning("No playable songs found for browseId '%s'", browse_id)
    except Exception:
        logger.warning(
            "Could not pick track for browseId '%s'", browse_id, exc_info=True
        )
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_niche_tracks(seed_artists: list[str]) -> list[dict]:
    """
    Discover niche tracks via two-degree artist graph expansion.

    Steps
    -----
    1. Resolve each seed artist name → browseId via search.
    2. Fetch each seed's related artists (first-degree).
    3. Fetch each first-degree artist's related artists (second-degree).
    4. Filter second-degree artists against the YT Music global charts.
    5. Pull one track from each remaining niche artist, up to MAX_TRACKS.

    Parameters
    ----------
    seed_artists:
        List of artist name strings to seed the graph walk.

    Returns
    -------
    List of dicts with keys: artist, title, youtube_id.
    """

    # ------------------------------------------------------------------
    # Step 1: Resolve seed browseIds
    # ------------------------------------------------------------------
    seed_ids: list[str] = []
    for name in seed_artists:
        bid = _resolve_browse_id(name)
        if bid:
            seed_ids.append(bid)
        else:
            logger.warning("Skipping unresolvable seed artist: '%s'", name)

    if not seed_ids:
        logger.error("No valid seed artists — cannot continue")
        return []

    # ------------------------------------------------------------------
    # Step 2: First-degree related artists
    # ------------------------------------------------------------------
    # browseId → display title; avoids processing the seeds themselves
    seen: set[str] = set(seed_ids)
    first_degree: dict[str, str] = {}

    for bid in seed_ids:
        for item in _get_related_artists(bid):
            item_bid = item.get("browseId")
            item_title = item.get("title", "")
            if item_bid and item_bid not in seen:
                first_degree[item_bid] = item_title
                seen.add(item_bid)

    if not first_degree:
        logger.warning("No first-degree related artists found for seeds: %s", seed_artists)
        return []

    # ------------------------------------------------------------------
    # Step 3: Second-degree related artists (the "jump")
    # ------------------------------------------------------------------
    second_degree: dict[str, str] = {}

    for bid, name in first_degree.items():
        for item in _get_related_artists(bid):
            item_bid = item.get("browseId")
            item_title = item.get("title", "")
            if item_bid and item_bid not in seen:
                second_degree[item_bid] = item_title
                seen.add(item_bid)

    if not second_degree:
        logger.warning("No second-degree related artists found — graph may be too shallow")
        return []

    # ------------------------------------------------------------------
    # Step 4: Filter against global charts
    # ------------------------------------------------------------------
    chart_names = _get_chart_artist_names()

    niche_pool: dict[str, str] = {
        bid: name
        for bid, name in second_degree.items()
        if name.lower() not in chart_names
    }

    if not niche_pool:
        logger.warning(
            "All %d second-degree artists were filtered out by the global charts",
            len(second_degree),
        )
        return []

    logger.info(
        "Niche pool: %d artists after filtering %d chart hits from %d candidates",
        len(niche_pool),
        len(second_degree) - len(niche_pool),
        len(second_degree),
    )

    # ------------------------------------------------------------------
    # Step 5: Pick one track per niche artist, up to MAX_TRACKS
    # ------------------------------------------------------------------
    tracks: list[dict] = []

    for bid in niche_pool:
        if len(tracks) >= MAX_TRACKS:
            break
        track = _pick_track(bid)
        if track:
            tracks.append(track)

    logger.info(
        "Niche discovery complete: %d/%d tracks collected", len(tracks), MAX_TRACKS
    )
    return tracks
