from datetime import datetime, timedelta, timezone

from app.db.models import EvaluationQueryResult, EvaluationRun
from app.tasks.evaluation import run_evaluation_task


class _FakeAsyncResult:
    def __init__(self, task_id):
        self.id = task_id


def test_post_run_dispatches_task_without_touching_a_broker(client, monkeypatch):
    """POST /evaluations/run must work without a live Celery broker -- only
    .delay() is stubbed, so this proves the endpoint's own wiring (request
    parsing, calling .delay with the right kwargs, shaping the response)
    independently of whether a worker or Redis is reachable. The task's own
    logic is covered separately in test_evaluation_task.py.
    """
    captured = {}

    def fake_delay(**kwargs):
        captured.update(kwargs)
        return _FakeAsyncResult("fake-task-id")

    monkeypatch.setattr(run_evaluation_task, "delay", fake_delay)

    response = client.post(
        "/api/v1/evaluations/run", json={"model_version": "v1_vector", "k": 5}
    )

    assert response.status_code == 202
    assert response.json() == {"task_id": "fake-task-id"}
    assert captured == {
        "model_version": "v1_vector",
        "k": 5,
        "relevance_set_path": None,
        "model_params": None,
    }


def test_post_run_passes_through_optional_dataset_path(client, monkeypatch):
    captured = {}

    def fake_delay(**kwargs):
        captured.update(kwargs)
        return _FakeAsyncResult("fake-task-id")

    monkeypatch.setattr(run_evaluation_task, "delay", fake_delay)

    client.post(
        "/api/v1/evaluations/run",
        json={"model_version": "v2_tactical", "k": 20, "dataset_path": "/tmp/custom.yaml"},
    )

    assert captured == {
        "model_version": "v2_tactical",
        "k": 20,
        "relevance_set_path": "/tmp/custom.yaml",
        "model_params": None,
    }


def test_post_run_passes_through_model_params(client, monkeypatch):
    captured = {}

    def fake_delay(**kwargs):
        captured.update(kwargs)
        return _FakeAsyncResult("fake-task-id")

    monkeypatch.setattr(run_evaluation_task, "delay", fake_delay)

    client.post(
        "/api/v1/evaluations/run",
        json={"model_version": "v2_tactical", "model_params": {"alpha": 0.3}},
    )

    assert captured["model_params"] == {"alpha": 0.3}


def _make_run(db_session, **overrides):
    defaults = dict(
        model_version="v1_vector",
        dataset_name="placeholder_v0",
        dataset_fingerprint="deadbeef",
        k=10,
        num_queries=2,
        num_errors=0,
        mean_precision_at_k=0.5,
        mean_recall_at_k=1.0,
        mean_ndcg_at_k=0.9,
        mean_position_consistency=1.0,
        self_similarity_violation_count=0,
    )
    defaults.update(overrides)
    run = EvaluationRun(**defaults)
    db_session.add(run)
    db_session.flush()
    return run


def test_get_evaluation_run_returns_detail_with_query_results(db_session, make_player, client):
    make_player(1, position="CM")
    run = _make_run(db_session)
    db_session.add(
        EvaluationQueryResult(
            evaluation_run_id=run.id,
            query_player_id=1,
            status="ok",
            precision_at_k=0.5,
            recall_at_k=1.0,
            ndcg_at_k=0.9,
            position_consistency=1.0,
            self_similarity_violation=False,
            num_relevant=2,
            num_ranked=4,
        )
    )
    db_session.flush()

    response = client.get(f"/api/v1/evaluations/{run.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run.id
    assert body["model_version"] == "v1_vector"
    assert body["dataset_fingerprint"] == "deadbeef"
    assert len(body["query_results"]) == 1
    assert body["query_results"][0]["query_player_id"] == 1
    assert body["query_results"][0]["status"] == "ok"


def test_get_evaluation_run_404_when_missing(client):
    response = client.get("/api/v1/evaluations/999999")

    assert response.status_code == 404


def test_list_evaluation_runs_orders_most_recent_first(db_session, client):
    older = _make_run(db_session, model_version="v1_vector")
    older.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    newer = _make_run(db_session, model_version="v2_tactical")
    newer.created_at = datetime.now(timezone.utc)
    db_session.flush()

    response = client.get("/api/v1/evaluations/")

    assert response.status_code == 200
    body = response.json()
    ids = [row["id"] for row in body]
    assert ids.index(newer.id) < ids.index(older.id)


def test_list_evaluation_runs_summary_has_no_query_results_field(db_session, client):
    _make_run(db_session)

    response = client.get("/api/v1/evaluations/")

    assert response.status_code == 200
    assert "query_results" not in response.json()[0]


def test_list_evaluation_runs_exposes_model_params(db_session, client):
    """A sweep writes several rows under one model_version; the parameter
    each was run at has to be visible in the API or the rows are
    indistinguishable.
    """
    _make_run(db_session, model_version="v2_tactical", model_params={"alpha": 0.3})
    _make_run(db_session, model_version="v2_tactical", model_params={"alpha": 0.7})
    _make_run(db_session, model_version="v1_vector")

    response = client.get("/api/v1/evaluations/")

    assert response.status_code == 200
    params = [run["model_params"] for run in response.json()]
    assert {"alpha": 0.3} in params
    assert {"alpha": 0.7} in params
    assert None in params
