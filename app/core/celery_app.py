import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# Redis is provisioned in docker-compose but was unused until now (see
# .env.example) -- reused here as both broker and result backend rather
# than standing up a second piece of infra, since a single Redis instance
# is enough at this scale. Read from env vars, same pattern DATABASE_URL
# already uses in app/db/session.py -- never hardcoded, so a worker running
# outside docker compose (e.g. against a local Redis) just needs .env
# changed, not code.
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

celery_app = Celery(
    "scoutintel",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    # `include`, not autodiscover_tasks(): the app package has no
    # __init__.py anywhere (implicit namespace packages throughout, see
    # app/evaluation and app/services), so autodiscover's default
    # "import <package>.tasks" convention wouldn't reliably pick up
    # task-registering side effects the way it does in a regular package.
    # Listing the task module explicitly is unambiguous and is Celery's
    # documented mechanism for exactly this.
    include=["app.tasks.evaluation"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)
