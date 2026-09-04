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
    m = snap.get("meta", {})
    civic_note = ""
    if m.get("civic_only"):
        civic_note = (f" · **civic-only** ({m.get('noncivic_dropped',0)} non-civic "
                      f"of {m.get('all_in_window','?')} filtered out)")
    L = []
    L.append(f"# Illinois Civic Media Weekly Summary — {ws} → {we}\n")
    L.append(f"*{snap['total_items']} civic articles{civic_note} · backend `{backend}`"
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

    # ── HTML version (clickable, printable to PDF, easy to open/share) ──
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    H = ["""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Illinois Civic Media — %s</title><style>
body{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
max-width:820px;margin:32px auto;padding:0 20px;color:#1a1a1a}
h1{font-size:24px;margin:0 0 4px} h2{font-size:18px;margin:28px 0 10px;border-bottom:2px solid #eee;padding-bottom:4px}
h3{font-size:15px;margin:18px 0 6px;color:#333} .meta{color:#666;font-size:13px;margin-bottom:8px}
table{border-collapse:collapse;width:100%%;font-size:14px;margin:8px 0} th,td{border-bottom:1px solid #eee;padding:6px 8px;text-align:left}
th{color:#666;font-weight:600;font-size:12px;text-transform:uppercase}
a{color:#1a56db;text-decoration:none} a:hover{text-decoration:underline}
ul{margin:6px 0;padding-left:20px} li{margin:3px 0} .warn{background:#fff7e6;border-left:3px solid #f0b429;padding:8px 12px;color:#7a5c00;margin:8px 0}
.ent{color:#666;font-size:12px} .small{color:#888;font-size:12px}
@media print{a{color:#1a56db}}
</style></head><body>""" % esc(snap["date"])]
    H.append(f"<h1>Illinois Civic Media — Weekly Summary</h1>")
    H.append(f'<div class="meta">{ws} → {we} · {snap["total_items"]} civic articles'
             + (f' ({m.get("noncivic_dropped",0)} non-civic filtered of {m.get("all_in_window","?")})' if m.get("civic_only") else "")
             + f' · backend {esc(backend)}'
             + (' · ⚠️ offline keyword themer (run with API key for the real read)' if offline else '')
             + ' · counts, not sentiment</div>')

    H.append("<h2>What Illinois was focused on</h2><table><tr><th>Theme</th><th>Share</th><th>Articles</th></tr>")
    for sh, t, v in themes:
        if v:
            H.append(f"<tr><td>{esc(t)}</td><td>{round(sh*100)}%</td><td>{v}</td></tr>")
    H.append("</table>")

    H.append("<h2>Surge radar</h2>")
    if conf and conf.startswith("low"):
        H.append(f'<div class="warn">{esc(conf)}. Early hints, not established surges.</div>')
    for label, key in [("Surging", "surging"), ("Emerging", "emerging")]:
        rows = surge.get(key, [])
        if rows:
            H.append(f"<p><b>{label}:</b> " + ", ".join(
                f"{esc(e['theme'])} (now {round(e['latest_share']*100)}%, normal {round(e['baseline_mean_share']*100)}%"
                + (", broadening" if e.get('broadening') else "") + ")" for e in rows) + "</p>")
        else:
            H.append(f"<p><b>{label}:</b> none this week</p>")

    H.append("<h2>Themes with example articles — click to verify the tag</h2>")
    for sh, t, v in themes:
        if not v or t == "other":
            continue
        H.append(f"<h3>{esc(t)} — {round(sh*100)}% ({v})</h3><ul>")
        for it in by_theme.get(t, [])[:args.examples]:
            ents = ", ".join(it.get("entities", [])[:4])
            H.append(f'<li><a href="{esc(it["link"])}" target="_blank">{esc(it["title"])}</a> '
                     f'<span class="small">— {esc(it["outlet"])}, {esc(it["published"])}</span>'
                     + (f' <span class="ent">· {esc(ents)}</span>' if ents else "") + "</li>")
        H.append("</ul>")

    H.append("<h2>Most-named entities</h2><p>"
             + ", ".join(f"{esc(e[0])} ({e[1]})" for e in snap.get("entities_top", [])[:20]) + "</p>")

    H.append("<h2>Outlet divergence — each outlet's top theme</h2><table><tr><th>Outlet</th><th>Region</th><th>Articles</th><th>Top theme</th></tr>")
    for o, blk in sorted(snap.get("outlets", {}).items(), key=lambda kv: -kv[1]["total"]):
        ts = blk.get("theme_share", {})
        top = max(ts.items(), key=lambda kv: kv[1])[0] if ts else "—"
        H.append(f"<tr><td>{esc(o)}</td><td>{esc(blk.get('region',''))}</td><td>{blk['total']}</td><td>{esc(top)}</td></tr>")
    H.append("</table>")

    H.append("<h2>Story penetration — what broke through across outlets</h2><ul>")
    for p in snap.get("penetration", []):
        H.append(f'<li><b>[{p["outlet_count"]}]</b> <a href="{esc(p["link"])}" target="_blank">{esc(p["title"])}</a> '
                 f'<span class="small">— {esc(p["theme"])} · {esc(", ".join(p["outlets"]))}</span></li>')
    H.append("</ul>" if snap.get("penetration") else "<p>No story in 2+ outlets this week.</p>")

    H.append('<h2>Chicago vs. downstate</h2><p class="small">Downstate is thinner (legacy dailies paywalled).</p><ul>')
    for r, blk in sorted(snap.get("regions", {}).items(), key=lambda kv: -kv[1]["total"]):
        top3 = sorted(blk.get("theme_share", {}).items(), key=lambda kv: -kv[1])[:3]
        H.append(f"<li><b>{esc(r)}</b> ({blk['total']}): " + ", ".join(f"{esc(t)} {round(s*100)}%" for t, s in top3) + "</li>")
    H.append("</ul></body></html>")

    out_html = Path(data_dir) / f"weekly_summary_{snap['date']}.html"
    out_html.write_text("\n".join(H))
    print(f"wrote {out.name} and {out_html.name} "
          f"({snap['total_items']} civic articles, {len([t for _,t,v in themes if v])} themes with examples)")


if __name__ == "__main__":
    main()
