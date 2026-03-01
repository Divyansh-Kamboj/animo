"""
database.py — Animo Supabase Data Layer

Handles all reads and writes to the Supabase backend.

Expected schema
---------------
tracks   : id (uuid PK), title, artist, youtube_id, spotify_img,
           genre_tags (text[]), vouch_count (int, default 0)
comments : id (uuid PK), track_id (uuid FK → tracks.id), text, created_at

The increment_vouch() function requires a Postgres helper:

    CREATE OR REPLACE FUNCTION increment_vouch(row_id uuid)
    RETURNS void LANGUAGE sql AS $$
        UPDATE tracks SET vouch_count = vouch_count + 1 WHERE id = row_id;
    $$;
"""

import logging
import os

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

def save_track_to_db(track_data: dict) -> str | None:
    """
    Insert a track record and return its newly assigned UUID.

    Parameters
    ----------
    track_data:
        Must contain ``title``, ``artist``, ``youtube_id``.
        May contain ``spotify_img`` (str) and ``genre_tags`` (list[str]).

    Returns
    -------
    The ``id`` (UUID string) of the inserted row, or ``None`` on failure.
    """
    payload = {
        "title":       track_data.get("title"),
        "artist":      track_data.get("artist"),
        "youtube_id":  track_data.get("youtube_id"),
        "spotify_img": track_data.get("spotify_img"),
        "genre_tags":  track_data.get("genre_tags", []),
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
