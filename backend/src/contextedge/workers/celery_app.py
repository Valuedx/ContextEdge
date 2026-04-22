from celery import Celery

from contextedge.config import settings

celery_app = Celery(
    "contextedge",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "contextedge.workers.sync_tasks",
        "contextedge.workers.hydration_tasks",
        "contextedge.workers.extraction_tasks",
        "contextedge.workers.evidence_baseline_tasks",  # Moved up for reliable registration
        "contextedge.workers.artifact_tasks",
        "contextedge.workers.correlation_tasks",
        "contextedge.workers.pattern_tasks",
        "contextedge.workers.evaluation_tasks",
        "contextedge.workers.review_queue_tasks",
    ],
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
    task_create_missing_queues=True,
    task_routes={
        "sync.*": {"queue": "sync"},
        "hydration.*": {"queue": "hydration"},
        "extraction.*": {"queue": "extraction"},
        "artifact.*": {"queue": "extraction"},
        "pattern.*": {"queue": "pattern"},
        "evaluation.*": {"queue": "evaluation"},
        # Fallback for any tasks still using full module paths
        "contextedge.workers.*": {"queue": "default"},
    },
    task_default_queue="default",
    beat_schedule={
        "detect-drift-every-6h": {
            "task": "evaluation.detect_drift",
            "schedule": 21600.0,
            "args": ("all",),
        },
        "scan-contradictions-every-12h": {
            "task": "evaluation.scan_contradictions_task",
            "schedule": 43200.0,
            "args": ("all",),
        },
        "trigger-syncs-every-15m": {
            "task": "sync.trigger_scheduled_syncs",
            "schedule": 900.0,
        },
    },
)

# Explicitly import task modules to ensure registration in environments
# where the 'include' configuration might be delayed or shadowed.
try:
    import contextedge.workers.extraction_tasks
    import contextedge.workers.evidence_baseline_tasks
    import contextedge.workers.correlation_tasks
except ImportError:
    pass
