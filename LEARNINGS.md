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
