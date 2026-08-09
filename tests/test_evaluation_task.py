import pytest

from app.db.models import EvaluationQueryResult, EvaluationRun
from app.tasks.evaluation import run_evaluation_task


def outfield_vector(first, rest=0.0):
    return [first] + [rest] * 17


@pytest.fixture()
def patched_task_session(monkeypatch, db_session):
    """run_evaluation_task (Step 4) opens its own SessionLocal() and calls
    db.commit() -- correct for a real worker process, but unsafe here:
    db_session is bound to a connection with an outer transaction already
    begun by the transaction-per-test fixture in conftest.py, and calling
    .commit() on it would commit *that* transaction for real, ending the
    test's rollback-based isolation for every test that runs after this one
    in the same session. SessionLocal is patched to return db_session, with
    commit() aliased to flush() (visible within the same session without an
    actual COMMIT) and close() a no-op -- the same "never close the shared
    session" approach override_get_db already uses in conftest.py.
    """
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr("app.tasks.evaluation.SessionLocal", lambda: db_session)
    return db_session


def test_task_runs_evaluation_and_persists_it(
    db_session, make_player, make_vector, patched_task_session, tmp_path
):
    make_player(1, position="CM")
    make_vector(1, outfield_vector(1.0))
    make_player(2, position="CM")
    make_vector(2, outfield_vector(0.99, rest=0.01))

    relevance_set_path = tmp_path / "relevance_set.yaml"
    relevance_set_path.write_text(
        """
        dataset_name: task_test_v1
        relevance:
          1:
            - 2
        """
    )

    # Called directly (not via .delay()) -- Celery task objects run their
    # underlying function synchronously in-process when called this way, so
    # this exercises the real task body with no broker involved.
    run_id = run_evaluation_task(
        model_version="v1_vector", k=5, relevance_set_path=str(relevance_set_path)
    )

    assert isinstance(run_id, int)

    stored_run = db_session.query(EvaluationRun).filter(EvaluationRun.id == run_id).one()
    assert stored_run.model_version == "v1_vector"
    assert stored_run.dataset_name == "task_test_v1"
    assert stored_run.num_queries == 1
    assert stored_run.num_errors == 0
    assert stored_run.mean_precision_at_k == 1.0

    stored_query_rows = (
        db_session.query(EvaluationQueryResult)
        .filter(EvaluationQueryResult.evaluation_run_id == run_id)
        .all()
    )
    assert len(stored_query_rows) == 1
    assert stored_query_rows[0].query_player_id == 1


def test_task_uses_default_relevance_set_path_when_none_given(
    db_session, make_player, make_vector, patched_task_session
):
    # The shipped placeholder file (app/evaluation/relevance_set.yaml)
    # references these specific player_ids -- see its header comment.
    for player_id in (231747, 239085, 238794, 252371, 256630, 251854):
        make_player(player_id, position="ST")
        make_vector(player_id, outfield_vector(float(player_id % 10) / 10))

    run_id = run_evaluation_task(model_version="v1_vector", k=5, relevance_set_path=None)

    stored_run = db_session.query(EvaluationRun).filter(EvaluationRun.id == run_id).one()
    assert stored_run.dataset_name == "placeholder_v0"
    assert stored_run.num_queries == 3
