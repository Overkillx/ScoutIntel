import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.routes.players import get_db
from app.db.models import GoalkeeperVector, Player, PlayerStats, PlayerVector
from app.main import app

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _test_database_url() -> str:
    """Explicit TEST_DATABASE_URL wins. Otherwise derive from DATABASE_URL by
    appending _test to the db name, so tests never touch the dev database.
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    parts = urlsplit(os.environ["DATABASE_URL"])
    db_name = parts.path.lstrip("/") + "_test"
    return urlunsplit((parts.scheme, parts.netloc, f"/{db_name}", parts.query, parts.fragment))


def _maintenance_url(url: str) -> str:
    """Postgres won't let you DROP/CREATE a database you're connected to, so
    connect to the always-present `postgres` db to run those statements.
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))


TEST_DATABASE_URL = _test_database_url()


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Create a fresh test database once per test session and run real
    migrations against it (subprocess, not create_all) so the test suite
    exercises the same migration path production does.
    """
    db_name = urlsplit(TEST_DATABASE_URL).path.lstrip("/")

    admin_engine = create_engine(_maintenance_url(TEST_DATABASE_URL), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        check=True,
    )

    yield TEST_DATABASE_URL


@pytest.fixture(scope="session")
def test_engine(test_database):
    engine = create_engine(test_database)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    """Each test gets its own connection + transaction, rolled back on
    teardown — isolation without touching the dev DB or needing per-test
    cleanup logic.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    """TestClient wired to the same transactional session as the test, so
    data seeded in the test is visible to the request and vice versa.
    """

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def make_player(db_session):
    def _make(player_id, position="CM", value_eur=100000.0, **kwargs):
        defaults = dict(short_name=f"Player {player_id}", age=25, preferred_foot="Right")
        defaults.update(kwargs)
        player = Player(
            player_id=player_id,
            primary_position=position,
            value_eur=value_eur,
            **defaults,
        )
        db_session.add(player)
        db_session.flush()
        return player

    return _make


@pytest.fixture()
def make_stats(db_session):
    def _make(player_id, **kwargs):
        defaults = dict(overall=75, potential=80)
        defaults.update(kwargs)
        stats = PlayerStats(player_id=player_id, **defaults)
        db_session.add(stats)
        db_session.flush()
        return stats

    return _make


@pytest.fixture()
def make_vector(db_session):
    def _make(player_id, embedding, goalkeeper=False):
        model = GoalkeeperVector if goalkeeper else PlayerVector
        vector = model(player_id=player_id, embedding=embedding)
        db_session.add(vector)
        db_session.flush()
        return vector

    return _make
