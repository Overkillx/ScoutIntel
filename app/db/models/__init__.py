from sqlalchemy import Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import relationship, declarative_base

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

    player = relationship("Player", back_populates="stats")
