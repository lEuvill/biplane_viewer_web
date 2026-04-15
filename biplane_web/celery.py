import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "biplane_web.settings")

app = Celery("biplane_web")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Auto-reload worker when viewer source files change (dev only).
# Prevents stale module cache after code updates without restarting the worker.
app.conf.worker_max_tasks_per_child = 1
