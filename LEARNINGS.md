# Learnings

Concepts I now understand because of building this project — for my own
revision and interview prep, separate from DECISIONS.md (which covers
project-specific choices).

## Day 1 — Data & environment
- Hidden files on Unix/Mac start with `.` (e.g. `.gitkeep`, `.DS_Store`) and
  don't show with plain `ls` — need `ls -a`.
- `.gitignore` prevents files from being tracked by git — useful for large
  datasets, secrets, and OS junk files (`.DS_Store`).
- Nulls in a dataset aren't automatically "bad data" — need to check *why*
  they're null (e.g. goalkeepers legitimately don't have outfield attributes).

## Day 2 — Schema & environment
- A venv can have `python` and `pip` pointing to different installs even
  when activated — `which python` / `which pip` shows the actual paths.
  Using `python3` explicitly avoided the mismatch.
- SQLAlchemy models map Python classes to database tables (ORM = Object-
  Relational Mapping) — lets you work with Python objects instead of
  writing raw SQL for every query.
- Splitting data into separate tables (bio vs stats) based on *how often
  each changes* / *how it's used* is a real schema design principle, not
  just organization for its own sake.

## Day 3 — Pipelines & APIs
- Idempotent means safe to run multiple times without changing the result
  beyond the first run — critical for data pipelines that might get re-run
  (retries, scheduled jobs, manual re-triggers).
- FastAPI's dependency injection (`Depends(get_db)`) automatically manages
  resource lifecycle — opens a DB session per request, closes it after,
  without manually writing try/finally in every route.
- Environment/venv mismatches (python vs python3, uvicorn run outside venv)
  are a common real-world debugging category — "why does this work in one
  terminal but not another" almost always traces back to which Python/pip
  is actually being used.
- `/docs` (FastAPI's auto-generated Swagger UI) lets you test API endpoints
  interactively without needing a frontend or tools like Postman.


## Day 4 — Data integrity, migrations, API correctness

- **Latent vs live bugs.** A bug can be real without currently firing. Worth
  checking which you have before fixing: "the dataset didn't trigger it, but
  the code path was unsafe and the pipeline is scheduled" is a more precise
  claim than implying you found a crash.
- **NaN is not NULL.** Pandas represents missing numerics as `float('nan')`,
  so `float()` doesn't raise on them. Postgres accepts NaN in a
  `double precision` column, so the insert succeeds silently. NaN then loses
  every comparison — `NaN <= x` and `NaN > x` are both false — so affected rows
  disappear from filters without appearing anywhere else. NULL is the safe
  representation: SQL aggregates skip it, so "unknown" doesn't contaminate
  "known".
- **Sentinel values vs NULL.** Storing `0` for "no data" is a real design
  error, not a style choice — `0` asserts a fact ("worth nothing") where NULL
  says "unknown", and `AVG` includes the former and skips the latter. Source
  data often does this, and you have to decide whether to preserve or normalise
  it.
- **`create_all` is a bootstrap tool, not a migration tool.** It creates tables
  that don't exist and does nothing to ones that do. It will never alter a
  column or add a constraint, so a model change silently diverges from the live
  schema.
- **Migrations are version control for the database.** Alembic autogenerate
  diffs your models against the live DB and writes the delta as a Python file
  committed to git. Adopting it against a database that already matches your
  models produces an empty baseline migration, which is the cheapest possible
  time to start.
- **Don't blindly trust autogenerate.** For an unnamed constraint it emits
  `op.drop_constraint(None, ...)` in `downgrade()`, which fails at runtime —
  there is no constraint called "None". Caught this by reading the generated
  file before running it. Fix: name constraints explicitly in the model.
- **Autogenerate deletes what it can't see.** It treats the models as the
  source of truth, so anything present in the database but absent from the
  models gets a `drop` in the next revision. Schema changes made by hand in
  `psql` will get reverted by the next migration.
- **Postgres has transactional DDL.** Schema changes run inside a transaction
  and roll back cleanly on failure. MySQL doesn't — a failed migration there
  can leave a half-applied schema.
- **A unique constraint is backed by a btree index.** So enforcing uniqueness
  on a foreign key also gives you the index for joins on it — a correctness fix
  that's also a performance one.
- **`is not None` vs truthiness for optional parameters.** `if x:` is false for
  `0`, `""`, and `[]`, all of which may be legitimate user input. For anything
  optional-and-numeric, test for `None` explicitly.
- **HTTP status codes are part of the API contract.** Returning an error body
  with a 200 means clients checking `response.ok` treat failure as success.
  FastAPI's `HTTPException` sets the status properly.
- **`.env` is read by Python, not by the shell.** `load_dotenv()` populates the
  Python process's environment; zsh never sees those variables, so
  `psql "$DATABASE_URL"` gets an empty string and falls back to connecting to a
  database named after your user.
- **Framework-generated files encode ordering assumptions.** In Alembic's
  `env.py`, `config` doesn't exist until `config = context.config` runs, so
  inserting config code near the imports raises `NameError`. Read what the
  template is doing before adding to it.

## Day 5 — Runnability, Docker, and testing

- **An autogenerated migration is only as trustworthy as the database it was
  diffed against.** Alembic's autogenerate compares models to a live
  database and writes the delta. If that database already matches the
  models (e.g. via `create_all` before Alembic existed), the delta is empty
  — which is *correct* for that database, but wrong as a "baseline" for
  anyone starting from zero. The fix isn't a smarter tool, it's actually
  running the migration against an empty database before trusting it —
  the tool can't know your dev database wasn't empty when you baselined it.
- **`depends_on` (even with a Docker healthcheck) answers "is the container
  running," not "is the thing inside it ready."** Postgres's process can
  be up and accepting TCP connections before it's finished initializing, or
  a healthcheck can pass moments before real readiness. `pg_isready` inside
  the entrypoint checks the actual thing the next step depends on, rather
  than a proxy for it.
- **`pg_isready` takes a full connection string via `-d`,** not just a bare
  database name — so a wait-loop can reuse the exact same `DATABASE_URL`
  the app itself uses, instead of duplicating host/port/user as separate
  variables that can drift out of sync with it.
- **Two sockets can bind the "same" port without erroring**, if one binds a
  specific address (`127.0.0.1`) and the other binds the wildcard
  (`0.0.0.0`) — the kernel prefers the more specific match per connection.
  Practically: `localhost:5432` is not guaranteed to mean "the Postgres I
  just started," if something else already had that exact address bound.
  `docker ps` showing the port mapped doesn't rule this out.
- **Docker Compose's `env_file:` and the `--env-file` CLI flag are not the
  same mechanism.** `--env-file` only controls `${VAR}` substitution while
  parsing the compose YAML itself; a service's `env_file:` entry is a
  separate, literal file path (default `.env` in the project directory)
  read to populate that container's environment. Pointing `--env-file` at
  a different file does not change what a service's `env_file: - .env`
  loads.
- **The standard way to isolate a test from a shared database is one
  connection + one transaction per test, rolled back at the end** — not
  deleting rows after each test. Bind a `sessionmaker` to a single
  `Connection` that already has `begin()` called on it; every write in the
  test happens inside that one transaction; `rollback()` in teardown erases
  it completely regardless of what the test did. Nothing can be left behind
  because nothing was ever committed.
- **`DROP DATABASE` needs two things a normal query doesn't: to not be
  connected to the database you're dropping, and to not be inside a
  transaction.** Postgres refuses both. In practice this means a
  maintenance connection to a different database (conventionally
  `postgres`) with `isolation_level="AUTOCOMMIT"`.
- **FastAPI's `app.dependency_overrides` swaps a dependency for the whole
  app, not per-request** — assign a replacement function to the same key
  the route depends on (`get_db`), and every request through that
  `TestClient` gets the replacement until it's cleared. This is what makes
  the transaction-per-test pattern reachable from HTTP-level tests instead
  of only from direct function calls.
- **In-process test clients (FastAPI's `TestClient`) talk ASGI, not real
  sockets** — they exercise routing, dependency injection, and response
  serialization, but never touch an actual TCP port. Bugs that live at the
  networking/infra layer (like the port-collision above) are invisible to
  them by construction, which is the actual justification for also
  smoke-testing over real HTTP against a running container, not just
  "more confidence."

## Day 6 — Vector search

- **Z-scoring vs. correlation are different questions.** Correlation
  (attribute selection) asks "does this dimension carry information the
  others don't already carry" — high correlation means redundant signal.
  Z-scoring (normalization) asks "are these dimensions on comparable scales"
  — without it, an attribute with a wider raw range would dominate cosine
  distance for reasons that have nothing to do with actual similarity.
  Both matter, and they're solving different problems.
- **Cosine distance vs. cosine similarity.** Similarity ranges -1 to 1 (1 =
  identical direction). pgvector's `<=>` operator returns distance, i.e.
  `1 - similarity` for normalized vectors, so 0 = identical and it sorts
  ascending for "most similar first" — no need to flip the sort order or
  negate anything.
- **A vector index changes ordering strategy, not correctness.** Without an
  index, "nearest 10" is exact: compute distance to every row, sort, take the
  top N. HNSW is approximate nearest-neighbor — it trades a small chance of
  missing the true nearest point for skipping most of the table. For ~16K
  rows the accuracy cost is negligible and the speed difference is already
  measurable (0.766ms vs 7.272ms via `EXPLAIN ANALYZE`); the gap would matter
  more at larger scale, not less.
- **HNSW vs. IVFFlat is a build-cost/query-cost tradeoff, not "always pick
  the newer one."** IVFFlat clusters vectors into lists at index-build time
  and needs enough existing data to cluster well; HNSW builds a navigable
  graph incrementally with no training phase. The right choice depends on
  whether the data is static or growing, not which algorithm is newer.
- **`SET enable_indexscan = off` is a real technique for isolating index
  impact**, not just a curiosity — it forces the planner to consider only
  sequential scans without having to actually drop and recreate the index,
  making an honest with/without benchmark cheap to run.

## Day 8 — Evaluation runner + results schema

- **`alembic revision --autogenerate` flags an index it doesn't know about
  as "removed," even though it's still in the database.** Any index created
  via raw `op.execute()` (like the HNSW indexes in `e2072121286f`, since
  pgvector's `USING hnsw` syntax isn't expressible as a plain SQLAlchemy
  `Index`) has no corresponding object in `Base.metadata`. The next
  autogenerate diff compares live DB state against that metadata, sees an
  index in the DB with nothing matching in the models, and concludes it was
  dropped from the schema — proposing to drop it for real. The fix isn't a
  process change, just discipline: read every line of an autogenerated
  migration against what you actually intended before accepting it, every
  time, not just when the diff looks surprising.

## Day 9 — Celery dispatch layer

- **`Celery.autodiscover_tasks()` can silently register nothing, with no
  error, if the app doesn't use regular packages with `__init__.py`.** Its
  default behavior imports `<package>.tasks` for each package name given to
  it — a convention built around Django-style apps with real `__init__.py`
  files that can carry side-effecting imports. This project's `app/` tree
  is implicit namespace packages throughout (confirmed via `find app -name
  __init__.py`), so `autodiscover_tasks(["app.tasks"])` ran without
  complaint and left `celery_app.tasks` with only Celery's built-in tasks —
  the custom task was never registered, and nothing about the call itself
  signaled that. `include=[...]` on the `Celery()` constructor names the
  module explicitly and isn't subject to this ambiguity; verified by
  checking `celery_app.tasks.keys()` before and after switching, and by
  booting the real worker CLI and confirming `evaluation.run` in its
  startup `[tasks]` banner.
- **A migration verified on a throwaway DB and the pytest test DB still
  needs to be run against the actual dev DB separately — nothing does that
  automatically outside of `entrypoint.sh`.** Running `uvicorn`/`celery`
  directly (not through the Docker image) bypasses `entrypoint.sh`'s
  `alembic upgrade head` step entirely, so the first live dispatch against
  the real dev database failed with `UndefinedTable: evaluation_runs` even
  though the same migration had already passed rigorous verification
  (Day 8) on two other databases. A green migration check on one database
  is a claim about that database, not about every database an environment
  happens to have lying around.
