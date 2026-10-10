import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

_env_file = BASE_DIR / ".env"
if _env_file.is_file():
    try:
        with open(_env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())
    except Exception:
        pass

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "unsafe-development-key-change-me")
DEBUG = False
ALLOWED_HOSTS = [host.strip() for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "apps.core", "apps.monitoring",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "fa"
TIME_ZONE = "Asia/Tehran"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
LOGIN_URL = "/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_BEAT_SCHEDULE = {
    "automation-dispatcher": {"task": "apps.monitoring.tasks.dispatch_automation", "schedule": 60.0},
    "idea-outbox-maintenance": {"task": "apps.monitoring.tasks.maintain_idea_deliveries", "schedule": 60.0},
    "retention-cleanup": {"task": "apps.monitoring.tasks.cleanup_retention", "schedule": 24 * 60 * 60.0},
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": CELERY_BROKER_URL}}


def _bounded_int_env(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")
X_API_BASE_URL = os.environ.get("X_API_BASE_URL", "https://api.x.com/2").rstrip("/")
X_INITIAL_LOOKBACK_HOURS = _bounded_int_env("X_INITIAL_LOOKBACK_HOURS", 24, 1, 168)
X_MAX_POSTS_PER_SOURCE = _bounded_int_env("X_MAX_POSTS_PER_SOURCE", 50, 1, 100)
X_HTTP_TIMEOUT_SECONDS = _bounded_int_env("X_HTTP_TIMEOUT_SECONDS", 15, 1, 60)

BALE_BOT_TOKEN = os.environ.get("BALE_BOT_TOKEN", "")
BALE_API_BASE_URL = os.environ.get("BALE_API_BASE_URL", "https://tapi.bale.ai").rstrip("/")
BALE_HTTP_TIMEOUT_SECONDS = _bounded_int_env("BALE_HTTP_TIMEOUT_SECONDS", 15, 1, 60)
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "").rstrip("/")
SHARED_ACCESS_TOKEN = os.environ.get("SHARED_ACCESS_TOKEN", "")
TELEGRAM_BASE_URL = os.environ.get("TELEGRAM_BASE_URL", "https://t.me").rstrip("/")
IDEA_INTEGRATION_ENABLED = os.environ.get("IDEA_INTEGRATION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
IDEA_API_BASE_URL = os.environ.get("IDEA_API_BASE_URL", "https://idea.haminyar.ir/api/v1").rstrip("/")
IDEA_API_TOKEN = os.environ.get("IDEA_API_TOKEN", "")
IDEA_SOURCE_SYSTEM = os.environ.get("IDEA_SOURCE_SYSTEM", "trend-monitor").strip() or "trend-monitor"
IDEA_HTTP_TIMEOUT_SECONDS = _bounded_int_env("IDEA_HTTP_TIMEOUT_SECONDS", 20, 2, 120)
IDEA_MAX_ATTEMPTS = _bounded_int_env("IDEA_MAX_ATTEMPTS", 8, 1, 20)
