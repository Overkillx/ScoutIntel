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

## Day 5 — Runnability, Docker Compose, and a real migration bug

**requirements.txt built from the import graph, not memory.** Grepped every
`import`/`from` line across `app/`, `ingest.py`, `compute_vectors.py`, and
`alembic/`, then cross-referenced against what's actually installed in the
project venv (which has been accumulating packages organically since Day 2).
Pinned to those proven-working versions rather than latest, since "installs
today" and "known to work with this code" aren't the same guarantee. Split
`pytest`/`httpx` into `requirements-dev.txt` — the production image installs
only `requirements.txt`, so a test framework never ships in the runtime
image. Verified by creating a brand-new venv (not the dev one) and importing
every module cleanly before trusting the split.

**Docker Compose: Postgres (pgvector image) + Redis + API.** Redis is
provisioned now with nothing reading or writing to it yet — caching is the
next feature, and standing up infra twice for the same service is wasted
motion. The entrypoint script waits on `pg_isready -d "$DATABASE_URL"`
before running `alembic upgrade head`, not on container-started state —
Compose's `depends_on` (even with a healthcheck) only gates container
scheduling order, not "the database inside is actually ready to accept a
connection," and those are different moments. `pg_isready` accepting a full
connection URI via `-d` meant the entrypoint didn't need `POSTGRES_HOST`/
`POSTGRES_PORT`/`POSTGRES_USER` as separate variables — `DATABASE_URL`
stays the single source of truth, same as it already is for `session.py`
and `alembic/env.py`.

**Found a real port collision while verifying the stack.** This machine
already has a local (non-Docker) Postgres bound to `127.0.0.1:5432` and
`::1:5432`. Docker's published port bound `0.0.0.0:5432` successfully —
no "address in use" error — but any client connecting to `localhost:5432`
still reached the local Postgres, not the container, because the kernel
prefers the more specific bind over the wildcard one. Caught this because
the container Postgres has a `scoutintel` role that the local one doesn't,
so the connection failed loudly ("role does not exist") instead of quietly
touching the wrong database — but on a setup where the roles happened to
match, this would have silently pointed dev traffic at the wrong Postgres.
Fixed by publishing on `5433` instead of `5432` in `.env.example`, so the
stack doesn't depend on the host having port 5432 free.

**The baseline migration was a silent no-op — caught it before it shipped.**
Spun up a genuinely empty Postgres (fresh Docker volume, not the dev
database anything had touched) and ran `alembic upgrade head` for real,
rather than trusting that three migration files existing meant three
migrations worked. It failed on the second migration —
`relation "player_stats" does not exist` — because the first migration's
`upgrade()` was literally `pass`. Root cause: Day 4 baselined Alembic
against a dev database that already had `players`/`player_stats` (created
earlier by `Base.metadata.create_all()`, per Day 2, before Alembic was
adopted). Alembic diffed the models against that database, found no
difference, and honestly emitted an empty migration — correct for that one
database's history, silently wrong as the *first* migration in anyone
else's. Fixed by replacing the empty `upgrade()`/`downgrade()` with the
actual `CREATE TABLE` statements for both tables as they existed at that
point (pre unique-constraint, which the next migration still adds
separately). Re-ran against a fresh volume afterward: all three migrations
applied cleanly, all five expected tables existed
(`players`, `player_stats`, `player_vectors`, `goalkeeper_vectors`,
`alembic_version`).

**Tests use a second database with transaction-per-test rollback, not
cleanup logic.** `tests/conftest.py` derives `scoutintel_test` from
`DATABASE_URL` (or reads `TEST_DATABASE_URL` if set), drops/recreates it
once per test session, and runs `alembic upgrade head` against it via
subprocess — so the test suite exercises the same migration path
production does, not `create_all`. Each individual test gets its own
connection with `connection.begin()`, a session bound to that connection,
and a rollback in teardown; `get_db` is overridden via
`app.dependency_overrides` to hand the app that same session. No test ever
commits, so nothing needs deleting between tests and tests can run in any
order without interfering with each other. Verified this actually works
(not just "looks right") by running the full suite twice in a row — a
leaked transaction would show up as duplicate-key errors on the second run.

**Verified over real HTTP too, not just TestClient.** `TestClient` talks to
the app in-process over ASGI — it would never have caught the port
collision above, since it never opens a real socket. After the automated
suite passed, seeded a running containerized server with a handful of rows
by hand and hit `/api/v1/players/?max_value=0`, a missing player, and
`/players/{id}/similar` (same-group hit, cross-group near-neighbor
exclusion, GK auto-routing, 422, 404) with `curl` against
`localhost:8000`. Same results as the test suite, confirming the dependency
override in tests isn't hiding something that only works in-process.

## Day 6 — Vector similarity search (pgvector)

**Two vector tables, not one.** `player_vectors` (outfield, `vector(18)`) and
`goalkeeper_vectors` (GK, `vector(6)`), rather than a single table with
nulls. pgvector columns have fixed dimensionality, and outfield/GK attribute
spaces aren't comparable — a GK's `defending_standing_tackle` is meaningless,
an outfielder has no `goalkeeping_reflexes`. Padding one schema to fit the
other would make the unused dimensions arbitrary tie-breakers in cosine
distance rather than real signal.

**Attribute selection.** Ran a pairwise correlation check over all 30
granular FC26 attributes. Dropped near-duplicates above r=0.85 — the
defending cluster (`standing_tackle` / `sliding_tackle` / `marking_awareness`
/ `interceptions`) correlated 0.95-0.98, kept one representative
(`defending_standing_tackle`). Exception: kept both `skill_dribbling` and
`skill_ball_control` despite r=0.95, because they're conceptually distinct
(beating a man vs. first touch) and dribbling is a meaningful stylistic
descriptor, not just redundant signal. Landed on 18 outfield attributes, 6 GK
attributes (all `goalkeeping_*`).

Excluded `overall`, `potential`, `value_eur` from both vectors — these are
quality/valuation judgments, not playing style. Including them would make
"similar" mean "similar tier" rather than "similar style", collapsing the
feature into a worse version of sorting by `overall`.

**Computed from the CSV directly, not from new Postgres columns.**
`player_stats` intentionally only carries the 6 aggregate attributes (Day 2).
The 30 granular attributes have exactly one consumer — this vector
computation — so `compute_vectors.py` reads `data/FC26_*.csv` itself (same
pattern as `ingest.py`) rather than the migration adding 24 columns to
`player_stats` just to be read back once. `primary_position` is still pulled
from the `players` table rather than re-derived from the CSV, so there's one
source of truth for it. Z-scoring happens at write time — the dataset is a
static snapshot, so there's no staleness cost. If this becomes a live feed,
z-scoring moves to a scheduled recompute job, but that job would still be
re-reading a fresh CSV/feed at that point, so a Postgres staging table isn't
actually skipped by adding it now — it'd just be unused until then.

**`player_id` as the primary key on both vector tables**, not a surrogate
`id`. `player_stats.id` is a documented open item — a redundant surrogate key
that should have been `player_id` — not worth a destructive migration to fix
now. New tables don't need to repeat that mistake.

**HNSW over IVFFlat for the index.** HNSW needs no separate build/train step
and pgvector's own guidance favors it for read-heavy workloads, which
matches a static snapshot with no write churn. IVFFlat needs a `lists`
parameter tuned to row count and improves with more data over time — neither
applies here. Benchmarked with `EXPLAIN ANALYZE` on the 16,343-row
`player_vectors` table: HNSW index scan took 0.766ms vs. 7.272ms for a forced
sequential scan (`enable_indexscan/bitmapscan = off`) — about 9.5x faster,
and the gap widens as the table grows since seq scan cost is linear in row
count while HNSW is sub-linear.

**Position-group filtering before ranking, not exact-position filtering.**
`GET /players/{id}/similar` filters candidates to the same group
(defenders / midfielders / attackers / goalkeepers), derived from the
existing `primary_position` field via a lookup table, rather than requiring
an exact position match. A CAM and a CM are both creative central
midfielders — filtering to exact position would exclude legitimately similar
players over a labeling technicality the vector itself is supposed to look
past.

**GK requests route automatically, not via error.** If the requested player
is a goalkeeper, the endpoint queries `goalkeeper_vectors` instead of
`player_vectors` automatically, rather than 400-ing and requiring the client
to know which table applies. The two-table split is an implementation
decision the API shouldn't leak.
