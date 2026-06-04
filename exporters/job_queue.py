"""Job queue for async STEP/DXF generation with queueing and throttling."""
import json
import uuid
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

_JOBS = {}  # {job_id: Job}
_QUEUE = []  # [job_id, job_id, ...] waiting to process
_ACTIVE = set()  # {job_id, ...} currently processing
_MAX_CONCURRENT = 2  # only 2 jobs at once on Starter tier
_LOCK = threading.Lock()
_JOB_CLEANUP_INTERVAL = 60


class Job:
    """Represents a download job (STEP, DXF, etc)."""
    def __init__(self, job_type='all-step', params=None):
        self.id = str(uuid.uuid4())[:8]
        self.type = job_type
        self.status = 'queued'  # queued, waiting, processing, done, failed
        self.created = datetime.now()
        self.started = None  # when processing started
        self.finished = None  # when finished
        self.progress = 0  # 0-100, overall progress
        self.output_file = None
        self.error = None
        self.params = params  # user's request params

    def to_dict(self):
        with _LOCK:
            queue_position = None
            if self.status == 'queued':
                try:
                    queue_position = _QUEUE.index(self.id) + 1
                except ValueError:
                    pass

            active_count = len(_ACTIVE)

        return {
            'id': self.id,
            'type': self.type,
            'status': self.status,
            'created': self.created.isoformat(),
            'started': self.started.isoformat() if self.started else None,
            'progress': self.progress,
            'queue_position': queue_position,
            'active_jobs': active_count,
            'output_file': self.output_file,
            'error': self.error,
        }


def create_job(job_type='all-step', params=None):
    """Create a new job and enqueue it."""
    job = Job(job_type, params)
    with _LOCK:
        _JOBS[job.id] = job
        _QUEUE.append(job.id)
    return job


def get_job(job_id):
    """Get job by ID."""
    with _LOCK:
        return _JOBS.get(job_id)


def get_queue_status():
    """Get current queue and active jobs."""
    with _LOCK:
        return {
            'queue': _QUEUE.copy(),
            'active': list(_ACTIVE),
            'max_concurrent': _MAX_CONCURRENT,
        }


def start_job(job_id):
    """Move job from queue to active processing."""
    with _LOCK:
        if job_id not in _QUEUE:
            return False
        _QUEUE.remove(job_id)
        _ACTIVE.add(job_id)
        job = _JOBS.get(job_id)
        if job:
            job.status = 'processing'
            job.started = datetime.now()
        return True


def finish_job(job_id, output_file=None, error=None):
    """Mark job as done and process next queued job."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job.status = 'failed' if error else 'done'
            job.finished = datetime.now()
            job.output_file = output_file
            job.error = error
        if job_id in _ACTIVE:
            _ACTIVE.remove(job_id)

    # Process next job in queue
    process_next()


def update_progress(job_id, progress):
    """Update a job's progress (0-100)."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job.progress = min(100, max(0, progress))


def process_next():
    """Start next queued job if capacity available."""
    with _LOCK:
        if len(_ACTIVE) >= _MAX_CONCURRENT or not _QUEUE:
            return
        next_job_id = _QUEUE[0]

    start_job(next_job_id)


def start_queue_processor():
    """Background thread that processes the queue."""
    def worker():
        while True:
            time.sleep(1)
            process_next()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()


def start_cleanup_thread():
    """Background thread to cleanup old jobs (30+ min old)."""
    def cleanup():
        while True:
            time.sleep(_JOB_CLEANUP_INTERVAL)
            with _LOCK:
                now = datetime.now()
                expired = [
                    jid for jid, job in _JOBS.items()
                    if (now - job.created) > timedelta(minutes=30)
                ]
                for jid in expired:
                    if jid in _JOBS:
                        del _JOBS[jid]
                    if jid in _QUEUE:
                        _QUEUE.remove(jid)
                    if jid in _ACTIVE:
                        _ACTIVE.discard(jid)

    thread = threading.Thread(target=cleanup, daemon=True)
    thread.start()


# Start background threads on module import
start_queue_processor()
start_cleanup_thread()
