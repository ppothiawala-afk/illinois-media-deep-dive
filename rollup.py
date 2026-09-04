#!/usr/bin/env python3
"""
rollup.py — stage 3: aggregate the analyzed archive into a weekly snapshot and
append the durable time series that surge detection reads.

Produces / updates:
  * media_history.json   — one snapshot per run-date (committed, bounded growth).
      Per snapshot: theme volume+share, top entities, per-OUTLET theme mix (for
      divergence), per-REGION theme mix (chicago/suburban/downstate), and story
      PENETRATION (items carried by the most distinct outlets, from the sightings
      log). Window is recorded so verify can de-circularize.
  * theme_history.jsonl  — append-only: one row per (date, theme): volume, share,
      outlet_breadth. This is the time series "normal" is computed from.
  * entity_history.jsonl — append-only: top-N entities per date (emergent signal).
  * items_recent.json    — BOUNDED (last N days) analyzed items for the dashboard.
      This replaces committing the full items_analyzed.json (storage rule).

Metric note: SHARE (theme volume / total) is primary; absolute volume secondary.
Counts only — no sentiment anywhere.

Usage:
    python3 rollup.py
    python3 rollup.py --window-days 7 --recent-days 14
    python3 rollup.py --data-dir /tmp/demo
"""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import archive_store as store

HERE = Path(__file__).resolve().parent
TAXONOMY_PATH = HERE / "themes_taxonomy.json"
ANALYZED_NAME = "items_analyzed.json"
THEME_HISTORY = "theme_history.jsonl"
ENTITY_HISTORY = "entity_history.jsonl"
RECENT_NAME = "items_recent.json"
TOP_ENTITIES_HISTORY = 25


def shares(counter, total):
    return {k: round(v / total, 4) for k, v in counter.items()} if total else {}


def main():
    ap = argparse.ArgumentParser(description="Weekly emergent-coverage rollup.")
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--recent-days", type=int, default=14)
    ap.add_argument("--include-noncivic", action="store_true",
                    help="aggregate ALL items; default is civic-only (the instrument's focus)")
    ap.add_argument("--data-dir")
    args = ap.parse_args()

    data_dir = store.resolve_data_dir(args.data_dir)
    analyzed_path = Path(data_dir) / ANALYZED_NAME
    if not analyzed_path.exists():
        raise SystemExit("items_analyzed.json not found — run analyze.py first.")
    data = json.loads(analyzed_path.read_text())
    canon = list(json.loads(TAXONOMY_PATH.read_text())["canonical"].keys()) + ["other"]

    run_date = data.get("generated") or datetime.now(timezone.utc).date().isoformat()
    win_start, win_end = store.week_window(run_date, days=args.window_days)
    win_items = [it for it in data["items"]
                 if win_start <= str(it.get("published", ""))[:10] <= win_end]
    # CIVIC LENS: by default the snapshot measures civic coverage only. The archive
    # still holds everything; --include-noncivic aggregates all. Non-civic counts
    # are recorded in meta so the filtering is transparent.
    all_in_window = len(win_items)
    if args.include_noncivic:
        items = win_items
    else:
        items = [it for it in win_items if it.get("civic")]
    noncivic_dropped = all_in_window - len(items)
    total = len(items)

    theme_vol = Counter()
    theme_outlets = defaultdict(set)
    theme_entities = defaultdict(Counter)
    entities_all = Counter()
    region_theme = defaultdict(Counter); region_total = Counter()
    outlet_theme = defaultdict(Counter); outlet_total = Counter(); outlet_region = {}

    for it in items:
        th = it.get("theme", "other")
        outlet = it.get("outlet", "?")
        region = it.get("region", "unknown")
        theme_vol[th] += 1
        theme_outlets[th].add(outlet)
        region_theme[region][th] += 1; region_total[region] += 1
        outlet_theme[outlet][th] += 1; outlet_total[outlet] += 1
        outlet_region[outlet] = region
        for e in it.get("entities", []):
            entities_all[e] += 1
            theme_entities[th][e] += 1

    themes_block = {}
    for th in canon:
        v = theme_vol.get(th, 0)
        themes_block[th] = {
            "volume": v,
            "share": round(v / total, 4) if total else 0.0,
            "outlet_breadth": len(theme_outlets.get(th, set())),
            "top_entities": theme_entities[th].most_common(6),
        }

    regions_block = {}
    for r, tot in region_total.items():
        regions_block[r] = {"total": tot, "theme_share": shares(region_theme[r], tot)}

    outlets_block = {}
    for o, tot in outlet_total.items():
        outlets_block[o] = {"total": tot, "region": outlet_region.get(o, "unknown"),
                            "theme_share": shares(outlet_theme[o], tot)}

    # story penetration from the sightings log (distinct outlets per story id in window)
    id_meta = {it["id"]: it for it in items}
    sight_outlets = defaultdict(set)
    for _, s, err in store.iter_jsonl(store.sightings_path(data_dir)):
        if not s:
            continue
        if store.in_window({"published": s.get("published")}, win_start, win_end):
            sight_outlets[s.get("id")].add(s.get("outlet"))
    penetration = []
    for sid, outs in sorted(sight_outlets.items(), key=lambda kv: -len(kv[1]))[:10]:
        m = id_meta.get(sid)
        if not m or len(outs) < 2:
            continue
        penetration.append({"title": m["title"], "link": m["link"],
                            "theme": m.get("theme", "other"),
                            "outlet_count": len(outs), "outlets": sorted(outs)})

    snapshot = {
        "date": run_date, "window_start": win_start, "window_end": win_end,
        "window_days": args.window_days, "total_items": total,
        "themes": themes_block,
        "entities_top": entities_all.most_common(20),
        "regions": regions_block,
        "outlets": outlets_block,
        "penetration": penetration,
        "meta": {"backend": data.get("backend"), "taxonomy_version": data.get("taxonomy_version"),
                 "civic_only": not args.include_noncivic,
                 "civic_items": total, "noncivic_dropped": noncivic_dropped,
                 "all_in_window": all_in_window,
                 "contains_synthetic": any(it.get("synthetic") for it in items)},
    }

    # media_history.json (idempotent by date)
    hist_path = Path(data_dir) / store.HISTORY_NAME
    hist = json.loads(hist_path.read_text()) if hist_path.exists() else {
        "_comment": "Weekly emergent-coverage snapshots. SHARE primary. Counts only, "
                    "no sentiment. History is the product; surge baselines read theme_history.jsonl.",
        "snapshots": []}
    hist["snapshots"] = [s for s in hist["snapshots"] if s["date"] != run_date]
    hist["snapshots"].append(snapshot)
    hist["snapshots"].sort(key=lambda s: s["date"])
    hist_path.write_text(json.dumps(hist, indent=1))

    # append-only theme + entity time series (idempotent: drop this date's rows first)
    def rewrite_series(name, rows_for_date, date):
        p = Path(data_dir) / name
        kept = [obj for _, obj, err in store.iter_jsonl(p) if obj and obj.get("date") != date]
        p.write_text("".join(json.dumps(r) + "\n" for r in kept + rows_for_date))

    theme_rows = [{"date": run_date, "theme": th, "volume": themes_block[th]["volume"],
                   "share": themes_block[th]["share"],
                   "outlet_breadth": themes_block[th]["outlet_breadth"]} for th in canon]
    entity_rows = [{"date": run_date, "entity": e, "count": n}
                   for e, n in entities_all.most_common(TOP_ENTITIES_HISTORY)]
    rewrite_series(THEME_HISTORY, theme_rows, run_date)
    rewrite_series(ENTITY_HISTORY, entity_rows, run_date)

    # bounded recent items for the dashboard (last N days), committed
    from datetime import date, timedelta
    cutoff = (store.parse_date(run_date) or date.today()) - timedelta(days=args.recent_days - 1)
    recent = [it for it in data["items"]
              if (store.parse_date(it.get("published")) or date.min) >= cutoff
              and (args.include_noncivic or it.get("civic"))]
    recent = sorted(recent, key=lambda x: x.get("published", ""), reverse=True)[:400]
    (Path(data_dir) / RECENT_NAME).write_text(json.dumps({
        "_comment": f"Bounded (last {args.recent_days}d) analyzed items for the dashboard. "
                    "Full analyzed corpus is git-ignored and regenerable (storage rule).",
        "generated": run_date, "count": len(recent), "items": recent}, indent=1))

    top = sorted(((themes_block[t]["share"], t) for t in canon if t != "other"), reverse=True)[:3]
    lens = "civic-only" if not args.include_noncivic else "all"
    print(f"snapshot {run_date} [{win_start}..{win_end}]: {total} civic items "
          f"({noncivic_dropped} non-civic dropped of {all_in_window}) [{lens}] · "
          f"top themes {[t for _, t in top]} · {len(hist['snapshots'])} snapshots · "
          f"{len(penetration)} penetrating stories")
    if snapshot["meta"]["contains_synthetic"]:
        print("  ! snapshot contains SYNTHETIC items — verify will FAIL if shipped as real.")


if __name__ == "__main__":
    main()
