"""tools/release.py's deploy step: the revision it gives the traffic to is the one the deploy made
(GearBeltChain's release.py fix, as Gear's 3c27daf and Bevel's 339456e ported it). GBC's two
failures of 2026-11-09, with production's traffic pinned to 00002-2dv by the night's rollback:
- 1.0.0: read straight after `gcloud run deploy`, latestReadyRevisionName still named 00003-lpl, not
  the new 00004-764; --to-latest moved the traffic to 00004 and the check, comparing with 00003,
  rolled a good release back;
- 1.0.1: the new 00005-99x took no traffic, was retired at once, and latestReadyRevisionName stayed
  on 00004 for good (latestCreatedRevisionName 00005, observedGeneration caught up).
Pulley's release deploys pulley-test then pulley (the shared admin with them) through this one
step. Cloud Run is played here by the describes it answered, in order; gcloud by a recorder."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import release as R  # noqa: E402


def svc(latest_ready, latest_created, serving, generation=5, observed=5):
    status = {'observedGeneration': observed, 'latestReadyRevisionName': latest_ready,
              'traffic': [{'percent': 100, 'revisionName': serving}]}
    if latest_created:
        status['latestCreatedRevisionName'] = latest_created
    return {'metadata': {'generation': generation}, 'status': status}


def play(monkeypatch, answers):
    """service() answers these in turn (the last one again once they run out); gcloud calls are kept."""
    seen, calls = list(answers), []
    monkeypatch.setattr(R, 'service', lambda name: seen.pop(0) if len(seen) > 1 else seen[0])
    monkeypatch.setattr(R, 'gcloud', lambda *a, **k: calls.append(a) or '')
    monkeypatch.setattr(R.time, 'sleep', lambda s: None)
    return calls


def moved(calls):
    return any('--to-latest' in c for c in calls)


def test_1_0_0_a_lagging_status_and_pinned_traffic(monkeypatch):
    calls = play(monkeypatch, [svc('00003-lpl', '00003-lpl', '00002-2dv', generation=4, observed=4),   # before
                               svc('00003-lpl', '00003-lpl', '00002-2dv', generation=5, observed=4),   # lagging
                               svc('00003-lpl', '00004-764', '00002-2dv'),                             # seen
                               svc('00003-lpl', '00004-764', '00002-2dv'),                             # pinned
                               svc('00004-764', '00004-764', '00004-764')])                            # moved
    assert R.deploy('pulley') == '00004-764'
    assert moved(calls), 'the pinned traffic moved to the new revision'


def test_1_0_1_a_retired_revision_latest_ready_never_moves(monkeypatch):
    calls = play(monkeypatch, [svc('00004-764', '00004-764', '00002-2dv', generation=9, observed=9),
                               svc('00004-764', '00005-99x', '00002-2dv', generation=10, observed=10),
                               svc('00004-764', '00005-99x', '00002-2dv', generation=10, observed=10),
                               svc('00004-764', '00005-99x', '00002-2dv', generation=10, observed=10),   # still
                               svc('00005-99x', '00005-99x', '00005-99x', generation=10, observed=10)])
    assert R.deploy('pulley') == '00005-99x'
    assert moved(calls)


def test_traffic_already_following_the_latest_needs_no_move(monkeypatch):
    calls = play(monkeypatch, [svc('00004-tbl', '00004-tbl', '00004-tbl'),
                               svc('00005-8v4', '00005-8v4', '00005-8v4')])
    assert R.deploy('pulley-test') == '00005-8v4'
    assert not moved(calls)


def test_with_nothing_known_before_the_status_must_have_seen_the_deploy(monkeypatch):
    play(monkeypatch, [svc('00003-lpl', None, '00003-lpl', generation=5, observed=4),
                       svc('00003-lpl', '00003-lpl', '00003-lpl', generation=5, observed=4),
                       svc('00004-764', '00004-764', '00004-764')])
    assert R.deploy('pulley') == '00004-764'


def test_no_new_revision_is_a_failure_not_the_old_one(monkeypatch):
    play(monkeypatch, [svc('00003-lpl', '00003-lpl', '00003-lpl')])
    with pytest.raises(R.Fail, match='no new revision'):
        R.deploy('pulley')


def test_traffic_that_never_moves_is_a_failure(monkeypatch):
    play(monkeypatch, [svc('00004-764', '00004-764', '00002-2dv'),
                       svc('00004-764', '00005-99x', '00002-2dv')])
    with pytest.raises(R.Fail, match="didn't move to 00005-99x"):
        R.deploy('pulley')
