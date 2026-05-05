"""
gunicorn.conf.py
----------------
Production gunicorn configuration for PulleyWebApp on Render.

Worker model: sync (correct for CPU-bound STL/DXF generation — each worker
is a separate OS process, bypassing the GIL entirely).

WEB_CONCURRENCY is set automatically by Render based on instance RAM:
  Starter  512 MB → 2 workers  (~150–200 MB each under load)
  Standard 1 GB   → 4 workers
Override by setting WEB_CONCURRENCY in the Render environment variables.
"""
import os

workers      = int(os.environ.get('WEB_CONCURRENCY', 2))
worker_class = 'sync'

# Load the app in the parent process before forking workers.
# Workers inherit it via fork() (copy-on-write), saving ~100 MB RAM and
# shaving ~3 s off cold worker startup. Safe here because cadquery always
# runs in a subprocess, never in the gunicorn worker process itself.
preload_app = True

# Kill a worker that takes longer than this (seconds).
# STEP generation via cadquery subprocess can be slow; 120 s is generous.
timeout = 120

# Recycle each worker after this many requests to release accumulated
# trimesh/numpy memory without a full restart.
max_requests        = 500
max_requests_jitter = 50   # randomise so workers don't all recycle at once

# Keep-alive for persistent connections (load balancer / Cloudflare).
keepalive = 5

# Use shared memory for the worker heartbeat on Linux (faster than /tmp).
# Silently ignored on platforms where /dev/shm doesn't exist.
if os.path.isdir('/dev/shm'):
    worker_tmp_dir = '/dev/shm'

# Forward Render/Cloudflare proxy headers so request.remote_addr is correct.
forwarded_allow_ips = '*'
