from sqlalchemy.orm import Session

from app.db.models import GoalkeeperVector, Player, PlayerVector

# Position group used to filter similarity candidates. Derived from
# primary_position rather than stored separately, so it can't drift out
# of sync with the source field.
POSITION_GROUPS = {
    "GK": "goalkeepers",
    "CB": "defenders", "LB": "defenders", "RB": "defenders",
    "CDM": "midfielders", "CM": "midfielders", "CAM": "midfielders",
    "LM": "midfielders", "RM": "midfielders",
    "LW": "attackers", "RW": "attackers", "ST": "attackers",
}


class PlayerNotFoundError(Exception):
    """No player exists with the given player_id."""


class UnrecognizedPositionError(Exception):
    """The player's primary_position has no entry in POSITION_GROUPS."""

    def __init__(self, position):
        self.position = position
        super().__init__(f"Unrecognized position '{position}', can't determine position group")


class NoVectorError(Exception):
    """The player has no row in the relevant vector table."""


def _ranked_candidates(db: Session, player_id: int, limit: int):
    """Shared core: validates the target player, resolves its position
    group and vector table, and returns (Player, distance) rows ordered by
    ascending cosine distance. Raises the plain exceptions above rather
    than HTTPException so this stays usable outside FastAPI (e.g. the
    offline evaluation runner) -- the route layer maps these to status
    codes.
    """
    player = db.query(Player).filter(Player.player_id == player_id).first()
    if not player:
        raise PlayerNotFoundError(player_id)

    group = POSITION_GROUPS.get(player.primary_position)
    if group is None:
        raise UnrecognizedPositionError(player.primary_position)

    vector_model = GoalkeeperVector if group == "goalkeepers" else PlayerVector

    target = db.query(vector_model).filter(vector_model.player_id == player_id).first()
    if not target:
        raise NoVectorError(player_id)

    position_filter = [pos for pos, g in POSITION_GROUPS.items() if g == group]
    distance = vector_model.embedding.cosine_distance(target.embedding).label("distance")

    return (
        db.query(Player, distance)
        .join(vector_model, vector_model.player_id == Player.player_id)
        .filter(Player.primary_position.in_(position_filter))
        .filter(Player.player_id != player_id)
        .order_by(distance)
        .limit(limit)
        .all()
    )


def rank_similar(db: Session, player_id: int, limit: int = 10) -> list[int]:
    """Ordered player_ids only, nearest first. This is the shape the
    offline evaluation harness consumes -- it only needs a ranking to
    score against curated relevance labels, not the display fields.
    """
    return [p.player_id for p, _ in _ranked_candidates(db, player_id, limit)]


def get_similar_players(db: Session, player_id: int, limit: int = 10):
    """(Player, distance) rows for the API response shape."""
    return _ranked_candidates(db, player_id, limit)
