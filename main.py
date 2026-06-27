import logging
import os

import jwt
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

import agent
import database
import discovery
from api.routes import health

logger = logging.getLogger(__name__)

app = FastAPI(title="Animo API", version="0.1.0")

# Allowed origins for browser CORS. Production frontend domain should be
# added here once deployed; never ship "*" in production.
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ANIMO_ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:8080,http://127.0.0.1:5173,http://127.0.0.1:8080",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)

_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _decode_token(token: str) -> str:
    """Verify a Supabase HS256 JWT and return the user UUID (sub claim).

    Raises HTTPException(401) on any decode failure. ``InvalidKeyError`` is a
    sibling of ``InvalidTokenError`` (not a subclass), so it's caught
    explicitly via the broad ``PyJWTError`` base.
    """
    secret = os.getenv("SUPABASE_JWT_SECRET", "")
    if not secret:
        logger.error("SUPABASE_JWT_SECRET is not configured")
        raise HTTPException(status_code=500, detail="Auth not configured")
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError as e:
        logger.warning("JWT decode failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Dependency — returns the authenticated user UUID. 401 if not present or invalid."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return _decode_token(credentials.credentials)


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str | None:
    """Dependency — returns the user UUID if a valid token is present, else None.

    Unlike ``get_current_user`` this never raises — endpoints that allow
    anonymous access can use it and branch on the result.
    """
    if credentials is None:
        return None
    try:
        return _decode_token(credentials.credentials)
    except HTTPException:
        return None


# ---------------------------------------------------------------------------
# Background task helpers
# ---------------------------------------------------------------------------

def _sync_user_genres_from_vouch(user_id: str, track_id: str) -> None:
    """Fetch the vouched track's genre tags and merge them into the user's profile."""
    genres = database.get_track_genres(track_id)
    if genres:
        database.upsert_user_genres(user_id, genres)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SurveyRequest(BaseModel):
    seed_artists:    list[str]
    niche_value:     float = 0.5   # 0.0 = ultra-underground, 1.0 = mainstream allowed
    vibe:            str   = ""    # e.g. "High Energy / Digital"
    selected_genres: list[str] = []  # up to 3 genre tags chosen by the user


class CommentRequest(BaseModel):
    track_id: str
    text: str


class GlobalTrackRequest(BaseModel):
    youtube_id: str
    title: str
    artist: str
    view_count: int | None = None


def _to_animo_card(track: dict) -> dict:
    """Normalize a DB row or enriched track payload to frontend card shape."""
    return {
        "id": track.get("id"),
        "artist": track.get("artist", ""),
        "title": track.get("title", ""),
        "youtube_id": track.get("youtube_id", ""),
        "view_count": track.get("view_count"),
        "niche_score": track.get("niche_score"),
        "depth_level": track.get("depth_level"),
        "subscriber_count": track.get("subscriber_count"),
        "spotify_img": track.get("spotify_img"),
        "genre_tags": track.get("genre_tags") or [],
        "spotify_id": track.get("spotify_id"),
        "vibe_description": track.get("vibe_description"),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"message": "Animo API is running"}


@app.post("/open-pack")
def open_pack(
    survey: SurveyRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
):
    """Run the niche engine for the authenticated user and return enriched tracks.

    Side effects on success:
      - Each discovered track is persisted to ``tracks`` with this user_id.
      - The user's ``user_profiles`` row is upserted with the survey snapshot
        and ``onboarding_complete = true``.
      - ``last_pack_opened_at`` is stamped server-side so the 24h gate works.
      - A background vibe-generation task is queued per track.
    """
    logger.info(
        "open-pack user=%s vibe=%r selected_genres=%s niche=%.2f seeds=%s",
        user_id, survey.vibe, survey.selected_genres,
        survey.niche_value, survey.seed_artists,
    )

    # Seed order: explicit artists -> selected genres -> historical user genres
    seeds = list(survey.seed_artists)
    if survey.selected_genres:
        seeds = seeds + survey.selected_genres
    user_genres = database.get_user_favorite_genres(user_id)
    if user_genres:
        logger.info("Blending %d historical genre seeds for %s", len(user_genres), user_id)
        seeds = seeds + user_genres

    raw_tracks = discovery.get_niche_tracks(seeds, niche_value=survey.niche_value)
    if not raw_tracks:
        raise HTTPException(
            status_code=502,
            detail="Niche discovery returned no tracks. Try different seed artists.",
        )

    results = []
    for track in raw_tracks:
        enriched = discovery.enrich_track_data(
            artist_name=track.get("artist", ""),
            song_name=track.get("title", ""),
        )
        full_track = {**track, **enriched}

        db_id = database.save_track_to_db(full_track, user_id=user_id)
        if db_id is None:
            logger.warning("Could not save track '%s' — skipping", full_track.get("title"))
            continue

        background_tasks.add_task(agent.generate_new_vibe, db_id)
        vibe_description = database.get_track_vibe(db_id) or "Tuning into the vibe..."
        results.append({"id": db_id, "vibe_description": vibe_description, **full_track})

    if not results:
        raise HTTPException(
            status_code=500,
            detail="Tracks were discovered but could not be saved to the database.",
        )

    # Snapshot the survey + bump pack timestamp once everything succeeded
    database.save_survey_and_mark_pack_opened(
        user_id,
        seeds=list(survey.seed_artists),
        vibe=survey.vibe,
        niche=survey.niche_value,
        genres=list(survey.selected_genres),
    )

    return results


@app.get("/me")
def me(user_id: str = Depends(get_current_user)):
    """Return the authenticated user's profile (or a sensible default if missing).

    The frontend uses ``onboarding_complete`` to gate the survey, and
    ``last_pack_opened_at`` + ``survey_*`` to decide whether to open today's
    pack reveal or jump straight to the hub.
    """
    profile = database.get_user_profile(user_id) or {}
    return {
        "id":                   user_id,
        "onboarding_complete":  bool(profile.get("onboarding_complete")),
        "last_pack_opened_at":  profile.get("last_pack_opened_at"),
        "survey_seeds":         profile.get("survey_seeds")   or [],
        "survey_vibe":          profile.get("survey_vibe")    or "",
        "survey_niche":         profile.get("survey_niche"),
        "survey_genres":        profile.get("survey_genres")  or [],
        "favorite_genres":      profile.get("favorite_genres") or [],
    }


@app.get("/my-tracks")
def my_tracks(user_id: str = Depends(get_current_user)):
    """Return all tracks discovered by the authenticated user, newest first."""
    return database.get_user_tracks(user_id)


@app.get("/search-global")
def search_global(q: str = Query(..., min_length=1)):
    """Global YouTube Music search — returns up to top 5 song matches."""
    results = discovery.search_global_songs(q, limit=5)
    return {"results": results}


@app.post("/search-global/select")
def select_global_track(
    body: GlobalTrackRequest,
    user_id: str | None = Depends(get_optional_user),
):
    """
    Resolve a selected global search result into a full Animo card.

    - If the track already exists in ``tracks``, return it directly.
    - Otherwise enrich it, save it, generate vibe immediately, and return it.
    """
    existing = database.get_track_by_youtube_id(body.youtube_id)
    if existing:
        return _to_animo_card(existing)

    base_track = {
        "title": body.title,
        "artist": body.artist,
        "youtube_id": body.youtube_id,
        "view_count": body.view_count,
        "niche_score": None,
        "depth_level": 0,
        "subscriber_count": None,
    }
    enriched = discovery.enrich_track_data(
        artist_name=base_track["artist"],
        song_name=base_track["title"],
    )
    full_track = {**base_track, **enriched}

    db_id = database.save_track_to_db(full_track, user_id=user_id)
    if db_id is None:
        raise HTTPException(status_code=500, detail="Could not save selected track.")

    vibe = agent.generate_new_vibe(db_id)
    full_track["id"] = db_id
    full_track["vibe_description"] = vibe or database.get_track_vibe(db_id)
    return _to_animo_card(full_track)


@app.get("/comments/{track_id}")
def get_comments(track_id: str):
    """Return the most recent 50 comments for a track, newest first."""
    return database.get_track_comments(track_id)


@app.post("/vouch/{track_id}")
def vouch(
    track_id: str,
    background_tasks: BackgroundTasks,
    user_id: str | None = Depends(get_optional_user),
):
    """
    Increment vouch_count for the given track by 1.

    If the request includes a valid user token, the track's genre tags are
    merged into the user's favorite_genres profile in the background.
    """
    success = database.increment_vouch(track_id)
    if not success:
        raise HTTPException(status_code=500, detail="Could not register vouch.")

    if user_id:
        background_tasks.add_task(_sync_user_genres_from_vouch, user_id, track_id)

    return {"ok": True}


@app.post("/comment")
def comment(body: CommentRequest, background_tasks: BackgroundTasks):
    """
    Save a comment and — when the running total hits a multiple of 10 —
    regenerate the track's living vibe description in the background.
    """
    count = database.add_comment(body.track_id, body.text)
    if count is None:
        raise HTTPException(status_code=500, detail="Could not save comment.")

    if count % 5 == 0:
        logger.info(
            "Comment count hit %d for track %s — queueing vibe regeneration",
            count, body.track_id,
        )
        background_tasks.add_task(agent.generate_new_vibe, body.track_id)

    return {"comment_count": count}
