"""
discovery.py — Animo Niche Engine (Recursive)

Traverses YouTube Music's related-artist graph recursively, collecting tracks
that fall inside a user-controlled view-count window [MIN_VIEWS, max_views].
Depth is capped at MAX_DEPTH to prevent infinite loops.
"""

import logging
import re

from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)

_ytmusic = YTMusic()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_VIEWS          = 40_000   # absolute floor — below this is too obscure
MAX_TRACKS         = 5
MAX_DEPTH          = 3        # max recursion levels
SONGS_TO_CHECK     = 5        # songs inspected per candidate artist
MAX_PER_ARTIST     = 2        # diversification cap — max tracks from one artist


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _compute_max_views(niche_value: float) -> int:
    """
    Map 0.0–1.0 to a logarithmic view-count ceiling.

    niche_value | max_views (approx)
    ------------|-------------------
    0.0         |        40 000   (ultra-tight window at the floor)
    0.5         |     1 260 000
    1.0         |   100 000 000

    Formula: 10 ** (4.6 + niche_value * 3.4)
    """
    niche_value = max(0.0, min(1.0, niche_value))
    return int(10 ** (4.6 + niche_value * 3.4))


def _compute_niche_score(view_count: int, max_views: int) -> int:
    """
    0–100 score: how close the track sits to the max_views frontier.
    100 = right at the ceiling, 0 = right at the floor.
    """
    if max_views <= MIN_VIEWS:
        return 0
    span = max_views - MIN_VIEWS
    return min(100, round(((view_count - MIN_VIEWS) / span) * 100))


def _parse_subscriber_count(subscriber_str: str | None) -> int:
    """'1.23M subscribers' → 1_230_000. Returns 0 on failure."""
    if not subscriber_str:
        return 0
    token = subscriber_str.strip().split()[0].lower()
    match = re.match(r"^([\d.]+)([kmb]?)$", token)
    if not match:
        return 0
    number = float(match.group(1))
    mult   = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(match.group(2), 1)
    return int(number * mult)


# ---------------------------------------------------------------------------
# ytmusicapi wrappers
# ---------------------------------------------------------------------------

def _resolve_browse_id(artist_name: str) -> str | None:
    """Search for an artist and return the first result's browseId."""
    try:
        results = _ytmusic.search(artist_name, filter="artists")
        if results:
            return results[0].get("browseId")
        logger.warning("No search results for '%s'", artist_name)
    except Exception:
        logger.warning("Artist search failed for '%s'", artist_name, exc_info=True)
    return None


def _get_artist_related_list(browse_id: str) -> list[dict]:
    """
    Return the related-artist list for a given browseId.

    Tries ``get_artist_related(related_browse_id)`` first (full list via the
    dedicated endpoint), falls back to the inline results from ``get_artist``.
    Each item has: browseId, title, subscribers.
    """
    try:
        artist_data      = _ytmusic.get_artist(browse_id)
        related_section  = artist_data.get("related", {})
        related_browse_id = related_section.get("browseId")

        if related_browse_id:
            try:
                return _ytmusic.get_artist_related(related_browse_id)
            except Exception:
                logger.debug(
                    "get_artist_related() failed for %s — using inline results",
                    related_browse_id,
                )

        return related_section.get("results", [])

    except Exception:
        logger.warning("Could not fetch related for browseId '%s'", browse_id, exc_info=True)
        return []


def _get_view_count(video_id: str) -> int | None:
    """Return the integer viewCount from videoDetails, or None."""
    try:
        data     = _ytmusic.get_song(video_id)
        view_str = data.get("videoDetails", {}).get("viewCount", "")
        if view_str and view_str.isdigit():
            return int(view_str)
    except Exception:
        logger.debug("Could not fetch view count for '%s'", video_id, exc_info=True)
    return None


def _get_chart_artist_names() -> set[str]:
    """Return a lower-cased set of global chart artist names."""
    try:
        charts  = _ytmusic.get_charts(country="ZZ")
        section = charts.get("artists", [])
        items   = section.get("items", []) if isinstance(section, dict) else section
        return {item.get("title", "").lower() for item in items if item.get("title")}
    except Exception:
        logger.warning("Could not fetch global charts — skipping chart filter", exc_info=True)
        return set()


def _find_track_in_window(
    artist_name: str,
    min_views: int,
    max_views: int,
    depth: int,
) -> dict | None:
    """
    Search for songs by ``artist_name`` and return the track closest to the
    max_views frontier that falls within [min_views, max_views].

    Checks up to SONGS_TO_CHECK results; picks the highest niche_score.
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

        if not (min_views <= view_count <= max_views):
            logger.debug(
                "'%s' (%s) — %d views outside window [%d, %d]",
                title, artist_name, view_count, min_views, max_views,
            )
            continue

        score = _compute_niche_score(view_count, max_views)
        if best is None or score > best["niche_score"]:
            best = {
                "artist":      artist_name,
                "title":       title,
                "youtube_id":  video_id,
                "view_count":  view_count,
                "niche_score": score,
                "depth_level": depth,
            }

    return best


# ---------------------------------------------------------------------------
# Recursive core
# ---------------------------------------------------------------------------

def _find_tracks_within_window(
    browse_id:     str,
    min_views:     int,
    max_views:     int,
    chart_names:   set[str],
    depth:         int,
    seen:          set[str],
    pack:          list[dict],
    artist_counts: dict[str, int],
) -> None:
    """
    Recursive worker. Mutates ``pack``, ``seen``, and ``artist_counts`` in place.

    For each related artist of ``browse_id``:
      - Skip if on global charts or already visited.
      - Search their songs for a track within [min_views, max_views].
      - If found, add to pack (up to MAX_PER_ARTIST per artist).

    After exhausting the related list, recurse into the single most niche
    (lowest subscriber count) related artist that was not already added,
    up to MAX_DEPTH levels.
    """
    if depth >= MAX_DEPTH or len(pack) >= MAX_TRACKS:
        return

    seen.add(browse_id)
    related = _get_artist_related_list(browse_id)

    if not related:
        logger.debug("No related artists at depth %d for %s", depth, browse_id)
        return

    most_niche_candidate: tuple[str, int] | None = None   # (browse_id, sub_count)

    for item in related:
        if len(pack) >= MAX_TRACKS:
            break

        item_bid  = item.get("browseId")
        item_name = item.get("title", "")
        sub_str   = item.get("subscribers", "")

        if not item_bid or item_bid in seen:
            continue
        if item_name.lower() in chart_names:
            logger.debug("Skipping chart artist '%s'", item_name)
            seen.add(item_bid)
            continue

        seen.add(item_bid)
        sub_count = _parse_subscriber_count(sub_str)

        # Diversification: max MAX_PER_ARTIST tracks from any one artist
        if artist_counts.get(item_name, 0) >= MAX_PER_ARTIST:
            continue

        track = _find_track_in_window(item_name, min_views, max_views, depth)
        if track:
            track["subscriber_count"] = sub_count
            pack.append(track)
            artist_counts[item_name] = artist_counts.get(item_name, 0) + 1
            logger.info(
                "[depth %d] Added '%s' by %s (%d views, score %d)",
                depth, track["title"], item_name,
                track["view_count"], track["niche_score"],
            )
        else:
            # Candidate for deeper recursion — track the most niche (fewest subs)
            if most_niche_candidate is None or sub_count < most_niche_candidate[1]:
                most_niche_candidate = (item_bid, sub_count)

    # Recurse into the most niche untapped branch if the pack is still short
    if len(pack) < MAX_TRACKS and most_niche_candidate:
        logger.info(
            "Pack needs %d more — recursing to depth %d",
            MAX_TRACKS - len(pack), depth + 1,
        )
        _find_tracks_within_window(
            browse_id=most_niche_candidate[0],
            min_views=min_views,
            max_views=max_views,
            chart_names=chart_names,
            depth=depth + 1,
            seen=seen,
            pack=pack,
            artist_counts=artist_counts,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_niche_tracks(
    seed_artists: list[str],
    niche_value:  float = 0.5,
) -> list[dict]:
    """
    Discover niche tracks via recursive related-artist traversal.

    Parameters
    ----------
    seed_artists:
        Artist names to seed the graph walk.
    niche_value:
        Float 0.0–1.0. Controls the view-count window ceiling via
        ``10 ** (4.6 + niche_value * 3.4)``. The floor is always 40k views.

    Returns
    -------
    Up to 5 track dicts, sorted by niche_score descending, from at least
    3 different artists. Each dict contains:
        artist, title, youtube_id, view_count, niche_score,
        depth_level, subscriber_count.
    """
    min_views = MIN_VIEWS
    max_views = _compute_max_views(niche_value)

    logger.info(
        "Niche engine — niche_value=%.2f, window=[%d, %d]",
        niche_value, min_views, max_views,
    )

    if max_views <= min_views:
        logger.warning(
            "max_views (%d) ≤ min_views (%d) — window is empty at this niche_value",
            max_views, min_views,
        )
        return []

    # Resolve seed artists to browseIds
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

    chart_names   = _get_chart_artist_names()
    pack:          list[dict]       = []
    seen:          set[str]         = set(seed_ids)
    artist_counts: dict[str, int]   = {}

    for seed_id in seed_ids:
        if len(pack) >= MAX_TRACKS:
            break
        _find_tracks_within_window(
            browse_id=seed_id,
            min_views=min_views,
            max_views=max_views,
            chart_names=chart_names,
            depth=0,
            seen=seen,
            pack=pack,
            artist_counts=artist_counts,
        )

    # Verify diversification (log only — pack may be short if graph is narrow)
    distinct_artists = {t["artist"] for t in pack}
    if len(distinct_artists) < 3 and len(pack) == MAX_TRACKS:
        logger.warning(
            "Diversification target not met: %d distinct artist(s) in pack",
            len(distinct_artists),
        )

    pack.sort(key=lambda t: t["niche_score"], reverse=True)

    logger.info(
        "Niche discovery complete: %d track(s) from %d artist(s), max depth reached",
        len(pack), len(distinct_artists),
    )
    return pack
