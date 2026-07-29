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

## Day 4 — Code review fixes + migration tooling

Reviewed the existing code and found four bugs. Fixed them one at a time
rather than in a single sweep, so each one is understood rather than just
applied.

**Null handling in ingest.** `value_eur` and `wage_eur` were cast with a bare
`float()` while every neighbouring field guarded with `pd.notna`. Checked the
data first: zero nulls in both columns today, so this was latent, not live.
Fixed anyway — `ingest.py` will run on a schedule against future data (FC27,
or the FBref merge on the v2 roadmap), and a NaN inserts into Postgres
silently, then loses every comparison it appears in. A player with NaN value
would vanish from a `max_value` filter without appearing in the complement,
and one NaN poisons any league-level average. Cheap guard now, no stack trace
later.

**Zero-as-sentinel in the source data.** While checking the above: 89 players
have `wage_eur == 0` — the same 89 with null `club_name` from Day 1, i.e. free
agents with nobody paying them. But 109 have `value_eur == 0`, so 20 players
have a club and a wage yet no market value. Cristiano Ronaldo (age 40, null
club) is one of them. EA is using `0` where NULL would be correct. Decided to
preserve their `0` as-is rather than normalising, because converting would
misrepresent the source; documented here so the valuation model doesn't later
rank these players as bargains. Revisit if undervalued-player detection starts
surfacing them.

**Adopted Alembic instead of hand-editing schema.** Day 2 justified splitting
`players`/`player_stats` on the grounds that stats would be recomputed and
versioned over time. That reasoning doesn't survive the first schema change
happening by hand in `psql` with no trace in git. Also relevant: `create_all`
only creates tables that don't exist — it will not alter an existing one, so
adding a constraint to the model would have silently diverged from the live
schema. Baselined against the existing database, which produced an empty first
migration (models and schema already agreed). Doing this now means the pgvector
column is the second migration rather than a special one-off.

**Unique constraint on `player_stats.player_id`.** The model declared
`uselist=False` — one stats row per player — but nothing in the database
enforced it, so a partially failed ingest could have produced duplicates and
SQLAlchemy would have silently returned whichever came back first. Checked for
existing duplicates first (zero), then added the constraint via migration.
Named it `uq_player_stats_player_id` explicitly rather than letting Postgres
auto-assign, so the name is deterministic and in git. Kept the surrogate `id`
primary key for now — it's redundant, since `player_id` would serve as the PK,
but dropping a column is a destructive change and not worth bundling into this
migration. Noted as a follow-up.

**API correctness.** Optional numeric filters used `if max_age:` /
`if max_value:`, which treat `0` as "not provided" — so `?max_value=0` dropped
the filter and returned every player instead of the 109 zero-value ones.
Changed to `is not None`. Separately, `get_player` returned
`{"error": "Player not found"}` with HTTP 200, so a client checking
`response.ok` would see success on a missing record. Now raises
`HTTPException(404)`.

## Open items (known, deferred)

- `player_stats.id` is a redundant surrogate key — `player_id` would serve as
  the primary key. Dropping it is destructive; deferred.
- `ingest.py` upserts row by row (~37k queries for 18,405 players). Should be
  a bulk `on_conflict_do_update` before the nightly scheduler calls it.
- No `requirements.txt` — the repo can't currently be run from a clone.
- API routes build response dicts by hand; no Pydantic response models.

## Day 4 — Repositioning after data-premise review

Ran a correlation check: value_eur vs overall (r=0.55), potential (r=0.50),
age (r=0.02). Confirms value_eur is meaningfully but not fully derived from
these attributes (~25-30% variance explained) — real circularity risk for a
"stats vs value = undervalued" claim, but not total circularity.

Decision: dropped "undervalued player detection" as the headline feature.
Reframed the project around similarity search, which has no dependency on
value_eur and is fully defensible. Value-relative ranking may be added later
as an explicitly caveated secondary signal, not a market-inefficiency claim.

Also cut roadmap scope: prioritizing pgvector similarity + Docker Compose +
tests over adding more partially-wired features (Celery beat, caching, rate
limiting, NL search, AI reports) — a smaller complete system is a stronger
portfolio artifact than a wider incomplete one.
