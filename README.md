# Obsessed — Trivia Platform

Multi-player trivia game with AI-generated questions. Players join a room using a 6-character code, answer timed questions across 6 categories, and compete for wedges to complete the board.

## Tech Stack

- **Backend:** FastAPI + SQLAlchemy + SQLite
- **AI:** LiteLLM proxy (set `LITELLM_BASE` and `LITELLM_API_KEY` in `.env` — BYOK) for question generation
- **Scrapers:** Reddit, Pinterest, Threads, Instagram, Wikipedia, OSM, Wikidata, OpenLibrary, GDELT, crawl4ai
- **Cache:** Entity-cache layer (SQLite, persists across profiles)
- **Container:** Docker + docker-compose on phatt-RAID (Unraid)

## Setup

```bash
# Local development
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Docker
docker-compose up -d
```

## Project Structure

```
app/
  database.py    — SQLite setup, all table models (Profile, Question, GameSession, Player, Answer, PlayerStats, EntityCache)
  models.py      — Pydantic request/response schemas
  config.py      — Settings via pydantic-settings
  main.py        — FastAPI app, static file serving
  routes/
    profiles.py  — CRUD + scrape trigger + question generation
    games.py     — Game lifecycle, websocket, scoring
    admin.py     — Ops overview, cache management endpoints
  services/
    scraper/     — All content scrapers (reddit, wikipedia, osm, etc.)
    entity_cache.py — Shared cache service
    generator.py — Manual fact fallback question generation
```

## Scraper Architecture

Each scraper checks `entity_cache` before making HTTP calls. Cache miss → scrape → write cache. No expiration, no re-scrape policy.

Supported entity types: `person`, `place`, `thing`, `event`.

## API Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET/POST | `/api/profiles` | Profile CRUD |
| POST | `/api/profiles/{id}/scrape` | Trigger scrape + cache |
| POST | `/api/profiles/{id}/generate` | Trigger question generation |
| GET/POST | `/api/games` | Game management |
| POST | `/api/games/{room_code}/join` | Join a game |
| WS | `/ws/game/{room_code}` | WebSocket for live game events |
| GET | `/api/admin/overview` | Ops stats |
| POST | `/api/admin/cache/delete/all` | Clear entity cache |
| GET | `/api/admin/cache/stats` | Cache statistics |
| POST | `/api/admin/games/{room_code}/clear` | Delete a game session |

## Running on phatt-RAID

```bash
# Build and push to Docker Hub
docker build -t therealphatt/obsessed:latest .
docker push therealphatt/obsessed:latest

# Deploy via docker-compose
docker-compose up -d
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LITELLM_API_KEY` | — | API key for LiteLLM proxy (BYOK) |
| `LITELLM_BASE` | `http://localhost:4000` | LiteLLM proxy base URL — point at any OpenAI-compatible endpoint |
| `CONTENT_MAX_CHARS` | `200000` | Max chars per scraped source |
| `DATABASE_URL` | SQLite `data/trivia.db` | Database connection |

## Categories

history · entertainment · geography · science · sports · art_literature