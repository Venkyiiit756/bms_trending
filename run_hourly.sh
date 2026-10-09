#!/usr/bin/env bash
# One collection pass; used by Windows Task Scheduler (via WSL) or cron.
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
mkdir -p data
python boxoffice.py run >> data/run.log 2>&1
