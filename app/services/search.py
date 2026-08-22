"""Execute a structured SearchQuery against the existing retrieval stack.

The split matters: `app/search/parser.py` turns a sentence into a
`SearchQuery` and touches nothing else; this module takes an
already-validated `SearchQuery` and is the only place a query reaches the
database. Nothing here interpolates user text into SQL -- the one free-text
value a query can carry (an anchor player's name) is passed to SQLAlchemy
as a bound parameter, with LIKE wildcards escaped, and its only effect is
to select an integer player_id.

Ranking itself is unchanged: `rank_similar` / `rank_similar_v2` are called
exactly as the evaluation harness calls them, so a search returns the same
ordering the harness scores. Structured filters are applied AFTER ranking,
over an overfetched candidate list, rather than being pushed into the
ranking functions -- see the note on `_CANDIDATE_MULTIPLIER`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.attributes import TRAIT_DIMENSIONS
from app.db.models import Player, PlayerStats, PlayerVector
from app.schemas.search import POSITIONS_BY_GROUP, SearchQuery
from app.services.similarity import MODEL_REGISTRY

# How many candidates to rank before filtering. Filters run after ranking
# so that rank_similar/rank_similar_v2 stay byte-for-byte the functions the
# evaluation harness scores -- pushing a position or age filter into them
# would mean the thing being measured and the thing being served are no
# longer the same function. The cost is real and not hidden: a strict
# filter can leave fewer than k results, and no amount of overfetching
# fixes that in general. The multiplier is a pragmatic bound, and
# _MAX_CANDIDATES keeps a k=50 query from ranking the entire population.
_CANDIDATE_MULTIPLIER = 10
_MAX_CANDIDATES = 300

# Candidates reported back when a name matches more than one player. The
# full count is reported alongside, so "did you mean" stays useful without
# the error body growing with the match set.
_MAX_AMBIGUOUS_CANDIDATES = 5


class SearchExecutionError(Exception):
    """Base for failures that belong to the caller's query, not the code."""


class PlayerNameNotFoundError(SearchExecutionError):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"No player matching {name!r}.")


class AmbiguousPlayerNameError(SearchExecutionError):
    """A name matched several players. Resolved by asking, not by guessing:
    silently taking the first match would attribute a scouting result to a
    player the caller never named.
    """

    def __init__(self, name: str, candidates: list[Player], total: int):
        self.name = name
        self.candidates = candidates
        self.total = total
        described = ", ".join(
            f"{player.short_name} ({player.primary_position}, {player.club_name}) "
            f"= player_id {player.player_id}"
            for player in candidates
        )
        suffix = f" and {total - len(candidates)} more" if total > len(candidates) else ""
        super().__init__(
            f"{name!r} matches {total} players: {described}{suffix}. "
            f"Re-run with a player_id (e.g. \"similar to player {candidates[0].player_id}\")."
        )


@dataclass
class SearchOutcome:
    """What the search did, not just what it returned. `resolved_*` echo the
    anchor the query actually ran against, so a caller can see that "kdb"
    became a specific player_id rather than trusting that it did.
    """

    query: SearchQuery
    players: list[Player] = field(default_factory=list)
    resolved_player_id: int | None = None
    resolved_player_name: str | None = None


def _escape_like(value: str) -> str:
    r"""Escape LIKE wildcards in user text. Without this, a name containing
    % or _ silently becomes a pattern -- "_" alone would match every
    single-character name. Not an injection (the value is still bound), but
    the same class of mistake: user text acquiring meaning it didn't have.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resolve_player(db: Session, ref: int | str) -> Player:
    """Resolve an anchor reference -- a player_id or a name -- to a Player.

    Names are matched in order of decreasing confidence: exact
    (case-insensitive) short_name, then exact long_name, then a substring
    match on either. The first tier that matches anything wins, so an exact
    "Rodri" isn't drowned out by every long_name containing "rodri".
    """
    if isinstance(ref, int):
        player = db.query(Player).filter(Player.player_id == ref).first()
        if player is None:
            raise PlayerNameNotFoundError(str(ref))
        return player

    name = ref.strip()
    escaped = _escape_like(name)
    tiers = [
        Player.short_name.ilike(escaped, escape="\\"),
        Player.long_name.ilike(escaped, escape="\\"),
        Player.short_name.ilike(f"%{escaped}%", escape="\\")
        | Player.long_name.ilike(f"%{escaped}%", escape="\\"),
    ]

    for tier in tiers:
        matches = db.query(Player).filter(tier).order_by(Player.player_id).all()
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise AmbiguousPlayerNameError(
                name, matches[:_MAX_AMBIGUOUS_CANDIDATES], len(matches)
            )

    raise PlayerNameNotFoundError(name)


def _allowed_positions(query: SearchQuery) -> set[str] | None:
    """The position filter as plain primary_position strings, or None for
    "no position constraint"."""
    if query.positions:
        return {position.value for position in query.positions}
    if query.position_group is not None:
        return {position.value for position in POSITIONS_BY_GROUP[query.position_group]}
    return None


def _matches_attribute_filters(player: Player, query: SearchQuery, positions: set[str] | None) -> bool:
    if positions is not None and player.primary_position not in positions:
        return False
    if query.max_age is not None and (player.age is None or player.age > query.max_age):
        return False
    if query.min_age is not None and (player.age is None or player.age < query.min_age):
        return False
    if query.max_value_eur is not None and (
        player.value_eur is None or player.value_eur > query.max_value_eur
    ):
        return False
    if query.preferred_foot is not None and player.preferred_foot != query.preferred_foot.value:
        return False
    return True


def _trait_filtered_ids(db: Session, query: SearchQuery, player_ids: list[int]) -> set[int]:
    """Player ids whose embedding is above the population average on every
    requested trait.

    The embeddings are z-scored across the outfield population, so "above
    average on this dimension" is exactly "component > 0" -- no threshold
    invented, no extra data. Goalkeepers have no outfield vector and so
    can't satisfy a trait filter at all; they drop out here rather than
    being special-cased, which is the honest answer (the 6-dim GK space has
    no "dribbling" dimension to be above average on).

    One query for every candidate's vector, not one per candidate.
    """
    if not query.traits or not player_ids:
        return set(player_ids)

    dimensions = [TRAIT_DIMENSIONS[trait.value] for trait in query.traits]
    rows = (
        db.query(PlayerVector.player_id, PlayerVector.embedding)
        .filter(PlayerVector.player_id.in_(player_ids))
        .all()
    )
    return {
        player_id
        for player_id, embedding in rows
        if all(embedding[dimension] > 0 for dimension in dimensions)
    }


def _similarity_search(db: Session, query: SearchQuery) -> SearchOutcome:
    anchor = resolve_player(db, query.similar_to)

    rank_fn = MODEL_REGISTRY[query.model_version.value]
    limit = min(query.k * _CANDIDATE_MULTIPLIER, _MAX_CANDIDATES)
    ranked_ids = rank_fn(db, anchor.player_id, limit=limit)

    players_by_id = {
        player.player_id: player
        for player in db.query(Player).filter(Player.player_id.in_(ranked_ids)).all()
    }
    positions = _allowed_positions(query)
    candidates = [
        players_by_id[player_id]
        for player_id in ranked_ids
        if _matches_attribute_filters(players_by_id[player_id], query, positions)
    ]

    keep = _trait_filtered_ids(db, query, [player.player_id for player in candidates])
    # Ranking order is preserved throughout: filtering only removes.
    results = [player for player in candidates if player.player_id in keep][: query.k]

    return SearchOutcome(
        query=query,
        players=results,
        resolved_player_id=anchor.player_id,
        resolved_player_name=anchor.short_name,
    )


def _filter_search(db: Session, query: SearchQuery) -> SearchOutcome:
    """No anchor player, so there is no similarity signal to rank by
    ("fast wingers under 21"). Ordered by overall rating -- the one
    ordering that doesn't smuggle in a judgment: value_eur correlates with
    overall at r=0.55 and would make the ranking partly a restatement of
    the market's opinion (see DECISIONS.md, Day 4).
    """
    statement = db.query(Player).outerjoin(PlayerStats, PlayerStats.player_id == Player.player_id)

    positions = _allowed_positions(query)
    if positions is not None:
        statement = statement.filter(Player.primary_position.in_(sorted(positions)))
    if query.max_age is not None:
        statement = statement.filter(Player.age <= query.max_age)
    if query.min_age is not None:
        statement = statement.filter(Player.age >= query.min_age)
    if query.max_value_eur is not None:
        statement = statement.filter(Player.value_eur <= query.max_value_eur)
    if query.preferred_foot is not None:
        statement = statement.filter(Player.preferred_foot == query.preferred_foot.value)

    candidates = (
        statement.order_by(PlayerStats.overall.desc().nullslast(), Player.player_id)
        .limit(min(query.k * _CANDIDATE_MULTIPLIER, _MAX_CANDIDATES))
        .all()
    )

    keep = _trait_filtered_ids(db, query, [player.player_id for player in candidates])
    results = [player for player in candidates if player.player_id in keep][: query.k]

    return SearchOutcome(query=query, players=results)


def execute_search(db: Session, query: SearchQuery) -> SearchOutcome:
    """Run a validated SearchQuery and return the matching players in rank
    order. Two paths: with an anchor player it's similarity retrieval plus
    filters; without one it's a plain filter query.
    """
    if query.similar_to is None:
        return _filter_search(db, query)
    return _similarity_search(db, query)
