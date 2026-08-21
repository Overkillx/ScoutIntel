import logging
import os

from sqlalchemy import create_engine, event
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

# pgvector's HNSW index is approximate, and a filtered nearest-neighbour
# query -- which every similarity query here is, since candidates are
# restricted by position group -- can quietly return FEWER than LIMIT rows:
# the index scan walks the graph, most of the nearest entries it finds
# don't satisfy the filter (or aren't visible to this transaction), and it
# stops with a short result rather than an error. Turning on iterative scan
# makes it keep going until it has LIMIT usable rows. strict_order, not
# relaxed_order, because this is a ranking system: results must come back
# in true distance order, not merely be the right set.
#
# Requires pgvector >= 0.8.0. On an older server the parameter doesn't
# exist; that's a degradation (back to possibly-short results), not a
# corruption, so it's logged loudly rather than raised -- refusing to open
# a connection would take down an app that worked fine before.
HNSW_ITERATIVE_SCAN = "strict_order"


def configure_engine(engine):
    """Apply ScoutIntel's per-connection Postgres settings to `engine`.

    Called explicitly rather than registered against SQLAlchemy's Engine
    class globally, so it only touches engines that actually talk to a
    ScoutIntel database -- the test suite also opens a maintenance
    connection to the `postgres` database (to DROP/CREATE the test DB),
    where the vector extension isn't loaded at all.
    """

    @event.listens_for(engine, "connect")
    def _set_hnsw_iterative_scan(dbapi_connection, connection_record):
        try:
            with dbapi_connection.cursor() as cursor:
                cursor.execute(f"SET hnsw.iterative_scan = {HNSW_ITERATIVE_SCAN}")
            # SET is transactional in Postgres: without committing it here,
            # the first ROLLBACK on this connection reverts it -- including
            # the rollback SQLAlchemy's pool issues when a connection is
            # returned, and the per-test rollback in conftest.py. The
            # setting would then silently apply only until something first
            # rolled back.
            dbapi_connection.commit()
        except DatabaseError as exc:
            dbapi_connection.rollback()
            logger.warning(
                "Could not set hnsw.iterative_scan (pgvector >= 0.8.0 required): %s. "
                "Filtered similarity queries may return fewer than `limit` rows.",
                exc,
            )

    return engine


engine = configure_engine(create_engine(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
