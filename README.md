# Obsessed — Trivia Platform

Multi-player trivia game with AI-generated questions. Players join a room using a 6-character code, answer timed questions across 6 categories, and compete for wedges to complete the board.

## Tech Stack

- **Backend:** FastAPI + SQLAlchemy + SQLite
- **AI:** LiteLLM proxy (set `LITELLM_BASE` and `LITELLM_API_KEY` in `.env` — BYOK) for question generation
- **Scrapers:** Reddit, Pinterest, Threads, Instagram, Facebook, Steam, Wikipedia, OSM, Wikidata, OpenLibrary, GDELT, crawl4ai
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
| WS | `/ws/{room_code}/{player_id}` | WebSocket for live game events |
| GET | `/api/games/{room_code}/question` | Get current question |
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
| `STEAM_API_KEY` | — | Steam Web API key (free at [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey)). Enables full library enrichment; without it only basic profile XML is fetched. |
| `STEAM_API_KEY` | — | **Use a burner phattvip account** — not Brandon's personal Steam account. 100k calls/day quota; ~5 per profile scrape. If you want per-user review HTML (`/profiles/{sid}/recommended/`), that requires a separate account with a known-reviewer fixture — file a follow-up issue. |
| `LITELLM_BASE` | `http://localhost:4000` | LiteLLM proxy base URL — point at any OpenAI-compatible endpoint |
| `CONTENT_MAX_CHARS` | `200000` | Max chars per scraped source |
| `DATABASE_URL` | SQLite `data/trivia.db` | Database connection |
| `ADMIN_TOKEN` | _(empty)_ | If set, all `/api/admin/*` routes require `Authorization: Bearer <token>`. If empty, admin endpoints are open (single-host LAN/VPN deployments only — do not expose Obsessed to the public internet without setting this). |

## Admin Endpoints

All `/api/admin/*` routes are open by default (when `ADMIN_TOKEN` is unset). **If Obsessed is reachable from outside your LAN/VPN, set `ADMIN_TOKEN` before deploying.**

To enable token auth:
```bash
echo "ADMIN_TOKEN=your-secret-token" >> .env
```

All admin requests must then include the header:
```
Authorization: Bearer <ADMIN_TOKEN>
```

Destructive endpoints (require token when set):
- `POST /api/admin/cache/delete/all` — irreversibly wipe entity cache
- `POST /api/admin/cache/delete/by-date` — wipe cache by date range
- `POST /api/admin/profiles/{id}/rescrape` — re-run full scraper chain for a profile
- `POST /api/admin/games/{room_code}/clear` — delete a game session from DB

Read endpoints (also protected when token is set):
- `GET /api/admin/overview` — ops stats snapshot
- `GET /api/admin/profiles` — all profile records with scrape status
- `GET /api/admin/leaderboard` — player stats leaderboard
- `GET /api/admin/games/recent` — recently played games
- `GET /api/admin/cache/stats` — cache entry counts by type

## WebSocket Events

Connect to `/ws/{room_code}/{player_id}` for real-time game events. The server broadcasts:
- `player_joined` — new player entered the lobby
- `game_started` — game moved from lobby to active
- `new_question` — question text, options, timer, category badge
- `answer_result` — correct/incorrect with live player scores
- `question_advance` — scores update between questions
- `game_over` — game finished, players see final results

Client sends `{"type":"ping"}` to keep connection alive. Auto-reconnect on disconnect (3s backoff).