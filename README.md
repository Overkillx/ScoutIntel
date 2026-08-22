# ScoutIntel

ScoutIntel is a football scouting backend: given a player, it finds others
with a similar playing style, and it answers questions like *"creative
midfielders like Pedri, under 23, worth up to 40m"* typed as a sentence. It
runs on ~18,400 players from a public EA FC26 export, with attribute
embeddings in Postgres/pgvector behind a versioned FastAPI service.

The similarity part is a few hundred lines. The reason this repository
exists is everything built around it to find out whether the similarity is
any good — an offline evaluation harness with real IR metrics, two ranking
models scored head-to-head against a hand-curated relevance set, and a
written record of the results that includes the ones that didn't flatter
the design.

## The parts worth looking at

**An offline evaluation harness, not a demo.** `app/evaluation/` scores a
ranking function against a curated relevance set using Precision@k,
Recall@k, NDCG@k, plus two domain checks (position consistency, and a
self-similarity violation counter that catches a player being returned as
similar to themselves). Every run is persisted to `evaluation_runs` with a
SHA-256 fingerprint of the relevance set it scored against, because two
runs are only comparable if they were judged against the same judgments —
`GET /api/v1/evaluations/` lists them. Runs go through `evaluate.py`
synchronously, or through Celery for the dispatch path.

**Two ranking models, compared honestly.** v1 (`v1_vector`) is plain cosine
distance over 18 z-scored attributes with a hard same-position-group
filter. v2 (`v2_tactical`) weights each dimension by a blend of a
position-baseline and the query player's own strengths, and relaxes the
position filter to the whole outfield pool. Before running the comparison I
wrote down that I expected v2 to score *worse*. It didn't:

| metric | v1_vector | v2_tactical |
|---|---|---|
| Precision@10 | 0.0600 | 0.0900 |
| Recall@10 | 0.2667 | 0.3500 |
| NDCG@10 | 0.1688 | 0.2469 |
| position_consistency | 1.0000 | 0.8100 |

v2 wins on every relevance metric and loses on `position_consistency` —
which turns out to be the least informative of the four, because v1's hard
position filter makes it ~1.0 *by construction*. It measures that the
filter ran, not that the results are good. That's the kind of finding an
evaluation harness exists to produce, and it's why the number stays in the
table rather than being quietly dropped.

**A parameter sweep that deliberately doesn't pick a winner.** v2's `alpha`
blends the two weight sources. Swept at 0.3 / 0.5 / 0.7:

| alpha | Precision@10 | Recall@10 | NDCG@10 | position_consistency |
|---|---|---|---|---|
| 0.3 | 0.0800 | 0.3167 | 0.2473 | 0.8200 |
| 0.5 | 0.0900 | 0.3500 | 0.2469 | 0.8100 |
| 0.7 | 0.0900 | 0.3500 | 0.2484 | 0.8000 |

The whole spread across three settings is one relevant document (8, 9 and 9
hits out of 27 judgments), and the between-alpha difference in NDCG@10 is
about forty times smaller than NDCG@10's standard error across queries. So
alpha stays at its pre-sweep default of 0.5. The sweep still establishes
something: no query returned an identical top-10 *ordering* at any two
alphas, so the knob is live — the curated set is simply too small to say
which setting is better. Reporting that, rather than "alpha=0.7, +0.0015
NDCG", is the point.

**Natural-language search that never lets a model write SQL.**
`POST /api/v1/search` parses a sentence into a validated Pydantic
`SearchQuery` and runs *that* against the existing retrieval — explicitly
not text-to-SQL. The boundary is a function signature rather than a policy:
`parse_query(text) -> SearchQuery` takes no database session, and its
return type's fields are bounded numbers, closed enums, and exactly one
free-text value (a player name) that resolves through a parameterized
filter to an integer `player_id`. The widest thing a sentence can do to the
database is select an integer. The endpoint returns the parsed query
alongside the results, so the interpretation can be checked by whoever
asked. Parsing is a keyword/regex grammar — no LLM call, no new dependency,
unit-testable without a database.

**Retrieval details that took measurement to get right.** HNSW was chosen
over IVFFlat and benchmarked with `EXPLAIN ANALYZE` on the 16,343-row
vector table: 0.766ms for the index scan against 7.272ms for a forced
sequential scan. Separately — and this one was a real bug — a *filtered*
HNSW query can silently return fewer rows than its `LIMIT`, because the
index is approximate and a scan whose nearest graph entries fail the filter
stops early rather than erroring. It showed up as `rank_similar(limit=4)`
returning three of four available candidates, with no failure anywhere.
`hnsw.iterative_scan = strict_order` is now set per connection, committed
inside the connect handler because `SET` is transactional in Postgres and
an uncommitted one is reverted by the next `ROLLBACK`.

**Two findings that shaped what the project doesn't claim.** `value_eur`
correlates with `overall` at r=0.55 and is produced by EA's own model, not
by a market — so ScoutIntel makes no market-inefficiency claim, and no
ranking is ordered by value. And Alembic's `--autogenerate` repeatedly
proposes dropping the HNSW indexes, because they were created with raw
`op.execute` and were never SQLAlchemy `Index` objects; autogenerate diffs
ORM metadata against the live schema, so anything that exists only in
migration SQL reads as "removed". Every migration here is hand-checked
against its generated draft for that reason.

`DECISIONS.md` records why each choice was made, day by day; `LEARNINGS.md`
records what turned out to be wrong.

## Architecture

```
data/FC26_*.csv
   │  ingest.py                 (bio + aggregate stats)
   │  compute_vectors.py        (z-scored attribute embeddings)
   ▼
Postgres 16 + pgvector
   players · player_stats · player_vectors(18d) · goalkeeper_vectors(6d)
   evaluation_runs · evaluation_query_results
   ▲                     ▲
   │                     │
FastAPI                Celery worker ── Redis
   /api/v1/players/{id}/similar
   /api/v1/search            NL → SearchQuery → ranking + filters
   /api/v1/evaluations       trigger runs, list/inspect results
```

Ranking lives in `app/services/similarity.py` as plain functions over a
session — no FastAPI types — so the evaluation harness, the Celery task and
the HTTP layer all call the same code. `MODEL_REGISTRY` maps a model label
to its ranking function in one place, and both the harness and NL search
resolve labels through it. Outfield and goalkeeper vectors are separate
tables because pgvector columns are fixed-width and the two attribute
spaces aren't comparable; a GK query routes to the GK table automatically
rather than erroring.

Schema changes go through Alembic. Tests run real migrations against a
throwaway database rather than `create_all`, so the migration path itself
is exercised on every run.

## Running it from a fresh clone

Requires Docker and Docker Compose.

```bash
git clone <repo-url> scoutintel
cd scoutintel
cp .env.example .env
docker compose up -d --build
```

That starts Postgres (pgvector) and Redis, waits for Postgres to accept
connections, runs `alembic upgrade head`, and serves the API on
<http://localhost:8000> — `/docs` for Swagger. `.env.example` puts Postgres
on host port **5433** deliberately, to avoid colliding with a Postgres
already running locally; adjust `POSTGRES_PORT` / `API_PORT` / `REDIS_PORT`
there if they clash with something on your machine.

The API comes up empty, because the dataset isn't committed (it's a
third-party export). Drop an EA FC26 CSV into `data/` — the public Kaggle
"EA FC 26 player database" export, with columns `player_id`, `short_name`,
`long_name`, `age`, `player_positions`, `value_eur`, the six aggregate
attributes and the 30 granular `attacking_*` / `skill_*` / `movement_*` /
`power_*` / `mentality_*` / `defending_*` / `goalkeeping_*` columns — then:

```bash
docker compose exec api python ingest.py            # CSV → players, player_stats
docker compose exec api python compute_vectors.py   # → player_vectors, goalkeeper_vectors
```

Both are idempotent upserts, so re-running them on a newer export updates in
place. With data loaded:

```bash
curl 'http://localhost:8000/api/v1/players/231866/similar?limit=5'

curl -X POST http://localhost:8000/api/v1/search/ \
  -H 'Content-Type: application/json' \
  -d '{"query": "top 5 creative midfielders like Rodri under 26"}'
```

### Running the evaluation harness

`evaluate.py` runs the harness synchronously against the curated relevance
set and persists each run:

```bash
docker compose exec api python evaluate.py --model v1_vector
docker compose exec api python evaluate.py --model v2_tactical --sweep alpha=0.3,0.5,0.7
docker compose exec api python evaluate.py --model v2_tactical --param alpha=0.3 --no-persist
```

Results are queryable afterwards at `GET /api/v1/evaluations/`. The same
work can be dispatched to the Celery worker with
`POST /api/v1/evaluations/run`.

### Tests

The suite needs a Postgres it can create and drop databases on. The Compose
Postgres works; point `TEST_DATABASE_URL` at it. `DATABASE_URL` is set on
the same line because `.env` resolves the database by its Compose service
name (`postgres`), which doesn't resolve from the host:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql://scoutintel:scoutintel@localhost:5433/scoutintel_test \
DATABASE_URL=postgresql://scoutintel:scoutintel@localhost:5433/scoutintel \
  pytest
```

Each test session creates a fresh `scoutintel_test` database, runs the real
migrations into it, and gives every test its own transaction that is rolled
back on teardown. No test writes to the database in `.env`, and no test
needs the CSV — fixtures build the players they need.

## Data, and what it can't support

The dataset is a public EA FC26 export: ~18,400 players, bio data, market
value, and 110 columns of attributes. Two limitations matter enough to
shape the design.

The performance numbers are EA's attribute ratings, not match events — no
goals, no xG, no minutes. Similarity computed from them is similarity of
*rated attributes*, which is a reasonable proxy for playing style and is
not the same thing as similarity of performance.

`value_eur` is EA's own valuation, correlated with `overall` at r=0.55.
Anything built on "stats versus value" would partly be reverse-engineering
EA's valuation model and calling the residual a market inefficiency, so
that feature isn't here. Similarity search doesn't touch `value_eur` at
all, which is why it's the feature the project actually claims.

The relevance set (`app/evaluation/relevance_set.yaml`) is 10 query players
and 27 judgments, curated by hand from football knowledge — one person's
opinion, stated as such in the file. It's enough to catch a model that's
badly wrong and far too small for statistical claims, which is exactly why
the alpha sweep above reports "no meaningful difference" instead of a
winner.

## Not built

Deliberately out of scope, to keep this finished rather than wide: Redis
caching and rate limiting (Redis is provisioned for Celery only), a
scheduler for ingest/recompute, CI, authentication and saved shortlists,
real match statistics via FBref, and independent market values via
Transfermarkt. The last two would resolve both data limitations above and
are the first things worth doing next.
