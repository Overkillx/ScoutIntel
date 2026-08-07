# ScoutIntel

A backend-first football scouting platform for player discovery and comparison.

## The Problem

Smaller clubs and lower-league scouts don't have Wyscout/Hudl-level budgets,
so they scout using spreadsheets and gut feeling. Finding a player who plays
like a specific target, or a cheaper alternative with a similar profile,
means manually cross-referencing stats across sources by hand.

## What This Is

ScoutIntel ingests player data, computes attribute-based similarity vectors
via pgvector, and exposes the results through a versioned REST API. Given a
player, it finds statistically similar players — a "find me players like X"
tool, backed by real vector search infrastructure rather than a lookup table.

**One-liner:** ScoutIntel is a backend platform that ingests football player
data, computes attribute-based similarity using vector embeddings, and
exposes the results through a versioned, tested REST API.

## Status: In Progress

This is an active build-in-public portfolio project. Current status:

- [x] Data acquisition + exploration
- [x] Postgres schema + SQLAlchemy models
- [x] Data ingestion pipeline (CSV → Postgres)
- [x] Basic FastAPI CRUD endpoints
- [x] Database migrations (Alembic)
- [x] Vector similarity search (pgvector)
- [x] requirements.txt / requirements-dev.txt (repo runnable from a fresh clone)
- [x] Docker Compose (Postgres + pgvector, Redis, API — full stack runnable from a clone)
- [x] Tests (pytest, isolated test DB, transaction-per-test rollback)
- [ ] Pydantic response models
- [ ] CI (GitHub Actions)

Notes:
- An early code review pass caught and fixed 4 real correctness bugs (NaN
  handling, missing DB constraints, HTTP semantics) — see DECISIONS.md
  "Open items" and the Day 3-4 entries for details.
- Verifying migrations against a genuinely empty database (Day 5) caught a
  silent no-op baseline migration that would have broken any fresh clone —
  see DECISIONS.md Day 5 for the root cause and fix.

See `DECISIONS.md` for the reasoning behind each engineering choice as they're made,
and `LEARNINGS.md` for concepts learned along the way.

## Data

Player data sourced from a public EA FC26 player database (Kaggle), containing
~18,400 players with bio data, market value, and detailed performance attributes.

**Known limitations:**
- Performance stats are FIFA-game attribute ratings (pace, shooting, passing,
  dribbling, defending, physical), not live match statistics (goals, xG, xA).
- `value_eur` correlates moderately with `overall`/`potential` (r ≈ 0.5), and
  is derived by EA's own model rather than being an independent market price.
  Because of this, ScoutIntel does not claim to detect market inefficiency —
  a "stats vs value" score would partly reverse-engineer EA's own valuation
  logic. Value-relative ranking, if added, is framed as a rough filter for
  further review, not a market-inefficiency signal. Similarity search (which
  doesn't depend on `value_eur`) is the core, defensible feature.
- Real match statistics (via FBref) and independent market values (via
  Transfermarkt) are noted below as future improvements that would resolve
  both limitations.

## Tech Stack

**Backend:** FastAPI, PostgreSQL, SQLAlchemy, Alembic, pgvector
**DevOps:** Docker, Docker Compose
**Testing:** pytest

Redis is provisioned in Docker Compose but not yet used by any code — it's
infra for the caching layer on the roadmap, added now so it doesn't require
another infra change later.

## Architecture

CSV → ingest.py → Postgres (players, player_stats)
↓
compute_vectors.py → pgvector (player_vectors, goalkeeper_vectors)
↓
REST API (FastAPI, versioned)
↓
GET /players/{id}/similar

`ingest.py` and `compute_vectors.py` are run manually today, not on a
schedule — see Roadmap.

## Roadmap (Deferred, Not Built Yet)

Deliberately scoped out to keep the current build small and complete rather
than wide and half-finished:

- Redis caching layer, rate limiting
- Nightly scheduler for ingest/vector recompute (Celery beat, or simpler)
- CI (GitHub Actions) running the test suite on push
- Natural language search (LLM-powered query parsing)
- AI-generated scouting reports
- Authentication (JWT), user accounts, saved searches, shortlists
- Real match statistics via FBref + independent market values via Transfermarkt
- Kafka-based ingestion for higher-scale sources
- WebSocket-based live job progress updates
- Role-based access control

## Setup

Requires Docker and Docker Compose.

```bash
git clone <repo-url>
cd ScoutIntel
cp .env.example .env
docker compose up -d --build
```

This starts Postgres (with pgvector) and Redis, waits for Postgres to
actually accept connections, runs `alembic upgrade head`, then starts the
API at `http://localhost:8000` (`/docs` for interactive Swagger UI).

`.env.example` defaults Postgres to host port **5433**, not 5432 — this
avoids silently colliding with a Postgres already running locally on the
default port (see DECISIONS.md Day 5). Adjust `POSTGRES_PORT`/`API_PORT`/
`REDIS_PORT` in `.env` if those also collide with something on your machine.

### Loading data

The CSV in `data/` isn't committed (see `.gitignore`) — put your own FC26
export there, then:

```bash
docker compose exec api python ingest.py
docker compose exec api python compute_vectors.py
```

### Running tests

Tests need `requirements-dev.txt` and a Postgres reachable from the host
(the Docker Compose Postgres, published on `POSTGRES_PORT`, works):

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql://scoutintel:scoutintel@localhost:5433/scoutintel_test pytest
```

Tests create/drop a separate `scoutintel_test` database and roll back each
test's transaction — they never write to the dev database in `.env`.
