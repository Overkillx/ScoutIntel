import math

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


OUTFIELD_POSITIONS = [pos for pos, group in POSITION_GROUPS.items() if group != "goalkeepers"]

# Position-group baseline trait weights: round, hand-set proxy scores
# (1 = low relevance, 3 = high relevance) for how much each of the 18
# outfield dims matters for a player in that group. Order matches
# OUTFIELD_ATTRIBUTES in compute_vectors.py:
#   [crossing, finishing, short_pass, long_pass, ball_control, dribbling,
#    acceleration, agility, balance, reactions, shot_power, jumping,
#    stamina, strength, aggression, vision, composure, standing_tackle]
# These are deliberately coarse, stated proxies for "what matters for this
# position" -- not learned or fit to the relevance set.
POSITION_GROUP_TRAIT_WEIGHTS = {
    "defenders":   [1, 1, 1, 1, 1, 1, 2, 1, 1, 2, 1, 2, 2, 3, 3, 1, 2, 3],
    "midfielders": [1, 1, 3, 3, 3, 2, 1, 2, 1, 2, 1, 1, 3, 1, 1, 3, 2, 2],
    "attackers":   [2, 3, 1, 1, 2, 3, 3, 2, 1, 2, 3, 1, 1, 1, 1, 1, 2, 1],
}


def _min_max_normalize(values: list[float]) -> list[float]:
    """Scale `values` into [0, 1], preserving order (max -> 1.0, min ->
    0.0). Degenerate (all-equal) input carries no discriminating signal, so
    it maps to uniform full weight (1.0) rather than collapsing to 0 --
    a divide-by-zero guard that also avoids silently zeroing out this
    source of the blend.
    """
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _trait_weights(group: str, target_embedding: list[float], alpha: float) -> list[float]:
    """Per-dimension weight = alpha * position-baseline + (1 - alpha) *
    this-player's-own-strengths. Both sources are min-max normalized to
    [0, 1] independently BEFORE blending -- the baseline is small hand-set
    integers and the strengths are unbounded population z-scores, so
    without normalizing to a common scale first, alpha wouldn't mean
    anything (see DECISIONS.md).
    """
    baseline = _min_max_normalize(POSITION_GROUP_TRAIT_WEIGHTS[group])
    strengths = _min_max_normalize(target_embedding)
    return [alpha * b + (1 - alpha) * s for b, s in zip(baseline, strengths)]


def _weighted_cosine_distance(a: list[float], b: list[float], weights: list[float]) -> float:
    """Cosine distance with each dimension scaled by `weights` before the
    dot product -- algebraically equivalent to taking the plain cosine
    distance between a*sqrt(w) and b*sqrt(w), computed directly here to
    avoid a sqrt per dimension.
    """
    dot = sum(w * x * y for w, x, y in zip(weights, a, b))
    norm_a = math.sqrt(sum(w * x * x for w, x in zip(weights, a)))
    norm_b = math.sqrt(sum(w * y * y for w, y in zip(weights, b)))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


def rank_similar_v2(db: Session, player_id: int, limit: int = 10, alpha: float = 0.5) -> list[int]:
    """Trait-weighted similarity (v2_tactical). Same return contract as
    rank_similar(): list[int] of ranked player_ids, nearest first.

    Differs from v1 in two ways:
      - dimensions are weighted by trait relevance (see _trait_weights)
        before computing cosine distance, instead of all 18 dims counting
        equally
      - the outfield position filter is RELAXED: candidates are drawn from
        the whole outfield pool (any non-GK position), not just the query
        player's own position group -- so e.g. a winger can surface for a
        midfielder query. Intentional, per the design.

    The goalkeeper wall stays HARD, same as v1: GKs use a separate 6-dim
    vector space that isn't comparable to the 18-dim outfield space (not a
    tunable choice -- the vectors are different shapes), so a GK query is
    routed through the same GK-only, unweighted ranking rank_similar() uses.
    """
    player = db.query(Player).filter(Player.player_id == player_id).first()
    if not player:
        raise PlayerNotFoundError(player_id)

    group = POSITION_GROUPS.get(player.primary_position)
    if group is None:
        raise UnrecognizedPositionError(player.primary_position)

    if group == "goalkeepers":
        return [p.player_id for p, _ in _ranked_candidates(db, player_id, limit)]

    target = db.query(PlayerVector).filter(PlayerVector.player_id == player_id).first()
    if not target:
        raise NoVectorError(player_id)

    weights = _trait_weights(group, target.embedding, alpha)

    # One query for the whole relaxed outfield candidate pool (id +
    # embedding) -- weighting and ranking happen in Python afterward, so
    # this is the only per-candidate data fetch (no N+1).
    candidates = (
        db.query(PlayerVector.player_id, PlayerVector.embedding)
        .join(Player, Player.player_id == PlayerVector.player_id)
        .filter(Player.primary_position.in_(OUTFIELD_POSITIONS))
        .filter(Player.player_id != player_id)
        .all()
    )

    ranked = sorted(
        candidates,
        key=lambda c: _weighted_cosine_distance(target.embedding, c.embedding, weights),
    )
    return [c.player_id for c in ranked[:limit]]
