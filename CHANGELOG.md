# Changelog

All notable changes to Obsessed are documented here.

## [1.0.0] — 2026-05-02

### Added

- **Entity Cache Layer** — `EntityCache` table in SQLite; all scrapers check cache before HTTP calls. Cache miss scrapes and writes. No expiration. (`PHA-335`)
- **Search Fallback Chain** — Wikipedia REST → HTML fallback, OSM → GeoNames, travel → Wikipedia summary, GDELT/WikiNews non-fatal (`PHA-309`)
- **Rate-Limit Aware Pipelines** — `retry_with_backoff`, concurrent scraping, `RateLimiter` class (`PHA-310`)
- **Content Cap Raised** — 200K chars per scrape source (`PHA-308`)
- **People Pipeline** — Instagram, Threads, Pinterest scrapers with rate limiting (`PHA-295`)
- **README** — Project documentation (`PHA-344`)

### Architecture

- FastAPI + SQLAlchemy + SQLite (`trivia.db`)
- LiteLLM proxy at `http://10.0.0.100:4000`
- WebSocket live game events (`/ws/game/{room_code}`)
- Admin endpoints: `/api/admin/overview`, `/api/admin/cache/delete/all`, `/api/admin/cache/stats`
- Entity types: `person`, `place`, `thing`, `event`

### Deployment

- Docker image: `docker.io/phattbeatts/obsessed:latest` (GitHub Actions on every main push)
- Multi-arch: `linux/amd64`, `linux/arm64`
- Unraid: pull latest from Docker Hub → `docker-compose up -d`