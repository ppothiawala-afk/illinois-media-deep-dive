#!/usr/bin/env bash
# run_ingest.sh — DAILY. Cheap, API-free: fetch all Illinois feeds, append new
# items to the append-only archive. Run often; RSS windows expire.
set -euo pipefail
cd "$(dirname "$0")"
echo ">> ingest: fetch_feeds -> items_archive.jsonl"
python3 fetch_feeds.py "$@"
echo ">> ingest done."
