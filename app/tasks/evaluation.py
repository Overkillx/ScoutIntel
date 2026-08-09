from pathlib import Path

from app.core.celery_app import celery_app
from app.db.session import SessionLocal
from app.evaluation.relevance import DEFAULT_RELEVANCE_SET_PATH, load_relevance_set
from app.evaluation.runner import persist_evaluation_result, run_evaluation


@celery_app.task(name="evaluation.run")
def run_evaluation_task(model_version: str, k: int = 10, relevance_set_path: str | None = None) -> int:
    """Celery entrypoint for the offline evaluation harness.

    Opens its own DB session rather than reusing a request-scoped one --
    this runs in a separate worker process with no request to borrow a
    session from, and SessionLocal is exactly the same sessionmaker the API
    process uses (app/db/session.py), so behavior doesn't diverge by
    process. The synchronous run_evaluation()/persist_evaluation_result()
    calls (Step 3) are unchanged -- this task is only the dispatch wrapper
    around them, not where any evaluation logic lives.

    Returns the new evaluation_runs.id.
    """
    db = SessionLocal()
    try:
        path = Path(relevance_set_path) if relevance_set_path else DEFAULT_RELEVANCE_SET_PATH
        relevance_set = load_relevance_set(db, path)
        result = run_evaluation(db, relevance_set, model_version=model_version, k=k)
        run = persist_evaluation_result(db, result)
        db.commit()
        return run.id
    finally:
        db.close()
