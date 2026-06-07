"""Job queue for async STEP/DXF generation with single-user session queueing."""
import json
import uuid
import time
import threading
import os
from datetime import datetime, timedelta
from pathlib import Path

_JOBS = {}  # {job_id: Job}
_QUEUE = []  # [job_id, job_id, ...] waiting to process
_ACTIVE = set()  # {job_id, ...} currently processing
_MAX_CONCURRENT = 1  # SINGLE USER at a time (5 min limit, 1 min idle logout)
_LOCK = threading.Lock()
_JOB_CLEANUP_INTERVAL = 60
_SESSION_CLEANUP_INTERVAL = 10  # Check for stale sessions every 10 seconds

# Session management (single-user access)
_LOG_DIR = os.environ.get('PULLEY_LOG_DIR',
                          os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs'))
_SESSIONS = {}  # {session_id: {'user_id': str, 'started_at': time, 'last_heartbeat': time}}
_ACTIVE_SESSION = None  # {session_id, user_id, started_at, last_heartbeat}
SESSION_TIMEOUT_SEC = 5 * 60  # 5 minutes
IDLE_TIMEOUT_SEC = 60  # 1 minute idle logout

# Trial download tracking (FreeCAD addin)
_TRIAL_DOWNLOADS_FILE = os.path.join(_LOG_DIR, 'trial_downloads.json')
_TRIAL_LOCK = threading.Lock()
TRIAL_DOWNLOADS_PER_WEEK = 2
TRIAL_RETENTION_DAYS = 7  # Keep old entries for this long before cleanup


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


# ── Session Management (Single-User Access) ────────────────────────────────

def create_session():
    """Create a new session (immediate or queued)."""
    global _ACTIVE_SESSION
    user_id = str(uuid.uuid4())[:8]
    session_id = str(uuid.uuid4())
    now = time.time()

    with _LOCK:
        _cleanup_expired_session()

        if _ACTIVE_SESSION is None:
            # Grant immediate access
            _ACTIVE_SESSION = {
                'session_id': session_id,
                'user_id': user_id,
                'started_at': now,
                'last_heartbeat': now,
            }
            return {
                'session_id': session_id,
                'user_id': user_id,
                'is_active': True,
                'position': 0,
                'estimated_wait_sec': 0,
            }
        else:
            # Add to queue
            _SESSIONS[session_id] = {
                'user_id': user_id,
                'created_at': now,
                'last_heartbeat': now,
            }
            position = len(_SESSIONS)
            # Calculate wait based on remaining active session time + 1 min idle buffer
            elapsed = now - _ACTIVE_SESSION['started_at']
            remaining_active = max(0, SESSION_TIMEOUT_SEC - elapsed)
            estimated_wait = remaining_active + (position - 1) * (SESSION_TIMEOUT_SEC + 60)
            return {
                'session_id': session_id,
                'user_id': user_id,
                'is_active': False,
                'position': position,
                'estimated_wait_sec': estimated_wait,
            }


def _cleanup_expired_session():
    """Check if active session expired; clear if so and promote next from queue."""
    global _ACTIVE_SESSION
    if not _ACTIVE_SESSION:
        return

    now = time.time()
    elapsed = now - _ACTIVE_SESSION['started_at']
    idle = now - _ACTIVE_SESSION.get('last_heartbeat', _ACTIVE_SESSION['started_at'])

    expired = False

    # Idle timeout: 1 min
    if idle > IDLE_TIMEOUT_SEC:
        _ACTIVE_SESSION = None
        expired = True

    # Session timeout: 5 min
    elif elapsed > SESSION_TIMEOUT_SEC:
        _ACTIVE_SESSION = None
        expired = True

    # If expired, promote next from queue
    if expired and _SESSIONS:
        # Find oldest queued session (first created)
        oldest_id = min(_SESSIONS.keys(), key=lambda s: _SESSIONS[s]['created_at'])
        oldest = _SESSIONS[oldest_id]
        del _SESSIONS[oldest_id]

        _ACTIVE_SESSION = {
            'session_id': oldest_id,
            'user_id': oldest['user_id'],
            'started_at': now,
            'last_heartbeat': now,
        }


def _cleanup_stale_queued_sessions():
    """Remove queued sessions whose browser has genuinely disconnected.

    Waiting sessions are NOT subject to idle or session timeouts — only the
    active session expires. We only remove a waiting session if its heartbeat
    has been absent for 10 minutes (browser closed / network lost).
    """
    global _SESSIONS
    now = time.time()
    stale_timeout = 10 * 60  # 10 minutes — browser genuinely gone
    to_remove = [
        sid for sid, sess in _SESSIONS.items()
        if (now - sess.get('last_heartbeat', sess['created_at'])) > stale_timeout
    ]
    for sid in to_remove:
        del _SESSIONS[sid]


# ── Trial Download Tracking (FreeCAD addin) ────────────────────────────────

def register_trial_download(machine_id, fmt='step'):
    """Register a download from a trial user (FreeCAD addin).

    Returns: (allowed, count_this_week, limit)
    - allowed: True if within weekly limit
    - count_this_week: current download count for this week
    - limit: TRIAL_DOWNLOADS_PER_WEEK
    """
    with _TRIAL_LOCK:
        # Load existing data
        data = _load_trial_downloads()
        now = datetime.now()
        week_start = now - timedelta(days=now.weekday())  # Monday of this week

        # Get or create entry for this machine
        if machine_id not in data:
            data[machine_id] = []

        # Count downloads this week
        this_week = [
            d for d in data[machine_id]
            if datetime.fromisoformat(d['timestamp']) >= week_start
        ]

        count_this_week = len(this_week)
        allowed = count_this_week < TRIAL_DOWNLOADS_PER_WEEK

        # Register this download if allowed
        if allowed:
            data[machine_id].append({
                'timestamp': now.isoformat(),
                'format': fmt,
            })
            _save_trial_downloads(data)

        return allowed, count_this_week, TRIAL_DOWNLOADS_PER_WEEK


def _load_trial_downloads():
    """Load trial downloads data from file."""
    if not os.path.exists(_TRIAL_DOWNLOADS_FILE):
        return {}
    try:
        with open(_TRIAL_DOWNLOADS_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_trial_downloads(data):
    """Save trial downloads data to file."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_TRIAL_DOWNLOADS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def _cleanup_old_trial_downloads():
    """Remove trial download entries older than TRIAL_RETENTION_DAYS."""
    with _TRIAL_LOCK:
        data = _load_trial_downloads()
        cutoff = datetime.now() - timedelta(days=TRIAL_RETENTION_DAYS)

        for machine_id in list(data.keys()):
            # Keep only recent entries
            data[machine_id] = [
                d for d in data[machine_id]
                if datetime.fromisoformat(d['timestamp']) >= cutoff
            ]
            # Remove machine_id if no entries left
            if not data[machine_id]:
                del data[machine_id]

        if data:
            _save_trial_downloads(data)


def heartbeat(session_id):
    """Update session last-heartbeat (keep alive)."""
    with _LOCK:
        if _ACTIVE_SESSION and _ACTIVE_SESSION['session_id'] == session_id:
            _ACTIVE_SESSION['last_heartbeat'] = time.time()
            return True
        # Also update for queued sessions
        if session_id in _SESSIONS:
            _SESSIONS[session_id]['last_heartbeat'] = time.time()
            return True
        return False


def get_session_status(session_id):
    """Get status of a session."""
    with _LOCK:
        _cleanup_expired_session()

        if _ACTIVE_SESSION and _ACTIVE_SESSION['session_id'] == session_id:
            now = time.time()
            elapsed = now - _ACTIVE_SESSION['started_at']
            remaining = max(0, SESSION_TIMEOUT_SEC - elapsed)
            return {
                'is_active': True,
                'position': 0,
                'remaining_sec': remaining,
                'queue_length': len(_SESSIONS),
            }

        # Check if in queue
        if session_id in _SESSIONS:
            now = time.time()
            position = len([s for s in _SESSIONS if _SESSIONS[s]['created_at'] < _SESSIONS[session_id]['created_at']]) + 1

            # Calculate wait based on remaining active session time + others ahead
            if _ACTIVE_SESSION:
                elapsed = now - _ACTIVE_SESSION['started_at']
                remaining_active = max(0, SESSION_TIMEOUT_SEC - elapsed)
            else:
                remaining_active = 0

            estimated_wait = remaining_active + (position - 1) * (SESSION_TIMEOUT_SEC + 60)
            return {
                'is_active': False,
                'position': position,
                'estimated_wait_sec': estimated_wait,
                'queue_length': len(_SESSIONS),
            }

        return {'error': 'Session not found'}


def release_session(session_id):
    """Manually release a session and promote next from queue."""
    global _ACTIVE_SESSION
    with _LOCK:
        # If releasing active session, clear it
        if _ACTIVE_SESSION and _ACTIVE_SESSION['session_id'] == session_id:
            _ACTIVE_SESSION = None

        # Remove from queue if there
        _SESSIONS.pop(session_id, None)

        # Promote next queued session to active
        if _SESSIONS:
            oldest_id = min(_SESSIONS.keys(), key=lambda s: _SESSIONS[s]['created_at'])
            oldest = _SESSIONS[oldest_id]
            del _SESSIONS[oldest_id]

            now = time.time()
            _ACTIVE_SESSION = {
                'session_id': oldest_id,
                'user_id': oldest['user_id'],
                'started_at': now,
                'last_heartbeat': now,
            }

    return True


def get_queue_info():
    """Get queue length and active user info."""
    with _LOCK:
        active_user = _ACTIVE_SESSION['user_id'][:4] if _ACTIVE_SESSION else 'none'
        queue_length = len(_SESSIONS)
    return {'active_user': active_user, 'queue_length': queue_length}


def start_queue_processor():
    """Background thread that processes the queue."""
    def worker():
        while True:
            time.sleep(1)
            process_next()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()


def start_cleanup_thread():
    """Background thread to cleanup old jobs, stale sessions, and trial downloads."""
    def cleanup():
        job_cleanup_counter = 0
        trial_cleanup_counter = 0
        while True:
            time.sleep(_SESSION_CLEANUP_INTERVAL)
            with _LOCK:
                # Clean up stale queued sessions every 10 seconds (disconnected browsers)
                _cleanup_stale_queued_sessions()

                # Clean up old jobs every 60 seconds
                job_cleanup_counter += 1
                if job_cleanup_counter >= (_JOB_CLEANUP_INTERVAL // _SESSION_CLEANUP_INTERVAL):
                    job_cleanup_counter = 0
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

                # Clean up old trial downloads every 24 hours (but check every 10 sec)
                trial_cleanup_counter += 1
                if trial_cleanup_counter >= (86400 // _SESSION_CLEANUP_INTERVAL):  # 24 hours
                    trial_cleanup_counter = 0
                    _cleanup_old_trial_downloads()

    thread = threading.Thread(target=cleanup, daemon=True)
    thread.start()


def clear_all_state():
    """Clear all sessions and jobs for testing."""
    global _SESSIONS, _ACTIVE_SESSION, _JOBS, _QUEUE, _ACTIVE
    with _LOCK:
        _SESSIONS.clear()
        _ACTIVE_SESSION = None
        _JOBS.clear()
        _QUEUE.clear()
        _ACTIVE.clear()


# Start background threads on module import
start_queue_processor()
start_cleanup_thread()
