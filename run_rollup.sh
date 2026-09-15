#!/usr/bin/env bash
# run_rollup.sh — WEEKLY. analyze -> rollup -> surge -> verify.
# Analysis uses the API if ANTHROPIC_API_KEY is set, else deterministic --offline.
set -euo pipefail
cd "$(dirname "$0")"

echo ">> [1/4] analyze"
if [[ -n "${PIPELINE_OFFLINE:-}" || -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "   (offline keyword themes — no API key)"; python3 analyze.py --offline
else
  python3 analyze.py
fi
echo ">> [2/4] rollup (snapshot + time series + bounded recent slice)"
python3 rollup.py
echo ">> [3/5] surge"
python3 surge.py
echo ">> [4/6] track_events (watchlist)"
python3 track_events.py
echo ">> [5/6] build_explorer (interactive drill-down data)"
python3 build_explorer.py
echo ">> [6/6] verify"
python3 verify_pipeline.py
echo ">> rollup done."
