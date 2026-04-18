from fastapi import APIRouter
from app.database import SessionLocal, PlayerStats

router = APIRouter(prefix="/api/stats", tags=["stats"])

@router.get("/leaderboard")
def leaderboard(limit: int = 20):
    db = SessionLocal()
    try:
        entries = db.query(PlayerStats).order_by(PlayerStats.total_score.desc()).limit(limit).all()
        return [
            {
                "player_name": e.player_name,
                "games_played": e.games_played,
                "games_won": e.games_won,
                "total_score": e.total_score or 0,
                "win_rate": round((e.games_won or 0) / max(e.games_played or 1, 1), 2),
            }
            for e in entries
        ]
    finally:
        db.close()

@router.get("/player/{player_name}")
def player_stats(player_name: str):
    db = SessionLocal()
    try:
        e = db.query(PlayerStats).filter(PlayerStats.player_name == player_name).first()
        if not e:
            return {"player_name": player_name, "games_played": 0, "games_won": 0, "total_score": 0, "total_correct": 0, "total_asked": 0}
        return {
            "player_name": e.player_name,
            "games_played": e.games_played or 0,
            "games_won": e.games_won or 0,
            "total_score": e.total_score or 0,
            "total_correct": e.total_correct or 0,
            "total_asked": e.total_asked or 0,
            "win_rate": round((e.games_won or 0) / max(e.games_played or 1, 1), 2),
        }
    finally:
        db.close()