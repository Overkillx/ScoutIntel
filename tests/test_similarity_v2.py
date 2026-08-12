import pytest

from app.services.similarity import (
    NoVectorError,
    PlayerNotFoundError,
    UnrecognizedPositionError,
    rank_similar,
    rank_similar_v2,
)


def outfield_vector(first, rest=0.0):
    return [first] + [rest] * 17


def gk_vector(first, rest=0.0):
    return [first] + [rest] * 5


def mf_vector(dim0=0.0, dim2=0.0, dim16=0.0):
    """18-dim vector with only crossing (0), short_passing (2), and
    composure (16) set -- the three dims exercised by the reordering test
    below, chosen because they land at the low/high/mid points of the
    midfielders baseline weight table (POSITION_GROUP_TRAIT_WEIGHTS).
    """
    v = [0.0] * 18
    v[0] = dim0
    v[2] = dim2
    v[16] = dim16
    return v


def test_trait_weighting_reorders_results_vs_pure_cosine_similarity(db_session, make_player, make_vector):
    """Hand-verified: with the midfielders baseline
    [1,1,3,3,3,2,1,2,1,2,1,1,3,1,1,3,2,2] (min=1, max=3), normalized
    per-dim weight is 0 for dim0 (crossing), 1.0 for dim2 (short_passing),
    0.5 for dim16 (composure).

    target = (dim0=3, dim2=1, dim16=1)
    A       = (dim0=3, dim2=0, dim16=1)  -- shares target's dim0
    B       = (dim0=0, dim2=1, dim16=1)  -- shares target's dim2

    Unweighted cosine (v1): dot(target,A)=10, |target|=sqrt(11), |A|=sqrt(10)
      -> distance_A = 1 - 10/sqrt(110) = 0.0465
    dot(target,B)=2, |B|=sqrt(2) -> distance_B = 1 - 2/sqrt(22) = 0.5736
    So v1 ranks A before B.

    Weighted (v2, alpha=1.0 -> pure position baseline, weights [0, 1.0, 0.5]
    on these 3 dims): the transformed (weight-scaled) target and B vectors
    are IDENTICAL -- (0, 1, sqrt(0.5)) -- so distance_B = 0 exactly, while
    A's transformed vector loses its only distinguishing (dim0) component
    entirely, giving distance_A = 1 - 0.5/(sqrt(1.5)*sqrt(0.5)) = 0.4226.
    So v2 ranks B before A -- the FLIP that proves weighting isn't a no-op.
    """
    make_player(1, position="CM")
    make_vector(1, mf_vector(dim0=3, dim2=1, dim16=1))

    make_player(2, position="CM")  # "A"
    make_vector(2, mf_vector(dim0=3, dim2=0, dim16=1))

    make_player(3, position="CM")  # "B"
    make_vector(3, mf_vector(dim0=0, dim2=1, dim16=1))

    v1_ranked = rank_similar(db_session, 1, limit=10)
    assert v1_ranked == [2, 3]

    v2_ranked = rank_similar_v2(db_session, 1, limit=10, alpha=1.0)
    assert v2_ranked == [3, 2]


def test_relaxed_outfield_filter_surfaces_a_different_position_player(db_session, make_player, make_vector):
    """v1 hard-filters to the query player's own position group; v2 relaxes
    that filter across the whole outfield pool. Player 3 (ST) has a
    near-identical vector to the CM query player and must be excluded by
    v1 but surfaced (and ranked first, since it's nearly identical) by v2.
    """
    make_player(1, position="CM")
    make_vector(1, outfield_vector(1.0))

    make_player(2, position="CM")  # same group, opposite vector -> far
    make_vector(2, outfield_vector(-1.0))

    make_player(3, position="ST")  # different group, near-identical vector
    make_vector(3, outfield_vector(0.999, rest=0.001))

    v1_ranked = rank_similar(db_session, 1, limit=10)
    assert v1_ranked == [2]
    assert 3 not in v1_ranked

    v2_ranked = rank_similar_v2(db_session, 1, limit=10)
    assert 3 in v2_ranked
    assert v2_ranked == [3, 2]


def test_gk_wall_stays_hard_in_v2(db_session, make_player, make_vector):
    """GKs use a separate 6-dim vector space -- v2 must route GK queries
    through the same GK-only, unweighted ranking v1 uses, not through the
    18-dim trait-weighted outfield path.
    """
    make_player(1, position="GK")
    make_vector(1, gk_vector(1.0), goalkeeper=True)

    make_player(2, position="GK")
    make_vector(2, gk_vector(0.9, rest=0.1), goalkeeper=True)

    # An outfield player who happens to share player_id-adjacent data in
    # player_vectors should never surface for a GK query, in v1 or v2.
    make_player(3, position="ST")
    make_vector(3, outfield_vector(1.0), goalkeeper=False)

    v1_ranked = rank_similar(db_session, 1, limit=10)
    v2_ranked = rank_similar_v2(db_session, 1, limit=10)

    assert v1_ranked == [2]
    assert v2_ranked == [2]
    assert 3 not in v2_ranked


def test_v2_raises_player_not_found(db_session):
    with pytest.raises(PlayerNotFoundError):
        rank_similar_v2(db_session, 999999, limit=10)


def test_v2_raises_unrecognized_position(db_session, make_player, make_vector):
    make_player(1, position="SW")  # not in POSITION_GROUPS
    make_vector(1, outfield_vector(1.0))

    with pytest.raises(UnrecognizedPositionError):
        rank_similar_v2(db_session, 1, limit=10)


def test_v2_raises_no_vector_for_target(db_session, make_player):
    make_player(1, position="CM")  # no PlayerVector row

    with pytest.raises(NoVectorError):
        rank_similar_v2(db_session, 1, limit=10)


def test_v2_returns_empty_list_for_empty_candidate_pool(db_session, make_player, make_vector):
    # Only player in the DB -- ranking succeeds but there's no one to rank.
    make_player(1, position="CM")
    make_vector(1, outfield_vector(1.0))

    assert rank_similar_v2(db_session, 1, limit=10) == []
