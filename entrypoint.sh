#!/bin/sh
set -e

echo "Waiting for postgres to accept connections..."
until pg_isready -d "$DATABASE_URL" > /dev/null 2>&1; do
  sleep 1
done
echo "Postgres is up."

alembic upgrade head

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
