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
