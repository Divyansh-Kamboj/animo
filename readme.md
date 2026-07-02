# Animo

Music discovery backend for Animo. Users answer a short survey (seed artists, a
vibe, a niche slider, a few preferred genres) and get back a small pack of
tracks that lean toward the deeper, less-crowded corners of YouTube Music.
Every track is enriched with Spotify metadata and gets an AI-written "vibe
description" that keeps evolving as people leave comments on it.

The service runs as a plain FastAPI app for local development and as an Azure
Functions v2 ASGI wrapper in production.

## What it does

- `POST /open-pack` runs the discovery engine for the current user. Given
  survey inputs plus their prior liked-genre history, it walks the YouTube
  Music related-tracks graph, filters by a view-count window (the niche slider
  controls how mainstream that window is), enriches every hit through Spotify,
  and writes the tracks back to Supabase. Discovery and enrichment fan out
  across a thread pool so a five-track pack usually lands in about a second
  instead of five.
- `POST /vouch/{track_id}` records a per-user vouch (one per user per track,
  idempotent) and merges the track's genre tags into that user's favorite
  genres so future packs get smarter.
- `POST /comment` saves a comment on a track. Every fifth comment triggers a
  background call to Azure OpenAI GPT-4o that rewrites the track's vibe
  description from the accumulated community reactions.
- `GET /search-global` and `POST /search-global/select` let a user pull any
  YouTube Music track into the archive on demand.
- `GET /me`, `GET /my-tracks`, `GET /tracks/{id}`, `GET /comments/{track_id}`,
  and `GET /vouches` back the frontend UI.

Auth is handled with Supabase JWTs, verified against the project's JWKS
endpoint (with a manual cache-bust on key rotation) or the legacy HS256 shared
secret depending on the token's `alg`.

## Layout

- `main.py` FastAPI app, auth middleware, routes
- `discovery.py` YouTube Music niche crawler
- `metadata.py` Spotify enrichment (spotipy)
- `agent.py` Azure OpenAI vibe-description generator
- `database.py` Supabase data layer
- `function_app.py` Azure Functions v2 ASGI wrapper
- `api/routes/` route modules (health check, etc.)
- `supabase/migrations/` schema migrations, applied in numeric order

## Requirements

- Python 3.11
- Supabase project with the migrations in `supabase/migrations/` applied
- Spotify API app (client id and secret)
- Azure OpenAI deployment for GPT-4o
- Azure Functions Core Tools v4 if you want to run the Functions host locally

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.template .env       # fill in the values listed below
```

Required environment variables:

- `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_JWT_SECRET`
- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`
- `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT_NAME`
- `ANIMO_ALLOWED_ORIGINS` (comma-separated list of CORS origins; defaults to
  common Vite dev ports)

## Running

Local FastAPI (recommended for iteration):

```bash
uvicorn main:app --reload
```

API at `http://localhost:8000`, interactive docs at `http://localhost:8000/docs`.

Azure Functions host:

```bash
func start
```

API at `http://localhost:7071`.

## Endpoints

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| GET | `/` | no | Root ping |
| GET | `/health` | no | Health check |
| POST | `/open-pack` | yes | Run niche discovery, save tracks, return the pack |
| GET | `/me` | yes | Current user profile and survey snapshot |
| GET | `/my-tracks` | yes | Every track the user has discovered |
| GET | `/tracks/{track_id}` | no | Single track by id |
| GET | `/search-global?q=...` | no | Top-5 YouTube Music search |
| POST | `/search-global/select` | optional | Import a searched track into the archive |
| GET | `/comments/{track_id}` | no | Comments on a track, newest first |
| POST | `/comment` | yes | Post a comment (regenerates the vibe every 5 comments) |
| GET | `/vouches` | yes | Track ids the current user has vouched for |
| POST | `/vouch/{track_id}` | yes | Idempotent vouch, one per user per track |
