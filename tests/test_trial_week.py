"""test_trial_week.py — the add-in trial's weekly limit counts the calendar week from Monday at
midnight (cct_common 0.56.8, the owner: "ship", 2026-11-04). It counted from Monday at the time
of day it was asked, so on a Monday nothing earlier counted and the limit never held. The
status route the add-in shows counts the same week as the limit."""
from datetime import datetime, timedelta

from cct_common import job_queue


def _monday_just_after_midnight():
    now = datetime.now()
    return (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=1, microsecond=0)


def test_a_download_early_on_monday_counts_all_week(client):
    mid = 'trial-week-test'
    data = job_queue._load_trial_downloads()
    data[mid] = [{'timestamp': _monday_just_after_midnight().isoformat(), 'format': 'step'}]
    job_queue._save_trial_downloads(data)
    try:
        status = client.get('/api/trial/status', query_string={'mid': mid}).get_json()
        assert status['count'] == 1
        assert status['week_start'] == _monday_just_after_midnight().strftime('%Y-%m-%d')
        r = client.post('/api/trial/register', json={'mid': mid, 'fmt': 'step'})
        assert r.get_json()['count'] == 1                     # the limit counts it too (before this one)
    finally:
        data = job_queue._load_trial_downloads()
        data.pop(mid, None)
        job_queue._save_trial_downloads(data)
