#!/usr/bin/env bash
# Start Sprocket. The first run creates a Python virtual environment in .venv.
# Any options are passed on, e.g.:  ./run.sh --open   or   ./run.sh --output ~/Scans
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v scanimage >/dev/null 2>&1; then
  echo "Warning: SANE's scanimage was not found. Install it with: sudo apt install sane-utils" >&2
fi

if [ ! -x .venv/bin/python ]; then
  echo "Setting up the Python environment (first run only)…"
  python3 -m venv .venv
fi
if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  .venv/bin/python -m pip install --quiet -r requirements.txt
  touch .venv/.installed
fi

exec .venv/bin/python -m sprocket "$@"
