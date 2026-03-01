"""
agent.py — Animo Living Description Agent

Uses Azure OpenAI (GPT-4o) to regenerate a track's 'vibe_description'
by blending the artist context with the 10 most recent community comments.

Expected schema addition
------------------------
tracks : vibe_description (text, nullable)
"""

import logging
import os

from dotenv import load_dotenv
from openai import AzureOpenAI

from database import _db

load_dotenv()

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are the Spirit of the Animo Community. Summarize this song's vibe "
    "by blending the artist's intent with the latest user interpretations. "
    "Keep it poetic, raw, and under 40 words."
)

# Single shared client — thread-safe; handles auth internally.
_ai = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_version="2024-02-15-preview",
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _fetch_track(track_id: str) -> dict | None:
    """Return {title, artist} for the track, or None if not found."""
    try:
        response = (
            _db.table("tracks")
            .select("title, artist")
            .eq("id", track_id)
            .single()
            .execute()
        )
        return response.data  # single() unwraps to a dict, not a list
    except Exception:
        logger.error("Could not fetch track %s", track_id, exc_info=True)
        return None


def _fetch_recent_comments(track_id: str, limit: int = 10) -> list[str]:
    """Return the body text of the most recent ``limit`` comments."""
    try:
        response = (
            _db.table("comments")
            .select("text")
            .eq("track_id", track_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [row.get("text", "") for row in (response.data or [])]
    except Exception:
        logger.warning(
            "Could not fetch comments for track %s — proceeding without them",
            track_id,
            exc_info=True,
        )
        return []


def _call_model(artist: str, title: str, comments: list[str]) -> str | None:
    """Build the prompt and call GPT-4o. Returns the raw text response."""
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")

    comment_block = (
        "\n".join(f"- {c}" for c in comments) if comments else "(no comments yet)"
    )
    user_prompt = (
        f"Artist: {artist}\n"
        f"Song: {title}\n"
        f"Recent community comments:\n{comment_block}"
    )

    try:
        completion = _ai.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=80,   # 40-word cap ≈ 55 tokens; 80 gives comfortable headroom
            temperature=0.8,
        )
        return completion.choices[0].message.content.strip()
    except Exception:
        logger.error(
            "Azure OpenAI call failed for '%s – %s'", artist, title, exc_info=True
        )
        return None


def _save_vibe(track_id: str, vibe: str) -> bool:
    """Persist the generated vibe description back to the tracks table."""
    try:
        _db.table("tracks").update({"vibe_description": vibe}).eq("id", track_id).execute()
        logger.info("Updated vibe_description for track %s", track_id)
        return True
    except Exception:
        logger.error(
            "Failed to save vibe_description for track %s", track_id, exc_info=True
        )
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_new_vibe(track_id: str) -> str | None:
    """
    Regenerate and persist the living vibe description for a track.

    Fetches the track metadata and its 10 most recent comments, sends them
    to GPT-4o with a community-spirit system prompt, then writes the result
    back to ``tracks.vibe_description``.

    Parameters
    ----------
    track_id:
        UUID of the track to update.

    Returns
    -------
    The freshly generated vibe string, or ``None`` if any step failed.
    """
    track = _fetch_track(track_id)
    if not track:
        logger.error("Aborting vibe generation — track %s not found", track_id)
        return None

    artist = track.get("artist", "Unknown Artist")
    title  = track.get("title",  "Unknown Title")

    comments = _fetch_recent_comments(track_id)
    logger.info(
        "Generating vibe for '%s – %s' with %d comment(s)", artist, title, len(comments)
    )

    vibe = _call_model(artist, title, comments)
    if not vibe:
        return None

    _save_vibe(track_id, vibe)
    return vibe
