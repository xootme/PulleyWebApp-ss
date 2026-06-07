"""Job queue for async STEP/DXF generation with single-user session queueing.

Session state is persisted to a shared JSON file on disk so all gunicorn
workers see the same state. A threading lock protects in-process reads/writes;
a file lock (fcntl on Linux, msvcrt on Windows) prevents cross-process races.
"""
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
_MAX_CONCURRENT = 1
_LOCK = threading.Lock()
_JOB_CLEANUP_INTERVAL = 60
_SESSION_CLEANUP_INTERVAL = 10

_LOG_DIR = os.environ.get('PULLEY_LOG_DIR',
                          os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs'))

SESSION_TIMEOUT_SEC = 5 * 60   # 5 minutes hard cap
IDLE_TIMEOUT_SEC    = 60        # 1 minute idle (active session only)

# Shared session state file — written by every worker, read by every worker
_SESSION_FILE = os.path.join(_LOG_DIR, 'sessions.json')

# Trial download tracking
_TRIAL_DOWNLOADS_FILE = os.path.join(_LOG_DIR, 'trial_downloads.json')
_TRIAL_LOCK = threading.Lock()
TRIAL_DOWNLOADS_PER_WEEK = 2
TRIAL_RETENTION_DAYS = 7


# ── File-backed shared state ───────────────────────────────────────────────────

def _file_lock(fh, exclusive=True):
    """Cross-platform advisory file lock."""
    try:
        import fcntl
        op = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(fh, op)
    except ImportError:
        import msvcrt
        if exclusive:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)


def _file_unlock(fh):
    try:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_UN)
    except ImportError:
        import msvcrt
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass


def _load_state():
    """Load session state from disk. Returns (active_session, sessions_dict)."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    if not os.path.exists(_SESSION_FILE):
        return None, {}
    try:
        with open(_SESSION_FILE, 'r') as f:
            _file_lock(f, exclusive=False)
            try:
                data = json.load(f)
            finally:
                _file_unlock(f)
        return data.get('active'), data.get('sessions', {})
    except Exception:
        return None, {}


def _save_state(active_session, sessions):
    """Persist session state to disk atomically."""
    os.makedirs(_LOG_DIR, exist_ok=True)
    tmp = _SESSION_FILE + '.tmp'
    with open(tmp, 'w') as f:
        _file_lock(f, exclusive=True)
        try:
            json.dump({'active': active_session, 'sessions': sessions}, f)
        finally:
            _file_unlock(f)
    os.replace(tmp, _SESSION_FILE)


# ── Job classes (in-memory only — single request lifecycle) ───────────────────

class Job:
    def __init__(self, job_type='all-step', params=None):
        self.id = str(uuid.uuid4())[:8]
        self.type = job_type
        self.status = 'queued'
        self.created = datetime.now()
        self.started = None
        self.finished = None
        self.progress = 0
        self.output_file = None
        self.error = None
        self.params = params

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
            'id': self.id, 'type': self.type, 'status': self.status,
            'created': self.created.isoformat(),
            'started': self.started.isoformat() if self.started else None,
            'progress': self.progress, 'queue_position': queue_position,
            'active_jobs': active_count, 'output_file': self.output_file,
            'error': self.error,
        }


def create_job(job_type='all-step', params=None):
    job = Job(job_type, params)
    with _LOCK:
        _JOBS[job.id] = job
        _QUEUE.append(job.id)
    return job


def get_job(job_id):
    with _LOCK:
        return _JOBS.get(job_id)


def get_queue_status():
    with _LOCK:
        return {'queue': _QUEUE.copy(), 'active': list(_ACTIVE),
                'max_concurrent': _MAX_CONCURRENT}


def start_job(job_id):
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
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job.status = 'failed' if error else 'done'
            job.finished = datetime.now()
            job.output_file = output_file
            job.error = error
        if job_id in _ACTIVE:
            _ACTIVE.remove(job_id)
    process_next()


def update_progress(job_id, progress):
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job.progress = min(100, max(0, progress))


def process_next():
    with _LOCK:
        if len(_ACTIVE) >= _MAX_CONCURRENT or not _QUEUE:
            return
        next_job_id = _QUEUE[0]
    start_job(next_job_id)


# ── Session management (shared via disk) ──────────────────────────────────────

def create_session():
    """Create a new session (immediate or queued). Returns status dict."""
    user_id    = str(uuid.uuid4())[:8]
    session_id = str(uuid.uuid4())
    now        = time.time()

    with _LOCK:
        active, sessions = _load_state()
        active, sessions = _expire_active(active, sessions, now)

        if active is None:
            active = {
                'session_id':    session_id,
                'user_id':       user_id,
                'started_at':    now,
                'last_heartbeat': now,
            }
            _save_state(active, sessions)
            return {'session_id': session_id, 'user_id': user_id,
                    'is_active': True, 'position': 0, 'estimated_wait_sec': 0}
        else:
            sessions[session_id] = {
                'user_id': user_id, 'created_at': now, 'last_heartbeat': now}
            position = _queue_position(session_id, sessions)
            elapsed  = now - active['started_at']
            remaining_active = max(0, SESSION_TIMEOUT_SEC - elapsed)
            estimated_wait   = remaining_active + (position - 1) * (SESSION_TIMEOUT_SEC + 60)
            _save_state(active, sessions)
            return {'session_id': session_id, 'user_id': user_id,
                    'is_active': False, 'position': position,
                    'estimated_wait_sec': estimated_wait}


def heartbeat(session_id):
    """Update last_heartbeat for active or queued session."""
    now = time.time()
    with _LOCK:
        active, sessions = _load_state()
        changed = False
        if active and active['session_id'] == session_id:
            active['last_heartbeat'] = now
            changed = True
        elif session_id in sessions:
            sessions[session_id]['last_heartbeat'] = now
            changed = True
        if changed:
            _save_state(active, sessions)
        return changed


def get_session_status(session_id):
    """Return status dict for a session."""
    now = time.time()
    with _LOCK:
        active, sessions = _load_state()
        active, sessions = _expire_active(active, sessions, now)

        if active and active['session_id'] == session_id:
            remaining = max(0, SESSION_TIMEOUT_SEC - (now - active['started_at']))
            _save_state(active, sessions)
            return {'is_active': True, 'position': 0,
                    'remaining_sec': remaining, 'queue_length': len(sessions)}

        if session_id in sessions:
            position = _queue_position(session_id, sessions)
            if active:
                remaining_active = max(0, SESSION_TIMEOUT_SEC - (now - active['started_at']))
            else:
                remaining_active = 0
            estimated_wait = remaining_active + (position - 1) * (SESSION_TIMEOUT_SEC + 60)
            _save_state(active, sessions)
            return {'is_active': False, 'position': position,
                    'estimated_wait_sec': estimated_wait,
                    'queue_length': len(sessions)}

        return {'error': 'Session not found'}


def release_session(session_id):
    """Release active or queued session and promote next."""
    now = time.time()
    with _LOCK:
        active, sessions = _load_state()
        if active and active['session_id'] == session_id:
            active = None
        sessions.pop(session_id, None)
        active = _promote_next(active, sessions, now)
        _save_state(active, sessions)
    return True


def get_queue_info():
    """Return active user and queue length for display."""
    with _LOCK:
        active, sessions = _load_state()
    active_user  = active['user_id'][:4] if active else 'none'
    queue_length = len(sessions)
    return {'active_user': active_user, 'queue_length': queue_length}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _queue_position(session_id, sessions):
    """1-based position of session_id among waiting sessions (oldest = 1)."""
    sess = sessions[session_id]
    return sum(1 for s in sessions.values()
               if s['created_at'] < sess['created_at']) + 1


def _promote_next(active, sessions, now):
    """Promote the oldest waiting session to active. Returns updated active."""
    if active is not None or not sessions:
        return active
    oldest_id = min(sessions, key=lambda s: sessions[s]['created_at'])
    oldest    = sessions.pop(oldest_id)
    return {'session_id': oldest_id, 'user_id': oldest['user_id'],
            'started_at': now, 'last_heartbeat': now}


def _expire_active(active, sessions, now):
    """Expire active session if idle or timed out; promote next if so."""
    if active is None:
        return active, sessions
    elapsed = now - active['started_at']
    idle    = now - active.get('last_heartbeat', active['started_at'])
    if idle > IDLE_TIMEOUT_SEC or elapsed > SESSION_TIMEOUT_SEC:
        active = _promote_next(None, sessions, now)
    return active, sessions


def _cleanup_stale_queued_sessions():
    """Drop waiting sessions whose browser has been gone for >30 seconds."""
    now = time.time()
    active, sessions = _load_state()
    stale = [sid for sid, s in sessions.items()
             if (now - s.get('last_heartbeat', s['created_at'])) > 30]
    if stale:
        for sid in stale:
            del sessions[sid]
        _save_state(active, sessions)


# ── Trial download tracking (unchanged) ───────────────────────────────────────

def register_trial_download(machine_id, fmt='step'):
    with _TRIAL_LOCK:
        data = _load_trial_downloads()
        now  = datetime.now()
        week_start = now - timedelta(days=now.weekday())
        if machine_id not in data:
            data[machine_id] = []
        this_week = [d for d in data[machine_id]
                     if datetime.fromisoformat(d['timestamp']) >= week_start]
        count_this_week = len(this_week)
        allowed = count_this_week < TRIAL_DOWNLOADS_PER_WEEK
        if allowed:
            data[machine_id].append({'timestamp': now.isoformat(), 'format': fmt})
            _save_trial_downloads(data)
        return allowed, count_this_week, TRIAL_DOWNLOADS_PER_WEEK


def _load_trial_downloads():
    if not os.path.exists(_TRIAL_DOWNLOADS_FILE):
        return {}
    try:
        with open(_TRIAL_DOWNLOADS_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_trial_downloads(data):
    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_TRIAL_DOWNLOADS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def _cleanup_old_trial_downloads():
    with _TRIAL_LOCK:
        data   = _load_trial_downloads()
        cutoff = datetime.now() - timedelta(days=TRIAL_RETENTION_DAYS)
        for mid in list(data.keys()):
            data[mid] = [d for d in data[mid]
                         if datetime.fromisoformat(d['timestamp']) >= cutoff]
            if not data[mid]:
                del data[mid]
        if data:
            _save_trial_downloads(data)


# ── Background threads ─────────────────────────────────────────────────────────

def start_queue_processor():
    def worker():
        while True:
            time.sleep(1)
            process_next()
    threading.Thread(target=worker, daemon=True).start()


def start_cleanup_thread():
    def cleanup():
        job_cleanup_counter   = 0
        trial_cleanup_counter = 0
        while True:
            time.sleep(_SESSION_CLEANUP_INTERVAL)
            with _LOCK:
                _cleanup_stale_queued_sessions()

                job_cleanup_counter += 1
                if job_cleanup_counter >= (_JOB_CLEANUP_INTERVAL // _SESSION_CLEANUP_INTERVAL):
                    job_cleanup_counter = 0
                    now = datetime.now()
                    expired = [jid for jid, job in _JOBS.items()
                               if (now - job.created) > timedelta(minutes=30)]
                    for jid in expired:
                        _JOBS.pop(jid, None)
                        if jid in _QUEUE: _QUEUE.remove(jid)
                        _ACTIVE.discard(jid)

                trial_cleanup_counter += 1
                if trial_cleanup_counter >= (86400 // _SESSION_CLEANUP_INTERVAL):
                    trial_cleanup_counter = 0
                    _cleanup_old_trial_downloads()

    threading.Thread(target=cleanup, daemon=True).start()


def clear_all_state():
    global _JOBS, _QUEUE, _ACTIVE
    with _LOCK:
        _JOBS.clear()
        _QUEUE.clear()
        _ACTIVE.clear()
        _save_state(None, {})


# Start background threads on module import
start_queue_processor()
start_cleanup_thread()
