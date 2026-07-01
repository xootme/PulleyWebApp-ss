"""
prepare_release.py — Prepare a release for upload to the Render provisioning server.

Run this after build_release.py has produced releases/PulleyApp_<date>.zip.

What it does:
  1. Generates a time-limited licence.lic (1 year from today, no machine binding).
  2. Base64-encodes the licence for storage as a Render env var.
  3. Prints the four Render environment variables you need to set:
       PULLEY_LICENCE_B64    — paste into Render dashboard
       PULLEY_LICENCE_EXPIRY — paste into Render dashboard
       PULLEY_APP_URL        — URL of the uploaded PulleyApp.zip (GitHub Release etc.)
       PULLEY_APP_VERSION    — version string used by the addin for update detection

Usage:
  .venv312/Scripts/python packaging/prepare_release.py
  .venv312/Scripts/python packaging/prepare_release.py --app-url https://github.com/.../releases/download/v1/PulleyApp.zip
"""

import argparse
import base64
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT   = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app-url', default='',
                        help='Public download URL for PulleyApp.zip (GitHub Release asset)')
    args = parser.parse_args()

    version = (ROOT / 'version.txt').read_text(encoding='utf-8').strip()
    expiry  = (date.today() + timedelta(days=365)).strftime('%Y-%m-%d')

    print(f'\nGenerating licence.lic expiring {expiry} ...')

    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run([
            PYTHON, '-m', 'pyarmor.cli', 'gen', 'key',
            '--output', tmp,
            '--period', '7',
            '--expired', expiry,
        ], capture_output=True, text=True)

        if result.returncode != 0:
            print('[FAILED]\n' + result.stderr)
            sys.exit(result.returncode)

        # PyArmor 9.x outputs pyarmor.rkey; older versions output licence.lic
        lic_file = Path(tmp) / 'pyarmor.rkey'
        if not lic_file.exists():
            lic_file = Path(tmp) / 'licence.lic'
        if not lic_file.exists():
            print('[ERROR] licence key not found in PyArmor output (tried pyarmor.rkey, licence.lic).')
            print(result.stdout)
            sys.exit(1)

        lic_b64 = base64.b64encode(lic_file.read_bytes()).decode()

    print('\n── Render environment variables ──────────────────────────────────────\n')
    print(f'PULLEY_LICENCE_EXPIRY={expiry}')
    print(f'PULLEY_APP_URL={args.app_url or "<paste GitHub Release URL here>"}')
    print(f'PULLEY_APP_VERSION={version}')
    print(f'PULLEY_LICENCE_B64={lic_b64}')
    print('\n──────────────────────────────────────────────────────────────────────')
    print('\nPaste these into: Render dashboard → your service → Environment → Edit')
    print('Then redeploy the service.\n')

    # Also save locally for reference
    out = ROOT / 'licences' / f'local_release_{expiry}.env'
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        f'PULLEY_LICENCE_EXPIRY={expiry}\n'
        f'PULLEY_APP_URL={args.app_url}\n'
        f'PULLEY_APP_VERSION={version}\n'
        f'PULLEY_LICENCE_B64={lic_b64}\n'
    )
    print(f'Saved to: {out}')


if __name__ == '__main__':
    main()
