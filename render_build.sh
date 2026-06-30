#!/usr/bin/env bash
# Render build script.
# Set as the Render "Build Command": bash render_build.sh
#
# small_step is a private repo and cannot be cloned as a submodule on Render.
# A pre-compiled Linux x86_64 binary is committed to bin/small_step_linux.
# See RELEASE.md in the small_step repo for the rebuild procedure.
set -e

# ── System packages ───────────────────────────────────────────────────────────
echo "[build] Installing system packages..."
apt-get update -qq
apt-get install -y libcairo2

# ── small_step binary ─────────────────────────────────────────────────────────
echo "[build] Verifying small_step binary..."
chmod +x bin/small_step_linux
bin/small_step_linux --version

# ── Python deps ───────────────────────────────────────────────────────────────
echo "[build] Installing Python dependencies..."
pip install -r requirements.txt

echo "[build] Done."
