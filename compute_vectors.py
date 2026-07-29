import pandas as pd
from app.db.session import SessionLocal
from app.db.models import Player, PlayerVector, GoalkeeperVector

OUTFIELD_ATTRIBUTES = [
    "attacking_crossing", "attacking_finishing", "attacking_short_passing",
    "skill_long_passing", "skill_ball_control", "skill_dribbling",
    "movement_acceleration", "movement_agility", "movement_balance",
    "movement_reactions", "power_shot_power", "power_jumping",
    "power_stamina", "power_strength", "mentality_aggression",
    "mentality_vision", "mentality_composure", "defending_standing_tackle",
]

GOALKEEPER_ATTRIBUTES = [
    "goalkeeping_diving", "goalkeeping_handling", "goalkeeping_kicking",
    "goalkeeping_positioning", "goalkeeping_reflexes", "goalkeeping_speed",
]


def compute_vectors(csv_path="data/FC26_20250921.csv", session=None):
    """Compute z-scored attribute vectors and upsert into player_vectors /
    goalkeeper_vectors. Isolated from __main__, and takes an optional
    session, so this can be wrapped in a Celery task later without
    restructuring.
    """
    owns_session = session is None
    session = session or SessionLocal()

    df = pd.read_csv(csv_path, low_memory=False)

    # primary_position comes from the players table (parsed once by
    # ingest.py), not re-derived from the CSV, so there's one source of
    # truth for it. Players not yet ingested are dropped rather than
    # guessed at.
    positions = dict(session.query(Player.player_id, Player.primary_position).all())
    unmapped = (~df["player_id"].isin(positions.keys())).sum()
    if unmapped:
        print(f"Skipping {unmapped} players not found in players table (run ingest.py first)")
    df = df[df["player_id"].isin(positions.keys())].copy()
    df["primary_position"] = df["player_id"].map(positions)

    gk_mask = df["primary_position"] == "GK"

    outfield_written = _write_vectors(session, df[~gk_mask], OUTFIELD_ATTRIBUTES, PlayerVector)
    gk_written = _write_vectors(session, df[gk_mask], GOALKEEPER_ATTRIBUTES, GoalkeeperVector)

    session.commit()
    if owns_session:
        session.close()

    print(f"Done. Outfield vectors: {outfield_written}, GK vectors: {gk_written}")
    return outfield_written, gk_written


def _write_vectors(session, df, attributes, model):
    """Z-score `attributes` across `df` (the relevant population) and
    upsert one vector row per player.
    """
    valid = df.dropna(subset=attributes)
    skipped = len(df) - len(valid)
    if skipped:
        print(f"Skipped {skipped} rows for {model.__tablename__}: missing attribute values")

    means = valid[attributes].mean()
    stds = valid[attributes].std(ddof=0)
    zscored = (valid[attributes] - means) / stds

    written = 0
    for player_id, row in zip(valid["player_id"], zscored.itertuples(index=False)):
        player_id = int(player_id)
        embedding = [float(v) for v in row]

        existing = session.query(model).filter_by(player_id=player_id).first()
        if existing:
            existing.embedding = embedding
        else:
            session.add(model(player_id=player_id, embedding=embedding))
        written += 1

    return written


if __name__ == "__main__":
    compute_vectors()
