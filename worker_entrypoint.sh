#!/bin/sh
set -e

echo "Waiting for postgres to accept connections..."
until pg_isready -d "$DATABASE_URL" > /dev/null 2>&1; do
  sleep 1
done
echo "Postgres is up."

# No `alembic upgrade head` here -- the api container's entrypoint already
# owns running migrations. Running them again from a second container at
# the same startup moment would race it for no benefit.
exec celery -A app.core.celery_app worker --loglevel=info
