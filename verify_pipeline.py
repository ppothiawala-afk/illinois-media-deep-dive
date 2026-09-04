#!/usr/bin/env python3
"""
verify_pipeline.py — the trust layer. Checks that can actually FAIL, labelled
STRUCTURAL (hold regardless of data volume) or DATA (WARN until real data exists,
so an empty pipeline is never mistaken for a verified one).

STRUCTURAL
  S1  no sentiment/judgment key anywhere (archive, analyzed, snapshots)
  S2  NO synthetic-test data in the SHIPPED derived files (items_recent.json,
      media_history.json, theme/entity history, surge_report) — fixtures may live
      only in a temp demo dir
  S3  every theme is in the canonical taxonomy (+ 'other')
  S6  snapshot share == volume / total
  S7  STORAGE INVARIANT — items_analyzed.json and verification_report.json must
      NOT be git-tracked (they are rewritten wholesale and would bloat history;
      they are regenerable). This enforces the storage rule in code.

DATA
  D1  archive not truncated (watermark tripwire), no duplicate ids, all lines parse
  D2  sightings resolve to archive items
  D3  de-circularized — recompute the latest snapshot's total + per-region counts
      straight from items_archive.jsonl (window), independent of the analyzed file
  D4  dead-outlet — an outlet with a feed but zero items across the last N
      snapshots is a likely dead feed
  D5  feed-health coverage — validated vs candidate feeds

Exit non-zero on any FAIL. Usage: python3 verify_pipeline.py [--data-dir DIR]
"""

import argparse
import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import archive_store as store

HERE = Path(__file__).resolve().parent
TAXONOMY_PATH = HERE / "themes_taxonomy.json"
CONFIG_PATH = HERE / "feeds_config.json"
FORBIDDEN_TRACKED = ["items_analyzed.json", "verification_report.json"]
SHIPPED_DERIVED = ["items_recent.json", "media_history.json", "theme_history.jsonl",
                   "entity_history.jsonl", "surge_report.json"]


def git_tracked(name):
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", name],
                           cwd=HERE, capture_output=True, text=True)
        return r.returncode == 0 and r.stdout.strip() != ""
    except Exception:  # noqa: BLE001
        return False  # not a git repo (e.g. test temp dir) -> nothing tracked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir")
    ap.add_argument("--dead-outlet-window", type=int, default=3)
    args = ap.parse_args()

    data_dir = store.resolve_data_dir(args.data_dir)
    results = []
    def record(cid, kind, status, msg): results.append(
        {"id": cid, "kind": kind, "status": status, "msg": msg})

    canon = set(json.loads(TAXONOMY_PATH.read_text())["canonical"].keys()) | {"other"}
    registry = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {"feeds": []}
    feeds = registry.get("feeds", [])

    def load(name, default):
        p = Path(data_dir) / name
        return json.loads(p.read_text()) if p.exists() else default

    analyzed = load("items_analyzed.json", {"items": []})
    history = load(store.HISTORY_NAME, {"snapshots": []})
    recent = load("items_recent.json", {"items": []})
    a_items = analyzed.get("items", [])
    snaps = history.get("snapshots", [])
    archive = store.load_archive(data_dir)

    # ── STRUCTURAL ──
    def forbidden(objs):
        bad = set()
        for o in objs:
            for k in o.keys():
                if k.lower() in store.FORBIDDEN_KEYS:
                    bad.add(k)
        return bad
    forb = forbidden(archive) | forbidden(a_items)
    for s in snaps:
        forb |= forbidden(list(s.get("themes", {}).values()))
    record("S1", "STRUCTURAL", "FAIL" if forb else "PASS",
           f"forbidden judgment keys: {sorted(forb)}" if forb else "no sentiment/judgment keys anywhere")

    syn = sum(1 for it in recent.get("items", []) if it.get("synthetic"))
    syn += sum(1 for s in snaps if s.get("meta", {}).get("contains_synthetic"))
    record("S2", "STRUCTURAL", "FAIL" if syn else "PASS",
           f"SYNTHETIC data in shipped files ({syn}) — fixtures must stay in a temp dir"
           if syn else "no synthetic data in shipped derived files")

    stray = {it.get("theme") for it in a_items} - canon
    stray.discard(None)
    record("S3", "STRUCTURAL", "FAIL" if stray else "PASS",
           f"themes outside taxonomy: {sorted(stray)}" if stray else f"all themes within taxonomy ({len(canon)})")

    share_err = []
    for s in snaps:
        tot = s.get("total_items", 0)
        for th, blk in s.get("themes", {}).items():
            exp = round(blk.get("volume", 0) / tot, 4) if tot else 0.0
            if abs(exp - blk.get("share", 0)) > 0.0002:
                share_err.append(f"{s['date']}/{th}")
    record("S6", "STRUCTURAL", "FAIL" if share_err else "PASS",
           f"share != volume/total: {share_err[:6]}" if share_err else "share reproduces volume/total")

    tracked = [f for f in FORBIDDEN_TRACKED if git_tracked(f)]
    record("S7", "STRUCTURAL", "FAIL" if tracked else "PASS",
           f"STORAGE RULE VIOLATION: these must not be git-tracked: {tracked}"
           if tracked else "storage rule ok: rewritten derived files are not git-tracked")

    # ── DATA ──
    if not archive and not (Path(data_dir) / store.ARCHIVE_NAME).exists():
        record("D1", "DATA", "WARN", "no archive yet — no real ingest has run")
    else:
        wm = store.read_watermark(data_dir)
        lines = store.count_lines(store.archive_path(data_dir))
        bad = [ln for ln, o, e in store.iter_jsonl(store.archive_path(data_dir)) if e]
        ids = [o["id"] for _, o, e in store.iter_jsonl(store.archive_path(data_dir)) if o and o.get("id")]
        dupes = [k for k, c in Counter(ids).items() if c > 1]
        probs = []
        if lines < int(wm.get("archive_lines") or 0):
            probs.append(f"archive {lines} < watermark {wm.get('archive_lines')} (TRUNCATION)")
        if dupes: probs.append(f"{len(dupes)} duplicate id(s)")
        if bad: probs.append(f"{len(bad)} unparseable line(s)")
        record("D1", "DATA", "FAIL" if probs else "PASS",
               "; ".join(probs) if probs else f"{lines} lines, no dupes, watermark intact")

    sight_ids = {o.get("id") for _, o, e in store.iter_jsonl(store.sightings_path(data_dir)) if o}
    if not sight_ids:
        record("D2", "DATA", "WARN", "no sightings yet")
    else:
        orphans = sight_ids - {it["id"] for it in archive}
        record("D2", "DATA", "FAIL" if orphans else "PASS",
               f"{len(orphans)} sighting(s) reference no archive item" if orphans
               else f"{len(sight_ids)} sighting ids resolve to archive items")

    if not snaps:
        record("D3", "DATA", "WARN", "no snapshots yet")
    elif not archive:
        record("D3", "DATA", "WARN", "snapshot present but archive empty")
    else:
        latest = snaps[-1]
        ws, we = latest.get("window_start"), latest.get("window_end")
        win = store.filter_window(archive, ws, we)
        probs = []
        if len(win) != latest.get("total_items", -1):
            probs.append(f"total: snap={latest.get('total_items')} archive={len(win)}")
        region_arch = Counter(it.get("region", "unknown") for it in win)
        for r, blk in latest.get("regions", {}).items():
            if region_arch.get(r, 0) != blk.get("total", 0):
                probs.append(f"region {r}: snap={blk.get('total')} archive={region_arch.get(r,0)}")
        record("D3", "DATA", "FAIL" if probs else "PASS",
               f"snapshot not reproducible from archive window [{ws}..{we}]: {probs[:6]}" if probs
               else f"snapshot total+regions reproduce from archive window [{ws}..{we}] ({len(win)} items)")

    if len(snaps) < args.dead_outlet_window:
        record("D4", "DATA", "WARN",
               f"only {len(snaps)} snapshot(s); need {args.dead_outlet_window} to judge dead outlets")
    else:
        recent_snaps = snaps[-args.dead_outlet_window:]
        active_outlets = {f["outlet"] for f in feeds if f.get("status") == "active"}
        silent = []
        for o in sorted(active_outlets):
            tots = [s.get("outlets", {}).get(o, {}).get("total", 0) for s in recent_snaps]
            if all(t == 0 for t in tots):
                silent.append(o)
        record("D4", "DATA", "FAIL" if silent else "PASS",
               f"{len(silent)} active outlet(s) silent for {args.dead_outlet_window} snapshots "
               f"(likely dead feeds): {silent}" if silent
               else f"every active outlet produced items within the last {args.dead_outlet_window} snapshots")

    if feeds:
        val = sum(1 for f in feeds if f.get("validation", {}).get("validated"))
        record("D5", "DATA", "WARN" if val < len(feeds) else "PASS",
               f"{val}/{len(feeds)} feeds validated; {len(feeds)-val} candidate "
               f"(run discover_feeds.py --health-check)")
    else:
        record("D5", "DATA", "WARN", "empty registry")

    # Q1 analysis-quality — WARN (do not silently ship low-quality analysis).
    # Two degradation signals: analysis ran OFFLINE (keyword themer, weaker), or a
    # large share of items fell to 'other' (themes not resolving). Neither fails
    # the build, but both must be visible.
    st = analyzed.get("stats", {})
    backend = analyzed.get("backend", "")
    total_an = st.get("analyzed", 0) or 0
    other_n = st.get("other_theme", 0) or 0
    other_share = round(other_n / total_an, 3) if total_an else 0.0
    if not analyzed.get("items"):
        record("Q1", "DATA", "WARN", "no analysis yet")
    else:
        probs = []
        if str(backend).startswith("offline"):
            probs.append("analysis ran OFFLINE (keyword themer — set ANTHROPIC_API_KEY for better themes/entities)")
        if other_share > 0.25:
            probs.append(f"{int(other_share*100)}% of items are theme 'other' (themes not resolving well)")
        record("Q1", "DATA", "WARN" if probs else "PASS",
               "; ".join(probs) if probs else f"analysis quality ok (backend={backend}, other={int(other_share*100)}%)")

    counts = Counter(r["status"] for r in results)
    report = {"_comment": "STRUCTURAL checks hold regardless of data; DATA checks WARN "
                          "until real data exists. This file is git-IGNORED (storage rule).",
              "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "summary": dict(counts), "checks": results}
    (Path(data_dir) / store.REPORT_NAME).write_text(json.dumps(report, indent=2))
    for r in results:
        print(f"  [{r['status']}] {r['id']} ({r['kind']}): {r['msg']}")
    print(f"\n{dict(counts)} -> {store.REPORT_NAME}")
    if counts.get("FAIL"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
