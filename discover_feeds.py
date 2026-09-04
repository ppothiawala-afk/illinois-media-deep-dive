#!/usr/bin/env python3
"""
discover_feeds.py — feed discovery + health-check for the Illinois registry.
Human-in-the-loop: PROPOSES registry changes to feeds_patch.json; never edits
feeds_config.json. apply_feeds_patch.py merges after review.

  --health-check   Re-validate registry feeds. Dead/parse-fail -> "flag"; healthy
                   candidates (status != active / not yet validated) -> "validate"
                   (promote to active). This is how the candidate feeds — Block
                   Club, Chicago Reader, Illinois Policy, etc. — get confirmed.
  --candidates F   Validate a JSON list of new candidate feeds; healthy ones ->
                   "add" proposals.

Network via feedparser at runtime; --fixtures DIR validates against saved XML.

Usage:
  python3 discover_feeds.py --health-check
  python3 discover_feeds.py --candidates cands.json --stale-days 30
"""

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import feedparser
except ImportError:  # pragma: no cover
    feedparser = None

HERE = Path(__file__).resolve().parent
FEEDS = HERE / "feeds_config.json"
PATCH = HERE / "feeds_patch.json"
UA = "Mozilla/5.0 (IllinoisMediaDeepDive discovery)"


def slugify(s): return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def recent_count(parsed, days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    n = 0
    for e in parsed.entries:
        tp = e.get("published_parsed") or e.get("updated_parsed")
        if tp:
            if datetime(*tp[:6], tzinfo=timezone.utc) >= cutoff:
                n += 1
        else:
            n += 1
    return n


def validate(source, is_fix, stale_days):
    if feedparser is None:
        return False, 0, 0, "feedparser not installed"
    try:
        parsed = feedparser.parse(source) if is_fix else feedparser.parse(source, agent=UA)
    except Exception as e:  # noqa: BLE001
        return False, 0, 0, f"parse error: {e}"
    n = len(parsed.entries)
    if n == 0:
        return False, 0, 0, "no items"
    return True, n, recent_count(parsed, stale_days), "ok"


def fixture_for(fixtures_dir, outlet):
    if not fixtures_dir:
        return None
    for p in Path(fixtures_dir).glob("*.xml"):
        if re.sub(r"^synthetic_", "", p.stem) == slugify(outlet):
            return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates")
    ap.add_argument("--health-check", action="store_true")
    ap.add_argument("--fixtures")
    ap.add_argument("--stale-days", type=int, default=30)
    ap.add_argument("--min-recent", type=int, default=1)
    args = ap.parse_args()
    if not args.candidates and not args.health_check:
        ap.error("choose --candidates FILE and/or --health-check")

    config = json.loads(FEEDS.read_text())
    registered = {f["feed_url"] for f in config["feeds"]}
    today = datetime.now(timezone.utc).date().isoformat()
    proposals = []

    if args.candidates:
        for c in json.loads(Path(args.candidates).read_text()):
            src = fixture_for(args.fixtures, c["outlet"]) if args.fixtures else c["feed_url"]
            if c["feed_url"] in registered or src is None:
                continue
            ok, n, rec, note = validate(src, bool(args.fixtures), args.stale_days)
            if ok and rec >= args.min_recent:
                e = dict(c); e.setdefault("outlet_type", "nonprofit"); e["status"] = "active"
                e["added"] = today
                e["validation"] = {"validated": True, "validated_on": today,
                                   "method": "discover_feeds validate", "items_seen": n, "recent": rec}
                proposals.append({"action": "add", "feed": e, "reason": f"healthy: {n} items, {rec} recent"})
            else:
                proposals.append({"action": "reject", "feed": c, "reason": f"{note} ({n} items, {rec} recent)"})

    if args.health_check:
        for f in config["feeds"]:
            src = fixture_for(args.fixtures, f["outlet"]) if args.fixtures else f["feed_url"]
            if src is None:
                continue
            ok, n, rec, note = validate(src, bool(args.fixtures), args.stale_days)
            already = f.get("validation", {}).get("validated")
            if not ok:
                proposals.append({"action": "flag", "feed_url": f["feed_url"], "outlet": f["outlet"],
                                  "status_proposed": "dead", "reason": note})
            elif rec == 0:
                proposals.append({"action": "flag", "feed_url": f["feed_url"], "outlet": f["outlet"],
                                  "status_proposed": "flagged", "reason": f"stale: 0 in {args.stale_days}d ({n} total)"})
            elif not already or f.get("status") != "active":
                proposals.append({"action": "validate", "feed_url": f["feed_url"], "outlet": f["outlet"],
                                  "status_proposed": "active",
                                  "validation": {"validated": True, "validated_on": today,
                                                 "method": "discover_feeds --health-check live parse",
                                                 "items_seen": n, "recent": rec},
                                  "reason": f"healthy: {n} items, {rec} recent"})

    patch = {"_comment": "PROPOSALS ONLY — human review, then apply_feeds_patch.py. "
                        "Discovery/health-check never edits feeds_config.json directly.",
             "generated": datetime.now(timezone.utc).isoformat(),
             "adds": [p for p in proposals if p["action"] == "add"],
             "flags": [p for p in proposals if p["action"] == "flag"],
             "validates": [p for p in proposals if p["action"] == "validate"],
             "rejects": [p for p in proposals if p["action"] == "reject"]}
    PATCH.write_text(json.dumps(patch, indent=2))
    print(f"proposals -> {PATCH.name}: {len(patch['adds'])} adds, {len(patch['flags'])} flags, "
          f"{len(patch['validates'])} validates, {len(patch['rejects'])} rejects (review before applying)")


if __name__ == "__main__":
    main()
