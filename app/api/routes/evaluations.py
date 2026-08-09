from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.models import EvaluationRun
from app.db.session import get_db
from app.schemas.evaluation import (
    EvaluationRunDetail,
    EvaluationRunRequest,
    EvaluationRunSummary,
    EvaluationRunTriggerResponse,
)
from app.tasks.evaluation import run_evaluation_task

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])


@router.post("/run", response_model=EvaluationRunTriggerResponse, status_code=202)
def trigger_evaluation_run(payload: EvaluationRunRequest):
    async_result = run_evaluation_task.delay(
        model_version=payload.model_version,
        k=payload.k,
        relevance_set_path=payload.dataset_path,
    )
    return EvaluationRunTriggerResponse(task_id=async_result.id)


@router.get("/", response_model=list[EvaluationRunSummary])
def list_evaluation_runs(limit: int = Query(default=20, le=100), db: Session = Depends(get_db)):
    return (
        db.query(EvaluationRun)
        .order_by(EvaluationRun.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/{run_id}", response_model=EvaluationRunDetail)
def get_evaluation_run(run_id: int, db: Session = Depends(get_db)):
    run = db.query(EvaluationRun).filter(EvaluationRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    return run
