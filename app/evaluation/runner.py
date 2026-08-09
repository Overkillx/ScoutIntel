"""Synchronous offline evaluation runner. Scores rank_similar() (the plain
service function from Step 0, not the HTTP endpoint) against a loaded
RelevanceSet and returns a structured, DB-independent result. Celery (Step
4) only dispatches this -- everything here stays independently callable and
testable without a message broker.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.models import EvaluationQueryResult, EvaluationRun, Player
from app.evaluation.metrics import (
    has_self_similarity_violation,
    ndcg_at_k,
    position_consistency,
    precision_at_k,
    recall_at_k,
)
from app.evaluation.relevance import RelevanceSet, relevance_set_fingerprint
from app.services.similarity import (
    NoVectorError,
    PlayerNotFoundError,
    UnrecognizedPositionError,
    rank_similar,
)

# Every non-"ok" status corresponds to one of the plain exceptions
# rank_similar() can raise (Step 0) -- recorded on the query's own result
# row rather than raised, so one query in an edge state doesn't abort the
# whole run. "ok" covers a query that ranked successfully even if it
# returned zero candidates (an empty result is a legitimate outcome of a
# small position-group pool, not an error).
STATUS_OK = "ok"
STATUS_PLAYER_NOT_FOUND = "player_not_found"
STATUS_UNRECOGNIZED_POSITION = "unrecognized_position"
STATUS_NO_VECTOR = "no_vector"


@dataclass
class QueryResult:
    query_player_id: int
    status: str
    num_relevant: int = 0
    num_ranked: int = 0
    precision_at_k: float | None = None
    recall_at_k: float | None = None
    ndcg_at_k: float | None = None
    position_consistency: float | None = None
    self_similarity_violation: bool | None = None


@dataclass
class EvaluationResult:
    model_version: str
    dataset_name: str
    dataset_fingerprint: str
    k: int
    query_results: list[QueryResult] = field(default_factory=list)

    @property
    def num_queries(self) -> int:
        return len(self.query_results)

    @property
    def num_errors(self) -> int:
        return sum(1 for r in self.query_results if r.status != STATUS_OK)

    def _ok_results(self) -> list[QueryResult]:
        return [r for r in self.query_results if r.status == STATUS_OK]

    def _mean(self, attr: str) -> float | None:
        ok = self._ok_results()
        if not ok:
            return None
        return sum(getattr(r, attr) for r in ok) / len(ok)

    @property
    def mean_precision_at_k(self) -> float | None:
        return self._mean("precision_at_k")

    @property
    def mean_recall_at_k(self) -> float | None:
        return self._mean("recall_at_k")

    @property
    def mean_ndcg_at_k(self) -> float | None:
        return self._mean("ndcg_at_k")

    @property
    def mean_position_consistency(self) -> float | None:
        return self._mean("position_consistency")

    @property
    def self_similarity_violation_count(self) -> int:
        return sum(1 for r in self._ok_results() if r.self_similarity_violation)


def _score_query(db: Session, query_id: int, relevant_ids: frozenset[int], k: int) -> QueryResult:
    try:
        ranked_ids = rank_similar(db, query_id, limit=k)
    except PlayerNotFoundError:
        return QueryResult(query_id, status=STATUS_PLAYER_NOT_FOUND, num_relevant=len(relevant_ids))
    except UnrecognizedPositionError:
        return QueryResult(query_id, status=STATUS_UNRECOGNIZED_POSITION, num_relevant=len(relevant_ids))
    except NoVectorError:
        return QueryResult(query_id, status=STATUS_NO_VECTOR, num_relevant=len(relevant_ids))

    query_player = db.query(Player).filter(Player.player_id == query_id).first()

    position_by_id = dict(
        db.query(Player.player_id, Player.primary_position)
        .filter(Player.player_id.in_(ranked_ids))
        .all()
    )
    ranked_positions = [position_by_id[rid] for rid in ranked_ids]

    return QueryResult(
        query_player_id=query_id,
        status=STATUS_OK,
        num_relevant=len(relevant_ids),
        num_ranked=len(ranked_ids),
        precision_at_k=precision_at_k(ranked_ids, relevant_ids, k),
        recall_at_k=recall_at_k(ranked_ids, relevant_ids, k),
        ndcg_at_k=ndcg_at_k(ranked_ids, relevant_ids, k),
        position_consistency=position_consistency(query_player.primary_position, ranked_positions),
        self_similarity_violation=has_self_similarity_violation(query_id, ranked_ids),
    )


def run_evaluation(
    db: Session,
    relevance_set: RelevanceSet,
    model_version: str,
    k: int = 10,
) -> EvaluationResult:
    """Run every query player in relevance_set through rank_similar(),
    score each ranking against its curated relevant_ids, and return the
    per-query + aggregate result. model_version is an opaque caller-supplied
    label (e.g. "v1_vector", "v2_tactical") -- never derived from code
    introspection, so it stays meaningful across whatever v2 turns out to
    be.
    """
    query_results = [
        _score_query(db, query_id, relevance_set.relevant_for(query_id), k)
        for query_id in relevance_set.query_player_ids
    ]

    return EvaluationResult(
        model_version=model_version,
        dataset_name=relevance_set.dataset_name,
        dataset_fingerprint=relevance_set_fingerprint(relevance_set),
        k=k,
        query_results=query_results,
    )


def persist_evaluation_result(db: Session, result: EvaluationResult) -> EvaluationRun:
    """Write an EvaluationResult to evaluation_runs / evaluation_query_results.

    Flushes (so the returned row has its `id` and `created_at` populated)
    but does not commit -- the caller owns the transaction boundary. In
    Step 4 that's a Celery task; in tests it's left uncommitted on purpose
    so the transaction-per-test rollback fixture in conftest.py can undo it.
    """
    run = EvaluationRun(
        model_version=result.model_version,
        dataset_name=result.dataset_name,
        dataset_fingerprint=result.dataset_fingerprint,
        k=result.k,
        num_queries=result.num_queries,
        num_errors=result.num_errors,
        mean_precision_at_k=result.mean_precision_at_k,
        mean_recall_at_k=result.mean_recall_at_k,
        mean_ndcg_at_k=result.mean_ndcg_at_k,
        mean_position_consistency=result.mean_position_consistency,
        self_similarity_violation_count=result.self_similarity_violation_count,
        query_results=[
            EvaluationQueryResult(
                query_player_id=qr.query_player_id,
                status=qr.status,
                num_relevant=qr.num_relevant,
                num_ranked=qr.num_ranked,
                precision_at_k=qr.precision_at_k,
                recall_at_k=qr.recall_at_k,
                ndcg_at_k=qr.ndcg_at_k,
                position_consistency=qr.position_consistency,
                self_similarity_violation=qr.self_similarity_violation,
            )
            for qr in result.query_results
        ],
    )
    db.add(run)
    db.flush()
    return run
