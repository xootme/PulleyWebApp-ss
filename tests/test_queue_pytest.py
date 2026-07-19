"""
Queue system tests — functional + stress.

Run against a local server started with PULLEY_TESTING=1:

    PULLEY_TESTING=1 python app.py          # terminal 1
    pytest tests/test_queue_pytest.py -v    # terminal 2

Or use the Makefile target:
    make test-queue
"""
import threading
import time
import subprocess
import sys
import os
import pytest
import requests

BASE_URL = os.environ.get('PULLEY_TEST_URL', 'http://localhost:5000')

def _base():
    """Return current BASE_URL (allows run_tests.py to patch it after import)."""
    import tests.test_queue_pytest as _self
    return _self.BASE_URL

# ── Helpers ───────────────────────────────────────────────────────────────────

def create():
    r = requests.post(f'{_base()}/api/session/create', timeout=5)
    r.raise_for_status()
    return r.json()

def status(sid):
    r = requests.get(f'{_base()}/api/session/status?session_id={sid}', timeout=5)
    r.raise_for_status()
    return r.json()

def beat(sid):
    r = requests.post(f'{_base()}/api/session/heartbeat',
                      json={'session_id': sid}, timeout=5)
    r.raise_for_status()
    return r.json()

def release(sid):
    requests.post(f'{_base()}/api/session/release',
                  json={'session_id': sid}, timeout=5)

def reset():
    r = requests.post(f'{_base()}/api/test/reset', timeout=5)
    assert r.status_code == 200, f'Reset failed: {r.status_code} {r.text}'

def queue_info():
    r = requests.get(f'{_base()}/api/queue/status?session_id=x', timeout=5)
    r.raise_for_status()
    return r.json()

def heartbeat_thread(sid, stop_event, interval=0.5):
    """Background thread: send heartbeats until stop_event is set."""
    while not stop_event.is_set():
        try:
            beat(sid)
        except Exception:
            pass
        stop_event.wait(interval)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_state():
    """Reset server state before every test."""
    reset()
    yield
    reset()


# ── Functional tests ──────────────────────────────────────────────────────────

class TestSessionBasics:

    def test_first_session_active(self):
        d = create()
        assert d['is_active'] is True
        assert d['position'] == 0

    def test_second_session_queued(self):
        s1 = create()['session_id']
        d2 = create()
        assert d2['is_active'] is False
        assert d2['position'] == 1
        release(s1)

    def test_positions_increment(self):
        s1 = create()['session_id']
        d2 = create()
        d3 = create()
        assert d2['position'] == 1
        assert d3['position'] == 2
        release(s1)

    def test_release_promotes_next(self):
        s1 = create()['session_id']
        s2 = create()['session_id']
        release(s1)
        time.sleep(0.3)
        assert status(s2)['is_active'] is True
        release(s2)

    def test_positions_reorder_after_promotion(self):
        s1 = create()['session_id']
        s2 = create()['session_id']
        s3 = create()['session_id']
        release(s1)
        time.sleep(0.3)
        assert status(s2)['is_active'] is True
        assert status(s3)['position'] == 1
        release(s2)
        release(s3)

    def test_queue_length_reported(self):
        s1 = create()['session_id']
        create()
        create()
        info = queue_info()
        assert info['queue_length'] == 2
        release(s1)

    def test_heartbeat_success(self):
        s = create()['session_id']
        r = beat(s)
        assert r['success'] is True
        release(s)

    def test_heartbeat_unknown_session(self):
        r = beat('not-a-real-session-id')
        assert r['success'] is False

    def test_release_unknown_session_is_safe(self):
        release('not-a-real-session-id')   # should not raise

    def test_session_not_found_after_release(self):
        s = create()['session_id']
        release(s)
        time.sleep(0.2)
        d = status(s)
        assert 'error' in d


class TestSessionTimeouts:

    def test_idle_timeout_drops_active_session(self):
        """Active session with no heartbeat for >IDLE_TIMEOUT_SEC is dropped."""
        from cct_common.job_queue import idle_timeout_sec
        IDLE_TIMEOUT_SEC = idle_timeout_sec()
        s1 = create()['session_id']
        s2 = create()['session_id']
        # Heartbeat s2 so it isn't evicted by stale-queue cleanup (30s threshold)
        # while we wait for s1's idle timeout to fire.
        stop = threading.Event()
        t = threading.Thread(target=heartbeat_thread, args=(s2, stop, 10))
        t.start()
        try:
            time.sleep(IDLE_TIMEOUT_SEC + 2)
        finally:
            stop.set()
            t.join()
        # s2 should now be active (promoted on s1 expiry)
        d = status(s2)
        assert d['is_active'] is True, f's2 not promoted: {d}'
        release(s2)

    def test_stale_queued_session_removed(self):
        """Queued session with no heartbeat for >30s is removed."""
        s1 = create()['session_id']
        s2 = create()['session_id']
        # Keep s1 alive, let s2 go stale
        stop = threading.Event()
        t = threading.Thread(target=heartbeat_thread, args=(s1, stop, 5))
        t.start()
        time.sleep(35)   # wait for stale cleanup (30s threshold)
        stop.set()
        t.join()
        info = queue_info()
        assert info['queue_length'] == 0, f's2 not cleaned up: queue={info}'
        release(s1)

    def test_heartbeat_prevents_idle_timeout(self):
        """Active session sending heartbeats survives past IDLE_TIMEOUT_SEC."""
        from cct_common.job_queue import idle_timeout_sec
        IDLE_TIMEOUT_SEC = idle_timeout_sec()
        s = create()['session_id']
        stop = threading.Event()
        t = threading.Thread(target=heartbeat_thread, args=(s, stop, 5))
        t.start()
        time.sleep(IDLE_TIMEOUT_SEC + 5)
        stop.set()
        t.join()
        d = status(s)
        assert d['is_active'] is True, f'Session dropped despite heartbeating: {d}'
        release(s)


# ── Stress tests ──────────────────────────────────────────────────────────────

class TestStress:

    def test_burst_join_10_sessions(self):
        """10 sessions created simultaneously — exactly 1 active, 9 queued, no duplicates."""
        results = [None] * 10
        errors  = []
        barrier = threading.Barrier(10)

        def join(i):
            barrier.wait()
            try:
                results[i] = create()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=join, args=(i,)) for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert not errors, f'Errors during burst join: {errors}'
        active  = [r for r in results if r and r.get('is_active')]
        queued  = [r for r in results if r and not r.get('is_active')]
        sids    = [r['session_id'] for r in results if r]

        assert len(active) == 1,  f'Expected 1 active, got {len(active)}'
        assert len(queued) == 9,  f'Expected 9 queued, got {len(queued)}'
        assert len(set(sids)) == 10, 'Duplicate session IDs!'

        positions = sorted(r['position'] for r in queued)
        assert positions == list(range(1, 10)), f'Positions not 1-9: {positions}'

        for r in results:
            if r: release(r['session_id'])

    def test_heartbeat_storm_no_drops(self):
        """5 sessions heartbeating rapidly for 15s — none should be dropped."""
        sessions = [create() for _ in range(5)]
        stops = [threading.Event() for _ in range(5)]
        threads = [
            threading.Thread(target=heartbeat_thread,
                             args=(s['session_id'], stops[i], 0.5))
            for i, s in enumerate(sessions)
        ]
        for t in threads: t.start()
        time.sleep(15)
        for e in stops: e.set()
        for t in threads: t.join()

        # Active session should still be active
        active = next(s for s in sessions if s['is_active'])
        d = status(active['session_id'])
        assert d['is_active'] is True, f'Active session dropped during heartbeat storm: {d}'

        # Queue length should still be 4
        info = queue_info()
        assert info['queue_length'] == 4, f'Queue length wrong: {info}'

        for s in sessions: release(s['session_id'])

    def test_fast_claim_on_release(self):
        """Position-1 waiter claims an abandoned active session within 2 seconds."""
        s1 = create()['session_id']
        s2 = create()['session_id']

        # Keep s2 heartbeating
        stop = threading.Event()
        t = threading.Thread(target=heartbeat_thread, args=(s2, stop, 0.5))
        t.start()

        release(s1)
        t_release = time.time()

        # Poll until s2 is active or 2s elapsed
        promoted = False
        while time.time() - t_release < 2.0:
            if status(s2).get('is_active'):
                promoted = True
                break
            time.sleep(0.1)

        stop.set()
        t.join()
        assert promoted, f's2 not promoted within 2s of s1 release'
        release(s2)

    def test_multi_worker_race_one_active(self):
        """Two threads call create_session simultaneously — exactly one must be active."""
        results = []
        barrier = threading.Barrier(2)

        def join():
            barrier.wait()
            results.append(create())

        threads = [threading.Thread(target=join) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()

        active = [r for r in results if r.get('is_active')]
        queued = [r for r in results if not r.get('is_active')]
        assert len(active) == 1, f'Race: {len(active)} active sessions (expected 1)'
        assert len(queued) == 1, f'Race: {len(queued)} queued sessions (expected 1)'

        for r in results: release(r['session_id'])

    def test_release_cascade(self):
        """Release 5 sessions one by one — positions decrement correctly each time."""
        sessions = [create() for _ in range(5)]
        active_sid = sessions[0]['session_id']
        queued     = sessions[1:]

        for i, s in enumerate(queued):
            assert status(s['session_id'])['position'] == i + 1

        # Release active, each queued session moves up
        for step in range(5):
            release(active_sid)
            time.sleep(0.3)
            remaining = queued[step:]
            if not remaining:
                break
            active_sid = remaining[0]['session_id']
            for j, s in enumerate(remaining[1:]):
                d = status(s['session_id'])
                assert d['position'] == j + 1, \
                    f'Step {step}: expected position {j+1}, got {d["position"]}'

    def test_state_persistence_across_restart(self):
        """Queue state survives a server restart (disk-backed sessions.json)."""
        # Create sessions
        s1 = create()['session_id']
        s2 = create()['session_id']
        assert status(s2)['position'] == 1

        # Restart the server process
        server_url = _base()
        proc = subprocess.Popen(
            [sys.executable, 'app.py'],
            env={**os.environ, 'PULLEY_TESTING': '1'},
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )
        time.sleep(3)   # wait for startup

        # Check state survived
        try:
            d = status(s2)
            assert d['position'] == 1 or d['is_active'], \
                f's2 state not preserved after restart: {d}'
        finally:
            proc.terminate()
            proc.wait()

        release(s1)
        release(s2)

    def test_concurrent_heartbeats_and_status_polls(self):
        """Concurrent heartbeats and status reads under load — no crashes or 500s."""
        sessions = [create() for _ in range(3)]
        errors = []
        stop = threading.Event()

        def beater(sid):
            while not stop.is_set():
                try:
                    beat(sid)
                except Exception as e:
                    errors.append(f'heartbeat {sid[:8]}: {e}')
                stop.wait(0.3)

        def poller(sid):
            while not stop.is_set():
                try:
                    status(sid)
                except Exception as e:
                    errors.append(f'status {sid[:8]}: {e}')
                stop.wait(0.5)

        threads = []
        for s in sessions:
            threads.append(threading.Thread(target=beater, args=(s['session_id'],)))
            threads.append(threading.Thread(target=poller, args=(s['session_id'],)))
        for t in threads: t.start()

        time.sleep(10)
        stop.set()
        for t in threads: t.join()

        assert not errors, f'Errors under concurrent load:\n' + '\n'.join(errors[:10])
        for s in sessions: release(s['session_id'])


# ── Trial download tests ──────────────────────────────────────────────────────

class TestTrialDownloads:

    def test_first_download_allowed(self):
        mid = f'stress-{time.time()}'
        r = requests.post(f'{_base()}/api/trial/register',
                          json={'mid': mid, 'fmt': 'step'}, timeout=5)
        assert r.status_code == 200
        assert r.json()['allowed'] is True

    def test_limit_enforced(self):
        mid = f'stress-{time.time()}'
        for _ in range(2):
            requests.post(f'{_base()}/api/trial/register',
                          json={'mid': mid, 'fmt': 'step'}, timeout=5)
        r = requests.post(f'{_base()}/api/trial/register',
                          json={'mid': mid, 'fmt': 'step'}, timeout=5)
        assert r.status_code == 429
        assert r.json()['allowed'] is False

    def test_different_machines_independent(self):
        ts = time.time()
        for i in range(3):
            mid = f'machine-{ts}-{i}'
            r = requests.post(f'{_base()}/api/trial/register',
                              json={'mid': mid, 'fmt': 'step'}, timeout=5)
            assert r.json()['allowed'] is True, f'Machine {i} blocked unexpectedly'


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
