import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import agent
import database
import discovery
import metadata

logger = logging.getLogger(__name__)

app = FastAPI(title="Animo API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Keep the health router
from api.routes import health  # noqa: E402
app.include_router(health.router)


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
def open_pack(survey: SurveyRequest):
    """
    Receive a survey from Person A, run the niche engine, enrich each track
    with Spotify metadata, persist to Supabase, and return the full objects.
    """
    # 1. Discover niche tracks from the seed artists
    raw_tracks = discovery.get_niche_tracks(survey.seed_artists)
    if not raw_tracks:
        raise HTTPException(
            status_code=502,
            detail="Niche discovery returned no tracks. Try different seed artists.",
        )

    # 2. Enrich each track and save to the database
    results = []
    for track in raw_tracks:
        # Merge discovery data with Spotify metadata
        enriched = metadata.enrich_track_data(
            artist_name=track.get("artist", ""),
            song_name=track.get("title", ""),
        )
        full_track = {**track, **enriched}

        # Persist and attach the Supabase-assigned UUID
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
def vouch(track_id: str):
    """Increment vouch_count for the given track by 1."""
    success = database.increment_vouch(track_id)
    if not success:
        raise HTTPException(status_code=500, detail="Could not register vouch.")
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

    # Trigger AI vibe regeneration without making the user wait
    if count % 10 == 0:
        logger.info(
            "Comment count hit %d for track %s — queueing vibe regeneration",
            count, body.track_id,
        )
        background_tasks.add_task(agent.generate_new_vibe, body.track_id)

    return {"comment_count": count}
