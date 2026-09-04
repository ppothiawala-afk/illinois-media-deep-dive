#!/usr/bin/env python3
"""
weekly_summary.py — a human-readable weekly digest with LINKED articles, built
for review-and-compare. It doubles as the spot-check tool: every theme lists real
article links so you can confirm the tag is right, and compare how outlets covered
the same week.

Reads media_history.json (latest snapshot aggregates), items_recent.json (analyzed
items with links), and surge_report.json. Writes weekly_summary_<date>.md.

No sentiment — counts and links only.

Usage:
    python3 weekly_summary.py
    python3 weekly_summary.py --examples 6 --data-dir /tmp/demo
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import archive_store as store

RECENT_NAME = "items_recent.json"
SURGE_NAME = "surge_report.json"


def load(data_dir, name, default):
    p = Path(data_dir) / name
    return json.loads(p.read_text()) if p.exists() else default


def main():
    ap = argparse.ArgumentParser(description="Weekly Illinois media digest with linked articles.")
    ap.add_argument("--examples", type=int, default=5, help="linked example articles per theme")
    ap.add_argument("--data-dir")
    args = ap.parse_args()

    data_dir = store.resolve_data_dir(args.data_dir)
    hist = load(data_dir, store.HISTORY_NAME, {"snapshots": []})
    recent = load(data_dir, RECENT_NAME, {"items": []})
    surge = load(data_dir, SURGE_NAME, {"surging": [], "emerging": []})
    if not hist["snapshots"]:
        raise SystemExit("No snapshots — run rollup.py first.")
    snap = hist["snapshots"][-1]
    ws, we = snap["window_start"], snap["window_end"]

    # group recent analyzed items by theme, within the snapshot window
    by_theme = defaultdict(list)
    for it in recent.get("items", []):
        if ws <= str(it.get("published", ""))[:10] <= we:
            by_theme[it.get("theme", "other")].append(it)

    backend = snap.get("meta", {}).get("backend", "?")
    offline = str(backend).startswith("offline")
    L = []
    L.append(f"# Illinois Media Weekly Summary — {ws} → {we}\n")
    L.append(f"*{snap['total_items']} articles · backend `{backend}`"
             + ("  ⚠️ offline keyword themer — themes are coarse; run with the API key for the real read" if offline else "")
             + " · counts, not sentiment*\n")

    # themes ranked by share (skip 'other' in the headline table)
    themes = sorted(((b["share"], t, b["volume"]) for t, b in snap["themes"].items()),
                    reverse=True)
    L.append("## What Illinois was focused on\n")
    L.append("| Theme | Share | Articles |")
    L.append("|---|---:|---:|")
    for sh, t, v in themes:
        if v == 0:
            continue
        L.append(f"| {t} | {round(sh*100)}% | {v} |")
    L.append("")

    # surge
    L.append("## Surge radar — rising vs. its own normal\n")
    conf = surge.get("report_confidence", "")
    if conf and conf.startswith("low"):
        L.append(f"> ⚠️ {conf}. Treat as early hints, not established surges.\n")
    for label, key in [("Surging", "surging"), ("Emerging", "emerging")]:
        rows = surge.get(key, [])
        if rows:
            L.append(f"**{label}:** " + ", ".join(
                f"{e['theme']} (now {round(e['latest_share']*100)}%, normal {round(e['baseline_mean_share']*100)}%"
                + (", broadening" if e.get("broadening") else "") + ")" for e in rows) + "\n")
        else:
            L.append(f"**{label}:** none this week\n")

    # themes with linked examples (the review-and-compare core)
    L.append("## Themes with example articles — click to verify the tag\n")
    for sh, t, v in themes:
        if v == 0 or t == "other":
            continue
        L.append(f"### {t} — {round(sh*100)}% ({v} articles)")
        for it in by_theme.get(t, [])[:args.examples]:
            ents = ", ".join(it.get("entities", [])[:4])
            L.append(f"- [{it['title']}]({it['link']}) — *{it['outlet']}*, {it['published']}"
                     + (f"  ·  {ents}" if ents else ""))
        L.append("")
    # show 'other' too so you can see what's slipping through (quality check)
    if by_theme.get("other"):
        L.append(f"### other / unclassified — {len(by_theme['other'])} articles (review: are these mis-binned?)")
        for it in by_theme["other"][:args.examples]:
            L.append(f"- [{it['title']}]({it['link']}) — *{it['outlet']}*, {it['published']}")
        L.append("")

    # entities
    L.append("## Most-named entities (resolved)\n")
    L.append(", ".join(f"{e[0]} ({e[1]})" for e in snap.get("entities_top", [])[:20]) or "none")
    L.append("")

    # outlet divergence highlights
    L.append("## Outlet divergence — each outlet's top theme this week\n")
    L.append("| Outlet | Region | Articles | Top theme |")
    L.append("|---|---|---:|---|")
    for o, blk in sorted(snap.get("outlets", {}).items(), key=lambda kv: -kv[1]["total"]):
        ts = blk.get("theme_share", {})
        top = max(ts.items(), key=lambda kv: kv[1])[0] if ts else "—"
        L.append(f"| {o} | {blk.get('region','')} | {blk['total']} | {top} |")
    L.append("")

    # penetration
    L.append("## Story penetration — what broke through across outlets\n")
    pen = snap.get("penetration", [])
    if pen:
        for p in pen:
            L.append(f"- **[{p['outlet_count']} outlets]** [{p['title']}]({p['link']}) "
                     f"— {p['theme']} · {', '.join(p['outlets'])}")
    else:
        L.append("- No story appeared in 2+ outlets this week.")
    L.append("")

    # region compare
    L.append("## Chicago vs. downstate — theme share by region\n")
    L.append("*Downstate is thinner (legacy downstate dailies are paywalled) — compare with that caveat.*\n")
    for r, blk in sorted(snap.get("regions", {}).items(), key=lambda kv: -kv[1]["total"]):
        top3 = sorted(blk.get("theme_share", {}).items(), key=lambda kv: -kv[1])[:3]
        L.append(f"- **{r}** ({blk['total']}): " + ", ".join(f"{t} {round(s*100)}%" for t, s in top3))
    L.append("")

    out = Path(data_dir) / f"weekly_summary_{snap['date']}.md"
    out.write_text("\n".join(L))
    print(f"wrote {out.name} ({snap['total_items']} articles, {len([t for _,t,v in themes if v])} themes with examples)")


if __name__ == "__main__":
    main()
