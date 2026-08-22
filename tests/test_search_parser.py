"""NL -> structured query. No database: the parser is a pure function over
a string, which is most of the point of splitting it out from retrieval.
"""
import pytest

from app.schemas.search import Foot, Position, PositionGroup, SearchQuery, Trait
from app.search.parser import (
    InvalidQueryError,
    UnparseableQueryError,
    parse_query,
)


def parsed(text: str) -> dict:
    """Only the fields the parser actually set, so a case documents its
    own extraction instead of restating every default."""
    return parse_query(text).model_dump(exclude_defaults=True, mode="json")


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "players similar to Rodri, under 23",
            {"similar_to": "Rodri", "max_age": 23},
        ),
        (
            "fast left-footed wingers under 21",
            {
                "positions": ["LW", "RW"],
                "max_age": 21,
                "preferred_foot": "Left",
                "traits": ["acceleration"],
            },
        ),
        (
            "creative midfielders like Pedri worth up to 40m",
            {
                "similar_to": "Pedri",
                "position_group": "midfielders",
                "max_value_eur": 40_000_000.0,
                "traits": ["vision"],
            },
        ),
        (
            "top 5 strong centre backs older than 28",
            {"positions": ["CB"], "min_age": 28, "traits": ["strength"], "k": 5},
        ),
        (
            "aerial centre backs with high stamina, u21",
            {"positions": ["CB"], "max_age": 21, "traits": ["jumping", "stamina"]},
        ),
        (
            "a replacement for V. van Dijk",
            {"similar_to": "V. van Dijk"},
        ),
        (
            "similar to player 231866",
            {"similar_to": 231866},
        ),
        (
            "find me 3 clinical strikers",
            {"positions": ["ST"], "traits": ["finishing"], "k": 3},
        ),
        (
            "right-footed goalkeepers under 25",
            {"position_group": "goalkeepers", "max_age": 25, "preferred_foot": "Right"},
        ),
    ],
)
def test_parses_natural_language_into_the_expected_structured_query(text, expected):
    assert parsed(text) == expected


def test_a_fee_is_not_read_as_an_age():
    """"under 20m" and "under 20" differ only by a unit. The value rule
    runs first and blanks its span so the age rule can't also claim it --
    guessing between the two from magnitude would be a silent misreading.
    """
    assert parsed("midfielders under 20m") == {
        "position_group": "midfielders",
        "max_value_eur": 20_000_000.0,
    }
    assert parsed("midfielders under 20") == {"position_group": "midfielders", "max_age": 20}


def test_a_name_stops_at_a_clause_boundary():
    """Without this, the anchor swallows the rest of the sentence and the
    retrieval layer goes looking for a player called "Rodri but younger"."""
    assert parsed("similar to Kevin De Bruyne but younger than 25") == {
        "similar_to": "Kevin De Bruyne",
        "max_age": 25,
    }


def test_a_name_stops_at_a_vocabulary_word():
    """"like" is also an ordinary English word. "players like fast wingers"
    is a description, not a player named "fast wingers"."""
    assert parsed("players like fast wingers") == {
        "positions": ["LW", "RW"],
        "traits": ["acceleration"],
    }


def test_a_surname_that_only_appears_inside_a_multi_word_trait_is_still_a_name():
    """Shane Long vs the "long passing" trait: excluding every token of
    every multi-word phrase would eat real surnames."""
    assert parsed("similar to Shane Long") == {"similar_to": "Shane Long"}


def test_two_position_groups_become_the_union_of_their_positions():
    """No single group covers "wingers and midfielders", and picking one
    would silently drop half the request."""
    result = parse_query("wingers and midfielders")
    assert result.position_group is None
    assert set(result.positions) == {
        Position.LW,
        Position.RW,
        Position.CM,
        Position.CDM,
        Position.CAM,
        Position.LM,
        Position.RM,
    }


def test_a_redundant_group_alongside_a_position_is_dropped_not_expanded():
    """"wingers, attackers" narrows to wingers -- they're already inside
    the group, so expanding would widen a query the user narrowed."""
    result = parse_query("wingers, attackers under 24")
    assert result.positions == [Position.LW, Position.RW]
    assert result.position_group is None


@pytest.mark.parametrize("text", ["", "   ", "hello world", "find me something good"])
def test_unrecognised_input_raises_a_clear_error_rather_than_matching_everything(text):
    with pytest.raises(UnparseableQueryError):
        parse_query(text)


def test_the_error_for_unrecognised_input_lists_what_is_understood():
    with pytest.raises(UnparseableQueryError) as exc_info:
        parse_query("hello world")

    message = str(exc_info.value)
    assert "hello world" in message
    assert "similar to" in message
    assert "wingers" in message


def test_a_number_alone_is_not_a_query():
    """"top 5" sets k but expresses no intent; treating it as a query would
    return an arbitrary five players."""
    with pytest.raises(UnparseableQueryError):
        parse_query("top 5")


def test_contradictory_constraints_are_rejected_by_the_schema():
    with pytest.raises(InvalidQueryError) as exc_info:
        parse_query("wingers under 30 and over 35")

    assert "min_age" in str(exc_info.value)


def test_an_out_of_range_value_is_rejected_with_the_schemas_message():
    with pytest.raises(InvalidQueryError) as exc_info:
        parse_query("top 500 wingers")

    assert "k" in str(exc_info.value)


def test_the_parser_only_ever_emits_a_search_query():
    """The security boundary in one assertion: whatever the input, the
    parser's output is a validated SearchQuery -- never a string, never a
    fragment of SQL. Fields are bounded numbers, closed enums, and one
    free-text name the retrieval layer binds as a parameter.
    """
    result = parse_query("'; DROP TABLE players; -- fast wingers under 21")

    assert isinstance(result, SearchQuery)
    assert result.positions == [Position.LW, Position.RW]
    assert result.max_age == 21
    assert result.traits == [Trait.ACCELERATION]
    # The injection text is not carried anywhere: it isn't a name, because
    # a name is only ever taken from an anchor phrase.
    assert result.similar_to is None


def test_a_search_query_rejects_fields_it_does_not_define():
    """extra="forbid" is the backstop: a parser bug that invents a field
    fails validation instead of attaching an unvalidated value."""
    with pytest.raises(ValueError):
        SearchQuery(order_by="value_eur; DROP TABLE players")


def test_positions_must_belong_to_the_position_group_they_are_paired_with():
    with pytest.raises(ValueError):
        SearchQuery(position_group=PositionGroup.DEFENDERS, positions=[Position.ST])


def test_foot_is_a_closed_enum():
    with pytest.raises(ValueError):
        SearchQuery(preferred_foot="Either")

    assert SearchQuery(preferred_foot="Left").preferred_foot is Foot.LEFT
