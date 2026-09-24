from celery import Celery

from ..core.config import get_settings

settings = get_settings()

celery_app = Celery("email_prioritizer", broker=settings.redis_url, include=["app.worker.tasks"])
celery_app.conf.timezone = "UTC"
celery_app.conf.beat_schedule = {
    "sync-inbox": {
        "task": "app.worker.tasks.sync_inbox",
        "schedule": settings.sync_interval_seconds,
    },
}
