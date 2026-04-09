#!/bin/bash
# deploy.sh - Local deployment script for PulleyWebApp

SYNC_ONLY=0
if [ "$1" == "--sync" ]; then
    SYNC_ONLY=1
fi

echo "Syncing files to GreenGeeks..."
rsync -avz \
    --exclude 'venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'tests/' \
    --exclude '*.md' \
    --exclude 'deploy.sh' \
    --exclude '.pytest_cache/' \
    --exclude '.vscode/' \
    --exclude '.claude/' \
    --exclude 'testing.html' \
    ./ xootpro@chi203.greengeeks.net:~/public_html/cheapcadtools/tst_pulleys/

if [ $SYNC_ONLY -eq 0 ]; then
    echo "Running provisioner on remote server..."
    ssh xootpro@chi203.greengeeks.net "bash ~/public_html/cheapcadtools/tst_pulleys/provision_remote.sh"
else
    echo "Sync complete. Skipping remote provisioning."
fi
