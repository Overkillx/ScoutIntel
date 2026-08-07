def test_list_players_returns_players(client, make_player):
    make_player(1, position="ST", value_eur=1_000_000.0)
    make_player(2, position="CB", value_eur=2_000_000.0)

    response = client.get("/api/v1/players/")

    assert response.status_code == 200
    ids = {p["player_id"] for p in response.json()}
    assert ids == {1, 2}


def test_max_value_zero_is_a_real_filter_not_falsy(client, make_player):
    """?max_value=0 must be treated as 'value_eur <= 0', not as 'no filter
    provided'. `if max_value:` would be falsy for 0 and silently return
    every player instead of just the zero-value ones.
    """
    make_player(1, position="ST", value_eur=0.0)
    make_player(2, position="ST", value_eur=500_000.0)

    response = client.get("/api/v1/players/", params={"max_value": 0})

    assert response.status_code == 200
    body = response.json()
    assert [p["player_id"] for p in body] == [1]


def test_max_age_zero_style_filters_still_apply(client, make_player):
    """Same is-not-None concern for max_age, guarding against regressing
    back to truthiness checks.
    """
    make_player(1, position="ST", age=30)
    make_player(2, position="ST", age=20)

    response = client.get("/api/v1/players/", params={"max_age": 20})

    assert response.status_code == 200
    assert [p["player_id"] for p in response.json()] == [2]


def test_get_player_detail_success(client, make_player, make_stats):
    make_player(1, position="ST", value_eur=1_000_000.0)
    make_stats(1, overall=88, potential=90, pace=91.0)

    response = client.get("/api/v1/players/1")

    assert response.status_code == 200
    body = response.json()
    assert body["player_id"] == 1
    assert body["stats"]["overall"] == 88
    assert body["stats"]["pace"] == 91.0


def test_get_player_detail_without_stats_row(client, make_player):
    make_player(1, position="ST")

    response = client.get("/api/v1/players/1")

    assert response.status_code == 200
    assert response.json()["stats"] is None


def test_get_player_404_not_200_with_error_body(client):
    """A missing player must be a real 404, not HTTP 200 with an
    {"error": ...} body — a client checking response.ok would otherwise see
    success on a missing record.
    """
    response = client.get("/api/v1/players/999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Player not found"}
