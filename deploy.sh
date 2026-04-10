#!/bin/bash
# deploy.sh - Convenience wrapper for Git-based deployment to Render

if [ -z "$1" ]; then
    echo "Usage: ./deploy.sh \"Your commit message\""
    exit 1
fi

COMMIT_MSG=$1

echo "--- Starting Deployment to Render ---"

# 1. Add all changes
echo "Step 1: Staging changes..."
git add .

# 2. Commit
echo "Step 2: Committing changes..."
git commit -m "$COMMIT_MSG"

# 3. Push (Triggers Render build)
echo "Step 3: Pushing to GitHub..."
git push origin main

echo "--- Deployment Triggered! ---"
echo "Check progress at: https://dashboard.render.com"
