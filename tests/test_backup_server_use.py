"""The cct-db-backup job's run is counted in the admin's Server use tab (cct_common 0.27.0's
job_use, 2026-10-29). No Postgres needed: the store takes a SQLite path as DATABASE_URL, and the
dump and the bucket are stand-ins."""
import sys
import time

import db_offsite_backup as bk


def test_a_backup_run_is_counted(tmp_path, monkeypatch):
    db = str(tmp_path / 'use.db')
    monkeypatch.setenv('DATABASE_URL', db)
    monkeypatch.setenv('BACKUP_BUCKET', 'a-bucket')
    monkeypatch.setenv('BACKUP_KEY', bk.new_key())
    monkeypatch.setitem(sys.modules, 'results', type(sys)('results'))
    sys.modules['results'].GcsResults = lambda bucket: object()
    monkeypatch.setattr(bk, 'backup', lambda url, bucket, key: ('postgres/x.zip.enc', {'t': 1}))

    assert bk.main(['db_offsite_backup.py', 'backup']) == 0

    from cct_common.server_use import ServerUseStore
    rows = ServerUseStore(db).query(0, time.time() + 3600)
    assert [(r['service'], r['category'], r['action'], r['count']) for r in rows
            if r['action'] == 'backup'] == [('cct-db-backup', 'scheduled', 'backup', 1)]
