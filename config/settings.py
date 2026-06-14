"""
Django settings for Mitaxy.

Mitaxy — AI meeting assistant. A bot joins your Zoom/Google Meet call,
records & transcribes it, and emails you structured AI notes.
"""
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from project root
load_dotenv(BASE_DIR / ".env")


def env(key, default=None):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def env_list(key, default=""):
    raw = os.environ.get(key, default) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


# ----------------------------------------------------------------------------
# Core
# ----------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

SITE_URL = env("SITE_URL", "http://127.0.0.1:8000").rstrip("/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Local apps
    "accounts",
    "meetings",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "meetings.context_processors.branding",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
DATABASES = {
    "default": dj_database_url.config(
        default=env("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=600,
    )
}

# ----------------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.EmailBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "meetings:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

# ----------------------------------------------------------------------------
# I18N / TZ
# ----------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

# ----------------------------------------------------------------------------
# Static / media
# ----------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ----------------------------------------------------------------------------
# Security (relaxed for HTTP-on-IP deploy; tighten when a domain + SSL exist)
# ----------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"

# ----------------------------------------------------------------------------
# Celery
# ----------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://127.0.0.1:6379/5")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/6")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 60 * 30
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "poll-pending-meetings": {
        "task": "meetings.tasks.poll_pending_meetings",
        "schedule": 60.0,  # every minute: drive bots through their lifecycle
    },
}

# ----------------------------------------------------------------------------
# Third-party integrations (Mitaxy pipeline)
# ----------------------------------------------------------------------------
RECALL_API_KEY = env("RECALL_API_KEY", "")
RECALL_REGION = env("RECALL_REGION", "us-east-1")
RECALL_BOT_NAME = env("RECALL_BOT_NAME", "Mitaxy Notetaker")

DEEPGRAM_API_KEY = env("DEEPGRAM_API_KEY", "")
DEEPGRAM_MODEL = env("DEEPGRAM_MODEL", "nova-2")

GROQ_API_KEY = env("GROQ_API_KEY", "")
GROQ_MODEL = env("GROQ_MODEL", "llama-3.3-70b-versatile")

RESEND_API_KEY = env("RESEND_API_KEY", "")
RESEND_FROM = env("RESEND_FROM", "Mitaxy <onboarding@resend.dev>")

# Route all of Django's outgoing mail (password reset, etc.) through Resend.
EMAIL_BACKEND = "accounts.email_backend.ResendEmailBackend"
DEFAULT_FROM_EMAIL = RESEND_FROM
SERVER_EMAIL = RESEND_FROM

# Branding (used in templates + emails)
BRAND_NAME = "Mitaxy"
BRAND_TAGLINE = "Never sit through a meeting again."

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "mitaxy": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
