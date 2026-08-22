"""Structured query schema for natural-language player search.

This module defines the ONLY shape a natural-language query is allowed to
turn into. The parser (app/search/parser.py) emits a `SearchQuery` and
nothing else: no SQL string, no fragment of one, no free-form filter
expression. Everything that reaches the database from a user's sentence is
therefore either a bounded number, a member of a closed enum, or -- in the
single free-text case, a player name -- a value bound as a parameter by
SQLAlchemy whose only effect is to select an integer player_id.

`extra="forbid"` matters here rather than being tidiness: it means a
parser bug that invents a field fails validation instead of quietly
attaching an unvalidated value to the query object.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.attributes import TRAIT_ATTRIBUTES


class PositionGroup(str, Enum):
    """Coarse position filter. Values match app.services.similarity.POSITION_GROUPS."""

    GOALKEEPERS = "goalkeepers"
    DEFENDERS = "defenders"
    MIDFIELDERS = "midfielders"
    ATTACKERS = "attackers"


class Position(str, Enum):
    """A specific primary_position, for queries finer-grained than a group
    ("wingers" is LW+RW, not the whole attackers group)."""

    GK = "GK"
    CB = "CB"
    LB = "LB"
    RB = "RB"
    CDM = "CDM"
    CM = "CM"
    CAM = "CAM"
    LM = "LM"
    RM = "RM"
    LW = "LW"
    RW = "RW"
    ST = "ST"


# Which positions each group contains, mirroring POSITION_GROUPS. Kept as a
# derived mapping so the consistency check below can't disagree with the
# retrieval layer about what "midfielders" means.
POSITIONS_BY_GROUP: dict[PositionGroup, frozenset[Position]] = {
    PositionGroup.GOALKEEPERS: frozenset({Position.GK}),
    PositionGroup.DEFENDERS: frozenset({Position.CB, Position.LB, Position.RB}),
    PositionGroup.MIDFIELDERS: frozenset(
        {Position.CDM, Position.CM, Position.CAM, Position.LM, Position.RM}
    ),
    PositionGroup.ATTACKERS: frozenset({Position.LW, Position.RW, Position.ST}),
}


class Foot(str, Enum):
    LEFT = "Left"
    RIGHT = "Right"


# One enum member per outfield embedding dimension, named from
# app.core.attributes so the trait vocabulary can't drift from the vectors
# it filters on.
Trait = Enum("Trait", {name.upper(): name for name in TRAIT_ATTRIBUTES}, type=str)
Trait.__doc__ = "An outfield embedding dimension, addressable by name in a query."


class ModelVersion(str, Enum):
    """Ranking model, same labels the evaluation harness registers."""

    V1_VECTOR = "v1_vector"
    V2_TACTICAL = "v2_tactical"


class SearchQuery(BaseModel):
    """A validated, structured scouting query.

    Every field is optional because natural-language queries are partial by
    nature ("fast wingers under 21" has no anchor player; "similar to
    Rodri" has no filters). A query with no constraint at all is rejected
    by the parser, not here -- this schema's job is to say what a query may
    contain, not to guess intent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Anchor player for similarity retrieval: an int player_id, or a name
    # to resolve against the players table at execution time. The name is
    # the only free text in this schema; it is passed to SQLAlchemy as a
    # bound parameter and its sole effect is to select a player_id.
    similar_to: int | str | None = None

    position_group: PositionGroup | None = None
    positions: list[Position] = Field(default_factory=list)

    min_age: int | None = Field(default=None, ge=14, le=50)
    max_age: int | None = Field(default=None, ge=14, le=50)
    max_value_eur: float | None = Field(default=None, gt=0)
    preferred_foot: Foot | None = None

    # Traits a candidate must be ABOVE the population average on. The
    # embeddings are z-scored across the outfield population, so "above
    # average on this dimension" is just "component > 0" -- no extra data
    # and no threshold to invent. Multiple traits are conjunctive.
    traits: list[Trait] = Field(default_factory=list)

    k: int = Field(default=10, ge=1, le=50)
    model_version: ModelVersion = ModelVersion.V2_TACTICAL

    @model_validator(mode="after")
    def _check_coherent(self) -> "SearchQuery":
        if self.min_age is not None and self.max_age is not None and self.min_age > self.max_age:
            raise ValueError(f"min_age {self.min_age} is greater than max_age {self.max_age}")

        if self.position_group is not None and self.positions:
            allowed = POSITIONS_BY_GROUP[self.position_group]
            outside = [p.value for p in self.positions if p not in allowed]
            if outside:
                raise ValueError(
                    f"positions {outside} are not in position_group '{self.position_group.value}'"
                )

        if len(set(self.positions)) != len(self.positions):
            raise ValueError("positions contains duplicates")
        if len(set(self.traits)) != len(self.traits):
            raise ValueError("traits contains duplicates")

        return self

    @property
    def is_empty(self) -> bool:
        """True when nothing but defaults was extracted -- i.e. the parser
        recognised no intent at all. `k` and `model_version` don't count:
        "top 5" on its own is not a query.
        """
        return not any(
            (
                self.similar_to is not None,
                self.position_group is not None,
                self.positions,
                self.min_age is not None,
                self.max_age is not None,
                self.max_value_eur is not None,
                self.preferred_foot is not None,
                self.traits,
            )
        )


class SearchRequest(BaseModel):
    """The natural-language input. Length-bounded so an unbounded string
    can't be handed to the parser's regex pass."""

    query: str = Field(min_length=1, max_length=500)


class SearchResultOut(BaseModel):
    """One ranked player. `rank` rather than a distance: v1 and v2 don't
    expose comparable distances (v2's is a weighted cosine over a
    per-player weight vector, so its scale differs query to query), and a
    number that looks comparable across models but isn't would be worse
    than no number.
    """

    rank: int
    player_id: int
    name: str | None
    position: str | None
    club: str | None
    age: int | None
    value_eur: float | None
    preferred_foot: str | None


class SearchResponse(BaseModel):
    """The parsed query is returned alongside the results on purpose: the
    interpretation is the part worth checking, and an answer with no
    visible interpretation can't be audited by whoever asked.
    """

    query: SearchQuery
    # What `query.similar_to` actually resolved to, so "like kdb" can be
    # seen to have become one specific player rather than assumed to have.
    resolved_player_id: int | None = None
    resolved_player_name: str | None = None
    results: list[SearchResultOut]
