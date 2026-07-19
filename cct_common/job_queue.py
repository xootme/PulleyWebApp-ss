"""
job_queue.py — async job tracking + single-active-user session queueing,
with weekly trial/web-download rate limiting. Ported from
PulleyWebApp-ss/exporters/job_queue.py, generalized via configure().

Session state is persisted to a shared JSON file on disk so all gunicorn
workers see the same state. A threading lock protects in-process
reads/writes; a file lock (fcntl on Linux, msvcrt on Windows) prevents
cross-process races.

    from cct_common import job_queue

    job_queue.configure(log_dir="/path/to/logs")  # optional; see configure()
    job_queue.start_background_threads()          # queue processor + cleanup

    job = job_queue.create_job("step", params)
    job_queue.start_job(job.id)
    ...
    job_queue.finish_job(job.id, output_file="/download/x.step")

Unlike the original, background threads are NOT started as an import
side effect — call start_background_threads() explicitly once at app
startup. Everything else (file locking, session/queue semantics, rate
limit bookkeeping) is behavior-identical.

For an admin/debug dashboard, use the public getters instead of reaching
into module internals: get_all_jobs(), get_full_queue_snapshot(),
session_timeout_sec()/idle_timeout_sec()/trial_downloads_per_week()/
web_downloads_per_week() (the current configure()'d values), and
register_machine_id(session_id, machine_id) to attach a machine_id to an
active or queued session's record.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta

# ── configuration (call configure() once at app startup; defaults match
#    PulleyWebApp-ss's original values) ─────────────────────────────────
_log_dir = os.environ.get("CCT_LOG_DIR", os.path.join(os.getcwd(), "logs"))
_max_concurrent = 1
_session_timeout_sec = 5 * 60
_idle_timeout_sec = 60
_job_cleanup_interval_sec = 60
_session_cleanup_interval_sec = 2
_job_retention_minutes = 30
_trial_downloads_per_week = 2
_trial_retention_days = 7
_web_downloads_per_week = 2
_fp_ambiguity_ips = 5  # >= this many distinct IPs sharing a fingerprint -> allow


def configure(*, log_dir=None, max_concurrent=None, session_timeout_sec=None,
             idle_timeout_sec=None, job_cleanup_interval_sec=None,
             session_cleanup_interval_sec=None, job_retention_minutes=None,
             trial_downloads_per_week=None, trial_retention_days=None,
             web_downloads_per_week=None, fp_ambiguity_ips=None):
    """Override any subset of the module's config. Call once at startup,
    before create_job/create_session/etc. Unset values keep their
    current value (defaults on first call)."""
    global _log_dir, _max_concurrent, _session_timeout_sec, _idle_timeout_sec
    global _job_cleanup_interval_sec, _session_cleanup_interval_sec
    global _job_retention_minutes, _trial_downloads_per_week, _trial_retention_days
    global _web_downloads_per_week, _fp_ambiguity_ips
    global _SESSION_FILE, _TRIAL_DOWNLOADS_FILE, _WEB_DOWNLOADS_FILE
    if log_dir is not None:
        _log_dir = log_dir
        _SESSION_FILE = os.path.join(_log_dir, "sessions.json")
        _TRIAL_DOWNLOADS_FILE = os.path.join(_log_dir, "trial_downloads.json")
        _WEB_DOWNLOADS_FILE = os.path.join(_log_dir, "web_downloads.json")
    if max_concurrent is not None:
        _max_concurrent = max_concurrent
    if session_timeout_sec is not None:
        _session_timeout_sec = session_timeout_sec
    if idle_timeout_sec is not None:
        _idle_timeout_sec = idle_timeout_sec
    if job_cleanup_interval_sec is not None:
        _job_cleanup_interval_sec = job_cleanup_interval_sec
    if session_cleanup_interval_sec is not None:
        _session_cleanup_interval_sec = session_cleanup_interval_sec
    if job_retention_minutes is not None:
        _job_retention_minutes = job_retention_minutes
    if trial_downloads_per_week is not None:
        _trial_downloads_per_week = trial_downloads_per_week
    if trial_retention_days is not None:
        _trial_retention_days = trial_retention_days
    if web_downloads_per_week is not None:
        _web_downloads_per_week = web_downloads_per_week
    if fp_ambiguity_ips is not None:
        _fp_ambiguity_ips = fp_ambiguity_ips


def session_timeout_sec() -> float:
    return _session_timeout_sec


def idle_timeout_sec() -> float:
    return _idle_timeout_sec


def trial_downloads_per_week() -> int:
    return _trial_downloads_per_week


def web_downloads_per_week() -> int:
    return _web_downloads_per_week


_SESSION_FILE = os.path.join(_log_dir, "sessions.json")
_TRIAL_DOWNLOADS_FILE = os.path.join(_log_dir, "trial_downloads.json")
_WEB_DOWNLOADS_FILE = os.path.join(_log_dir, "web_downloads.json")

_JOBS = {}       # {job_id: Job}
_QUEUE = []      # [job_id, ...] waiting to process
_ACTIVE = set()  # {job_id, ...} currently processing
_LOCK = threading.Lock()
_SESSION_SEQ = 0  # monotonic counter for queue ordering (avoids same-timestamp ties)

_TRIAL_LOCK = threading.Lock()
_WEB_DL_LOCK = threading.Lock()


# ── file-backed shared state ────────────────────────────────────────────

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
    os.makedirs(_log_dir, exist_ok=True)
    if not os.path.exists(_SESSION_FILE):
        return None, {}
    try:
        with open(_SESSION_FILE, "r") as f:
            _file_lock(f, exclusive=False)
            try:
                data = json.load(f)
            finally:
                _file_unlock(f)
        return data.get("active"), data.get("sessions", {})
    except Exception:
        return None, {}


def _save_state(active_session, sessions):
    """Persist session state to disk atomically."""
    os.makedirs(_log_dir, exist_ok=True)
    tmp = _SESSION_FILE + ".tmp"
    with open(tmp, "w") as f:
        _file_lock(f, exclusive=True)
        try:
            json.dump({"active": active_session, "sessions": sessions}, f)
        finally:
            _file_unlock(f)
    os.replace(tmp, _SESSION_FILE)


def _load_and_lock():
    """Open the session file with an exclusive lock held open.
    Returns (fh, active, sessions). Caller must call _save_and_unlock(fh, ...).
    """
    os.makedirs(_log_dir, exist_ok=True)
    fh = open(_SESSION_FILE, "a+")  # creates if missing, allows read
    fh.seek(0)
    _file_lock(fh, exclusive=True)
    try:
        content = fh.read()
        data = json.loads(content) if content.strip() else {}
    except Exception:
        data = {}
    return fh, data.get("active"), data.get("sessions", {})


def _save_and_unlock(fh, active_session, sessions):
    try:
        fh.seek(0)
        fh.truncate()
        json.dump({"active": active_session, "sessions": sessions}, fh)
        fh.flush()
    finally:
        _file_unlock(fh)
        fh.close()


# ── job classes (in-memory only — single process lifecycle) ────────────

class Job:
    def __init__(self, job_type="job", params=None):
        self.id = str(uuid.uuid4())[:8]
        self.type = job_type
        self.status = "queued"
        self.created = datetime.now()
        self.started = None
        self.finished = None
        self.progress = 0
        self.output_file = None
        self.error = None
        self.params = params
        # True when the file was mirrored to a connected CAD add-in's watch
        # folder; the caller can then skip the redundant browser download.
        self.mirrored = False
        self.output_name = None

    def to_dict(self):
        with _LOCK:
            queue_position = None
            if self.status == "queued":
                try:
                    queue_position = _QUEUE.index(self.id) + 1
                except ValueError:
                    pass
            active_count = len(_ACTIVE)
        return {
            "id": self.id, "type": self.type, "status": self.status,
            "created": self.created.isoformat(),
            "started": self.started.isoformat() if self.started else None,
            "progress": self.progress, "queue_position": queue_position,
            "active_jobs": active_count, "output_file": self.output_file,
            "error": self.error, "mirrored": self.mirrored,
        }


def create_job(job_type="job", params=None):
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
        return {"queue": _QUEUE.copy(), "active": list(_ACTIVE),
                "max_concurrent": _max_concurrent}


def get_all_jobs():
    """Full job list + queue/active summary, for an admin/debug dashboard."""
    with _LOCK:
        jobs_list = []
        for job_id in sorted(_JOBS.keys()):
            job = _JOBS[job_id]
            jobs_list.append({
                "id": job.id, "status": job.status, "type": job.type,
                "progress": job.progress,
                "created": job.created.isoformat() if job.created else None,
                "started": job.started.isoformat() if job.started else None,
                "finished": job.finished.isoformat() if job.finished else None,
                "error": job.error,
                "queue_position": _QUEUE.index(job.id) + 1 if job.id in _QUEUE else None,
                "is_active": job.id in _ACTIVE,
            })
        return {
            "jobs": jobs_list, "queue_length": len(_QUEUE),
            "active_count": len(_ACTIVE), "max_concurrent": _max_concurrent,
        }


def start_job(job_id):
    with _LOCK:
        if job_id not in _QUEUE:
            return False
        _QUEUE.remove(job_id)
        _ACTIVE.add(job_id)
        job = _JOBS.get(job_id)
        if job:
            job.status = "processing"
            job.started = datetime.now()
        return True


def finish_job(job_id, output_file=None, error=None):
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job.status = "failed" if error else "done"
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
        if len(_ACTIVE) >= _max_concurrent or not _QUEUE:
            return
        next_job_id = _QUEUE[0]
    start_job(next_job_id)


# ── session management (shared via disk) ────────────────────────────────

def create_session():
    """Create a new session (immediate or queued). Returns status dict.

    Holds a file lock across the entire read-modify-write so concurrent
    requests from multiple workers cannot race and assign duplicate queue
    positions.
    """
    user_id = str(uuid.uuid4())[:8]
    session_id = str(uuid.uuid4())
    now = time.time()

    with _LOCK:
        fh, active, sessions = _load_and_lock()
        try:
            active, sessions = _expire_active(active, sessions, now)

            if active is None:
                active = {
                    "session_id": session_id, "user_id": user_id,
                    "started_at": now, "last_heartbeat": now,
                }
                _save_and_unlock(fh, active, sessions)
                return {"session_id": session_id, "user_id": user_id,
                        "is_active": True, "position": 0, "estimated_wait_sec": 0}
            else:
                global _SESSION_SEQ
                _SESSION_SEQ += 1
                sessions[session_id] = {
                    "user_id": user_id, "created_at": now,
                    "last_heartbeat": now, "seq": _SESSION_SEQ}
                position = _queue_position(session_id, sessions)
                elapsed = now - active["started_at"]
                remaining_active = max(0, _session_timeout_sec - elapsed)
                estimated_wait = remaining_active + (position - 1) * (_session_timeout_sec + 60)
                _save_and_unlock(fh, active, sessions)
                return {"session_id": session_id, "user_id": user_id,
                        "is_active": False, "position": position,
                        "estimated_wait_sec": estimated_wait}
        except Exception:
            _file_unlock(fh)
            fh.close()
            raise


def heartbeat(session_id):
    """Update last_heartbeat for active or queued session."""
    now = time.time()
    with _LOCK:
        active, sessions = _load_state()
        changed = False
        if active and active["session_id"] == session_id:
            active["last_heartbeat"] = now
            changed = True
        elif session_id in sessions:
            sessions[session_id]["last_heartbeat"] = now
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

        if active and active["session_id"] == session_id:
            remaining = max(0, _session_timeout_sec - (now - active["started_at"]))
            _save_state(active, sessions)
            return {"is_active": True, "position": 0,
                    "remaining_sec": remaining, "queue_length": len(sessions)}

        if session_id in sessions:
            position = _queue_position(session_id, sessions)
            remaining_active = (max(0, _session_timeout_sec - (now - active["started_at"]))
                               if active else 0)
            estimated_wait = remaining_active + (position - 1) * (_session_timeout_sec + 60)
            _save_state(active, sessions)
            return {"is_active": False, "position": position,
                    "estimated_wait_sec": estimated_wait,
                    "queue_length": len(sessions)}

        return {"error": "Session not found"}


def release_session(session_id):
    """Release active or queued session and promote next."""
    now = time.time()
    with _LOCK:
        active, sessions = _load_state()
        if active and active["session_id"] == session_id:
            active = None
        sessions.pop(session_id, None)
        active = _promote_next(active, sessions, now)
        _save_state(active, sessions)
    return True


def get_queue_info():
    """Return active user and queue length for display."""
    with _LOCK:
        active, sessions = _load_state()
    active_user = active["user_id"][:4] if active else "none"
    queue_length = len(sessions)
    return {"active_user": active_user, "queue_length": queue_length}


def get_full_queue_snapshot():
    """Active-session + full waiting-queue detail, for an admin dashboard."""
    now = time.time()
    with _LOCK:
        active, sessions = _load_state()

    active_out = None
    if active:
        elapsed = now - active["started_at"]
        idle = now - active.get("last_heartbeat", active["started_at"])
        active_out = {
            "user_id": active["user_id"],
            "session_id": active["session_id"][:8],
            "started_at": active["started_at"],
            "last_heartbeat": active.get("last_heartbeat", active["started_at"]),
            "elapsed_sec": round(elapsed),
            "idle_sec": round(idle),
            "remaining_sec": max(0, round(_session_timeout_sec - elapsed)),
        }

    queue_out = []
    for sid, s in sorted(sessions.items(), key=lambda x: x[1].get("seq", x[1]["created_at"])):
        pos = sum(
            1 for v in sessions.values()
            if v.get("seq", v["created_at"]) < s.get("seq", s["created_at"])
        ) + 1
        remaining_active = (max(0, _session_timeout_sec - (now - active["started_at"]))
                           if active else 0)
        wait = remaining_active + (pos - 1) * (_session_timeout_sec + 60)
        queue_out.append({
            "position": pos, "user_id": s["user_id"], "session_id": sid[:8],
            "created_at": s["created_at"],
            "last_heartbeat": s.get("last_heartbeat", s["created_at"]),
            "wait_sec": round(wait),
        })

    return {
        "active": active_out, "queue": queue_out, "queue_length": len(sessions),
        "timeout_sec": _session_timeout_sec, "idle_timeout_sec": _idle_timeout_sec,
    }


def register_machine_id(session_id, machine_id) -> bool:
    """Attach a machine_id to an active or queued session's record.

    Returns False if the session isn't found (active or queued) — the
    caller should map that to a 403 SESSION_NOT_FOUND response.
    """
    with _LOCK:
        active, sessions = _load_state()
        if active and active["session_id"] == session_id:
            active["machine_id"] = machine_id
            _save_state(active, sessions)
            return True
        if session_id in sessions:
            sessions[session_id]["machine_id"] = machine_id
            _save_state(active, sessions)
            return True
        return False


# ── internal session helpers ─────────────────────────────────────────────

def _queue_position(session_id, sessions):
    """1-based position of session_id among waiting sessions (oldest = 1).
    Uses seq (insertion order counter) if available, falls back to created_at.
    """
    sess = sessions[session_id]
    my_key = sess.get("seq", sess["created_at"])
    return sum(
        1 for s in sessions.values()
        if s.get("seq", s["created_at"]) < my_key
    ) + 1


def _promote_next(active, sessions, now):
    """Promote the oldest waiting session to active. Returns updated active."""
    if active is not None or not sessions:
        return active
    oldest_id = min(sessions, key=lambda s: sessions[s]["created_at"])
    oldest = sessions.pop(oldest_id)
    return {"session_id": oldest_id, "user_id": oldest["user_id"],
            "started_at": now, "last_heartbeat": now}


def _expire_active(active, sessions, now):
    """Expire active session if idle or timed out; promote next if so."""
    if active is None:
        return active, sessions
    elapsed = now - active["started_at"]
    idle = now - active.get("last_heartbeat", active["started_at"])
    # Hard session cap only applies when others are waiting; with an empty
    # queue the idle timeout alone is sufficient (avoids evicting a solo
    # user who takes longer than the cap to configure something complex).
    hard_cap_exceeded = sessions and elapsed > _session_timeout_sec
    if idle > _idle_timeout_sec or hard_cap_exceeded:
        active = _promote_next(None, sessions, now)
    return active, sessions


def _cleanup_stale_queued_sessions():
    """Drop waiting sessions whose browser has been gone for >30 seconds."""
    now = time.time()
    active, sessions = _load_state()
    stale = [sid for sid, s in sessions.items()
             if (now - s.get("last_heartbeat", s["created_at"])) > 30]
    if stale:
        for sid in stale:
            del sessions[sid]
        _save_state(active, sessions)


def clear_stale_on_startup():
    """Clear any active session left over from a previous server process.

    Call once at Flask startup. A new process means the previous server
    (and its active session) is gone, so any persisted active session is
    stale by definition and must be cleared to unblock the queue. Queued
    waiting sessions are also dropped since their browsers are gone.
    """
    if not os.path.exists(_SESSION_FILE):
        return
    with _LOCK:
        fh, active, sessions = _load_and_lock()
        try:
            if active is not None or sessions:
                _save_and_unlock(fh, None, {})
            else:
                _file_unlock(fh)
                fh.close()
        except Exception:
            _file_unlock(fh)
            fh.close()
            raise


# ── trial download tracking (machine-id based) ──────────────────────────

def register_trial_download(machine_id, fmt="step"):
    with _TRIAL_LOCK:
        data = _load_trial_downloads()
        now = datetime.now()
        week_start = now - timedelta(days=now.weekday())
        if machine_id not in data:
            data[machine_id] = []
        this_week = [d for d in data[machine_id]
                    if datetime.fromisoformat(d["timestamp"]) >= week_start]
        count_this_week = len(this_week)
        allowed = count_this_week < _trial_downloads_per_week
        if allowed:
            data[machine_id].append({"timestamp": now.isoformat(), "format": fmt})
            _save_trial_downloads(data)
        return allowed, count_this_week, _trial_downloads_per_week


def _load_trial_downloads():
    if not os.path.exists(_TRIAL_DOWNLOADS_FILE):
        return {}
    try:
        with open(_TRIAL_DOWNLOADS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_trial_downloads(data):
    os.makedirs(_log_dir, exist_ok=True)
    with open(_TRIAL_DOWNLOADS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def cleanup_old_trial_downloads():
    with _TRIAL_LOCK:
        data = _load_trial_downloads()
        cutoff = datetime.now() - timedelta(days=_trial_retention_days)
        for mid in list(data.keys()):
            data[mid] = [d for d in data[mid]
                        if datetime.fromisoformat(d["timestamp"]) >= cutoff]
            if not data[mid]:
                del data[mid]
        if data:
            _save_trial_downloads(data)


# ── web download tracking (browser fingerprint based) ───────────────────

def _web_week_key():
    y, w, _ = datetime.now().isocalendar()
    return f"{y}-W{w:02d}"


def check_web_download(fp: str, ip: str):
    """Check whether this browser fingerprint may download this week.

    Returns (allowed, count, limit). Fails open on any error or
    ambiguity — the caller should allow the download whenever allowed
    is True.
    """
    try:
        with _WEB_DL_LOCK:
            data = _load_web_downloads()
            wk = _web_week_key()
            wk_rec = data.get(fp, {}).get(wk, {"count": 0, "ips": []})
            count = wk_rec["count"]
            # Ambiguous fingerprint: too many distinct IPs -> allow (shared machine/VPN/NAT)
            if len(set(wk_rec["ips"])) >= _fp_ambiguity_ips:
                return True, count, _web_downloads_per_week
            return count < _web_downloads_per_week, count, _web_downloads_per_week
    except Exception:
        return True, 0, _web_downloads_per_week  # fail open


def record_web_download(fp: str, ip: str):
    """Increment the download counter for this fingerprint. Fails silently."""
    try:
        with _WEB_DL_LOCK:
            data = _load_web_downloads()
            wk = _web_week_key()
            fp_rec = data.setdefault(fp, {})
            wk_rec = fp_rec.setdefault(wk, {"count": 0, "ips": []})
            wk_rec["count"] += 1
            if ip and ip not in wk_rec["ips"]:
                wk_rec["ips"].append(ip)
            # Prune all weeks except the current one for this fingerprint.
            for old_wk in [k for k in fp_rec if k != wk]:
                del fp_rec[old_wk]
            _save_web_downloads(data)
    except Exception:
        pass


def _load_web_downloads():
    if not os.path.exists(_WEB_DOWNLOADS_FILE):
        return {}
    try:
        with open(_WEB_DOWNLOADS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_web_downloads(data):
    os.makedirs(_log_dir, exist_ok=True)
    with open(_WEB_DOWNLOADS_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── background threads (opt-in — call explicitly, not on import) ───────

def start_queue_processor():
    def worker():
        while True:
            time.sleep(1)
            process_next()
    threading.Thread(target=worker, daemon=True).start()


def start_cleanup_thread():
    def cleanup():
        job_cleanup_counter = 0
        trial_cleanup_counter = 0
        while True:
            time.sleep(_session_cleanup_interval_sec)
            with _LOCK:
                _cleanup_stale_queued_sessions()

                job_cleanup_counter += 1
                if job_cleanup_counter >= (_job_cleanup_interval_sec // _session_cleanup_interval_sec):
                    job_cleanup_counter = 0
                    now = datetime.now()
                    expired = [jid for jid, job in _JOBS.items()
                              if (now - job.created) > timedelta(minutes=_job_retention_minutes)]
                    for jid in expired:
                        _JOBS.pop(jid, None)
                        if jid in _QUEUE:
                            _QUEUE.remove(jid)
                        _ACTIVE.discard(jid)

                trial_cleanup_counter += 1
                if trial_cleanup_counter >= (86400 // _session_cleanup_interval_sec):
                    trial_cleanup_counter = 0
                    cleanup_old_trial_downloads()

    threading.Thread(target=cleanup, daemon=True).start()


def start_background_threads():
    """Start the queue-processor and cleanup daemon threads. Call once at
    app startup — NOT automatic on import (unlike the original module)."""
    start_queue_processor()
    start_cleanup_thread()


def clear_all_state():
    global _JOBS, _QUEUE, _ACTIVE
    with _LOCK:
        _JOBS.clear()
        _QUEUE.clear()
        _ACTIVE.clear()
        _save_state(None, {})
