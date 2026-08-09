import math

import pytest

from app.evaluation.metrics import (
    has_self_similarity_violation,
    ndcg_at_k,
    position_consistency,
    precision_at_k,
    recall_at_k,
)


class TestPrecisionAtK:
    def test_hand_computed(self):
        # top-5 = [1,2,3,4,5]; relevant hits are 2 and 4 -> 2/5
        assert precision_at_k([1, 2, 3, 4, 5], {2, 4, 6}, k=5) == pytest.approx(0.4)

    def test_k_greater_than_len_results_divides_by_what_was_retrieved(self):
        # only 2 results exist; both relevant -> 2/2, not 2/5
        assert precision_at_k([1, 2], {1, 2, 3}, k=5) == pytest.approx(1.0)

    def test_empty_relevant_set(self):
        assert precision_at_k([1, 2, 3], set(), k=3) == pytest.approx(0.0)

    def test_zero_relevant_retrieved(self):
        assert precision_at_k([1, 2, 3], {4, 5}, k=3) == pytest.approx(0.0)

    def test_empty_ranked_ids(self):
        assert precision_at_k([], {1, 2}, k=3) == pytest.approx(0.0)

    def test_rejects_non_positive_k(self):
        with pytest.raises(ValueError):
            precision_at_k([1, 2, 3], {1}, k=0)


class TestRecallAtK:
    def test_hand_computed(self):
        # relevant = {2,4,6}; top-5 = [1,2,3,4,5] hits 2 of 3 -> 2/3
        assert recall_at_k([1, 2, 3, 4, 5], {2, 4, 6}, k=5) == pytest.approx(2 / 3)

    def test_k_greater_than_len_results(self):
        assert recall_at_k([1, 2], {1, 2, 3}, k=10) == pytest.approx(2 / 3)

    def test_empty_relevant_set_is_zero_not_a_crash(self):
        assert recall_at_k([1, 2, 3], set(), k=3) == pytest.approx(0.0)

    def test_zero_relevant_retrieved(self):
        assert recall_at_k([1, 2, 3], {4, 5}, k=3) == pytest.approx(0.0)

    def test_rejects_non_positive_k(self):
        with pytest.raises(ValueError):
            recall_at_k([1, 2, 3], {1}, k=-1)


class TestNdcgAtK:
    def test_hand_computed(self):
        # ranked = [1,2,3], relevant = {1,3}
        # gains:  i=0 rid=1 hit -> 1/log2(2)=1.0
        #         i=1 rid=2 miss -> 0
        #         i=2 rid=3 hit -> 1/log2(4)=0.5
        # dcg = 1.5
        # ideal_hits = min(3, 2) = 2
        # idcg = 1/log2(2) + 1/log2(3) = 1.0 + 0.6309297535714573 = 1.6309297535714573
        # ndcg = 1.5 / 1.6309297535714573
        expected = 1.5 / (1.0 + 1.0 / math.log2(3))
        assert ndcg_at_k([1, 2, 3], {1, 3}, k=3) == pytest.approx(expected)

    def test_perfect_ranking_scores_one(self):
        # relevant ids occupy the first len(relevant_ids) ranks -> DCG == IDCG
        assert ndcg_at_k([1, 3, 2], {1, 3}, k=3) == pytest.approx(1.0)

    def test_no_hits_scores_zero(self):
        assert ndcg_at_k([1, 2, 3], {4}, k=3) == pytest.approx(0.0)

    def test_empty_relevant_set_is_zero_not_a_crash(self):
        assert ndcg_at_k([1, 2, 3], set(), k=3) == pytest.approx(0.0)

    def test_k_greater_than_len_results(self):
        # ranked=[1,2], relevant={1,2,3}, k=5
        # dcg  = 1/log2(2) + 1/log2(3) = 1.0 + 0.6309297535714573
        # idcg uses ideal_hits = min(5, 3) = 3:
        #        1/log2(2) + 1/log2(3) + 1/log2(4) = 1.0 + 0.6309297535714573 + 0.5
        dcg = 1.0 + 1.0 / math.log2(3)
        idcg = 1.0 + 1.0 / math.log2(3) + 0.5
        assert ndcg_at_k([1, 2], {1, 2, 3}, k=5) == pytest.approx(dcg / idcg)

    def test_rejects_non_positive_k(self):
        with pytest.raises(ValueError):
            ndcg_at_k([1, 2, 3], {1}, k=0)


class TestSelfSimilarity:
    def test_no_violation_when_query_absent(self):
        assert has_self_similarity_violation(1, [2, 3, 4]) is False

    def test_violation_when_query_present(self):
        assert has_self_similarity_violation(1, [2, 1, 4]) is True

    def test_no_violation_on_empty_ranking(self):
        assert has_self_similarity_violation(1, []) is False


class TestPositionConsistency:
    def test_hand_computed(self):
        # CM is a midfielder; CAM and CDM are midfielders, ST is an attacker
        assert position_consistency("CM", ["CAM", "CDM", "ST"]) == pytest.approx(2 / 3)

    def test_all_consistent(self):
        assert position_consistency("CM", ["CAM", "CDM", "LM"]) == pytest.approx(1.0)

    def test_none_consistent(self):
        assert position_consistency("CM", ["ST", "LW", "RW"]) == pytest.approx(0.0)

    def test_empty_ranked_positions_is_vacuously_consistent(self):
        assert position_consistency("CM", []) == pytest.approx(1.0)

    def test_rejects_unrecognized_query_position(self):
        with pytest.raises(ValueError):
            position_consistency("SW", ["CM"])

    def test_accepts_custom_position_groups(self):
        custom_groups = {"A": "group1", "B": "group1", "C": "group2"}
        assert position_consistency("A", ["B", "C"], position_groups=custom_groups) == pytest.approx(0.5)
