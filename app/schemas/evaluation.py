from datetime import datetime

from pydantic import BaseModel


class EvaluationRunRequest(BaseModel):
    model_version: str
    k: int = 10
    # Path to a relevance set YAML file, overriding the default
    # (app/evaluation/relevance_set.yaml). Optional -- most callers just
    # want the current curated set.
    dataset_path: str | None = None


class EvaluationRunTriggerResponse(BaseModel):
    task_id: str


class EvaluationQueryResultOut(BaseModel):
    query_player_id: int
    status: str
    num_relevant: int
    num_ranked: int
    precision_at_k: float | None
    recall_at_k: float | None
    ndcg_at_k: float | None
    position_consistency: float | None
    self_similarity_violation: bool | None

    model_config = {"from_attributes": True}


class EvaluationRunSummary(BaseModel):
    id: int
    model_version: str
    dataset_name: str
    dataset_fingerprint: str
    k: int
    num_queries: int
    num_errors: int
    mean_precision_at_k: float | None
    mean_recall_at_k: float | None
    mean_ndcg_at_k: float | None
    mean_position_consistency: float | None
    self_similarity_violation_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class EvaluationRunDetail(EvaluationRunSummary):
    query_results: list[EvaluationQueryResultOut]

    model_config = {"from_attributes": True}
