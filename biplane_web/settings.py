from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ["SECRET_KEY"]   # no fallback — must be set explicitly
DEBUG = os.getenv("DEBUG", "False") == "True"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# Required in production when behind a reverse proxy / HTTPS
CSRF_TRUSTED_ORIGINS = [
    h for h in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if h
]

INSTALLED_APPS = [
    "daphne",                           # must be first — overrides runserver
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "viewer",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",   # serves static files in production
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "biplane_web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

ASGI_APPLICATION = "biplane_web.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# ── Redis ─────────────────────────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL + "/0"]},
    }
}

FRAME_TTL = int(os.getenv("FRAME_TTL", "86400"))   # default 24 h; lower if RAM is tight

# Optional disk frame store. When set, decoded frames are saved to disk permanently
# and Redis acts as a read-through cache on top. Frames are never re-decoded if
# found on disk, even after the Redis TTL expires.
# Leave empty (default) to keep the original Redis-only behaviour.
_store = os.getenv("FRAME_STORE_DIR", "")
FRAME_STORE_DIR = Path(_store) if _store else None

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL + "/1",
        "TIMEOUT": FRAME_TTL,
    }
}

# ── Celery ────────────────────────────────────────────────────────────────────
CELERY_BROKER_URL        = REDIS_URL + "/2"
CELERY_RESULT_BACKEND    = REDIS_URL + "/2"
CELERY_TASK_SERIALIZER   = "json"
CELERY_RESULT_SERIALIZER = "json"

# ── Orthanc PACS ──────────────────────────────────────────────────────────────
ORTHANC_URL  = os.getenv("ORTHANC_URL")    # required — no fallback
ORTHANC_USER = os.getenv("ORTHANC_USER")   # required — no fallback
ORTHANC_PASS = os.getenv("ORTHANC_PASS")   # required — no fallback

# ── Static files ──────────────────────────────────────────────────────────────
STATIC_URL  = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"     # populated by: python manage.py collectstatic
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE     = "UTC"
USE_I18N      = True
USE_TZ        = True

# ── Production security (only when DEBUG=False) ───────────────────────────────
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE   = True
    CSRF_COOKIE_SECURE      = True
    SECURE_BROWSER_XSS_FILTER   = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
