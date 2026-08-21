import pytest

from app.evaluation.relevance import RelevanceSetError, load_relevance_set


def _write(tmp_path, contents):
    path = tmp_path / "relevance_set.yaml"
    path.write_text(contents)
    return path


def test_loads_a_valid_relevance_set(db_session, make_player, tmp_path):
    make_player(1)
    make_player(2)
    make_player(3)
    path = _write(
        tmp_path,
        """
        dataset_name: test_v1
        relevance:
          1:  # Player 1
            - 2
            - 3
        """,
    )

    relevance_set = load_relevance_set(db_session, path)

    assert relevance_set.dataset_name == "test_v1"
    assert relevance_set.query_player_ids == [1]
    assert relevance_set.relevant_for(1) == frozenset({2, 3})


def test_relevant_for_unknown_query_returns_empty_set(db_session, make_player, tmp_path):
    make_player(1)
    make_player(2)
    path = _write(
        tmp_path,
        """
        dataset_name: test_v1
        relevance:
          1:
            - 2
        """,
    )

    relevance_set = load_relevance_set(db_session, path)

    assert relevance_set.relevant_for(999) == frozenset()


def test_fails_loudly_when_a_referenced_player_does_not_exist(db_session, make_player, tmp_path):
    make_player(1)
    # player 2 is referenced but never created
    path = _write(
        tmp_path,
        """
        dataset_name: test_v1
        relevance:
          1:
            - 2
        """,
    )

    with pytest.raises(RelevanceSetError, match="unknown player_id"):
        load_relevance_set(db_session, path)


def test_fails_loudly_when_query_player_does_not_exist(db_session, make_player, tmp_path):
    make_player(2)
    # query player 1 is never created
    path = _write(
        tmp_path,
        """
        dataset_name: test_v1
        relevance:
          1:
            - 2
        """,
    )

    with pytest.raises(RelevanceSetError, match="unknown player_id"):
        load_relevance_set(db_session, path)


def test_fails_loudly_on_duplicate_query_player_id(db_session, make_player, tmp_path):
    make_player(1)
    make_player(2)
    make_player(3)
    path = _write(
        tmp_path,
        """
        dataset_name: test_v1
        relevance:
          1:
            - 2
          1:
            - 3
        """,
    )

    with pytest.raises(RelevanceSetError, match="duplicate key"):
        load_relevance_set(db_session, path)


def test_fails_loudly_on_duplicate_relevant_player_id(db_session, make_player, tmp_path):
    make_player(1)
    make_player(2)
    path = _write(
        tmp_path,
        """
        dataset_name: test_v1
        relevance:
          1:
            - 2
            - 2
        """,
    )

    with pytest.raises(RelevanceSetError, match="duplicate relevant player_id"):
        load_relevance_set(db_session, path)


def test_fails_loudly_on_malformed_yaml(db_session, tmp_path):
    path = _write(
        tmp_path,
        """
        dataset_name: test_v1
        relevance:
          1: [2, 3
        """,
    )

    with pytest.raises(RelevanceSetError, match="malformed YAML"):
        load_relevance_set(db_session, path)


def test_fails_loudly_when_missing_dataset_name(db_session, make_player, tmp_path):
    make_player(1)
    make_player(2)
    path = _write(
        tmp_path,
        """
        relevance:
          1:
            - 2
        """,
    )

    with pytest.raises(RelevanceSetError, match="dataset_name"):
        load_relevance_set(db_session, path)


def test_fails_loudly_when_relevance_key_missing(db_session, tmp_path):
    path = _write(
        tmp_path,
        """
        dataset_name: test_v1
        """,
    )

    with pytest.raises(RelevanceSetError, match="relevance"):
        load_relevance_set(db_session, path)


def test_fails_loudly_when_relevant_list_is_empty(db_session, make_player, tmp_path):
    make_player(1)
    path = _write(
        tmp_path,
        """
        dataset_name: test_v1
        relevance:
          1: []
        """,
    )

    with pytest.raises(RelevanceSetError, match="non-empty list"):
        load_relevance_set(db_session, path)


def test_fails_loudly_on_non_integer_player_id(db_session, make_player, tmp_path):
    make_player(1)
    path = _write(
        tmp_path,
        """
        dataset_name: test_v1
        relevance:
          1:
            - "abc"
        """,
    )

    with pytest.raises(RelevanceSetError, match="must be an integer"):
        load_relevance_set(db_session, path)


def test_seed_relevance_set_file_is_well_formed(db_session, shipped_relevance_set):
    """The shipped relevance set (app/evaluation/relevance_set.yaml) should
    parse and validate once its referenced players exist. A format check,
    not a claim that the judgments themselves are correct -- they're one
    person's subjective curation, see the file's header comment.
    """
    relevance_set = load_relevance_set(db_session)

    assert relevance_set.dataset_name == shipped_relevance_set["dataset_name"]
    assert set(relevance_set.query_player_ids) == set(shipped_relevance_set["relevance"])
    assert all(relevance_set.relevant_for(query_id) for query_id in relevance_set.query_player_ids)
