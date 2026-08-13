"""Gunicorn config for Personal Life OS."""
import os

bind = "127.0.0.1:8000"
# Single worker: login rate-limiting uses LocMemCache, which is per-process.
# A single-user app gains nothing from multiple sync workers, and one worker
# keeps the cache (and thus rate limiting) correct. Override GUNICORN_WORKERS if needed.
workers = int(os.getenv("GUNICORN_WORKERS", "1"))
worker_class = "sync"
timeout = 60
keepalive = 5
max_requests = 1000
max_requests_jitter = 100

# Logging
accesslog = "logs/gunicorn-access.log"
errorlog = "logs/gunicorn-error.log"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "lifeos"
