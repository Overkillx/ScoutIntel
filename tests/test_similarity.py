def outfield_vector(first, rest=0.0):
    return [first] + [rest] * 17


def gk_vector(first, rest=0.0):
    return [first] + [rest] * 5


def test_similarity_filters_by_position_group_not_just_distance(client, make_player, make_vector):
    """A cross-group player can be numerically closer in cosine distance
    than a same-group player, and must still be excluded — position-group
    filtering happens before ranking, not as a tiebreaker after it.
    """
    make_player(1, position="CM")
    make_vector(1, outfield_vector(1.0))

    make_player(2, position="CAM")  # same group (midfielders), far vector
    make_vector(2, outfield_vector(-1.0))

    make_player(3, position="ST")  # different group (attackers), near-identical vector
    make_vector(3, outfield_vector(0.999, rest=0.001))

    response = client.get("/api/v1/players/1/similar")

    assert response.status_code == 200
    ids = [p["player_id"] for p in response.json()]
    assert ids == [2]


def test_similarity_excludes_the_target_player(client, make_player, make_vector):
    make_player(1, position="CM")
    make_vector(1, outfield_vector(1.0))

    make_player(2, position="CM")
    make_vector(2, outfield_vector(1.0))  # identical vector, distance 0

    response = client.get("/api/v1/players/1/similar")

    assert response.status_code == 200
    ids = [p["player_id"] for p in response.json()]
    assert 1 not in ids
    assert ids == [2]


def test_similarity_404_when_target_has_no_vector(client, make_player):
    make_player(1, position="CM")  # no PlayerVector row

    response = client.get("/api/v1/players/1/similar")

    assert response.status_code == 404


def test_similarity_404_when_player_does_not_exist(client):
    response = client.get("/api/v1/players/999999/similar")

    assert response.status_code == 404


def test_similarity_422_for_unrecognized_position(client, make_player, make_vector):
    make_player(1, position="SW")  # not in POSITION_GROUPS
    make_vector(1, outfield_vector(1.0))

    response = client.get("/api/v1/players/1/similar")

    assert response.status_code == 422


def test_similarity_routes_goalkeepers_to_goalkeeper_vector_table(client, make_player, make_vector):
    make_player(1, position="GK")
    make_vector(1, gk_vector(1.0), goalkeeper=True)

    make_player(2, position="GK")
    make_vector(2, gk_vector(0.9, rest=0.1), goalkeeper=True)

    # An outfield player who happens to share player_id-adjacent data in
    # player_vectors should never surface for a GK query.
    make_player(3, position="ST")
    make_vector(3, outfield_vector(1.0), goalkeeper=False)

    response = client.get("/api/v1/players/1/similar")

    assert response.status_code == 200
    ids = [p["player_id"] for p in response.json()]
    assert ids == [2]
    assert 3 not in ids
