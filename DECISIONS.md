## Day 1 — Data source

Chose EA FC26 player database (Kaggle) over scraping FBref/Transfermarkt directly.
Reason: single file with bio + market value + performance attributes together,
avoiding the need to merge multiple sources and match player names across them.
Tradeoff: performance stats are FIFA-game attribute ratings (pace, shooting, passing,
etc.), not real match statistics like goals/xG. Acceptable for MVP; documented as
a known limitation. Real match data (via FBref) noted as a v2 roadmap item.

Data quality check:
- 18,405 players, 110 columns, no duplicate player_ids.
- 89 nulls in club_name/league_name/contract fields — likely free agents/players
  without a current club. Will filter these out or flag them explicitly.
- 2,062 nulls in pace/shooting/passing/dribbling/defending/physic — confirmed these
  are exactly the 2,062 goalkeepers in the dataset, who have separate goalkeeping_*
  stats instead. Not a data quality issue, expected by design given the schema.

Note: player_positions is a multi-value string (e.g. "CDM, CM", "LM, RM, LW") —
will need parsing into a primary position field for clean filtering in the API.


## Day 2 — Schema design + Postgres setup

Split player data into two tables: `players` (bio + market data — name, age,
position, club, contract, value) and `player_stats` (performance attributes —
overall, potential, pace/shooting/passing/dribbling/defending/physic).

Reason: bio data changes rarely, while stats will be recomputed and potentially
versioned over time once the vector/similarity pipeline is built (Day 6+).
Separating them now avoids painful schema migrations later when stats start
getting updated on a schedule.

Used SQLAlchemy over raw SQL for models — gives type safety, easier to evolve
schema via code, and integrates cleanly with FastAPI later.

Environment note: `python` and `pip` pointed to different installs even inside
the venv on this Mac — resolved by using `python3` explicitly for all scripts.


## Day 3 — Ingestion pipeline + FastAPI layer

Built `ingest.py` as an idempotent upsert script — checks if a player_id
already exists before inserting, updates instead of duplicating on re-run.
Verified by running it twice: first run showed "Inserted: 18405, Updated: 0",
second run showed "Inserted: 0, Updated: 18405" — confirms safe to re-run.

Built FastAPI routes under /api/v1/players — versioned from the start so
future breaking changes won't silently break existing consumers.

Used SQLAlchemy Session dependency injection (get_db) rather than a global
session — each request gets its own DB session, properly closed after,
avoiding connection leaks under concurrent requests.
