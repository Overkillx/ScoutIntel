import pytest

from app.db.models import EvaluationQueryResult, EvaluationRun
from app.evaluation.metrics import ndcg_at_k, precision_at_k, recall_at_k
from app.evaluation.relevance import RelevanceSet, relevance_set_fingerprint
from app.evaluation.runner import (
    STATUS_NO_VECTOR,
    STATUS_OK,
    STATUS_PLAYER_NOT_FOUND,
    STATUS_UNRECOGNIZED_POSITION,
    persist_evaluation_result,
    run_evaluation,
)


def vec(x, y):
    """18-dim outfield vector with only the first two dimensions set, so
    cosine distance between two of these depends only on the (x, y) angle
    -- lets a fixture pin down an exact, hand-verifiable ranking order.
    """
    return [x, y] + [0.0] * 16


def gk_vector(first, rest=0.0):
    return [first] + [rest] * 5


@pytest.fixture()
def known_fixture(make_player, make_vector):
    """5 CM players with vectors placed at known angles from (1, 0), so
    the cosine-distance ranking is exact and hand-verifiable:
    from player 1: nearest -> [2, 3, 4, 5]
    from player 5: nearest -> [4, 3, 2, 1]
    (see DECISIONS.md Day 7 for the worked distance/NDCG arithmetic).
    """
    make_player(1, position="CM")
    make_vector(1, vec(1.0, 0.0))
    make_player(2, position="CM")
    make_vector(2, vec(0.99, 0.01))
    make_player(3, position="CM")
    make_vector(3, vec(0.7, 0.7))
    make_player(4, position="CM")
    make_vector(4, vec(0.0, 1.0))
    make_player(5, position="CM")
    make_vector(5, vec(-1.0, 0.0))


def test_run_evaluation_produces_expected_per_query_and_aggregate_metrics(db_session, known_fixture):
    relevance_set = RelevanceSet(
        dataset_name="known_fixture",
        judgments={
            1: frozenset({2, 4}),
            5: frozenset({4, 1}),
        },
    )

    result = run_evaluation(db_session, relevance_set, model_version="v1_vector", k=4)

    assert result.model_version == "v1_vector"
    assert result.dataset_name == "known_fixture"
    assert result.dataset_fingerprint == relevance_set_fingerprint(relevance_set)
    assert result.k == 4
    assert result.num_queries == 2
    assert result.num_errors == 0

    by_query = {r.query_player_id: r for r in result.query_results}

    # Ranking order is pinned down by the fixture's exact cosine angles.
    query_1_ranked = [2, 3, 4, 5]
    query_5_ranked = [4, 3, 2, 1]

    expected_1 = by_query[1]
    assert expected_1.status == STATUS_OK
    assert expected_1.num_relevant == 2
    assert expected_1.num_ranked == 4
    assert expected_1.precision_at_k == pytest.approx(precision_at_k(query_1_ranked, {2, 4}, 4))
    assert expected_1.recall_at_k == pytest.approx(recall_at_k(query_1_ranked, {2, 4}, 4))
    assert expected_1.ndcg_at_k == pytest.approx(ndcg_at_k(query_1_ranked, {2, 4}, 4))
    assert expected_1.position_consistency == pytest.approx(1.0)
    assert expected_1.self_similarity_violation is False

    expected_5 = by_query[5]
    assert expected_5.status == STATUS_OK
    assert expected_5.precision_at_k == pytest.approx(precision_at_k(query_5_ranked, {4, 1}, 4))
    assert expected_5.recall_at_k == pytest.approx(recall_at_k(query_5_ranked, {4, 1}, 4))
    assert expected_5.ndcg_at_k == pytest.approx(ndcg_at_k(query_5_ranked, {4, 1}, 4))
    assert expected_5.position_consistency == pytest.approx(1.0)
    assert expected_5.self_similarity_violation is False

    # Aggregate metrics are the mean over the two (error-free) queries.
    expected_mean_precision = (expected_1.precision_at_k + expected_5.precision_at_k) / 2
    expected_mean_recall = (expected_1.recall_at_k + expected_5.recall_at_k) / 2
    expected_mean_ndcg = (expected_1.ndcg_at_k + expected_5.ndcg_at_k) / 2

    assert result.mean_precision_at_k == pytest.approx(expected_mean_precision)
    assert result.mean_recall_at_k == pytest.approx(expected_mean_recall)
    assert result.mean_ndcg_at_k == pytest.approx(expected_mean_ndcg)
    assert result.mean_position_consistency == pytest.approx(1.0)
    assert result.self_similarity_violation_count == 0

    # Hand-computed literal anchors -- checks the aggregation against real
    # numbers, not just against its own inputs, so a bug that shifted both
    # the per-query and mean computation the same way wouldn't slip through.
    # precision: each query hits 2 of the top-4 -> 2/4 for both.
    # recall: each query's 2 relevant ids are both in the top-4 -> 2/2 for both.
    # NDCG is NOT the same for both queries, despite both having 2 hits out
    # of 2 relevant: query 1's second hit (player 4) lands at rank 3, but
    # query 5's second hit (player 1) lands at rank 4 -- one rank further
    # out, since the fixture's cosine distances aren't symmetric between the
    # two query directions. So:
    #   query 1: DCG = 1/log2(2) + 1/log2(4) = 1.5,        IDCG = 1/log2(2) + 1/log2(3) = 1.6309297535714575  -> NDCG = 0.9197207891481876
    #   query 5: DCG = 1/log2(2) + 1/log2(5) = 1.4306765580733931, IDCG = 1.6309297535714575                  -> NDCG = 0.8772153153380493
    #   mean NDCG = (0.9197207891481876 + 0.8772153153380493) / 2 = 0.8984680522431184
    assert result.mean_precision_at_k == pytest.approx(0.5)
    assert result.mean_recall_at_k == pytest.approx(1.0)
    assert result.mean_ndcg_at_k == pytest.approx(0.8984680522431184, abs=1e-4)


def test_run_evaluation_handles_no_vector_gracefully(db_session, make_player, make_vector):
    make_player(1, position="CM")  # query player has no PlayerVector row
    make_player(2, position="CM")
    make_vector(2, vec(1.0, 0.0))

    relevance_set = RelevanceSet(dataset_name="edge_cases", judgments={1: frozenset({2})})

    result = run_evaluation(db_session, relevance_set, model_version="v1_vector", k=10)

    assert result.num_queries == 1
    assert result.num_errors == 1

    query_result = result.query_results[0]
    assert query_result.status == STATUS_NO_VECTOR
    assert query_result.num_relevant == 1
    assert query_result.precision_at_k is None
    assert query_result.recall_at_k is None
    assert query_result.ndcg_at_k is None
    assert query_result.self_similarity_violation is None

    # An entirely-errored run has no error-free query to average over.
    assert result.mean_precision_at_k is None
    assert result.mean_recall_at_k is None
    assert result.mean_ndcg_at_k is None
    assert result.mean_position_consistency is None
    assert result.self_similarity_violation_count == 0


def test_run_evaluation_handles_player_not_found_gracefully(db_session, make_player, make_vector):
    make_player(2, position="CM")
    make_vector(2, vec(1.0, 0.0))

    # query player_id 999 was never created -- constructed directly rather
    # than via load_relevance_set(), which would refuse this at load time.
    relevance_set = RelevanceSet(dataset_name="edge_cases", judgments={999: frozenset({2})})

    result = run_evaluation(db_session, relevance_set, model_version="v1_vector", k=10)

    assert result.query_results[0].status == STATUS_PLAYER_NOT_FOUND
    assert result.num_errors == 1


def test_run_evaluation_handles_unrecognized_position_gracefully(db_session, make_player, make_vector):
    make_player(1, position="SW")  # not in POSITION_GROUPS
    make_vector(1, vec(1.0, 0.0))

    relevance_set = RelevanceSet(dataset_name="edge_cases", judgments={1: frozenset({2})})

    result = run_evaluation(db_session, relevance_set, model_version="v1_vector", k=10)

    assert result.query_results[0].status == STATUS_UNRECOGNIZED_POSITION
    assert result.num_errors == 1


def test_run_evaluation_handles_empty_result_as_ok_not_an_error(db_session, make_player, make_vector):
    # Only goalkeeper in the DB -- rank_similar succeeds but returns no
    # candidates. That's a legitimate outcome (small pool), not an error.
    make_player(1, position="GK")
    make_vector(1, gk_vector(1.0), goalkeeper=True)

    relevance_set = RelevanceSet(dataset_name="edge_cases", judgments={1: frozenset({999})})

    result = run_evaluation(db_session, relevance_set, model_version="v1_vector", k=10)

    query_result = result.query_results[0]
    assert query_result.status == STATUS_OK
    assert query_result.num_ranked == 0
    assert query_result.precision_at_k == pytest.approx(0.0)
    assert query_result.recall_at_k == pytest.approx(0.0)
    assert query_result.ndcg_at_k == pytest.approx(0.0)
    assert query_result.position_consistency == pytest.approx(1.0)
    assert query_result.self_similarity_violation is False
    assert result.num_errors == 0


def test_persist_evaluation_result_writes_run_and_query_rows(db_session, known_fixture):
    relevance_set = RelevanceSet(
        dataset_name="known_fixture",
        judgments={1: frozenset({2, 4}), 5: frozenset({4, 1})},
    )
    result = run_evaluation(db_session, relevance_set, model_version="v1_vector", k=4)

    run = persist_evaluation_result(db_session, result)

    assert run.id is not None
    assert run.created_at is not None
    assert run.model_version == "v1_vector"
    assert run.dataset_name == "known_fixture"
    assert run.dataset_fingerprint == result.dataset_fingerprint
    assert run.k == 4
    assert run.num_queries == 2
    assert run.num_errors == 0
    assert run.mean_precision_at_k == pytest.approx(result.mean_precision_at_k)

    stored_run = db_session.query(EvaluationRun).filter(EvaluationRun.id == run.id).one()
    assert stored_run.model_version == "v1_vector"

    stored_query_rows = (
        db_session.query(EvaluationQueryResult)
        .filter(EvaluationQueryResult.evaluation_run_id == run.id)
        .all()
    )
    assert len(stored_query_rows) == 2
    assert {row.query_player_id for row in stored_query_rows} == {1, 5}
    assert all(row.status == STATUS_OK for row in stored_query_rows)
