"""gunicorn settings (docker-compose.yml / Dockerfile: gunicorn -c config/gunicorn.conf.py config.wsgi:application).

Before: 1 worker x 8 threads. Python runs one thread at a time per process (GIL), so a single CPU-heavy request
(Face ID, Excel import/export) slowed every other request of the whole site. Several worker processes use
several CPU cores and keep the site responsive while one of them is busy.

Override without editing the file: GUNICORN_WORKERS / GUNICORN_THREADS / GUNICORN_TIMEOUT in .env.
Workers share login/Face ID counters through the file cache (settings.CACHES), and each worker keeps
its own Face ID models (~100 MB RAM per worker).
"""
import multiprocessing
import os


def _int(name, default):
    try:
        return int(os.environ.get(name, '') or default)
    except ValueError:
        return default


bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:5000')
# 2-4 workers is enough for an internal service; more only costs memory.
workers = _int('GUNICORN_WORKERS', min(4, max(2, multiprocessing.cpu_count())))
worker_class = 'gthread'
threads = _int('GUNICORN_THREADS', 4)
timeout = _int('GUNICORN_TIMEOUT', 120)
graceful_timeout = 30
keepalive = 5
# Restart a worker now and then: returns memory that OpenCV/numpy/openpyxl may hold on to.
max_requests = _int('GUNICORN_MAX_REQUESTS', 2000)
max_requests_jitter = 200
# Heartbeat files in RAM, not on the container's overlay filesystem (known source of worker stalls in Docker).
worker_tmp_dir = '/dev/shm' if os.path.isdir('/dev/shm') else None
accesslog = '-'
errorlog = '-'
