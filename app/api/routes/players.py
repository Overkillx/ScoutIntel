from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from typing import Optional
from app.db.session import SessionLocal
from app.db.models import Player, PlayerStats

router = APIRouter(prefix="/api/v1/players", tags=["players"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/")
def get_players(
    position: Optional[str] = None,
    max_age: Optional[int] = None,
    max_value: Optional[float] = None,
    foot: Optional[str] = None,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(Player)

    if position:
        query = query.filter(Player.primary_position == position.upper())
    if max_age:
        query = query.filter(Player.age <= max_age)
    if max_value:
        query = query.filter(Player.value_eur <= max_value)
    if foot:
        query = query.filter(Player.preferred_foot.ilike(foot))

    players = query.limit(limit).all()

    return [
        {
            "player_id": p.player_id,
            "name": p.short_name,
            "age": p.age,
            "position": p.primary_position,
            "club": p.club_name,
            "league": p.league_name,
            "value_eur": p.value_eur,
            "foot": p.preferred_foot,
        }
        for p in players
    ]


@router.get("/{player_id}")
def get_player(player_id: int, db: Session = Depends(get_db)):
    player = db.query(Player).filter(Player.player_id == player_id).first()
    if not player:
        return {"error": "Player not found"}

    stats = db.query(PlayerStats).filter(PlayerStats.player_id == player_id).first()

    return {
        "player_id": player.player_id,
        "name": player.short_name,
        "long_name": player.long_name,
        "age": player.age,
        "position": player.primary_position,
        "all_positions": player.all_positions,
        "club": player.club_name,
        "league": player.league_name,
        "value_eur": player.value_eur,
        "wage_eur": player.wage_eur,
        "foot": player.preferred_foot,
        "stats": {
            "overall": stats.overall if stats else None,
            "potential": stats.potential if stats else None,
            "pace": stats.pace if stats else None,
            "shooting": stats.shooting if stats else None,
            "passing": stats.passing if stats else None,
            "dribbling": stats.dribbling if stats else None,
            "defending": stats.defending if stats else None,
            "physic": stats.physic if stats else None,
        } if stats else None,
    }
