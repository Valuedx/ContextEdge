from celery import Celery

from contextedge.config import settings

celery_app = Celery(
    "contextedge",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "contextedge.workers.sync_tasks.*": {"queue": "sync"},
        "contextedge.workers.hydration_tasks.*": {"queue": "hydration"},
        "contextedge.workers.extraction_tasks.*": {"queue": "extraction"},
        "contextedge.workers.artifact_tasks.*": {"queue": "extraction"},
        "contextedge.workers.correlation_tasks.*": {"queue": "extraction"},
        "contextedge.workers.evidence_baseline_tasks.*": {"queue": "extraction"},
        "contextedge.workers.pattern_tasks.*": {"queue": "pattern"},
        "contextedge.workers.evaluation_tasks.*": {"queue": "evaluation"},
    },
    task_default_queue="default",
    beat_schedule={
        "detect-drift-every-6h": {
            "task": "contextedge.workers.evaluation_tasks.detect_drift",
            "schedule": 21600.0,
            "args": ("all",),
        },
        "scan-contradictions-every-12h": {
            "task": "contextedge.workers.evaluation_tasks.scan_contradictions_task",
            "schedule": 43200.0,
            "args": ("all",),
        },
    },
)

celery_app.autodiscover_tasks(
    [
        "contextedge.workers.sync_tasks",
        "contextedge.workers.hydration_tasks",
        "contextedge.workers.extraction_tasks",
        "contextedge.workers.artifact_tasks",
        "contextedge.workers.correlation_tasks",
        "contextedge.workers.pattern_tasks",
        "contextedge.workers.evaluation_tasks",
        "contextedge.workers.review_queue_tasks",
        "contextedge.workers.evidence_baseline_tasks",
    ],
    force=True,
)
