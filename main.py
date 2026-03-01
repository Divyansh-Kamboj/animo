import logging
import os

import jwt
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

import agent
import database
import discovery
import metadata
from api.routes import health

logger = logging.getLogger(__name__)

app = FastAPI(title="Animo API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)

_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _decode_token(token: str) -> str:
    """Verify a Supabase JWT and return the user UUID (sub claim)."""
    try:
        payload = jwt.decode(
            token,
            os.getenv("SUPABASE_JWT_SECRET", ""),
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Dependency — requires a valid Bearer token; raises 401 otherwise."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authorization header required.")
    return _decode_token(credentials.credentials)


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str | None:
    """Dependency — returns the user UUID if a valid token is present, else None."""
    if credentials is None:
        return None
    return _decode_token(credentials.credentials)  # still raises 401 on bad tokens


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
    seed_artists: list[str]


class CommentRequest(BaseModel):
    track_id: str
    text: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"message": "Animo API is running"}


@app.post("/open-pack")
def open_pack(
    survey: SurveyRequest,
    user_id: str | None = Depends(get_optional_user),
):
    """
    Receive a survey from Person A, run the niche engine, enrich each track
    with Spotify metadata, persist to Supabase, and return the full objects.

    If the request includes a valid user token, the user's saved genre
    preferences are appended to the seed list so the discovery graph is
    biased toward their taste history.
    """
    # 1. Blend survey seeds with the user's genre preferences (if logged in)
    seeds = list(survey.seed_artists)
    if user_id:
        user_genres = database.get_user_favorite_genres(user_id)
        if user_genres:
            logger.info("Blending %d user genre seeds for user %s", len(user_genres), user_id)
            seeds = seeds + user_genres  # genres resolve as YT Music topics/artists

    # 2. Discover niche tracks
    raw_tracks = discovery.get_niche_tracks(seeds)
    if not raw_tracks:
        raise HTTPException(
            status_code=502,
            detail="Niche discovery returned no tracks. Try different seed artists.",
        )

    # 3. Enrich each track with Spotify metadata and persist
    results = []
    for track in raw_tracks:
        enriched = metadata.enrich_track_data(
            artist_name=track.get("artist", ""),
            song_name=track.get("title", ""),
        )
        full_track = {**track, **enriched}

        db_id = database.save_track_to_db(full_track)
        if db_id is None:
            logger.warning("Could not save track '%s' — skipping", full_track.get("title"))
            continue

        results.append({"id": db_id, **full_track})

    if not results:
        raise HTTPException(
            status_code=500,
            detail="Tracks were discovered but could not be saved to the database.",
        )

    return results


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

    if count % 10 == 0:
        logger.info(
            "Comment count hit %d for track %s — queueing vibe regeneration",
            count, body.track_id,
        )
        background_tasks.add_task(agent.generate_new_vibe, body.track_id)

    return {"comment_count": count}
