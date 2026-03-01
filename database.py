"""
database.py — Animo Supabase Data Layer

Handles all reads and writes to the Supabase backend.

Expected schema
---------------
tracks            : id (uuid PK), title, artist, youtube_id, spotify_img,
                    genre_tags (text[]), vouch_count (int, default 0),
                    vibe_description (text, nullable),
                    view_count (bigint, nullable), subscriber_count (bigint, nullable),
                    depth_level (smallint, nullable)
comments          : id (uuid PK), track_id (uuid FK → tracks.id), text, created_at
user_profiles     : id (uuid PK FK → auth.users), favorite_genres (text[]),
                    total_vouches (int), onboarding_complete (bool)
user_interactions : id (uuid PK), user_id (uuid FK → auth.users),
                    track_id (uuid FK → tracks.id),
                    interaction_type ('vouch'|'comment'), created_at

The increment_vouch() function requires a Postgres helper:

    CREATE OR REPLACE FUNCTION increment_vouch(row_id uuid)
    RETURNS void LANGUAGE sql AS $$
        UPDATE tracks SET vouch_count = vouch_count + 1 WHERE id = row_id;
    $$;
"""

import logging
import os
from collections import Counter

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

logger = logging.getLogger(__name__)

# TEMPORARY: must match _TEST_USER_ID in main.py — remove with auth bypass
_TEST_USER_ID = "00000000-0000-0000-0000-000000000001"

_db: Client = create_client(
    supabase_url=os.environ.get("SUPABASE_URL", ""),
    supabase_key=os.environ.get("SUPABASE_KEY", ""),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_track_to_db(track_data: dict, user_id: str | None = None) -> str | None:
    """
    Insert a track record and return its newly assigned UUID.

    Parameters
    ----------
    track_data:
        Must contain ``title``, ``artist``, ``youtube_id``.
        May contain ``spotify_img`` (str) and ``genre_tags`` (list[str]).
    user_id:
        UUID of the user who discovered this track. Stored so tracks can be
        filtered per-user via GET /my-tracks.

    Returns
    -------
    The ``id`` (UUID string) of the inserted row, or ``None`` on failure.
    """
    payload = {
        "title":            track_data.get("title"),
        "artist":           track_data.get("artist"),
        "youtube_id":       track_data.get("youtube_id"),
        "spotify_img":      track_data.get("spotify_img"),
        "genre_tags":       track_data.get("genre_tags", []),
        "view_count":       track_data.get("view_count"),
        "subscriber_count": track_data.get("subscriber_count"),
        "depth_level":      track_data.get("depth_level"),
        "niche_score":      track_data.get("niche_score"),
        "user_id":          user_id,
    }

    try:
        response = _db.table("tracks").insert(payload).execute()
        rows = response.data
        if not rows:
            logger.error("Track insert returned no data for payload: %s", payload)
            return None
        track_id = rows[0].get("id")
        logger.info("Saved track '%s' with id %s", payload.get("title"), track_id)
        return track_id
    except Exception:
        logger.error("Failed to insert track '%s'", payload.get("title"), exc_info=True)
        return None


def get_user_tracks(user_id: str) -> list[dict]:
    """
    Return all tracks discovered by the given user, newest first.

    Returns the full track row so the frontend can reconstruct TrackData
    without an additional enrichment step.
    """
    try:
        response = (
            _db.table("tracks")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return response.data or []
    except Exception:
        logger.warning("Could not fetch tracks for user %s", user_id, exc_info=True)
        return []


def increment_vouch(track_id: str) -> bool:
    """
    Atomically increment the ``vouch_count`` for a track by 1.

    Uses a Postgres RPC to avoid a read-modify-write race condition.
    See the module docstring for the required SQL function definition.

    Returns
    -------
    ``True`` on success, ``False`` on failure.
    """
    try:
        _db.rpc("increment_vouch", {"row_id": track_id}).execute()
        logger.info("Incremented vouch_count for track %s", track_id)
        return True
    except Exception:
        logger.error("Failed to increment vouch for track %s", track_id, exc_info=True)
        return False


def add_comment(track_id: str, text: str) -> int | None:
    """
    Insert a comment and return the updated total comment count for the track.

    The caller can use the returned count to detect multiples-of-ten triggers
    (e.g. ``if count % 10 == 0: fire_event()``).

    Parameters
    ----------
    track_id:
        UUID of the parent track.
    text:
        Comment body.

    Returns
    -------
    Total number of comments for ``track_id`` after the insert,
    or ``None`` on failure.
    """
    try:
        _db.table("comments").insert({"track_id": track_id, "text": text}).execute()
        logger.info("Added comment to track %s", track_id)
    except Exception:
        logger.error("Failed to insert comment for track %s", track_id, exc_info=True)
        return None

    try:
        response = (
            _db.table("comments")
            .select("id", count="exact")
            .eq("track_id", track_id)
            .execute()
        )
        count = response.count
        logger.info("Track %s now has %s comment(s)", track_id, count)
        return count
    except Exception:
        logger.error(
            "Comment inserted but count query failed for track %s", track_id, exc_info=True
        )
        # Comment was saved; return None to signal the count is unknown
        return None


def get_track_genres(track_id: str) -> list[str]:
    """Return the genre_tags list for a track, or empty list on failure."""
    try:
        response = (
            _db.table("tracks")
            .select("genre_tags")
            .eq("id", track_id)
            .single()
            .execute()
        )
        return response.data.get("genre_tags") or []
    except Exception:
        logger.warning("Could not fetch genres for track %s", track_id, exc_info=True)
        return []


def get_track_comments(track_id: str, limit: int = 50) -> list[dict]:
    """Return the most recent comments for a track, newest first."""
    try:
        response = (
            _db.table("comments")
            .select("text, created_at")
            .eq("track_id", track_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data or []
    except Exception:
        logger.warning("Could not fetch comments for track %s", track_id, exc_info=True)
        return []


def get_track_vibe(track_id: str) -> str | None:
    """Return vibe_description for a track, or None if not yet generated."""
    try:
        response = (
            _db.table("tracks")
            .select("vibe_description")
            .eq("id", track_id)
            .single()
            .execute()
        )
        return response.data.get("vibe_description")
    except Exception:
        logger.warning("Could not fetch vibe for track %s", track_id, exc_info=True)
        return None


def get_user_favorite_genres(user_id: str | None) -> list[str]:
    """Return favorite_genres from user_profiles, or empty list if no profile yet.

    Falls back to _TEST_USER_ID when ``user_id`` is None (auth-bypass mode).
    """
    uid = user_id or _TEST_USER_ID
    try:
        response = (
            _db.table("user_profiles")
            .select("favorite_genres")
            .eq("id", uid)
            .single()
            .execute()
        )
        return response.data.get("favorite_genres") or []
    except Exception:
        logger.warning("Could not fetch profile for user %s", uid, exc_info=True)
        return []


def upsert_user_genres(user_id: str | None, new_genres: list[str]) -> None:
    """
    Merge ``new_genres`` into ``user_profiles.favorite_genres`` for the user.

    Uses an ordered dedup so earlier (longer-standing) preferences stay first.
    Upserts the profile row so the first vouch creates the profile automatically.
    Falls back to _TEST_USER_ID when ``user_id`` is None (auth-bypass mode).
    """
    uid = user_id or _TEST_USER_ID
    current = get_user_favorite_genres(uid)
    # dict.fromkeys preserves order while deduplicating
    merged = list(dict.fromkeys(current + new_genres))
    try:
        _db.table("user_profiles").upsert(
            {"id": uid, "favorite_genres": merged},
            on_conflict="id",
        ).execute()
        logger.info("Upserted genres for user %s: %s", uid, merged)
    except Exception:
        logger.error("Could not upsert genres for user %s", uid, exc_info=True)


def get_user_taste(user_id: str) -> list[str]:
    """
    Return the top 3 genre tags across all tracks this user has interacted with.

    Aggregates genre_tags from every track the user has vouched for or
    commented on, counts occurrences, and returns the three most frequent.

    Parameters
    ----------
    user_id:
        UUID of the auth user.

    Returns
    -------
    List of up to 3 genre tag strings, ordered by interaction frequency.
    Returns an empty list when the user has no interactions or on failure.
    """
    # 1. Collect the track IDs this user has touched
    try:
        interactions = (
            _db.table("user_interactions")
            .select("track_id")
            .eq("user_id", user_id)
            .execute()
        )
        track_ids = list({row["track_id"] for row in (interactions.data or [])})
    except Exception:
        logger.error("Could not fetch interactions for user %s", user_id, exc_info=True)
        return []

    if not track_ids:
        logger.info("No interactions found for user %s", user_id)
        return []

    # 2. Fetch genre_tags for those tracks in one query
    try:
        tracks = (
            _db.table("tracks")
            .select("genre_tags")
            .in_("id", track_ids)
            .execute()
        )
    except Exception:
        logger.error("Could not fetch track genres for user %s", user_id, exc_info=True)
        return []

    # 3. Flatten all genre lists and tally frequency
    genre_counts: Counter = Counter()
    for row in (tracks.data or []):
        for genre in (row.get("genre_tags") or []):
            genre_counts[genre] += 1

    top_3 = [genre for genre, _ in genre_counts.most_common(3)]
    logger.info("Top genres for user %s: %s", user_id, top_3)
    return top_3
