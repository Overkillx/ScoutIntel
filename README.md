# ScoutIntel

A backend-first football scouting platform for player discovery and comparison.

## The Problem

Smaller clubs and lower-league scouts don't have Wyscout/Hudl-level budgets,
so they scout using spreadsheets and gut feeling. Finding a player who plays
like a specific target, or a cheaper alternative with a similar profile,
means manually cross-referencing stats across sources by hand.

## What This Is

ScoutIntel ingests player data, computes attribute-based similarity vectors
through an asynchronous background pipeline, and exposes the results through
a versioned REST API. Given a player, it finds statistically similar players —
a "find me players like X" tool, backed by real vector search infrastructure
rather than a lookup table.

**One-liner:** ScoutIntel is a backend platform that ingests football player
data, computes attribute-based similarity using vector embeddings and
asynchronous processing, and exposes the results through a versioned,
tested REST API.

## Status: In Progress

This is an active build-in-public portfolio project. Current status:

- [x] Data acquisition + exploration
- [x] Postgres schema + SQLAlchemy models
- [x] Data ingestion pipeline (CSV → Postgres)
- [x] Basic FastAPI CRUD endpoints
- [x] Database migrations (Alembic)
- [ ] requirements.txt (repo not yet runnable from a fresh clone)
- [ ] Vector similarity search (pgvector) — current focus
- [ ] Docker Compose (full stack runnable from a clone)
- [ ] Pydantic response models
- [ ] Tests + CI (GitHub Actions)

Note: an early code review pass caught and fixed 4 real correctness bugs
(NaN handling, missing DB constraints, HTTP semantics) — see DECISIONS.md
"Open items" and the Day 3-4 entries for details.

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
**Async processing:** Celery, Redis
**DevOps:** Docker, Docker Compose, GitHub Actions

## Architecture

CSV → Cleaning → Postgres → Celery background job
↓
Attribute vector computation (pgvector)
↓
REST API (FastAPI, versioned)
↓
GET /players/{id}/similar

## Roadmap (Deferred, Not Built Yet)

Deliberately scoped out to keep the current build small and complete rather
than wide and half-finished:

- Redis caching layer, rate limiting
- Nightly scheduler (Celery beat)
- Natural language search (LLM-powered query parsing)
- AI-generated scouting reports
- Authentication (JWT), user accounts, saved searches, shortlists
- Real match statistics via FBref + independent market values via Transfermarkt
- Kafka-based ingestion for higher-scale sources
- WebSocket-based live job progress updates
- Role-based access control

## Setup

_(Coming once Docker Compose and requirements.txt are added — will include
full instructions to run the stack from a fresh clone.)_
