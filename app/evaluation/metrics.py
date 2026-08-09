"""Pure, synchronous ranking-quality metrics for the offline evaluation
harness. No DB session, no Celery, no FastAPI -- these operate entirely on
plain lists/sets (player_ids, position strings) so they're unit-testable
against hand-computed values with no fixtures beyond the inputs themselves.
"""
from __future__ import annotations

import math

from app.services.similarity import POSITION_GROUPS


def precision_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    """Fraction of the top-k ranked ids that are relevant.

    Denominator is min(k, len(ranked_ids)) -- precision over what was
    actually retrieved, not over a fixed k. Position-group filtering means
    a query can have a candidate pool smaller than k (e.g. goalkeepers);
    dividing by a fixed k would penalize those queries just for having a
    small pool, independent of ranking quality.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    top_k = ranked_ids[:k]
    if not top_k:
        return 0.0
    hits = sum(1 for rid in top_k if rid in relevant_ids)
    return hits / len(top_k)


def recall_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    """Fraction of all relevant ids that appear in the top-k.

    An empty relevant_ids has no ground truth to recall against, so it's
    treated as 0.0 rather than raising a division error. In practice the
    relevance-set loader already refuses an empty relevant list at load
    time (Step 1), so this only fires if a caller passes one directly.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    if not relevant_ids:
        return 0.0
    top_k = ranked_ids[:k]
    hits = sum(1 for rid in top_k if rid in relevant_ids)
    return hits / len(relevant_ids)


def ndcg_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    """Normalized Discounted Cumulative Gain at k, binary relevance.

    gain(i)  = 1 if ranked_ids[i] in relevant_ids else 0     (i is 0-indexed)
    DCG@k    = sum_{i=0}^{k-1} gain(i) / log2(i + 2)           # rank (i+1), discount log2(rank+1)
    IDCG@k   = DCG of the ideal ranking (all relevant ids first):
               sum_{i=0}^{m-1} 1 / log2(i + 2), where m = min(k, len(relevant_ids))
    NDCG@k   = DCG@k / IDCG@k, or 0.0 if IDCG@k is 0 (no relevant ids at all).
    """
    if k <= 0:
        raise ValueError("k must be positive")
    if not relevant_ids:
        return 0.0

    top_k = ranked_ids[:k]
    dcg = sum(1.0 / math.log2(i + 2) for i, rid in enumerate(top_k) if rid in relevant_ids)

    ideal_hits = min(k, len(relevant_ids))
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))

    return dcg / idcg if idcg > 0 else 0.0


def has_self_similarity_violation(query_id: int, ranked_ids: list[int]) -> bool:
    """True if the query player appears among its own ranked results.

    rank_similar() already excludes the target by player_id, so this
    should always be False for a correctly-behaving ranking -- a sanity
    check on that invariant, independent of the relevance set.
    """
    return query_id in ranked_ids


def position_consistency(
    query_position: str,
    ranked_positions: list[str],
    position_groups: dict[str, str] = POSITION_GROUPS,
) -> float:
    """Fraction of ranked results in the same position group as the query
    player (defenders / midfielders / attackers / goalkeepers).

    Reuses the same POSITION_GROUPS table the ranking endpoint filters
    candidates by, rather than a second copy, so this check can't drift
    out of sync with what "consistent" means there -- it verifies the
    filter behaved, it doesn't define correctness independently.
    """
    query_group = position_groups.get(query_position)
    if query_group is None:
        raise ValueError(f"unrecognized query position '{query_position}'")
    if not ranked_positions:
        return 1.0  # nothing retrieved, so nothing inconsistent with the query
    matches = sum(1 for pos in ranked_positions if position_groups.get(pos) == query_group)
    return matches / len(ranked_positions)
