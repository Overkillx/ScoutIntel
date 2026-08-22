"""Executing a structured SearchQuery: anchor resolution, and the filters
applied on top of the existing ranking functions.
"""
import pytest

from app.core.attributes import TRAIT_DIMENSIONS
from app.schemas.search import ModelVersion, Position, SearchQuery, Trait
from app.services.search import (
    AmbiguousPlayerNameError,
    PlayerNameNotFoundError,
    execute_search,
    resolve_player,
)


def vec(x, y, **traits):
    """18-dim outfield vector: (x, y) fixes the cosine angle (and so the
    ranking), and any trait named as a keyword sets its own dimension --
    positive meaning "above the population average", which is what a trait
    filter tests.
    """
    embedding = [x, y] + [0.0] * 16
    for trait, value in traits.items():
        embedding[TRAIT_DIMENSIONS[trait]] = value
    return embedding


@pytest.fixture()
def anchor_and_candidates(make_player, make_vector):
    """One anchor (id 1) and four candidates at increasing distance from
    it, so the ranking is 2, 3, 4, 5 before any filter runs.
    """
    make_player(1, position="CM", short_name="Anchor", age=27)
    make_vector(1, vec(1.0, 0.0))
    make_player(2, position="CM", short_name="Nearest", age=30, preferred_foot="Right")
    make_vector(2, vec(0.99, 0.01, acceleration=1.0))
    make_player(3, position="CM", short_name="Second", age=21, preferred_foot="Left")
    make_vector(3, vec(0.9, 0.1, acceleration=-1.0))
    make_player(4, position="CM", short_name="Third", age=22, preferred_foot="Right")
    make_vector(4, vec(0.7, 0.7, acceleration=2.0))
    make_player(5, position="CM", short_name="Fourth", age=35, preferred_foot="Right")
    make_vector(5, vec(0.0, 1.0, acceleration=1.0))


def names(outcome):
    return [player.short_name for player in outcome.players]


def test_resolve_player_accepts_an_explicit_id(db_session, make_player):
    make_player(7, short_name="Someone")

    assert resolve_player(db_session, 7).player_id == 7


def test_resolve_player_matches_a_name_case_insensitively(db_session, make_player):
    make_player(7, short_name="Rodri")

    assert resolve_player(db_session, "rodri").player_id == 7


def test_resolve_player_prefers_an_exact_short_name_over_a_substring_match(db_session, make_player):
    """Otherwise an exact "Rodri" is drowned out by every long_name that
    happens to contain it."""
    make_player(7, short_name="Rodri", long_name="Rodrigo Hernandez")
    make_player(8, short_name="Rodrygo", long_name="Rodrygo Silva de Goes")

    assert resolve_player(db_session, "Rodri").player_id == 7


def test_resolve_player_raises_rather_than_guessing_between_matches(db_session, make_player):
    make_player(7, short_name="Silva", club_name="Club A", position="CM")
    make_player(8, short_name="Silva", club_name="Club B", position="ST")

    with pytest.raises(AmbiguousPlayerNameError) as exc_info:
        resolve_player(db_session, "Silva")

    message = str(exc_info.value)
    assert "matches 2 players" in message
    # The ids are in the message so the caller can disambiguate without a
    # second round trip through some other endpoint.
    assert "player_id 7" in message and "player_id 8" in message


def test_resolve_player_reports_the_total_when_there_are_more_matches_than_it_lists(
    db_session, make_player
):
    for player_id in range(20, 28):
        make_player(player_id, short_name="Santos")

    with pytest.raises(AmbiguousPlayerNameError) as exc_info:
        resolve_player(db_session, "Santos")

    assert exc_info.value.total == 8
    assert len(exc_info.value.candidates) == 5
    assert "3 more" in str(exc_info.value)


def test_resolve_player_raises_when_nothing_matches(db_session, make_player):
    make_player(7, short_name="Rodri")

    with pytest.raises(PlayerNameNotFoundError):
        resolve_player(db_session, "Nobody McNobody")


def test_resolve_player_escapes_like_wildcards_in_a_name(db_session, make_player):
    """"_" is a single-character LIKE wildcard. Unescaped, a one-character
    query would match every one-character name -- user text acquiring a
    meaning it didn't have.
    """
    make_player(7, short_name="A")
    make_player(8, short_name="B")

    with pytest.raises(PlayerNameNotFoundError):
        resolve_player(db_session, "_")


def test_similarity_search_returns_ranked_players(db_session, anchor_and_candidates):
    outcome = execute_search(
        db_session, SearchQuery(similar_to=1, k=4, model_version=ModelVersion.V1_VECTOR)
    )

    assert names(outcome) == ["Nearest", "Second", "Third", "Fourth"]
    assert outcome.resolved_player_id == 1
    assert outcome.resolved_player_name == "Anchor"


def test_filters_are_applied_after_ranking_and_preserve_its_order(
    db_session, anchor_and_candidates
):
    """Filtering only removes -- the survivors keep the ranking's order,
    they don't get re-sorted by whatever the filter looked at.
    """
    outcome = execute_search(
        db_session,
        SearchQuery(similar_to=1, max_age=25, k=10, model_version=ModelVersion.V1_VECTOR),
    )

    assert names(outcome) == ["Second", "Third"]


def test_a_strict_filter_can_return_fewer_than_k(db_session, anchor_and_candidates):
    """Documented consequence of filtering after ranking rather than inside
    it: overfetching widens the pool but can't conjure matches that aren't
    there.
    """
    outcome = execute_search(
        db_session,
        SearchQuery(similar_to=1, max_age=22, k=10, model_version=ModelVersion.V1_VECTOR),
    )

    assert len(outcome.players) == 2


def test_trait_filters_keep_only_players_above_the_population_average(
    db_session, anchor_and_candidates
):
    """Embeddings are z-scored, so "fast" is exactly "acceleration
    component > 0" -- no invented threshold."""
    outcome = execute_search(
        db_session,
        SearchQuery(
            similar_to=1,
            traits=[Trait.ACCELERATION],
            k=10,
            model_version=ModelVersion.V1_VECTOR,
        ),
    )

    assert names(outcome) == ["Nearest", "Third", "Fourth"]


def test_a_query_without_an_anchor_runs_as_a_plain_filter_search(
    db_session, make_player, make_vector, make_stats
):
    make_player(1, position="LW", short_name="Best winger", age=20)
    make_vector(1, vec(1.0, 0.0, acceleration=1.0))
    make_stats(1, overall=88)
    make_player(2, position="LW", short_name="Slower winger", age=20)
    make_vector(2, vec(1.0, 0.0, acceleration=-1.0))
    make_stats(2, overall=90)
    make_player(3, position="CB", short_name="Fast defender", age=20)
    make_vector(3, vec(1.0, 0.0, acceleration=1.0))
    make_stats(3, overall=91)

    outcome = execute_search(
        db_session,
        SearchQuery(positions=[Position.LW, Position.RW], max_age=21, traits=[Trait.ACCELERATION]),
    )

    assert names(outcome) == ["Best winger"]
    assert outcome.resolved_player_id is None


def test_a_filter_search_orders_by_overall_rating(db_session, make_player, make_stats):
    make_player(1, position="ST", short_name="Good", age=20)
    make_stats(1, overall=80)
    make_player(2, position="ST", short_name="Better", age=20)
    make_stats(2, overall=90)

    outcome = execute_search(db_session, SearchQuery(positions=[Position.ST]))

    assert names(outcome) == ["Better", "Good"]


def test_a_goalkeeper_cannot_satisfy_a_trait_filter(db_session, make_player, make_vector):
    """GKs live in a separate 6-dim space with no "dribbling" dimension to
    be above average on, so they drop out of a trait filter rather than
    being special-cased into passing it.
    """
    make_player(1, position="GK", short_name="A keeper", age=25)
    make_vector(1, [1.0] * 6, goalkeeper=True)

    unfiltered = execute_search(db_session, SearchQuery(positions=[Position.GK]))
    filtered = execute_search(
        db_session, SearchQuery(positions=[Position.GK], traits=[Trait.DRIBBLING])
    )

    assert names(unfiltered) == ["A keeper"]
    assert filtered.players == []
