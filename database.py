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


def get_track_by_id(track_id: str) -> dict | None:
    """Return a single track row by primary id, or None if not found."""
    try:
        response = (
            _db.table("tracks")
            .select("*")
            .eq("id", track_id)
            .single()
            .execute()
        )
        return response.data
    except Exception:
        logger.info("No track found with id %s", track_id)
        return None


def get_track_by_youtube_id(youtube_id: str) -> dict | None:
    """Return the most recent track row matching a YouTube video ID."""
    try:
        response = (
            _db.table("tracks")
            .select("*")
            .eq("youtube_id", youtube_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None
    except Exception:
        logger.warning(
            "Could not fetch track by youtube_id %s", youtube_id, exc_info=True
        )
        return None


def get_track_vouch_count(track_id: str) -> int:
    """Return current vouch_count for a track, or 0 on failure."""
    try:
        response = (
            _db.table("tracks")
            .select("vouch_count")
            .eq("id", track_id)
            .single()
            .execute()
        )
        return int(response.data.get("vouch_count") or 0)
    except Exception:
        return 0


def register_vouch(user_id: str, track_id: str) -> tuple[int, bool]:
    """Idempotent vouch — at most one per (user, track).

    Inserts a row into ``user_interactions`` with interaction_type='vouch';
    the existing UNIQUE (user_id, track_id, interaction_type) constraint
    prevents duplicates. If the insert succeeds (i.e. this is the first
    vouch from this user for this track), ``vouch_count`` is incremented.

    Returns
    -------
    (vouch_count, was_new) — ``was_new`` is False when the user had already
    vouched (idempotent no-op).
    """
    try:
        _db.table("user_interactions").insert({
            "user_id": user_id,
            "track_id": track_id,
            "interaction_type": "vouch",
        }).execute()
        # Insert succeeded -> first vouch from this user for this track
        _db.rpc("increment_vouch", {"row_id": track_id}).execute()
        return get_track_vouch_count(track_id), True
    except Exception as e:
        # Most common path: unique-constraint violation -> already vouched.
        # Anything else still leaves us with a sane read of the count.
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg or "23505" in msg:
            logger.info("User %s already vouched for %s — no-op", user_id, track_id)
            return get_track_vouch_count(track_id), False
        logger.error("Failed to register vouch", exc_info=True)
        return get_track_vouch_count(track_id), False


def get_user_vouched_track_ids(user_id: str) -> list[str]:
    """Return the list of track_ids this user has already vouched for."""
    try:
        response = (
            _db.table("user_interactions")
            .select("track_id")
            .eq("user_id", user_id)
            .eq("interaction_type", "vouch")
            .execute()
        )
        return [row["track_id"] for row in (response.data or [])]
    except Exception:
        logger.warning("Could not fetch vouches for %s", user_id, exc_info=True)
        return []


def add_comment(track_id: str, user_id: str, text: str) -> int | None:
    """Insert a comment authored by ``user_id`` and return the new total count.

    Returns ``None`` if the insert failed or the count couldn't be read.
    """
    try:
        _db.table("comments").insert({
            "track_id": track_id,
            "user_id":  user_id,
            "text":     text,
        }).execute()
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
        return response.count
    except Exception:
        logger.error("Comment saved but count failed for %s", track_id, exc_info=True)
        return None


def _email_prefix(email: str | None) -> str:
    """'alice@example.com' -> 'alice'. Returns 'anonymous' for falsy input."""
    if not email or "@" not in email:
        return "anonymous"
    return email.split("@", 1)[0]


def _resolve_user_labels(user_ids: list[str]) -> dict[str, str]:
    """Best-effort lookup of email-prefix display labels for a batch of UUIDs.

    Uses the admin auth API (service-role only). N+1 in the worst case but
    deduped by caller; comment lists are bounded at 50 so it's fine for now.
    """
    labels: dict[str, str] = {}
    for uid in user_ids:
        try:
            res = _db.auth.admin.get_user_by_id(uid)
            user = getattr(res, "user", None) or (res.get("user") if isinstance(res, dict) else None)
            email = getattr(user, "email", None) if user else None
            labels[uid] = _email_prefix(email)
        except Exception:
            labels[uid] = "anonymous"
    return labels


def get_track_comments(track_id: str, limit: int = 50) -> list[dict]:
    """Return recent comments for a track with author display labels.

    Shape per item: {text, created_at, author}. Legacy rows where user_id
    is NULL (pre-migration-009) come through as author='anonymous'.
    """
    try:
        response = (
            _db.table("comments")
            .select("text, created_at, user_id")
            .eq("track_id", track_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = response.data or []
    except Exception:
        logger.warning("Could not fetch comments for track %s", track_id, exc_info=True)
        return []

    unique_uids = list({r["user_id"] for r in rows if r.get("user_id")})
    labels = _resolve_user_labels(unique_uids) if unique_uids else {}
    return [
        {
            "text": r["text"],
            "created_at": r["created_at"],
            "author": labels.get(r.get("user_id") or "", "anonymous"),
        }
        for r in rows
    ]


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


def get_user_profile(user_id: str) -> dict | None:
    """Return the full user_profiles row for ``user_id``, or None if missing."""
    try:
        response = (
            _db.table("user_profiles")
            .select("*")
            .eq("id", user_id)
            .single()
            .execute()
        )
        return response.data
    except Exception:
        logger.info("No profile yet for user %s", user_id)
        return None


def save_survey_and_mark_pack_opened(
    user_id: str,
    *,
    seeds: list[str],
    vibe: str,
    niche: float,
    genres: list[str],
) -> None:
    """Snapshot the user's survey answers and stamp last_pack_opened_at = NOW().

    Called from /open-pack after a successful discovery so a returning user
    can regenerate the same flavour of pack tomorrow and the 24h gate has
    a reference point. Sets onboarding_complete to True as a side effect.
    """
    payload = {
        "id": user_id,
        "survey_seeds":   seeds,
        "survey_vibe":    vibe,
        "survey_niche":   niche,
        "survey_genres":  genres,
        "onboarding_complete": True,
    }
    try:
        _db.table("user_profiles").upsert(payload, on_conflict="id").execute()
        # Stamp last_pack_opened_at server-side via a second update so we
        # don't have to ship the timestamp from Python.
        _db.rpc("touch_last_pack_opened", {"uid": user_id}).execute()
        logger.info("Saved survey + bumped pack timestamp for user %s", user_id)
    except Exception:
        logger.error("Could not save survey for user %s", user_id, exc_info=True)


def get_user_favorite_genres(user_id: str | None) -> list[str]:
    """Return favorite_genres from user_profiles, or empty list when no user / no profile."""
    if not user_id:
        return []
    try:
        response = (
            _db.table("user_profiles")
            .select("favorite_genres")
            .eq("id", user_id)
            .single()
            .execute()
        )
        return response.data.get("favorite_genres") or []
    except Exception:
        logger.warning("Could not fetch profile for user %s", user_id, exc_info=True)
        return []


def upsert_user_genres(user_id: str | None, new_genres: list[str]) -> None:
    """Merge ``new_genres`` into ``user_profiles.favorite_genres`` for the user.

    Ordered-dedup keeps longer-standing preferences first. The upsert creates
    the profile row on first vouch. No-op when ``user_id`` is missing.
    """
    if not user_id:
        return
    current = get_user_favorite_genres(user_id)
    merged = list(dict.fromkeys(current + new_genres))
    try:
        _db.table("user_profiles").upsert(
            {"id": user_id, "favorite_genres": merged},
            on_conflict="id",
        ).execute()
        logger.info("Upserted genres for user %s: %s", user_id, merged)
    except Exception:
        logger.error("Could not upsert genres for user %s", user_id, exc_info=True)


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
