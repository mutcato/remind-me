"""Django settings for the @remindme.in Instagram mention bot."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Populate os.environ from a .env file. Real env vars always win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) > 1 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]  # quoted: everything inside is the value
        else:
            value = value.split(" #", 1)[0].rstrip()  # unquoted: drop a trailing comment
        os.environ.setdefault(key.strip(), value)


load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [
    h.strip()
    for h in env("DJANGO_ALLOWED_HOSTS", "remindme.nudgewire.com,localhost,127.0.0.1").split(",")
    if h.strip()
]
# nginx terminates TLS and forwards the original scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "bot",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

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

def parse_database_url(url: str) -> dict:
    """postgres://user:pass@host:port/name?sslmode=require -> a DATABASES entry.

    Credentials are percent-decoded, so a password with "@" or "/" survives.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"unsupported database scheme: {parsed.scheme!r}")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
        # Reused connections, checked before use: the worker runs for days and
        # Postgres may restart underneath it.
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {key: values[-1] for key, values in parse_qs(parsed.query).items()},
    }


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / env("DATABASE_PATH", "data/remindme.db"),
        "OPTIONS": {"timeout": 20},
    }
}
# POSTGRES_CONNECTION_STRING wins; DATABASE_URL kept as the conventional alias.
_database_url = env("POSTGRES_CONNECTION_STRING") or env("DATABASE_URL")
if _database_url:
    DATABASES["default"] = parse_database_url(_database_url)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if not DEBUG:
    # nginx + certbot zaten TLS'i yönetiyor; bunlar Django tarafındaki karşılığı.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    # HSTS kapalı başlar: açtıktan sonra tarayıcılar o süre boyunca bu alan adında
    # http kabul etmez, geri alması zor. Hazır olunca DJANGO_HSTS_SECONDS=2592000.
    SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 0)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAdminUser"],
    "UNAUTHENTICATED_USER": None,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)-7s %(name)s  %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {"django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}

# --- Bot configuration -------------------------------------------------------
# Everything the mention bot needs lives here so one import gives the whole picture.
REMINDME = {
    # Meta app / Instagram account
    "APP_SECRET": env("IG_APP_SECRET"),
    "VERIFY_TOKEN": env("IG_VERIFY_TOKEN"),
    "IG_USER_ID": env("IG_USER_ID"),
    "ACCESS_TOKEN": env("IG_ACCESS_TOKEN"),
    "GRAPH_BASE_URL": env("GRAPH_BASE_URL", "https://graph.facebook.com"),
    "GRAPH_VERSION": env("GRAPH_VERSION", "v27.0"),
    "REQUEST_TIMEOUT": env_float("GRAPH_REQUEST_TIMEOUT", 15.0),
    # Command surface
    "BOT_HANDLE": env("BOT_HANDLE", "@remindme.in"),
    "DEFAULT_TIMEZONE": env("DEFAULT_TIMEZONE", "Europe/Istanbul"),
    "DEFAULT_LOCALE": env("DEFAULT_LOCALE", "tr"),
    "MAX_COMMENT_CHARS": env_int("MAX_COMMENT_CHARS", 300),
    "MAX_NOTE_CHARS": env_int("MAX_NOTE_CHARS", 140),
    "MIN_DELAY_SECONDS": env_int("MIN_DELAY_SECONDS", 60),
    "MAX_DELAY_DAYS": env_int("MAX_DELAY_DAYS", 365),
    "MAX_ACTIVE_PER_USER": env_int("MAX_ACTIVE_PER_USER", 20),
    # Reply behaviour
    "CONFIRM_REPLY": env_bool("CONFIRM_REPLY", True),
    "REPLY_ON_UNPARSED": env_bool("REPLY_ON_UNPARSED", False),
    # Single rate-limited sender
    "SEND_INTERVAL_SECONDS": env_float("SEND_INTERVAL_SECONDS", 2.0),
    "SEND_MAX_PER_HOUR": env_int("SEND_MAX_PER_HOUR", 100),
    "SEND_BATCH_SIZE": env_int("SEND_BATCH_SIZE", 20),
    "INBOX_BATCH_SIZE": env_int("INBOX_BATCH_SIZE", 25),
    "MAX_ATTEMPTS": env_int("MAX_ATTEMPTS", 6),
    "RETRY_BASE_SECONDS": env_int("RETRY_BASE_SECONDS", 60),
    "RETRY_MAX_SECONDS": env_int("RETRY_MAX_SECONDS", 3600),
    "POLL_INTERVAL_SECONDS": env_float("POLL_INTERVAL_SECONDS", 15.0),
    # Parse and store, but never POST to Instagram. Use before App Review.
    "DRY_RUN": env_bool("DRY_RUN", False),
}
