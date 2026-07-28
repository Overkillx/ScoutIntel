import pandas as pd
from app.db.session import SessionLocal
from app.db.models import Player, PlayerStats

def parse_primary_position(positions_str):
    """FC26 gives positions like 'CDM, CM' — take the first as primary."""
    return positions_str.split(',')[0].strip()

def load_data(csv_path="data/FC26_20250921.csv"):
    df = pd.read_csv(csv_path, low_memory=False)

    session = SessionLocal()
    inserted, updated = 0, 0

    for _, row in df.iterrows():
        player_id = int(row["player_id"])

        existing = session.query(Player).filter_by(player_id=player_id).first()

        primary_position = parse_primary_position(row["player_positions"])

        if existing:
            player = existing
            updated += 1
        else:
            player = Player(player_id=player_id)
            inserted += 1

        player.short_name = row["short_name"]
        player.long_name = row["long_name"]
        player.age = int(row["age"])
        player.dob = row["dob"]
        player.nationality_name = row["nationality_name"]
        player.preferred_foot = row["preferred_foot"]
        player.primary_position = primary_position
        player.all_positions = row["player_positions"]
        player.club_name = row["club_name"] if pd.notna(row["club_name"]) else None
        player.league_name = row["league_name"] if pd.notna(row["league_name"]) else None
        player.league_level = int(row["league_level"]) if pd.notna(row["league_level"]) else None
        player.club_contract_valid_until_year = (
            int(row["club_contract_valid_until_year"])
            if pd.notna(row["club_contract_valid_until_year"]) else None
        )
        player.value_eur = float(row["value_eur"]) if pd.notna(row["value_eur"]) else None
        player.wage_eur = float(row["wage_eur"]) if pd.notna(row["wage_eur"]) else None
        player.release_clause_eur = (
            float(row["release_clause_eur"]) if pd.notna(row["release_clause_eur"]) else None
        )

        session.add(player)
        session.flush()  # ensures player_id is available for stats

        stats = session.query(PlayerStats).filter_by(player_id=player_id).first()
        if not stats:
            stats = PlayerStats(player_id=player_id)

        stats.overall = int(row["overall"])
        stats.potential = int(row["potential"])
        stats.pace = float(row["pace"]) if pd.notna(row["pace"]) else None
        stats.shooting = float(row["shooting"]) if pd.notna(row["shooting"]) else None
        stats.passing = float(row["passing"]) if pd.notna(row["passing"]) else None
        stats.dribbling = float(row["dribbling"]) if pd.notna(row["dribbling"]) else None
        stats.defending = float(row["defending"]) if pd.notna(row["defending"]) else None
        stats.physic = float(row["physic"]) if pd.notna(row["physic"]) else None

        session.add(stats)

    session.commit()
    session.close()
    print(f"Done. Inserted: {inserted}, Updated: {updated}")

if __name__ == "__main__":
    load_data()
