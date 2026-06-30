#!/usr/bin/env bash
# Render build script.
# Set as the Render "Build Command": bash render_build.sh
#
# small_step is a private repo and cannot be cloned as a submodule on Render.
# A pre-compiled Linux x86_64 musl-static binary is committed to bin/small_step_linux.
# See RELEASE.md in the small_step repo for the rebuild procedure.
set -e

# ── small_step binary ─────────────────────────────────────────────────────────
echo "[build] Verifying small_step binary..."
chmod +x bin/small_step_linux
bin/small_step_linux --version

# ── Python deps ───────────────────────────────────────────────────────────────
echo "[build] Installing Python dependencies..."
pip install -r requirements.txt

echo "[build] Done."
