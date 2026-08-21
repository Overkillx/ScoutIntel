from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, declarative_base
from pgvector.sqlalchemy import Vector

Base = declarative_base()

class Player(Base):
    __tablename__ = "players"

    player_id = Column(Integer, primary_key=True)
    short_name = Column(String)
    long_name = Column(String)
    age = Column(Integer)
    dob = Column(String)
    nationality_name = Column(String)
    preferred_foot = Column(String)
    primary_position = Column(String)
    all_positions = Column(String)
    club_name = Column(String, nullable=True)
    league_name = Column(String, nullable=True)
    league_level = Column(Integer, nullable=True)
    club_contract_valid_until_year = Column(Integer, nullable=True)
    value_eur = Column(Float)
    wage_eur = Column(Float)
    release_clause_eur = Column(Float, nullable=True)

    stats = relationship("PlayerStats", back_populates="player", uselist=False)


class PlayerStats(Base):
    __tablename__ = "player_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.player_id"))
    overall = Column(Integer)
    potential = Column(Integer)
    pace = Column(Float, nullable=True)
    shooting = Column(Float, nullable=True)
    passing = Column(Float, nullable=True)
    dribbling = Column(Float, nullable=True)
    defending = Column(Float, nullable=True)
    physic = Column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("player_id", name="uq_player_stats_player_id"),)


    player = relationship("Player", back_populates="stats")


class PlayerVector(Base):
    __tablename__ = "player_vectors"

    player_id = Column(Integer, ForeignKey("players.player_id"), primary_key=True)
    embedding = Column(Vector(18), nullable=False)


class GoalkeeperVector(Base):
    __tablename__ = "goalkeeper_vectors"

    player_id = Column(Integer, ForeignKey("players.player_id"), primary_key=True)
    embedding = Column(Vector(6), nullable=False)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_version = Column(String, nullable=False)
    # Keyword arguments the ranking function was called with, e.g.
    # {"alpha": 0.3}. Null means "the function's own defaults". Stored so a
    # parameter sweep produces rows that are self-describing -- three
    # v2_tactical runs at different alphas are otherwise indistinguishable
    # in the API, and the alpha would only live in someone's shell history.
    model_params = Column(JSONB, nullable=True)
    dataset_name = Column(String, nullable=False)
    # SHA-256 of the relevance set's content (dataset_name + every
    # query -> relevant_ids judgment). Two runs must not be compared
    # head-to-head unless this matches -- see relevance_set_fingerprint().
    dataset_fingerprint = Column(String, nullable=False)
    k = Column(Integer, nullable=False)
    num_queries = Column(Integer, nullable=False)
    num_errors = Column(Integer, nullable=False)
    mean_precision_at_k = Column(Float, nullable=True)
    mean_recall_at_k = Column(Float, nullable=True)
    mean_ndcg_at_k = Column(Float, nullable=True)
    mean_position_consistency = Column(Float, nullable=True)
    self_similarity_violation_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    query_results = relationship(
        "EvaluationQueryResult", back_populates="run", cascade="all, delete-orphan"
    )


class EvaluationQueryResult(Base):
    __tablename__ = "evaluation_query_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    evaluation_run_id = Column(
        Integer, ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    query_player_id = Column(Integer, ForeignKey("players.player_id"), nullable=False)
    # "ok" | "player_not_found" | "unrecognized_position" | "no_vector"
    status = Column(String, nullable=False)
    precision_at_k = Column(Float, nullable=True)
    recall_at_k = Column(Float, nullable=True)
    ndcg_at_k = Column(Float, nullable=True)
    position_consistency = Column(Float, nullable=True)
    self_similarity_violation = Column(Boolean, nullable=True)
    num_relevant = Column(Integer, nullable=False, default=0)
    num_ranked = Column(Integer, nullable=False, default=0)

    run = relationship("EvaluationRun", back_populates="query_results")
