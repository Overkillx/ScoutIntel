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

## Day 7 — Extract ranking core for offline evaluation (Step 0)

**Split into `_ranked_candidates` (private) + two thin public wrappers**,
rather than one public function returning full rows that the eval harness
also calls. The offline evaluation runner only needs `list[int]` — pulling
`player_id` off full `(Player, distance)` rows it doesn't otherwise use
would make the harness depend on ORM objects and column shapes it doesn't
care about, and would mean any future column added to the API response
(e.g. a display field) silently changes what the harness receives too.
`rank_similar(db, player_id, limit) -> list[int]` is the harness-facing
contract; `get_similar_players(db, player_id, limit) -> list[(Player,
float)]` is the route-facing one. Both call the same private query so
there's exactly one place the position-group filter, GK routing, and
target-exclusion logic can drift.

**Plain exceptions, not error codes or `(result, error)` tuples.**
`PlayerNotFoundError`, `UnrecognizedPositionError`, `NoVectorError` carry no
FastAPI dependency — `app/services/similarity.py` imports nothing from
`fastapi`. The route catches each and maps it to the exact status code and
detail string the old inline version produced, so this is a pure move, not
a behavior change. This matters for Step 3: the evaluation runner calls
`rank_similar` directly and needs to distinguish "this query player has no
vector" from "this query player doesn't exist" without an HTTP status code
in the way.

**Proof of no behavior change: the existing `tests/test_similarity.py`
suite passed unchanged, twice in a row, with no edits.** Those tests go
through `TestClient` → the real route → the extracted service function, so
they exercise the full path, not just the new module in isolation. No new
tests were added for this step since it's a pure refactor — Step 1 onward
adds new tested surface area.

**Relevance set format is YAML with a required `dataset_name` field, not JSON.**
JSON has no comment syntax, and the spec calls for `short_name` alongside each
entry as a human-readable comment only -- not parsed, not validated, purely
for whoever is hand-editing the file to sanity-check they're looking at the
right player. That's only expressible in YAML. `dataset_name` lives inside
the file itself (`app/evaluation/relevance_set.yaml`) rather than being
passed by the loader's caller, so the file is self-describing and the value
used to tag an `evaluation_run` row (Step 3) can't drift from which file
actually produced it.

**Identifier strategy: `player_id` (int, the `players` PK) on both sides of
every judgment, never `short_name`.** `short_name` isn't unique (confirmed
against the FC26 dataset in Day 1's data-quality pass) and duplicate labels
sourced from name-string matching would silently misjudge which player a
label was about -- exactly the kind of error `player_id` typing catches at
load time instead of at scoring time.

**Loader fails loudly and stops entirely, rather than dropping bad entries
and continuing.** A relevance set that silently drops an unresolvable
judgment would understate or overstate a model's measured quality without
anyone noticing, which defeats the point of having an independent ground
truth at all. `RelevanceSetError` covers: malformed YAML, wrong top-level
shape, missing/empty `dataset_name` or `relevance`, non-integer ids,
duplicate query player_id (a custom YAML mapping constructor rejects this --
plain `yaml.safe_load` would silently let the second occurrence overwrite
the first), duplicate relevant player_id within one query's list, and any
referenced player_id -- query or relevant -- that doesn't exist in the DB.
The DB check runs as a single `IN (...)` query over every referenced id
across the whole file rather than one query per id.

**Seeded placeholders use real player_ids from the current dev DB** (Mbappé
231747, Haaland 239085, Bellingham 252371, etc.), not invented ones, so the
loader/format tests exercise the DB-existence check honestly. The *labels*
are still explicitly fake -- the file header and every entry are commented
`PLACEHOLDER -- not a curated judgment` -- only the ids are real. A test
(`test_seed_relevance_set_file_is_well_formed`) checks the shipped file
parses and validates, as a format regression check, not a claim the
judgments mean anything.

**NDCG uses binary relevance, gain formula documented in `metrics.py`.**
`gain(i) = 1` if the id at rank `i` is in the relevant set, else `0` (no
graded/multi-level relevance -- the relevance set format from Step 1 is
just "relevant or not," not a 1-5 scale). Discount is the standard
`1/log2(rank+1)` with `rank` 1-indexed, i.e. `1/log2(i+2)` for 0-indexed
`i`. IDCG is the DCG of the ideal ranking (all relevant ids packed into
the first `min(k, len(relevant_ids))` ranks), so NDCG saturates at 1.0
exactly when every relevant id that could appear in the top-k does.

**`precision_at_k` divides by what was actually retrieved
(`min(k, len(ranked_ids))`), not by a fixed `k`.** Position-group filtering
(Day 6) means the candidate pool for a query can be smaller than `k` --
goalkeepers are a small group in the FC26 dataset. Dividing by a fixed `k`
would structurally punish those queries for pool size, a confound that has
nothing to do with whether the ranking itself is good. `recall_at_k` and
`ndcg_at_k` don't have this issue since their denominators are already
`len(relevant_ids)` / `IDCG@k`, not `k`.

**`position_consistency` and `has_self_similarity_violation` take
different inputs than the IR metrics on purpose.** They're not scored
against the relevance set at all -- they're sanity checks on the ranking
function's own invariants (same position group in, target excluded from
its own results), independent of whether the curated labels agree with
the result. `position_consistency` reuses `POSITION_GROUPS` from
`app/services/similarity.py` as its default (single source of truth, Day 6
precedent) rather than a second copy that could drift.

## Day 8 — Evaluation runner + results schema (Step 3)

**The runner calls `rank_similar()` directly, never the HTTP endpoint or
`get_similar_players()`.** It's an offline batch job over a fixed relevance
set, not a client of the live serving path -- going through HTTP would add
a network/serialization round-trip with no benefit, and `get_similar_players()`
returns `(Player, distance)` rows shaped for the API response, not the
`list[int]` the metric functions from Step 2 take.

**Per-query errors are recorded on that query's result row, not raised.**
`rank_similar()` can raise `PlayerNotFoundError`, `UnrecognizedPositionError`,
or `NoVectorError` (Step 0); the runner catches each into a `status` field
(`player_not_found` / `unrecognized_position` / `no_vector`) instead of
aborting the run, because a hand-curated relevance set will eventually
reference a player who got dropped from a later dataset snapshot or never
had a vector computed, and one such entry shouldn't invalidate every other
query in the run. Verified with three tests, one per exception type
(`tests/test_runner.py`).

**An empty ranking is `status="ok"`, not an error.** A query player with a
real vector but zero same-group candidates (e.g. the only goalkeeper in a
small dataset) is a legitimate outcome of Day 6's position-group filtering,
not a failure -- `rank_similar()` doesn't raise for it, so the runner
doesn't treat it as one either. `num_ranked=0` is stored on the row so it's
distinguishable from "had candidates, ranked them badly" during later
analysis. The metric functions already return 0.0 / vacuous-1.0 for empty
inputs (Step 2), so no special-casing was needed to make this fall out
correctly -- verified in `test_run_evaluation_handles_empty_result_as_ok_not_an_error`.

**Aggregate means are computed over error-free queries only, excluding
errored ones -- not treated as 0.** Averaging in a 0 for a
`player_not_found` query would conflate "the model ranks badly" with "the
relevance set references a player that doesn't exist," which is a data
problem, not a model problem. If every query in a run errors, the mean
fields are `None` (nothing to average), not `0.0` -- `0.0` would misreport
"perfectly measured, scored zero" when actually nothing was measured at
all. `evaluation_runs.mean_*` columns are nullable for exactly this case.

**`dataset_fingerprint` is a SHA-256 over a canonical JSON encoding of
`{dataset_name, judgments}`** (`relevance_set_fingerprint()` in
`app/evaluation/relevance.py`) -- sorted keys, sorted relevant-id lists, no
whitespace, so it depends only on the actual judgments, not YAML
formatting/comments/key order. Stored on every `evaluation_runs` row so a
later v1-vs-v2 comparison can assert both runs were scored against
byte-identical ground truth before treating a metric delta as meaningful;
two runs with different fingerprints must never be compared head-to-head.

**`model_version` is an opaque caller-supplied string** (e.g. `"v1_vector"`),
never derived from code introspection (git SHA, module path, etc.) --
there's no such thing yet to introspect for a "v2 tactical-fit" model that
doesn't exist, and a human-chosen label stays meaningful across whatever
v2 turns out to be, which a derived identifier can't promise.

**`evaluation_query_results.evaluation_run_id` is `ON DELETE CASCADE`**,
the first `ondelete` used in this schema -- per-query rows have no
independent lifetime from their run, so deleting a run (e.g. purging a bad
test run) shouldn't require separately deleting its ~dozens of query rows
first. Every other FK in the schema (e.g. `player_stats.player_id ->
players.player_id`) stays plain/RESTRICT, matching existing convention,
since those aren't pure parent-child relationships.

**Migration verified against a genuinely empty, throwaway database**
(`scoutintel_migration_check`, dropped and recreated fresh, confirmed
`\dt` showed zero tables beforehand) -- not the dev DB, and not inferred
from a green test suite. Ran the full chain `alembic upgrade head` from
`<base>` (all four migrations, including the pre-existing baseline and
vector-table ones) and confirmed both new tables, every column, the FK
`ON DELETE CASCADE`, and the new index exist via `\d`. Then
`alembic downgrade -1` and confirmed via `\dt` that both tables were
actually gone (not a no-op), then re-ran `upgrade head` to confirm the
round trip. This is the exact check the Day 5 baseline-migration bug
(silent no-op `upgrade()`, see that entry) skipped by trusting a green
test suite against a dev DB that already had the tables.

**Migration was hand-edited after `alembic revision --autogenerate`, not
used as generated.** The autogenerate diff correctly found the two new
tables, but also proposed *dropping* the HNSW indexes on
`player_vectors`/`goalkeeper_vectors` (Day 6) as a false positive --
those indexes were created via raw `op.execute()` in migration
`e2072121286f`, never represented as SQLAlchemy `Index` objects in the ORM
models, so Alembic's metadata diff read their absence from the models as
"removed." Applying the draft as-is would have silently dropped a
production index on an unrelated migration. Local Postgres has the
`vector` extension available (`pg_available_extensions` confirmed
`vector 0.8.5`), so this was caught by inspection before it became a
"works on my machine, breaks in prod" surprise -- flagging it here as the
kind of thing autogenerate output must always be read line-by-line, not
applied on trust, matching the Day 5 precedent.

## Day 9 — Celery dispatch layer (Step 4)

**`get_db` moved to `app/db/session.py` before anything else, as a
prerequisite, not a Step 4 feature.** It previously lived only in
`app/api/routes/players.py`; the new `evaluations` router needs the exact
same dependency object, not a second identical-looking function, because
`tests/conftest.py` overrides it by function identity
(`app.dependency_overrides[get_db] = ...`) -- two separately-defined
`get_db` functions would mean requests through the evaluations router
silently used the real `SessionLocal()` instead of the test's
transactional session, defeating isolation without any visible error.
Verified by re-running the full suite immediately after the move (56/56,
same count as before) before writing any Step 4 code on top of it.

**Celery is a dispatch layer only -- `run_evaluation()` and
`persist_evaluation_result()` (Step 3) are unchanged.**
`app/tasks/evaluation.py`'s `run_evaluation_task` does nothing but open a
session, call those two functions, commit, and return the id. This is the
literal requirement ("if async wiring fights us, the synchronous system is
still the deliverable"), but it's also why Step 3 was buildable and testable
before any broker existed at all.

**The task opens its own `SessionLocal()` rather than accepting an
injected session.** It runs inside a separate worker process with no
request lifecycle to borrow a session from -- unlike a FastAPI route, there
is no `Depends(get_db)` to receive. It uses the exact same `SessionLocal`
the API process does (`app/db/session.py`), so a task run by the worker and
a request handled by the API behave identically with respect to the DB,
not two divergent code paths that happen to look similar.

**Broker/backend URLs are `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` env
vars, defaulting to `redis://localhost:6379/0`** -- same pattern
`DATABASE_URL` already uses, never hardcoded. Both point at the same Redis
instance (one broker + one result backend) rather than provisioning a
second service, since current usage is a single small queue with no
reason to split them. Added to `.env.example` with the same
"host is the compose service name" caveat `DATABASE_URL` already carries.

**`include=["app.tasks.evaluation"]` on the `Celery()` constructor, not
`autodiscover_tasks()`.** This app has no `__init__.py` anywhere (every
package here is an implicit namespace package -- confirmed by `find app
-name __init__.py` turning up only the two files added in Steps 1-2 for
unrelated reasons). `autodiscover_tasks()`'s default convention is
"import `<package>.tasks`" for each package in its argument list, which
assumes a regular package structure that can carry side-effecting imports
in `__init__.py`; verified live that `autodiscover_tasks(["app.tasks"])`
registered zero tasks (`celery_app.tasks` came back without
`evaluation.run` even after `.finalize()`), while `include=[...]` -- the
mechanism actually documented for this -- worked immediately. Verified for
real, not just in-process: booted the actual worker CLI
(`celery -A app.core.celery_app worker`) against a local Redis and confirmed
`evaluation.run` in its `[tasks]` startup banner.

**The API returns a Celery task id from `POST /evaluations/run`, not an
`evaluation_runs.id`** -- the row doesn't exist yet at the moment the HTTP
response is sent (the task hasn't run), so there's nothing to reference
except the dispatch handle itself. `GET /evaluations` (list, most-recent-
first) is how a caller finds the resulting row once the task completes;
Celery's own result backend (keyed by task id) is available for polling
task status/return value directly, but wiring a `GET /evaluations/tasks/{id}`
convenience endpoint on top of it wasn't asked for and was left out --
narrower than adding speculative surface area.

**Endpoint tests never touch a live broker; the task's own logic never
runs against the real DB.** Two different fakes for two different
concerns: `POST /evaluations/run` is tested by monkeypatching
`run_evaluation_task.delay` to a stub that records its kwargs and returns a
fake id -- this proves the endpoint's own wiring (request parsing, kwargs
passed through correctly, response shape) without asserting anything about
Celery/Redis being reachable, exactly like `run_evaluation()` let Step 3 be
tested without a live DB dependency on the *task* layer. Separately, the
task's actual logic (`test_evaluation_task.py`) is exercised by calling
`run_evaluation_task(...)` directly (a Celery task object runs its
underlying function synchronously in-process when called this way, no
broker involved) with `SessionLocal` monkeypatched to return the test's
transactional `db_session` -- but with `commit()` aliased to `flush()`,
because the real task calls `db.commit()` (correct for production;
Step 3's `persist_evaluation_result()` deliberately doesn't commit for
exactly this reason) and a real commit on `db_session` would end the
outer transaction the whole test suite's rollback-based isolation depends
on. `flush()` makes the write visible within the same session without
that.

**Verified for real over live HTTP + a live worker + live Redis, not just
`TestClient`, matching the Day 5 precedent.** Started `uvicorn` and
`celery worker` against local Redis, `POST`ed a real run, watched the
worker log show `Task evaluation.run[...] succeeded`, then confirmed both
`GET /evaluations/` and `GET /evaluations/{id}` returned it correctly
(list has no `query_results` field, detail does). This caught a real gap
the automated suite couldn't have: the local dev database
(`postgresql://localhost/scoutintel`) had never had `alembic upgrade head`
run against it for the Day 8 migration (only the throwaway verification DB
and the pytest test DB had it), so the first live dispatch failed with
`UndefinedTable: evaluation_runs` inside the worker. Ran the migration
against the dev DB, re-dispatched, confirmed success, then deleted the
one smoke-test row afterward (`ON DELETE CASCADE` took its
`evaluation_query_results` row with it) so no fake data was left behind.


## Day 10 — v2 trait-weighted similarity + honest v1/v2 comparison

**`MODEL_REGISTRY` (`app/evaluation/runner.py`) maps `model_version` ->
ranking function, resolved once per `run_evaluation()` call rather than
per query.** `{"v1_vector": rank_similar, "v2_tactical": rank_similar_v2}`.
An unrecognized `model_version` raises `UnknownModelVersionError`
immediately, before any query runs, instead of failing query-by-query or
silently defaulting -- a typo in the label should fail loud and fast, not
produce a partial or misleading result set. `_score_query()` takes the
resolved function as a parameter rather than looking it up itself, so the
registry lookup and its failure mode live in exactly one place.

**`rank_similar_v2` is a separate function, not a parameterized `rank_similar`.**
v1 stays byte-for-byte unchanged (verified: existing test suite green,
unmodified, after v2 landed) -- a shared parameterized implementation would
have meant every future change to v2's ranking logic carries a chance of
perturbing v1's behavior, defeating the entire point of having a stable
baseline to score v2 against.

**Trait weighting blends two sources, each independently min-max
normalized to [0, 1] BEFORE blending -- not after.** `final_weight[i] =
alpha * norm(position_baseline[i]) + (1 - alpha) * norm(player_strengths[i])`.
The position baseline (`POSITION_GROUP_TRAIT_WEIGHTS`) is small hand-set
integers (1-3, documented as "low/high relevance" proxies, not fit or
learned); player strengths come from the query player's own 18-dim
z-scored embedding, which is unbounded and signed. Blending those two
raw scales directly would make `alpha` meaningless -- a raw z-score of
2.5 would dominate a raw baseline score of 3 for reasons that have
nothing to do with `alpha`'s intended 50/50 (or whatever ratio) split.
Normalizing each source to [0, 1] independently first means `alpha` is
actually the mixing ratio it claims to be. Player strengths specifically
use min-max, not the z-scores directly, because z-scores can be negative
and a negative *weight* is meaningless in a weighted cosine distance --
min-max maps each player's own weakest dim to 0 and strongest to 1,
preserving relative order without sign problems. Degenerate (all-equal)
input to either source maps to uniform weight 1.0 rather than 0 --
collapsing to zero would silently zero out that entire term of the blend
whenever a population happened to be flat on some dim, rather than
falling back to "no information, weight everything equally."

**Outfield position filter is relaxed to the whole non-GK pool; the GK
wall stays hard by routing through the same private `_ranked_candidates()`
helper v1 uses, unweighted.** Not a design choice for GKs -- the GK vector
space is 6-dim (`goalkeeping_diving/handling/kicking/positioning/reflexes/speed`,
see Day 6/`compute_vectors.py`) and isn't comparable to the 18-dim outfield
space at all, so there's no trait-weighting scheme defined for it and none
was invented. Reusing `_ranked_candidates()` for the v2 GK branch (rather
than duplicating the GK query) means the two functions can never drift
apart on GK behavior.

**Relevance set (`curated_v1`) replaces the 3-entry placeholder with 10
hand-curated query players and 27 relevant-player judgments**, resolved
from football knowledge to `player_id`s in the dev DB (ambiguous surnames
-- Bellingham, Vitinha, Laporte, Saliba, Gabriel -- confirmed one at a time
rather than guessed). Documented in the file itself as one person's
subjective judgment, not a benchmark, and flagged as leaning toward
same-position/same-role similarity -- relevant, since v2's whole design
premise is deviating from strict same-position similarity on purpose.

**Honest v1 vs v2 result (k=10, synchronous `run_evaluation()`, no Celery,
default `alpha=0.5`, no tuning after seeing the numbers):**

| metric | v1_vector | v2_tactical |
|---|---|---|
| mean Precision@10 | 0.0600 | 0.0900 |
| mean Recall@10 | 0.2667 | 0.3500 |
| mean NDCG@10 | 0.1688 | 0.2469 |
| mean position_consistency | 1.0000 | 0.8100 |
| errors / self-similarity violations | 0 / 0 | 0 / 0 |

v2 outperformed v1 on every relevance metric (Precision/Recall/NDCG@10)
against `curated_v1` and lost on `position_consistency`, as expected by
construction -- v1's hard position-group filter makes that metric
~tautologically 1.0, it doesn't reflect ranking quality, just that the
filter ran. See `LEARNINGS.md` Day 10 for why the relevance-metric result
went the opposite direction from the pre-registered expectation ("v2 may
score worse, and that's fine") -- short version: this particular curated
set turned out not to be strictly same-position, which structurally
favors v1 in a way that has nothing to do with v2's actual quality. No
weights or alpha were changed after seeing this result.
