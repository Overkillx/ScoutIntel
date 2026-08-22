"""POST /api/v1/search -- the natural-language path end to end, including
what happens to input the parser can't or won't interpret.
"""
from app.core.attributes import TRAIT_DIMENSIONS


def vec(x, y, **traits):
    embedding = [x, y] + [0.0] * 16
    for trait, value in traits.items():
        embedding[TRAIT_DIMENSIONS[trait]] = value
    return embedding


def seed_squad(make_player, make_vector, make_stats):
    make_player(1, position="CM", short_name="Rodri", age=28)
    make_vector(1, vec(1.0, 0.0))
    make_stats(1, overall=91)
    make_player(2, position="CM", short_name="Young passer", age=21, preferred_foot="Left")
    make_vector(2, vec(0.99, 0.01, acceleration=1.0))
    make_stats(2, overall=82)
    make_player(3, position="CM", short_name="Old passer", age=34)
    make_vector(3, vec(0.98, 0.02, acceleration=1.0))
    make_stats(3, overall=80)


def test_search_returns_the_parsed_query_alongside_the_results(
    client, make_player, make_vector, make_stats
):
    """The interpretation is the part worth checking: an answer with no
    visible interpretation can't be audited by whoever asked.
    """
    seed_squad(make_player, make_vector, make_stats)

    response = client.post("/api/v1/search/", json={"query": "players similar to Rodri under 25"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"]["similar_to"] == "Rodri"
    assert body["query"]["max_age"] == 25
    assert body["query"]["model_version"] == "v2_tactical"
    assert body["resolved_player_id"] == 1
    assert body["resolved_player_name"] == "Rodri"
    assert [result["name"] for result in body["results"]] == ["Young passer"]
    assert [result["rank"] for result in body["results"]] == [1]


def test_search_runs_a_query_with_no_anchor_player(
    client, make_player, make_vector, make_stats
):
    seed_squad(make_player, make_vector, make_stats)

    response = client.post("/api/v1/search/", json={"query": "fast midfielders under 25"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"]["position_group"] == "midfielders"
    assert body["query"]["traits"] == ["acceleration"]
    assert body["resolved_player_id"] is None
    assert [result["name"] for result in body["results"]] == ["Young passer"]


def test_search_rejects_input_it_cannot_parse_with_a_helpful_message(client):
    response = client.post("/api/v1/search/", json={"query": "hello world"})

    assert response.status_code == 400
    assert "Could not interpret" in response.json()["detail"]


def test_search_rejects_a_self_contradictory_query(client):
    response = client.post("/api/v1/search/", json={"query": "wingers under 30 and over 35"})

    assert response.status_code == 400
    assert "min_age" in response.json()["detail"]


def test_search_404s_when_the_named_player_does_not_exist(client, make_player):
    make_player(1, short_name="Rodri")

    response = client.post("/api/v1/search/", json={"query": "similar to Nobody McNobody"})

    assert response.status_code == 404


def test_search_422s_with_candidates_when_a_name_is_ambiguous(client, make_player):
    make_player(1, short_name="Silva", position="CM", club_name="Club A")
    make_player(2, short_name="Silva", position="ST", club_name="Club B")

    response = client.post("/api/v1/search/", json={"query": "similar to Silva"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "player_id 1" in detail and "player_id 2" in detail


def test_search_rejects_an_empty_query_body(client):
    response = client.post("/api/v1/search/", json={"query": ""})

    # Caught by the request schema's min_length before the parser runs.
    assert response.status_code == 422


def test_search_does_not_execute_sql_embedded_in_the_query_text(
    client, make_player, make_vector, make_stats
):
    """The whole security argument in one request: the sentence contains
    SQL, and the only thing that reaches the database is the structured
    query the parser produced from the parts it recognised.
    """
    seed_squad(make_player, make_vector, make_stats)

    response = client.post(
        "/api/v1/search/",
        json={"query": "'; DROP TABLE players; -- fast midfielders under 25"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query"]["similar_to"] is None
    assert body["query"]["position_group"] == "midfielders"
    assert body["query"]["traits"] == ["acceleration"]

    # The table is still there, which the next request proves by using it.
    still_alive = client.post("/api/v1/search/", json={"query": "midfielders under 40"})
    assert still_alive.status_code == 200
    assert len(still_alive.json()["results"]) == 3
