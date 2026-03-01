"""
metadata.py — Animo Metadata Enrichment

Enriches a (artist, track) pair with Spotify metadata: album art URL,
genre tags, and a Spotify track ID.
"""

import logging
import os

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyClientCredentials

load_dotenv()

logger = logging.getLogger(__name__)

# Initialised once at startup; SpotifyClientCredentials handles token refresh.
_sp = spotipy.Spotify(
    auth_manager=SpotifyClientCredentials(
        client_id=os.environ.get("SPOTIFY_CLIENT_ID"),
        client_secret=os.environ.get("SPOTIFY_CLIENT_SECRET"),
    )
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_track_data(artist_name: str, song_name: str) -> dict:
    """
    Enrich a track with Spotify metadata.

    Attempts an exact ``track:<name> artist:<name>`` search first.
    If no match is found, falls back to an artist-only search to recover
    at least a genre list and a representative image.

    Parameters
    ----------
    artist_name:
        Display name of the artist (e.g. ``"Aldous Harding"``).
    song_name:
        Display name of the track (e.g. ``"The Barrel"``).

    Returns
    -------
    {
        "spotify_img":  str | None   — album art URL, highest resolution,
        "genre_tags":   list[str]    — Spotify genre tags from the Artist object,
        "spotify_id":   str | None   — Spotify track ID (None on fallback),
    }
    """
    result: dict = {"spotify_img": None, "genre_tags": [], "spotify_id": None}

    # ------------------------------------------------------------------
    # Primary path: exact track + artist search
    # ------------------------------------------------------------------
    track_items: list = []
    try:
        query = f"track:{song_name} artist:{artist_name}"
        response = _sp.search(q=query, type="track", limit=1)
        track_items = response.get("tracks", {}).get("items", [])
    except Exception:
        logger.warning(
            "Spotify track search failed for '%s – %s'",
            artist_name, song_name,
            exc_info=True,
        )

    if track_items:
        track = track_items[0]
        result["spotify_id"] = track.get("id")

        # Album images are ordered largest → smallest by Spotify
        images = track.get("album", {}).get("images", [])
        if images:
            result["spotify_img"] = images[0].get("url")

        # Genres are only available on the full Artist object, not the track
        artists_on_track = track.get("artists", [])
        artist_id = artists_on_track[0].get("id") if artists_on_track else None
        if artist_id:
            try:
                artist_obj = _sp.artist(artist_id)
                result["genre_tags"] = artist_obj.get("genres", [])
            except Exception:
                logger.warning(
                    "Could not fetch genres for Spotify artist id '%s'",
                    artist_id,
                    exc_info=True,
                )

        return result

    # ------------------------------------------------------------------
    # Fallback: artist-only search for at least genre + image
    # ------------------------------------------------------------------
    logger.info(
        "No track match for '%s – %s'; falling back to artist search",
        artist_name, song_name,
    )

    artist_items: list = []
    try:
        response = _sp.search(q=artist_name, type="artist", limit=1)
        artist_items = response.get("artists", {}).get("items", [])
    except Exception:
        logger.warning(
            "Spotify artist fallback search failed for '%s'",
            artist_name,
            exc_info=True,
        )
        return result

    if not artist_items:
        logger.warning("No Spotify artist found for '%s'", artist_name)
        return result

    artist_obj = artist_items[0]
    result["genre_tags"] = artist_obj.get("genres", [])

    # Artist profile images are also ordered largest → smallest
    images = artist_obj.get("images", [])
    if images:
        result["spotify_img"] = images[0].get("url")

    return result
