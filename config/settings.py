import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    import secrets
    SECRET_KEY = secrets.token_urlsafe(50)
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if os.getenv("DJANGO_ALLOWED_HOSTS") else []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "life",
    "accounts",
    "common",
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
    "life.middleware.LoginRateLimitMiddleware",
    "life.middleware.ApiRateLimitMiddleware",
    "life.middleware.NoBrowserCacheMiddleware",
    "life.middleware.AdminAccessMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "life.context_processors.accounts",
        "life.context_processors.undo_state",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
# 头像等用户上传文件（2026-08-24）
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
DEFAULT_FROM_EMAIL = "lifeos@localhost"

# Session: 持久化登录，避免 iOS Safari / PWA 关闭后会话丢失导致首页打不开。
# 关闭浏览器不再登出；最长 14 天无活动才过期（SESSION_SAVE_EVERY_REQUEST 每次请求刷新过期时间）。
SESSION_COOKIE_AGE = 1209600  # 14 天
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# Cache for rate limiting.
# In production set REDIS_URL (e.g. redis://127.0.0.1:6379/1) to share the
# cache across workers; otherwise fall back to process-local memory (fine for
# single-process dev servers, but each worker keeps its own counters).
REDIS_URL = os.getenv("REDIS_URL")
if REDIS_URL:
    # Django's RedisCache imports redis-py lazily, so a missing package would
    # only surface as a runtime ImportError on the first cache hit. Degrade to
    # LocMem with a loud warning instead of taking the whole app down.
    try:
        import redis  # noqa: F401
    except ImportError:
        import logging
        logging.getLogger("django").warning(
            "REDIS_URL is set but the 'redis' package is missing; rate limiting "
            "falls back to LocMemCache (NOT shared across workers). Run: pip install redis"
        )
        REDIS_URL = None
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "lifeos",
        }
    }

# ── Production security ──────────────────────────────────────────
ENVIRONMENT = os.getenv("DJANGO_ENVIRONMENT", "development")

if ENVIRONMENT == "production":
    if not os.getenv("DJANGO_SECRET_KEY"):
        raise RuntimeError("DJANGO_SECRET_KEY must be set in .env for production mode")
    # 生产环境 fail-fast：禁止把开发用 .env（DEBUG=true / ALLOWED_HOSTS=*）直接上线，
    # 否则会泄露调试栈、允许任意 Host 头（Web 缓存投毒 / 密码重置投毒）。
    if DEBUG:
        raise RuntimeError("DJANGO_DEBUG 必须为 false（DJANGO_ENVIRONMENT=production 时）。")
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        raise RuntimeError(
            "生产环境 DJANGO_ALLOWED_HOSTS 必须设为具体域名，禁止使用 '*'。"
        )

# Trust Nginx X-Forwarded-Proto. Safe to always set: the actual redirect
# is controlled by SECURE_SSL_REDIRECT inside the production block below.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if ENVIRONMENT == "production" or not DEBUG:
    # HTTPS / SSL
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True").lower() == "true"
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    # Cookies
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    CSRF_COOKIE_HTTPONLY = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Security headers
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"

    # Password validation (enable in production)
    AUTH_PASSWORD_VALIDATORS = [
        {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
        {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
        {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
        {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    ]

# ── Production DB ───────────────────────────────────────────────
# 同时识别自管变量（MYSQL_*）与 Railway MySQL 插件注入变量（MYSQL* 无下划线），
# 挂上插件即自动切换 MySQL，无需手写映射。
_db_name = os.getenv("MYSQL_DATABASE") or os.getenv("MYSQLDATABASE")
_db_user = os.getenv("MYSQL_USER") or os.getenv("MYSQLUSER")
_db_pass = os.getenv("MYSQL_PASSWORD") or os.getenv("MYSQLPASSWORD")
_db_host = os.getenv("MYSQL_HOST") or os.getenv("MYSQLHOST", "127.0.0.1")
_db_port = os.getenv("MYSQL_PORT") or os.getenv("MYSQLPORT", "3306")
if _db_name and _db_user:
    DATABASES = {"default": {"ENGINE": "django.db.backends.mysql", "NAME": _db_name, "USER": _db_user, "PASSWORD": _db_pass, "HOST": _db_host, "PORT": _db_port, "OPTIONS": {"charset": "utf8mb4", "init_command": "SET sql_mode='STRICT_TRANS_TABLES'"}}}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

# ── Logging ─────────────────────────────────────────────────────
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "[{asctime}] {levelname} {module} {message}", "style": "{"},
        "simple": {"format": "{levelname} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "app.log"),
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 5,
            "formatter": "verbose",
        },
        "error_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "error.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "verbose",
            "level": "ERROR",
        },
    },
    "root": {
        "handlers": ["console"] + (["file", "error_file"] if ENVIRONMENT == "production" else []),
        "level": "INFO" if ENVIRONMENT == "production" else "DEBUG",
    },
    "loggers": {
        "django": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["error_file"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["error_file"], "level": "WARNING", "propagate": False},
    },
}

# ── OCR（图片识别记账）──────────────────────────────────────────
# 引擎：tesseract（默认，本地）| cloud（外部云服务）| mock（测试）
OCR_PROVIDER = os.getenv("OCR_PROVIDER", "tesseract")
OCR_TESSERACT_LANG = os.getenv("OCR_TESSERACT_LANG", "chi_sim+eng")
OCR_CLOUD_ENDPOINT = os.getenv("OCR_CLOUD_ENDPOINT", "")
OCR_CLOUD_API_KEY = os.getenv("OCR_CLOUD_API_KEY", "")
OCR_CLOUD_TYPE = os.getenv("OCR_CLOUD_TYPE", "generic")

# ── Static files ────────────────────────────────────────────────
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
# WhiteNoise 直接由 Django 服务压缩静态文件（生产无 nginx 时必需）。
# 用 CompressedStaticFilesStorage（不做文件名哈希），避免 Manifest 在测试渲染期
# 因找不到 manifest entry 而抛错；模板已用 ?v=N 查询串做缓存失效。
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
