#!/usr/bin/env bash
# Render build script — installs Rust, compiles small_step, installs Python deps.
# Set as the Render "Build Command": bash render_build.sh
set -e

# ── System packages ───────────────────────────────────────────────────────────
echo "[build] Installing system packages..."
apt-get install -y libcairo2

# ── Git submodules ────────────────────────────────────────────────────────────
echo "[build] Fetching git submodules (small_step)..."
git submodule update --init --recursive

# ── Rust ──────────────────────────────────────────────────────────────────────
if ! command -v cargo &>/dev/null; then
    echo "[build] Installing Rust..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --profile minimal --default-toolchain stable
    # shellcheck source=/dev/null
    source "$HOME/.cargo/env"
else
    echo "[build] Rust already available: $(cargo --version)"
    source "$HOME/.cargo/env" 2>/dev/null || true
fi

# ── small_step ────────────────────────────────────────────────────────────────
echo "[build] Compiling small_step..."
cargo build --release --manifest-path small_step/Cargo.toml
echo "[build] small_step binary: $(ls -lh small_step/target/release/small_step)"

# ── Python deps ───────────────────────────────────────────────────────────────
echo "[build] Installing Python dependencies..."
pip install -r requirements.txt

echo "[build] Done."
