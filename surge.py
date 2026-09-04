#!/usr/bin/env python3
"""
surge.py — "what is Illinois suddenly focused on?" Detects themes SURGING relative
to their OWN normal, reading the theme_history.jsonl time series.

DEFINITION OF "NORMAL" (see README): a theme's normal is its OWN trailing average
SHARE over the prior `--baseline-weeks` snapshots, measured against its OWN
variability. Surge = the latest share is `--z-threshold` standard deviations above
that trailing mean. Two flavors, kept separate:

  * EMERGING — the theme had ~no baseline (essentially absent) and just appeared.
    A genuinely new focus, not an established theme rising.
  * SURGING  — an established theme climbing well above its own norm.

Cross-checks and honesty:
  * BREADTH — is the theme also appearing across MORE outlets than usual? A spike
    in one outlet is often one newsroom's obsession; surging AND broadening is the
    high-confidence signal (the state actually turning its attention).
  * CONFIDENCE — a baseline needs history. With few prior snapshots the z-score is
    unreliable; the report labels its own confidence by how many baseline points
    exist, and flags the whole report low-confidence until enough weeks bank.
  * A rolling baseline "forgets" — a theme elevated for months becomes the new
    normal and stops surging. That is correct: surge means CHANGE, not dominance.
    (Sustained dominance is a separate view on the dashboard.)

Counts and arithmetic only — no sentiment. Every surge is traceable to the
theme_history rows and the underlying articles.

Writes surge_report.json (committed, small).

Usage:
    python3 surge.py
    python3 surge.py --baseline-weeks 8 --z-threshold 2.0
    python3 surge.py --data-dir /tmp/demo
"""

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import archive_store as store

THEME_HISTORY = "theme_history.jsonl"
REPORT_NAME = "surge_report.json"


def main():
    ap = argparse.ArgumentParser(description="Detect themes surging vs their own baseline.")
    ap.add_argument("--baseline-weeks", type=int, default=8,
                    help="trailing snapshots that define 'normal'")
    ap.add_argument("--z-threshold", type=float, default=2.0)
    ap.add_argument("--min-baseline", type=int, default=3,
                    help="minimum prior snapshots required to judge a theme at all")
    ap.add_argument("--min-confident", type=int, default=6,
                    help="baseline points below this -> low confidence")
    ap.add_argument("--emerging-baseline", type=float, default=0.005,
                    help="baseline share below this counts as 'absent' -> emerging")
    ap.add_argument("--data-dir")
    args = ap.parse_args()

    data_dir = store.resolve_data_dir(args.data_dir)
    series = defaultdict(list)  # theme -> [ {date, share, volume, outlet_breadth} ... ]
    dates = set()
    for _, row, err in store.iter_jsonl(Path(data_dir) / THEME_HISTORY):
        if row and row.get("theme"):
            series[row["theme"]].append(row)
            dates.add(row["date"])
    for th in series:
        series[th].sort(key=lambda r: r["date"])

    all_dates = sorted(dates)
    surging, emerging = [], []
    latest_date = all_dates[-1] if all_dates else None

    for th, rows in series.items():
        if not rows or rows[-1]["date"] != latest_date:
            continue  # theme absent in the latest snapshot
        latest = rows[-1]
        prior = rows[:-1][-args.baseline_weeks:]
        if len(prior) < args.min_baseline:
            continue  # not enough history to judge this theme yet
        shares = [r["share"] for r in prior]
        mean = statistics.fmean(shares)
        std = statistics.pstdev(shares) if len(shares) > 1 else 0.0
        breadth_now = latest.get("outlet_breadth", 0)
        breadth_base = statistics.fmean([r.get("outlet_breadth", 0) for r in prior])
        confidence = "low" if len(prior) < args.min_confident else "ok"
        entry = {
            "theme": th, "latest_share": latest["share"], "latest_volume": latest["volume"],
            "baseline_mean_share": round(mean, 4), "baseline_std": round(std, 4),
            "baseline_points": len(prior),
            "z": round((latest["share"] - mean) / std, 2) if std > 1e-9 else None,
            "breadth_now": breadth_now, "breadth_baseline": round(breadth_base, 2),
            "broadening": breadth_now > breadth_base,
            "confidence": confidence,
        }
        if mean < args.emerging_baseline and latest["share"] > args.emerging_baseline:
            entry["kind"] = "emerging"
            emerging.append(entry)
        elif entry["z"] is not None and entry["z"] >= args.z_threshold:
            entry["kind"] = "surging"
            surging.append(entry)
        elif std <= 1e-9 and latest["share"] > mean:  # flat-then-jump, std=0 edge
            entry["kind"] = "surging"; entry["z"] = None
            surging.append(entry)

    surging.sort(key=lambda e: (e["z"] is not None, e.get("z") or 0, e["broadening"]), reverse=True)
    emerging.sort(key=lambda e: e["latest_share"], reverse=True)

    n_snapshots = len(all_dates)
    report = {
        "_comment": "Themes surging vs their OWN trailing baseline. 'normal' = a "
                    "theme's mean share over prior snapshots; surge = z>=threshold. "
                    "emerging = was absent, just appeared. broadening = across more "
                    "outlets than usual. No sentiment; traceable to theme_history + articles.",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_snapshot": latest_date,
        "params": {"baseline_weeks": args.baseline_weeks, "z_threshold": args.z_threshold,
                   "min_baseline": args.min_baseline, "min_confident": args.min_confident},
        "history_depth_snapshots": n_snapshots,
        "report_confidence": ("low — baselines need ~%d+ snapshots; only %d exist"
                              % (args.min_confident, n_snapshots)) if n_snapshots < args.min_confident
                             else "ok",
        "surging": surging,
        "emerging": emerging,
    }
    (Path(data_dir) / REPORT_NAME).write_text(json.dumps(report, indent=2))
    print(f"surge: {len(surging)} surging, {len(emerging)} emerging "
          f"(history {n_snapshots} snapshots, confidence={report['report_confidence'].split(' ')[0]}) "
          f"-> {REPORT_NAME}")


if __name__ == "__main__":
    main()
