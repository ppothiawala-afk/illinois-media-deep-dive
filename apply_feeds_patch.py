#!/usr/bin/env python3
"""
apply_feeds_patch.py — merge a human-approved feeds_patch.json into the registry.
discover_feeds PROPOSES; a human reviews; this MERGES. --dry-run to preview.
Writes a dated audit copy feeds_patch.applied_<date>.json on apply.

  adds      -> appended (skipped if feed_url present)
  validates -> matching feed promoted to active + validation updated
  flags     -> matching feed status set to flagged/dead
  rejects   -> ignored (informational)
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
FEEDS = HERE / "feeds_config.json"
PATCH = HERE / "feeds_patch.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--patch", default=str(PATCH))
    args = ap.parse_args()

    config = json.loads(FEEDS.read_text())
    patch = json.loads(Path(args.patch).read_text())
    existing = {f["feed_url"] for f in config["feeds"]}
    added, flagged, validated = [], [], []

    for p in patch.get("adds", []):
        feed = p["feed"]
        if feed["feed_url"] in existing:
            continue
        if not args.dry_run:
            config["feeds"].append(feed)
        existing.add(feed["feed_url"])
        added.append(feed["outlet"])

    flag_status = {p["feed_url"]: p["status_proposed"] for p in patch.get("flags", [])}
    validate_map = {p["feed_url"]: p for p in patch.get("validates", [])}
    for f in config["feeds"]:
        if f["feed_url"] in flag_status:
            if not args.dry_run:
                f["status"] = flag_status[f["feed_url"]]
            flagged.append(f"{f['outlet']} -> {flag_status[f['feed_url']]}")
        if f["feed_url"] in validate_map:
            p = validate_map[f["feed_url"]]
            if not args.dry_run:
                f["status"] = p.get("status_proposed", "active")
                f["validation"] = p.get("validation", f.get("validation"))
            validated.append(f"{f['outlet']} -> active/validated")

    print(f"adds: {len(added)} | flags: {len(flagged)} | validates: {len(validated)}")
    for a in added: print(f"  + {a}")
    for fl in flagged: print(f"  ~ {fl}")
    for v in validated: print(f"  ✓ {v}")
    if args.dry_run:
        print("dry-run: no files written."); return

    FEEDS.write_text(json.dumps(config, indent=2))
    stamp = datetime.now(timezone.utc).date().isoformat()
    (HERE / f"feeds_patch.applied_{stamp}.json").write_text(json.dumps(patch, indent=2))
    print(f"applied. registry now {len(config['feeds'])} feeds. audit: feeds_patch.applied_{stamp}.json")


if __name__ == "__main__":
    main()
