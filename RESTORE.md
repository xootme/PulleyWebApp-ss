# Database backups and restore

The accounts, token ledger, orders, sessions and bug reports live in one Neon
Postgres database (`DATABASE_URL`), shared by every CCT tool. There are two
lines of defence:

1. **Neon point-in-time restore** — 7 days (Launch plan). First choice for
   anything recent: a bad deploy, a bad script, a deleted row.
2. **An encrypted daily copy in Cloud Storage** (`db_offsite_backup.py`) —
   `gs://cheapcadtools-backups/postgres/YYYY/MM/DD/…zip.enc`, kept 90 days by the
   bucket's lifecycle rule. For anything older than 7 days, or if Neon itself
   (or the Neon account) is lost.

## One-time setup (owner — nothing here is automatic)

```bash
# 1. The encryption key. Keep a SECOND copy offline (password manager) — without
#    it the backups can't be read. Pipe it; never paste it into a chat or a file.
.venv314/Scripts/python db_offsite_backup.py newkey \
  | gcloud --configuration=cheapcadtools secrets create BACKUP_KEY --data-file=-

# 2. Let the service account write backups (it can only write results today).
gcloud --configuration=cheapcadtools storage buckets add-iam-policy-binding gs://cheapcadtools-backups \
  --member=serviceAccount:pulley-run@cheapcadtools.iam.gserviceaccount.com \
  --role=roles/storage.objectCreator
gcloud --configuration=cheapcadtools secrets add-iam-policy-binding BACKUP_KEY \
  --member=serviceAccount:pulley-run@cheapcadtools.iam.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor

# 3. The job: the production image, running the backup command.
gcloud --configuration=cheapcadtools run jobs deploy cct-db-backup --source . --region us-central1 \
  --service-account pulley-run@cheapcadtools.iam.gserviceaccount.com \
  --command python --args db_offsite_backup.py,backup \
  --set-env-vars BACKUP_BUCKET=cheapcadtools-backups \
  --set-secrets DATABASE_URL=DATABASE_URL:latest,BACKUP_KEY=BACKUP_KEY:latest \
  --max-retries 2 --task-timeout 600

# 4. Run it once by hand and check the object appears.
gcloud --configuration=cheapcadtools run jobs execute cct-db-backup --region us-central1 --wait

# 5. Daily at 03:15 UTC (the scheduler starts the job as pulley-run).
gcloud --configuration=cheapcadtools run jobs add-iam-policy-binding cct-db-backup --region us-central1 \
  --member=serviceAccount:pulley-run@cheapcadtools.iam.gserviceaccount.com --role=roles/run.invoker
gcloud --configuration=cheapcadtools scheduler jobs create http cct-db-backup-daily \
  --location us-central1 --schedule "15 3 * * *" --time-zone UTC \
  --uri "https://run.googleapis.com/v2/projects/cheapcadtools/locations/us-central1/jobs/cct-db-backup:run" \
  --http-method POST \
  --oauth-service-account-email pulley-run@cheapcadtools.iam.gserviceaccount.com
```

If a backup fails the job exits non-zero and Cloud Run shows it as failed;
add a log-based alert on `cct-db-backup` failures to get an email.

## Restoring

Work on a **new, empty** database — never on top of the live one. The
restore refuses a target that already has tables.

1. **Decide which line.** Within 7 days: use Neon's own restore (console →
   the project → Restore / create a branch at a point in time) and stop
   here. Older, or Neon unavailable: carry on.
2. **Pick the backup.** Newest before the damage:
   ```bash
   gcloud --configuration=cheapcadtools storage ls -l gs://cheapcadtools-backups/postgres/2026/10/
   ```
   Note its time (it's in the name, UTC): purchases after it must be
   replayed in step 5.
3. **Make an empty target** — a new Neon branch/database, or locally
   `CREATE SCHEMA restore_test` for a drill. Its URL is `TARGET_URL`.
4. **Restore** (from a checkout with `.venv314`; `BACKUP_KEY` from Secret
   Manager or the offline copy, piped into the environment):
   ```bash
   BACKUP_BUCKET=cheapcadtools-backups BACKUP_KEY=… \
     .venv314/Scripts/python db_offsite_backup.py restore postgres/2026/10/01/cct-20261001T031500Z.zip.enc "$TARGET_URL"
   ```
   It prints the rows restored and fails if any table's count differs
   from the backup's manifest. Identity counters are moved past the
   restored ids, so new rows don't collide.
5. **Replay purchases newer than the backup.** In the PayPal and Stripe
   dashboards, list completed payments since the backup time. For each:
   ```bash
   DATABASE_URL="$TARGET_URL" .venv314/Scripts/python db_offsite_backup.py \
     replay-order <paypal|stripe> <order id> <buyer email> <tokens> <cents> <capture / payment-intent id>
   ```
   It records the order and credits its tokens exactly as the payment
   webhook would (purchased, so refundable), and is idempotent: running it
   twice credits once. Refunds issued after the backup: redo them from the
   admin dashboard's Sales tab.
6. **Check** in the admin dashboard (pointed at the target): accounts,
   balances and the latest orders look right; spot-check a few accounts
   against the provider dashboards.
7. **Switch over**: point the `DATABASE_URL` secret at the restored
   database (a new secret version) and redeploy `pulley` so it picks it up.
   Leave `pulley-test` alone — it has its own database (`DATABASE_URL_TEST`).
   Sign-ins and downloads between the backup and the switch are lost; the
   ledger holds everything the providers were paid for.

## Drills

Practise before launch, then quarterly: restore the newest backup into a
scratch schema on the local Postgres (`docker start cct-pg`, then
`CREATE SCHEMA drill` and a URL with `options=-csearch_path%3Ddrill`),
compare the printed counts with the manifest, then `DROP SCHEMA drill
CASCADE`. `tests/test_db_offsite_backup.py` runs the same round trip on
every test run that has `CCT_TEST_POSTGRES` set.
