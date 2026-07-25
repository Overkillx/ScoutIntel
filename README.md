# ScoutIntel

A backend-first football recruitment and player analytics platform.

## The Problem

Smaller clubs and lower-league scouts don't have Wyscout/Hudl-level budgets,
so they scout using spreadsheets and gut feeling — and miss undervalued players
simply because they can't query and compare stats the way big clubs can.

## What This Is

ScoutIntel ingests player data, computes similarity and valuation metrics through
asynchronous background pipelines, and exposes the results through a versioned
REST API. Natural language search is layered on top as one feature among several —
the core of the system is the data pipeline and async processing, not the AI.

**One-liner:** ScoutIntel is a production-style football recruitment platform
that ingests player data, computes similarity and valuation metrics through
asynchronous pipelines, exposes versioned REST APIs, and uses AI to generate
scouting reports from structured statistical analysis.

## Status: In Progress (Day 1 of ~18)

This is an active build-in-public portfolio project. Current status:

- [x] Data acquisition + exploration
- [ ] Postgres schema + ingestion pipeline
- [ ] Celery + Redis async job infrastructure
- [ ] Vector similarity search (pgvector)
- [ ] Undervalued-player detection
- [ ] Redis caching layer
- [ ] Rate limiting
- [ ] Nightly scheduler (Celery beat)
- [ ] Natural language search (LLM-powered)
- [ ] AI-generated scouting reports
- [ ] Tests + CI (GitHub Actions)

See `DECISIONS.md` for the reasoning behind each engineering choice as they're made.

## Data

Player data sourced from a public EA FC26 player database (Kaggle), containing
~18,400 players with bio data, market value, and detailed performance attributes.

**Known limitation:** performance stats are FIFA-game attribute ratings (pace,
shooting, passing, dribbling, defending, physical), not live match statistics
(goals, xG, xA). This is a deliberate MVP tradeoff — real match data via FBref
is noted below as a future improvement.

## Tech Stack

**Backend:** FastAPI, PostgreSQL, SQLAlchemy, pgvector
**Async processing:** Celery, Redis
**AI:** LLM-based structured query parsing, LLM-generated scouting reports
**DevOps:** Docker, Docker Compose, GitHub Actions

## Architecture

CSV/API → Cleaning → Postgres → Background jobs (Celery)
↓
Vector computation + valuation scoring
↓
Redis cache
↓
REST API (FastAPI)
↓
NL search + AI scouting reports (LLM)

## Roadmap (Not Built Yet, Deliberately Scoped Out)

- Authentication (JWT), user accounts, saved searches, shortlists
- Real match statistics via FBref, merged with market value data
- Kafka-based ingestion for higher-scale data sources
- WebSocket-based live job progress updates
- Role-based access control

## Setup

_(Coming as the project progresses — will include Docker Compose instructions
once the backend and database layers are built.)_
