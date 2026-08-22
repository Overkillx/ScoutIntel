"""Natural language -> structured query.

The parser turns a scouting sentence into a validated `SearchQuery` and
stops there. It never produces SQL, a SQL fragment, or a filter expression
of any kind: its entire output surface is one Pydantic model whose fields
are bounded numbers, closed enums, and a single free-text player name that
the retrieval layer binds as a parameter. That is the security boundary --
see DECISIONS.md. Nothing here calls out to a model or a network, and the
only dependency is the standard library's `re`.

Deliberately a keyword/regex grammar rather than anything cleverer. It is
exhaustively enumerable (every phrase it understands is in a table in this
file), deterministic, unit-testable without a database, and fails visibly
on input it doesn't recognise instead of guessing. The cost is real and
accepted: it understands the vocabulary below and nothing else.
"""
from __future__ import annotations

import re

from app.schemas.search import (
    POSITIONS_BY_GROUP,
    Foot,
    Position,
    PositionGroup,
    SearchQuery,
    Trait,
)


class SearchParseError(Exception):
    """Base for every parse failure. Carries a message meant to be shown to
    the caller -- these are user-input errors, not internal faults."""


class UnparseableQueryError(SearchParseError):
    """Nothing in the input matched any rule."""


class InvalidQueryError(SearchParseError):
    """The input parsed, but the structured query it produced failed
    validation -- contradictory constraints ("under 30 and over 35") or a
    value outside the schema's bounds ("top 500")."""


# --- vocabulary ------------------------------------------------------------
# Every phrase the parser understands lives in one of these three tables.
# Matching is longest-phrase-first throughout, so "defensive midfielder"
# never gets read as "midfielder" and "left back" never as "back".

_GROUP_TERMS: dict[str, PositionGroup] = {
    "goalkeepers": PositionGroup.GOALKEEPERS,
    "goalkeeper": PositionGroup.GOALKEEPERS,
    "keepers": PositionGroup.GOALKEEPERS,
    "keeper": PositionGroup.GOALKEEPERS,
    "defenders": PositionGroup.DEFENDERS,
    "defender": PositionGroup.DEFENDERS,
    "midfielders": PositionGroup.MIDFIELDERS,
    "midfielder": PositionGroup.MIDFIELDERS,
    "midfield": PositionGroup.MIDFIELDERS,
    "attackers": PositionGroup.ATTACKERS,
    "attacker": PositionGroup.ATTACKERS,
    "forwards": PositionGroup.ATTACKERS,
}

_POSITION_TERMS: dict[str, tuple[Position, ...]] = {
    "centre backs": (Position.CB,),
    "centre back": (Position.CB,),
    "center backs": (Position.CB,),
    "center back": (Position.CB,),
    "centre halves": (Position.CB,),
    "cbs": (Position.CB,),
    "cb": (Position.CB,),
    "left backs": (Position.LB,),
    "left back": (Position.LB,),
    "right backs": (Position.RB,),
    "right back": (Position.RB,),
    "full backs": (Position.LB, Position.RB),
    "full back": (Position.LB, Position.RB),
    "fullbacks": (Position.LB, Position.RB),
    "fullback": (Position.LB, Position.RB),
    "wing backs": (Position.LB, Position.RB),
    "wing back": (Position.LB, Position.RB),
    "defensive midfielders": (Position.CDM,),
    "defensive midfielder": (Position.CDM,),
    "holding midfielders": (Position.CDM,),
    "holding midfielder": (Position.CDM,),
    "cdms": (Position.CDM,),
    "cdm": (Position.CDM,),
    "attacking midfielders": (Position.CAM,),
    "attacking midfielder": (Position.CAM,),
    "cams": (Position.CAM,),
    "cam": (Position.CAM,),
    "central midfielders": (Position.CM,),
    "central midfielder": (Position.CM,),
    "left wingers": (Position.LW,),
    "left winger": (Position.LW,),
    "right wingers": (Position.RW,),
    "right winger": (Position.RW,),
    "wingers": (Position.LW, Position.RW),
    "winger": (Position.LW, Position.RW),
    "wide forwards": (Position.LW, Position.RW),
    "strikers": (Position.ST,),
    "striker": (Position.ST,),
    "centre forwards": (Position.ST,),
    "centre forward": (Position.ST,),
    "number nines": (Position.ST,),
    "number nine": (Position.ST,),
}

# Scouting adjectives -> the embedding dimension they stand for. The exact
# trait names are accepted too (added below), so "high stamina" and
# "stamina" both work.
_TRAIT_TERMS: dict[str, Trait] = {
    "fast": Trait.ACCELERATION,
    "quick": Trait.ACCELERATION,
    "pacy": Trait.ACCELERATION,
    "pacey": Trait.ACCELERATION,
    "rapid": Trait.ACCELERATION,
    "explosive": Trait.ACCELERATION,
    "pace": Trait.ACCELERATION,
    "agile": Trait.AGILITY,
    "nimble": Trait.AGILITY,
    "strong": Trait.STRENGTH,
    "physical": Trait.STRENGTH,
    "powerful": Trait.STRENGTH,
    "aerial": Trait.JUMPING,
    "good in the air": Trait.JUMPING,
    "dominant in the air": Trait.JUMPING,
    "tireless": Trait.STAMINA,
    "high stamina": Trait.STAMINA,
    "box to box": Trait.STAMINA,
    "box-to-box": Trait.STAMINA,
    "aggressive": Trait.AGGRESSION,
    "combative": Trait.AGGRESSION,
    "creative": Trait.VISION,
    "playmaking": Trait.VISION,
    "playmakers": Trait.VISION,
    "playmaker": Trait.VISION,
    "composed": Trait.COMPOSURE,
    "clinical": Trait.FINISHING,
    "prolific": Trait.FINISHING,
    "finishers": Trait.FINISHING,
    "finisher": Trait.FINISHING,
    "goalscoring": Trait.FINISHING,
    "dribblers": Trait.DRIBBLING,
    "dribbler": Trait.DRIBBLING,
    "skilful": Trait.DRIBBLING,
    "skillful": Trait.DRIBBLING,
    "technical": Trait.BALL_CONTROL,
    "press resistant": Trait.BALL_CONTROL,
    "passers": Trait.SHORT_PASSING,
    "passer": Trait.SHORT_PASSING,
    "passing": Trait.SHORT_PASSING,
    "long passing": Trait.LONG_PASSING,
    "long balls": Trait.LONG_PASSING,
    "long range passing": Trait.LONG_PASSING,
    "crossers": Trait.CROSSING,
    "crosser": Trait.CROSSING,
    "crossing": Trait.CROSSING,
    "tacklers": Trait.STANDING_TACKLE,
    "tackler": Trait.STANDING_TACKLE,
    "tackling": Trait.STANDING_TACKLE,
    "shot power": Trait.SHOT_POWER,
    "hard shot": Trait.SHOT_POWER,
    "sharp": Trait.REACTIONS,
}
# The canonical trait names themselves, spelled with spaces or underscores.
for _trait in Trait:
    _TRAIT_TERMS.setdefault(_trait.value, _trait)
    _TRAIT_TERMS.setdefault(_trait.value.replace("_", " "), _trait)


def _phrase_pattern(phrases) -> re.Pattern:
    """One alternation over `phrases`, longest first so a longer phrase
    always wins over a shorter one it contains."""
    ordered = sorted(phrases, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(p) for p in ordered) + r")\b")


_GROUP_PATTERN = _phrase_pattern(_GROUP_TERMS)
_POSITION_PATTERN = _phrase_pattern(_POSITION_TERMS)
_TRAIT_PATTERN = _phrase_pattern(_TRAIT_TERMS)

# --- numeric and anchor patterns -------------------------------------------

_MULTIPLIERS = {"k": 1_000.0, "m": 1_000_000.0, "million": 1_000_000.0, "bn": 1_000_000_000.0}

# Value must carry a unit (or a currency symbol). That's what keeps "under
# 21" an age and "under 21m" a fee -- the two phrasings are otherwise
# identical, and guessing between them from magnitude alone would be a
# silent misreading.
_VALUE_PATTERNS = [
    re.compile(
        r"\b(?:under|below|less than|cheaper than|up to|no more than|max(?:imum)?(?:\s+value)?"
        r"(?:\s+of)?|worth up to|valued? (?:under|below))\s*[€$£]?\s*"
        r"(\d+(?:\.\d+)?)\s*(k|m|million|bn)\b"
    ),
    re.compile(r"[€$£]\s*(\d+(?:\.\d+)?)\s*(k|m|million|bn)?\b"),
]

_MAX_AGE_PATTERNS = [
    re.compile(r"\bunder\s+(\d{1,2})\b"),
    re.compile(r"\byounger than\s+(\d{1,2})\b"),
    re.compile(r"\bu-?(\d{2})\b"),
    re.compile(r"\baged?\s+(\d{1,2})\s+(?:or\s+)?(?:younger|under|below)\b"),
    re.compile(r"\b(\d{1,2})\s+(?:or\s+)?younger\b"),
    re.compile(r"\bat most\s+(\d{1,2})\s+years?\b"),
]

_MIN_AGE_PATTERNS = [
    re.compile(r"\bover\s+(\d{1,2})\b"),
    re.compile(r"\bolder than\s+(\d{1,2})\b"),
    re.compile(r"\baged?\s+(\d{1,2})\s+(?:or\s+)?(?:older|above|over)\b"),
    re.compile(r"\b(\d{1,2})\s+(?:or\s+)?older\b"),
]

_FOOT_PATTERN = re.compile(r"\b(left|right)[- ]?footed\b")

_K_PATTERNS = [
    re.compile(r"\btop\s+(\d{1,3})\b"),
    re.compile(r"\b(?:best|first)\s+(\d{1,3})\b"),
    re.compile(r"\b(\d{1,3})\s+(?:players?|options?|alternatives?|names?|suggestions?|similar)\b"),
    re.compile(r"\b(?:give me|show me|find me)\s+(\d{1,3})\b"),
]

_ANCHOR_KEYWORD = (
    r"(?:similar to|similar players? to|players? like|comparable to|"
    r"in the (?:mould|mold|style) of|alternatives? to|replacements? for|"
    r"successors? to|like)"
)
# An id may be given directly: "similar to player 231866" / "similar to #231866".
_ANCHOR_ID_PATTERN = re.compile(_ANCHOR_KEYWORD + r"\s+(?:player\s*)?#?(\d{4,7})\b")
# Otherwise take the following words while they still look like a name.
# Letters, accents, apostrophes, hyphens and initials only -- so a digit or
# a clause boundary ends the name, and at most four tokens are taken.
_NAME_TOKEN = r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’.\-]*"
_ANCHOR_NAME_PATTERN = re.compile(
    _ANCHOR_KEYWORD + r"\s+((?:" + _NAME_TOKEN + r")(?:\s+" + _NAME_TOKEN + r"){0,3})",
    re.IGNORECASE,
)

# Words that end a name rather than belonging to it. "similar to Rodri but
# younger" must not resolve a player called "Rodri but younger".
_NAME_STOP_WORDS = frozenset(
    {
        "a", "an", "the", "but", "who", "whos", "with", "without", "and", "or", "that",
        "under", "over", "below", "above", "aged", "younger", "older", "cheaper", "costing",
        "valued", "worth", "in", "from", "for", "at", "on", "of", "playing", "plays", "play",
        "only", "except", "please", "max", "maximum", "less", "more", "than", "top", "best",
        "years", "year", "old", "still", "prefer", "preferably", "ideally", "someone", "somebody",
    }
)

# A name token can't also be a word the parser already knows: "players like
# fast wingers" is a description, not a player called "fast wingers". Only
# single-word vocabulary entries are excluded -- taking every token of every
# multi-word phrase would block real surnames (Shane *Long*, from "long
# passing"), and the phrases themselves are matched later anyway.
_VOCABULARY_WORDS = frozenset(
    term
    for table in (_GROUP_TERMS, _POSITION_TERMS, _TRAIT_TERMS)
    for term in table
    if " " not in term
)


def _consume(text: str, pattern: re.Pattern) -> tuple[str, list[re.Match]]:
    """Return `text` with every match of `pattern` blanked out, plus the
    matches. Blanking rather than deleting keeps offsets stable, and stops
    a span from being read twice by two different rules -- "under 20m" is a
    fee, and must not also register as "under 20" years old.
    """
    matches = list(pattern.finditer(text))
    for match in reversed(matches):
        text = text[: match.start()] + " " * (match.end() - match.start()) + text[match.end() :]
    return text, matches


def _first_int(text: str, patterns: list[re.Pattern]) -> tuple[str, int | None]:
    for pattern in patterns:
        text, matches = _consume(text, pattern)
        if matches:
            return text, int(matches[0].group(1))
    return text, None


def _parse_anchor(text: str) -> tuple[str, int | str | None]:
    """Extract the anchor player as an id or a name, and blank its span."""
    text, id_matches = _consume(text, _ANCHOR_ID_PATTERN)
    if id_matches:
        return text, int(id_matches[0].group(1))

    for match in _ANCHOR_NAME_PATTERN.finditer(text):
        tokens = match.group(1).split()
        name_tokens = []
        for token in tokens:
            word = token.strip(".'’-").lower()
            if word in _NAME_STOP_WORDS or word in _VOCABULARY_WORDS:
                break
            name_tokens.append(token)
        if not name_tokens:
            continue
        name = " ".join(name_tokens)
        # Blank the keyword and the name, leaving any trailing constraint
        # words ("... but under 21") in place for the other rules.
        end = match.start(1) + len(name)
        return text[: match.start()] + " " * (end - match.start()) + text[end:], name

    return text, None


def _parse_positions(text: str) -> tuple[str, PositionGroup | None, list[Position]]:
    text, position_matches = _consume(text, _POSITION_PATTERN)
    text, group_matches = _consume(text, _GROUP_PATTERN)

    positions: list[Position] = []
    for match in position_matches:
        for position in _POSITION_TERMS[match.group(1)]:
            if position not in positions:
                positions.append(position)

    groups = []
    for match in group_matches:
        group = _GROUP_TERMS[match.group(1)]
        if group not in groups:
            groups.append(group)

    if not positions:
        if len(groups) == 1:
            return text, groups[0], []
        if len(groups) > 1:
            # "midfielders or attackers": no single group covers it, so it
            # becomes the union of their positions instead of one of them
            # being silently dropped.
            for group in groups:
                for position in sorted(POSITIONS_BY_GROUP[group], key=lambda p: p.value):
                    if position not in positions:
                        positions.append(position)
        return text, None, positions

    if groups:
        # Both a specific position and a group were named. If the positions
        # sit inside the group ("wingers, attackers"), the group is
        # redundant; if they don't ("wingers and midfielders"), taking
        # either alone would drop half the request, so expand to the union.
        covered = set().union(*(POSITIONS_BY_GROUP[g] for g in groups))
        if not set(positions).issubset(covered):
            for group in groups:
                for position in sorted(POSITIONS_BY_GROUP[group], key=lambda p: p.value):
                    if position not in positions:
                        positions.append(position)

    return text, None, positions


def _parse_traits(text: str) -> tuple[str, list[Trait]]:
    text, matches = _consume(text, _TRAIT_PATTERN)
    traits: list[Trait] = []
    for match in matches:
        trait = _TRAIT_TERMS[match.group(1)]
        if trait not in traits:
            traits.append(trait)
    return text, traits


def parse_query(text: str) -> SearchQuery:
    """Parse a natural-language scouting query into a `SearchQuery`.

    Raises `UnparseableQueryError` when no rule matched (an empty query is
    not silently turned into "everything"), and `AmbiguousQueryError` when
    two rules matched contradictory things. Both are user-input errors and
    carry a message intended to be shown.

    Rules run in a fixed order and blank out the text they consume, so a
    span is read by exactly one rule: fees are taken before ages (both are
    "under N"), and the anchor player's name is taken before positions and
    traits so a name can't be mined for keywords.
    """
    if not text or not text.strip():
        raise UnparseableQueryError("Empty query.")

    working = re.sub(r"\s+", " ", text.strip())
    working, similar_to = _parse_anchor(working)

    lowered = working.lower()
    lowered, value_matches = _consume(lowered, _VALUE_PATTERNS[0])
    if not value_matches:
        lowered, value_matches = _consume(lowered, _VALUE_PATTERNS[1])
    max_value_eur = None
    if value_matches:
        amount = float(value_matches[0].group(1))
        unit = value_matches[0].group(2)
        max_value_eur = amount * _MULTIPLIERS.get(unit, 1.0)

    lowered, max_age = _first_int(lowered, _MAX_AGE_PATTERNS)
    lowered, min_age = _first_int(lowered, _MIN_AGE_PATTERNS)
    lowered, k = _first_int(lowered, _K_PATTERNS)

    lowered, foot_matches = _consume(lowered, _FOOT_PATTERN)
    preferred_foot = Foot(foot_matches[0].group(1).capitalize()) if foot_matches else None

    lowered, position_group, positions = _parse_positions(lowered)
    lowered, traits = _parse_traits(lowered)

    fields: dict = {
        "similar_to": similar_to,
        "position_group": position_group,
        "positions": positions,
        "min_age": min_age,
        "max_age": max_age,
        "max_value_eur": max_value_eur,
        "preferred_foot": preferred_foot,
        "traits": traits,
    }
    if k is not None:
        fields["k"] = k

    try:
        query = SearchQuery(**fields)
    except ValueError as exc:
        # A contradiction or out-of-range value the schema caught
        # ("under 30 and over 35", "top 500"). Surfaced as a parse error
        # carrying the schema's own message rather than as a 500.
        raise InvalidQueryError(_first_validation_message(exc)) from exc

    if query.is_empty:
        raise UnparseableQueryError(
            f"Could not interpret {text.strip()!r}. Understood terms include a player to "
            f"compare against (\"similar to Rodri\"), a position (\"wingers\", \"centre backs\"), "
            f"an age limit (\"under 23\"), a value limit (\"under 20m\"), a foot "
            f"(\"left-footed\"), and traits ({', '.join(sorted(t.value for t in Trait))})."
        )

    return query


def _first_validation_message(exc: ValueError) -> str:
    """Pydantic's ValidationError repr is multi-line and includes a docs
    URL; pull out just the message for an API error body."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc)
    first = errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = first.get("msg", "").removeprefix("Value error, ")
    return f"{location}: {message}" if location else message
