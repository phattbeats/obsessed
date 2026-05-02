"""
Ops / admin dashboard API routes for Obsessed.
Provides aggregated operational view: all profiles, games, stats.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from datetime import datetime, timedelta
from app.database import SessionLocal, Profile, GameSession, Player, PlayerStats
from app.services.scraper.reddit import scrape_reddit
from app.services.scraper.instagram import scrape_instagram
from app.services.scraper.pinterest import scrape_pinterest
from app.services.scraper.threads import scrape_threads
import json

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/overview")
def ops_overview():
    """
    High-level operational snapshot of the Obsessed deployment.
    """
    db = SessionLocal()
    try:
        total_profiles = db.query(Profile).count()
        profiles = db.query(Profile).order_by(Profile.created_at.desc()).all()

        scrape_status_counts = {"pending": 0, "running": 0, "done": 0, "error": 0}
        for p in profiles:
            status = p.scrape_status or "pending"
            if status in scrape_status_counts:
                scrape_status_counts[status] += 1

        total_games = db.query(GameSession).count()
        active_games = db.query(GameSession).filter(
            GameSession.status.in_(["lobby", "active"])
        ).count()
        recent_cutoff = datetime.now() - timedelta(days=7)
        recent_games = db.query(GameSession).filter(
            GameSession.created_at >= recent_cutoff
        ).count()

        total_players = db.query(Player).count()
        total_players_stats = db.query(PlayerStats).count()

        return {
            "profiles": {
                "total": total_profiles,
                "by_status": scrape_status_counts,
            },
            "games": {
                "total": total_games,
                "active": active_games,
                "last_7_days": recent_games,
            },
            "players": {
                "total_game_players": total_players,
                "with_stats": total_players_stats,
            }
        }
    finally:
        db.close()


@router.get("/profiles")
def list_all_profiles():
    """
    List all profiles with full status info for ops review.
    """
    db = SessionLocal()
    try:
        profiles = db.query(Profile).order_by(Profile.updated_at.desc()).all()
        return [
            {
                "id": p.id,
                "name": p.name,
                "bio": p.bio or "",
                "scrape_status": p.scrape_status or "pending",
                "scrape_error": p.scrape_error or "",
                "question_count": p.question_count or 0,
                "llm_calls": p.llm_calls or 0,
                "llm_spend_cents": p.llm_spend_cents or 0,
                "consent_obtained": bool(p.consent_obtained),
                "content_quality": p.content_quality or "",
                "content_chunks": p.content_chunks or 0,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            }
            for p in profiles
        ]
    finally:
        db.close()


@router.get("/leaderboard")
def admin_leaderboard(limit: int = 50):
    """
    Org-wide leaderboard - top players by total score across all games.
    """
    db = SessionLocal()
    try:
        entries = db.query(PlayerStats).order_by(
            PlayerStats.total_score.desc()
        ).limit(limit).all()
        return [
            {
                "player_name": e.player_name,
                "games_played": e.games_played or 0,
                "games_won": e.games_won or 0,
                "total_score": e.total_score or 0,
                "total_correct": e.total_correct or 0,
                "total_asked": e.total_asked or 0,
                "win_rate": round((e.games_won or 0) / max(e.games_played or 1, 1), 2),
            }
            for e in entries
        ]
    finally:
        db.close()


@router.get("/games/recent")
def recent_games(limit: int = 20):
    """
    Most recently played games.
    """
    db = SessionLocal()
    try:
        games = db.query(GameSession).order_by(
            GameSession.created_at.desc()
        ).limit(limit).all()
        return [
            {
                "room_code": g.room_code,
                "profile_id": g.profile_id,
                "status": g.status,
                "player_count": len(g.players) if g.players else 0,
                "created_at": g.created_at,
                "current_question": g.current_question or 0,
            }
            for g in games
        ]
    finally:
        db.close()


@router.post("/profiles/{profile_id}/rescrape")
async def rescrape_profile(profile_id: int):
    """  
    Re-run scraping for a profile. Triggers the full scraper chain.
    """
    import asyncio
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == profile_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Profile not found")

        p.scrape_status = "running"
        p.scrape_error = None
        db.commit()

        try:
            # Run all scrapers concurrently
            scrape_tasks = []
            if p.reddit_handle:
                scrape_tasks.append(("reddit", scrape_reddit(p.reddit_handle)))
            if p.instagram_handle:
                scrape_tasks.append(("instagram", scrape_instagram(p.instagram_handle)))
            if p.pinterest_handle:
                scrape_tasks.append(("pinterest", scrape_pinterest(p.pinterest_handle)))
            if p.threads_handle:
                scrape_tasks.append(("threads", scrape_threads(p.threads_handle)))

            # asyncio.gather runs all concurrently
            results = await asyncio.gather(
                *[task for _, task in scrape_tasks],
                return_exceptions=True
            )

            content_parts = []
            for i, (source, _) in enumerate(scrape_tasks):
                result = results[i]
                if isinstance(result, Exception):
                    p.scrape_error = f"{source} error: {result}"
                    p.scrape_status = "error"
                    db.commit()
                    return {"ok": False, "profile_id": profile_id, "status": "error", "error": str(result)}
                content_str, _ = result
                if content_str:
                    content_parts.append(content_str)

            combined = "\n".join(content_parts) if content_parts else ""
            p.scrape_status = "done" if combined else "pending"
            p.content = combined  # actually save the scraped content
            p.content_chunks = len(content_parts)
        except Exception as e:
            p.scrape_status = "error"
            p.scrape_error = str(e)

        db.commit()
        return {"ok": True, "profile_id": profile_id, "status": p.scrape_status}
    finally:
        db.close()


@router.post("/cache/delete/all")
def cache_delete_all():
    """Delete ALL cached entity content. Irreversible."""
    from app.services.entity_cache import delete_all_cached
    count = delete_all_cached()
    return {"deleted": count, "message": f"Deleted {count} cached entries."}


@router.post("/cache/delete/by-date")
def cache_delete_by_date(from_ts: int, to_ts: int):
    """Delete cached entries scraped between from_ts and to_ts (unix timestamps)."""
    from app.services.entity_cache import delete_cached_by_date
    count = delete_cached_by_date(from_ts, to_ts)
    return {"deleted": count, "from": from_ts, "to": to_ts}


@router.get("/cache/stats")
def cache_stats():
    """Return cache statistics: total entries and per-type breakdown."""
    from app.services.entity_cache import count_cached
    return count_cached()


@router.post("/games/{room_code}/clear")
def clear_game(room_code: str):
    """
    Delete a game session and its players/answers from DB.
    """
    db = SessionLocal()
    try:
        g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
        if not g:
            raise HTTPException(status_code=404, detail="Game not found")
        db.delete(g)
        db.commit()
        return {"ok": True, "room_code": room_code}
    finally:
        db.close()