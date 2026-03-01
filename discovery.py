"""
discovery.py — Animo Niche Engine

Expands a list of seed artists two degrees through YouTube Music's related-artist
graph, filters out chart artists, then applies a user-controlled view-count ceiling
(the "Niche Slider") to select and score the final tracks.
"""

import logging
import re

from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)

_ytmusic = YTMusic()

MAX_TRACKS    = 5
SONGS_TO_CHECK = 5   # max songs inspected per candidate artist for view count


# ---------------------------------------------------------------------------
# Ceiling + scoring helpers
# ---------------------------------------------------------------------------

def _compute_max_views(niche_value: float) -> int:
    """
    Map a 0.0–1.0 niche slider value to a logarithmic view-count ceiling.

    niche_value | max_views
    ------------|----------
    0.0         |      1 000   (ultra-underground)
    0.5         |    316 227   (deep niche)
    1.0         | 100 000 000  (mainstream allowed)
    """
    niche_value = max(0.0, min(1.0, niche_value))
    return int(10 ** (3 + niche_value * 5))


def _compute_niche_score(view_count: int, max_views: int) -> int:
    """
    Score 0–100 representing how close the track sits to the user's Niche Frontier.

    A track right at the ceiling scores 100 (perfectly at the frontier).
    A track with almost no views scores near 0 (deep underground).
    """
    if max_views == 0:
        return 0
    return min(100, round((view_count / max_views) * 100))


def _parse_subscriber_count(subscriber_str: str | None) -> int:
    """
    Convert a subscriber string like '1.23M subscribers' or '500K' to an integer.
    Returns 0 if the string cannot be parsed.
    """
    if not subscriber_str:
        return 0
    # Isolate the first token (e.g. "1.23M" from "1.23M subscribers")
    token = subscriber_str.strip().split()[0].lower()
    match = re.match(r"^([\d.]+)([kmb]?)$", token)
    if not match:
        return 0
    number   = float(match.group(1))
    suffix   = match.group(2)
    mult     = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    return int(number * mult)


# ---------------------------------------------------------------------------
# ytmusicapi wrappers
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
    Return the related-artist list from an artist page.
    Each item contains at minimum: browseId, title, subscribers.
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
    """Return a lower-cased set of global chart artist names."""
    try:
        charts = _ytmusic.get_charts(country="ZZ")
        section = charts.get("artists", [])
        items   = section.get("items", []) if isinstance(section, dict) else section
        return {item.get("title", "").lower() for item in items if item.get("title")}
    except Exception:
        logger.warning("Could not fetch global charts — skipping chart filter", exc_info=True)
        return set()


def _get_view_count(video_id: str) -> int | None:
    """
    Fetch the viewCount for a YouTube Music video.
    Returns an integer or None if unavailable.
    """
    try:
        song_data = _ytmusic.get_song(video_id)
        view_str  = song_data.get("videoDetails", {}).get("viewCount", "")
        if view_str and view_str.isdigit():
            return int(view_str)
    except Exception:
        logger.debug("Could not fetch view count for videoId '%s'", video_id, exc_info=True)
    return None


def _find_track_within_ceiling(
    artist_name: str,
    max_views: int,
) -> dict | None:
    """
    Search for songs by ``artist_name`` and return the track closest to (but
    under) the ``max_views`` ceiling — the one sitting at the Niche Frontier.

    Checks up to SONGS_TO_CHECK songs, picks the highest-scoring valid one.
    Returns None if no song passes the ceiling filter.
    """
    try:
        results = _ytmusic.search(artist_name, filter="songs")
    except Exception:
        logger.warning("Song search failed for '%s'", artist_name, exc_info=True)
        return None

    best: dict | None = None

    for song in results[:SONGS_TO_CHECK]:
        video_id = song.get("videoId")
        title    = song.get("title")
        if not video_id or not title:
            continue

        view_count = _get_view_count(video_id)
        if view_count is None:
            continue

        if view_count >= max_views:
            logger.debug(
                "'%s' by %s has %d views — exceeds ceiling of %d, skipping",
                title, artist_name, view_count, max_views,
            )
            continue

        score = _compute_niche_score(view_count, max_views)
        if best is None or score > best["niche_score"]:
            best = {
                "artist":     artist_name,
                "title":      title,
                "youtube_id": video_id,
                "view_count": view_count,
                "niche_score": score,
            }

    return best


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_niche_tracks(
    seed_artists: list[str],
    niche_value:  float = 0.5,
) -> list[dict]:
    """
    Discover niche tracks via two-degree artist graph expansion + view-count gating.

    Parameters
    ----------
    seed_artists:
        Artist names to seed the graph walk.
    niche_value:
        Float 0.0–1.0 controlling the view-count ceiling via a log scale.
        0.0 = only tracks with < 1k views; 1.0 = allows up to 100M views.

    Returns
    -------
    List of dicts (sorted by niche_score descending) with keys:
        artist, title, youtube_id, view_count, niche_score, subscriber_count.
    """
    max_views = _compute_max_views(niche_value)
    logger.info(
        "Niche engine starting — niche_value=%.2f → max_views=%d", niche_value, max_views
    )

    # ------------------------------------------------------------------
    # Step 1: Resolve seed browseIds
    # ------------------------------------------------------------------
    seed_ids: list[str] = []
    for name in seed_artists:
        bid = _resolve_browse_id(name)
        if bid:
            seed_ids.append(bid)
        else:
            logger.warning("Skipping unresolvable seed: '%s'", name)

    if not seed_ids:
        logger.error("No valid seed artists — cannot continue")
        return []

    # ------------------------------------------------------------------
    # Step 2: First-degree related artists
    # browseId → {"name": str, "subscriber_str": str}
    # ------------------------------------------------------------------
    seen: set[str] = set(seed_ids)
    first_degree: dict[str, dict] = {}

    for bid in seed_ids:
        for item in _get_related_artists(bid):
            item_bid = item.get("browseId")
            if item_bid and item_bid not in seen:
                first_degree[item_bid] = {
                    "name":           item.get("title", ""),
                    "subscriber_str": item.get("subscribers", ""),
                }
                seen.add(item_bid)

    if not first_degree:
        logger.warning("No first-degree related artists found for seeds: %s", seed_artists)
        return []

    # ------------------------------------------------------------------
    # Step 3: Second-degree related artists (the jump)
    # ------------------------------------------------------------------
    second_degree: dict[str, dict] = {}

    for bid, meta in first_degree.items():
        for item in _get_related_artists(bid):
            item_bid = item.get("browseId")
            if item_bid and item_bid not in seen:
                second_degree[item_bid] = {
                    "name":           item.get("title", ""),
                    "subscriber_str": item.get("subscribers", ""),
                }
                seen.add(item_bid)

    if not second_degree:
        logger.warning("No second-degree related artists found — graph may be too shallow")
        return []

    # ------------------------------------------------------------------
    # Step 4: Filter against global charts
    # ------------------------------------------------------------------
    chart_names = _get_chart_artist_names()
    niche_pool  = {
        bid: meta
        for bid, meta in second_degree.items()
        if meta["name"].lower() not in chart_names
    }

    if not niche_pool:
        logger.warning(
            "All %d second-degree artists filtered out by global charts", len(second_degree)
        )
        return []

    logger.info(
        "Niche pool: %d artists after removing %d chart hits",
        len(niche_pool), len(second_degree) - len(niche_pool),
    )

    # ------------------------------------------------------------------
    # Step 5: Find one track per niche artist within the view ceiling
    # ------------------------------------------------------------------
    tracks: list[dict] = []

    for bid, meta in niche_pool.items():
        if len(tracks) >= MAX_TRACKS:
            break

        artist_name      = meta["name"]
        subscriber_count = _parse_subscriber_count(meta["subscriber_str"])

        track = _find_track_within_ceiling(artist_name, max_views)
        if not track:
            logger.debug(
                "No track within ceiling for '%s' (subscriber_count=%d)",
                artist_name, subscriber_count,
            )
            continue

        track["subscriber_count"] = subscriber_count
        tracks.append(track)

    # Sort by niche_score descending — frontier tracks first
    tracks.sort(key=lambda t: t["niche_score"], reverse=True)

    logger.info(
        "Niche discovery complete: %d/%d tracks, ceiling=%d views",
        len(tracks), MAX_TRACKS, max_views,
    )
    return tracks
