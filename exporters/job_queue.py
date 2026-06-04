"""Background job queue for async STEP/DXF generation with parallel tasks."""
import json
import uuid
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

_JOBS = {}  # {job_id: {status, tasks, created, output_file}}
_JOBS_LOCK = threading.Lock()
_JOB_CLEANUP_INTERVAL = 60  # cleanup old jobs every 60s


class JobTask:
    """Represents a single parallel task (e.g., 'P1 STEP', 'Belt STEP')."""
    def __init__(self, name):
        self.name = name
        self.status = 'pending'  # pending, running, done, failed
        self.progress = 0  # 0-100
        self.error = None

    def to_dict(self):
        return {
            'name': self.name,
            'status': self.status,
            'progress': self.progress,
            'error': self.error,
        }


class Job:
    """Represents a download job with multiple parallel tasks."""
    def __init__(self, job_type='download-all'):
        self.id = str(uuid.uuid4())[:8]
        self.type = job_type
        self.status = 'queued'  # queued, running, done, failed
        self.created = datetime.now()
        self.tasks = {}
        self.output_file = None
        self.error = None

    def add_task(self, name):
        self.tasks[name] = JobTask(name)

    def get_task(self, name):
        return self.tasks.get(name)

    def to_dict(self):
        return {
            'id': self.id,
            'type': self.type,
            'status': self.status,
            'created': self.created.isoformat(),
            'tasks': {k: v.to_dict() for k, v in self.tasks.items()},
            'output_file': self.output_file,
            'error': self.error,
        }


def create_job(job_type='download-all'):
    """Create a new async job."""
    job = Job(job_type)
    with _JOBS_LOCK:
        _JOBS[job.id] = job
    return job


def get_job(job_id):
    """Get job by ID."""
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def update_task(job_id, task_name, progress=None, status=None, error=None):
    """Update a task's progress."""
    job = get_job(job_id)
    if not job:
        return
    task = job.get_task(task_name)
    if not task:
        return
    if progress is not None:
        task.progress = min(100, max(0, progress))
    if status is not None:
        task.status = status
    if error is not None:
        task.error = error


def finish_job(job_id, output_file=None, error=None):
    """Mark job as done."""
    job = get_job(job_id)
    if not job:
        return
    job.status = 'failed' if error else 'done'
    job.output_file = output_file
    job.error = error


def start_cleanup_thread():
    """Start background thread to cleanup old jobs (30+ min old)."""
    def cleanup():
        while True:
            time.sleep(_JOB_CLEANUP_INTERVAL)
            with _JOBS_LOCK:
                now = datetime.now()
                expired = [
                    jid for jid, job in _JOBS.items()
                    if (now - job.created) > timedelta(minutes=30)
                ]
                for jid in expired:
                    del _JOBS[jid]

    thread = threading.Thread(target=cleanup, daemon=True)
    thread.start()


# Start cleanup on module import
start_cleanup_thread()
