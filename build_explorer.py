#!/usr/bin/env python3
"""
build_explorer.py — precompute an interactive "explorer" dataset so the static
dashboard can let a viewer PICK any top entity or theme and drill into its full
history — no backend needed (the browser can't query the whole archive, so we
precompute the top set here).

For the top-N entities (by mentions) and every theme, it computes — over the WHOLE
archive — the same shape as the event tracker: weekly series (mentions, outlet
breadth, regions), virality (breadth + velocity/trend), co-occurring entities/
themes, and recent linked articles. Writes explorer.json (committed, bounded to
top-N so it stays small).

FUTURE (open text-search discovery): the same per-hit machinery here is what an
ad-hoc "search any term" tool will reuse — match arbitrary text over the archive,
surface it as a candidate topic, and offer to promote it into watchlist.json.
That is deliberately factored into compute_topic() below.

Counts, not sentiment; MEDIA coverage attention. Retroactive from the full archive.

Usage:
    python3 build_explorer.py --top-entities 40
    python3 build_explorer.py --data-dir /tmp/demo
"""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import archive_store as store

HERE = Path(__file__).resolve().parent
ANALYZED_NAME = "items_analyzed.json"
REPORT_NAME = "explorer.json"


def week_of(d):
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def compute_topic(name, kind, hits, recent_weeks, examples):
    """hits: list of joined item dicts (title, link, outlet, region, published,
    entities, theme). Returns the drill-down record — reused for entities, themes,
    and (later) ad-hoc text searches."""
    dated = sorted(((store.parse_date(h.get("published")), h) for h in hits
                    if store.parse_date(h.get("published"))), key=lambda x: x[0])
    if not dated:
        return None
    wk_m, wk_o, wk_r = Counter(), defaultdict(set), defaultdict(set)
    for d, h in dated:
        w = week_of(d)
        wk_m[w] += 1
        wk_o[w].add(h.get("outlet"))
        wk_r[w].add(h.get("region"))
    weeks = sorted(wk_m)
    series = [{"week": w, "mentions": wk_m[w], "outlets": len(wk_o[w]), "regions": len(wk_r[w])} for w in weeks]
    recent = series[-recent_weeks:]
    last = series[-1]["mentions"]
    prior = [s["mentions"] for s in recent[:-1]]
    prior_avg = round(sum(prior) / len(prior), 2) if prior else 0.0
    trend = ("new" if not prior else "dormant" if last == 0
             else "rising" if prior_avg and last >= 1.5 * prior_avg
             else "fading" if prior_avg and last <= 0.5 * prior_avg else "steady")
    co_ent, co_theme = Counter(), Counter()
    for _, h in dated:
        for e in h.get("entities", []):
            if e != name:
                co_ent[e] += 1
        if kind != "theme" and h.get("theme"):
            co_theme[h["theme"]] += 1
    arts = [{"title": h.get("title"), "link": h.get("link"), "outlet": h.get("outlet"),
             "published": h.get("published"), "theme": h.get("theme")}
            for _, h in reversed(dated)][:examples]
    return {
        "name": name, "kind": kind, "total_mentions": len(dated),
        "first_seen": dated[0][0].isoformat(), "last_seen": dated[-1][0].isoformat(),
        "virality": {"breadth_total_outlets": len({h.get("outlet") for _, h in dated}),
                     "breadth_peak_week_outlets": max((s["outlets"] for s in series), default=0),
                     "peak_week": max(series, key=lambda s: s["mentions"])["week"],
                     "peak_week_mentions": max(s["mentions"] for s in series),
                     "latest_week_mentions": last, "prior_weeks_avg_mentions": prior_avg,
                     "trend": trend},
        "series": series,
        "co_entities": co_ent.most_common(10),
        "co_themes": co_theme.most_common(6),
        "recent_articles": arts,
    }


def main():
    ap = argparse.ArgumentParser(description="Precompute the entity/theme explorer dataset.")
    ap.add_argument("--top-entities", type=int, default=40)
    ap.add_argument("--recent-weeks", type=int, default=8)
    ap.add_argument("--examples", type=int, default=6)
    ap.add_argument("--min-mentions", type=int, default=3,
                    help="entity needs at least this many mentions to be explorable")
    ap.add_argument("--search-days", type=int, default=180,
                    help="bounded window shipped to the browser for the open search box")
    ap.add_argument("--search-cap", type=int, default=3000,
                    help="max articles in the browser search corpus (keeps the JSON small)")
    ap.add_argument("--data-dir")
    args = ap.parse_args()

    data_dir = store.resolve_data_dir(args.data_dir)
    archive = store.load_archive(data_dir)
    an = {}
    ap_path = Path(data_dir) / ANALYZED_NAME
    if ap_path.exists():
        try:
            for it in json.loads(ap_path.read_text()).get("items", []):
                an[it["id"]] = it
        except Exception:  # noqa: BLE001
            an = {}

    # join archive to analyzed (entities/theme)
    items = []
    for h in archive:
        a = an.get(h["id"], {})
        items.append({"id": h["id"], "title": h.get("title"), "link": h.get("link"),
                      "outlet": h.get("outlet"), "region": h.get("region"),
                      "published": h.get("published"),
                      "entities": a.get("entities", []), "theme": a.get("theme")})

    ent_count = Counter()
    for it in items:
        for e in it["entities"]:
            ent_count[e] += 1
    top_entities = [e for e, n in ent_count.most_common(args.top_entities) if n >= args.min_mentions]
    themes = sorted({it["theme"] for it in items if it.get("theme")})

    topics = []
    for e in top_entities:
        rec = compute_topic(e, "entity", [it for it in items if e in it["entities"]],
                            args.recent_weeks, args.examples)
        if rec:
            topics.append(rec)
    for t in themes:
        rec = compute_topic(t, "theme", [it for it in items if it.get("theme") == t],
                            args.recent_weeks, args.examples)
        if rec:
            topics.append(rec)

    report = {
        "_comment": "Precomputed drill-down data for the dashboard's interactive "
                    "Explore picker: top entities + all themes, each with full-history "
                    "weekly series, virality, co-occurrence, and linked articles. "
                    "Bounded to top-N. Counts, not sentiment; media coverage attention.",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entities": [t["name"] for t in topics if t["kind"] == "entity"],
        "themes": [t["name"] for t in topics if t["kind"] == "theme"],
        "topics": topics,
    }
    (Path(data_dir) / REPORT_NAME).write_text(json.dumps(report, indent=2))
    print(f"explorer: {len(top_entities)} entities + {len(themes)} themes -> {REPORT_NAME}")

    # ── bounded search corpus for the browser's open search/discovery box ──
    # Anything OLDER or DEEPER than this window is reached via search_archive.py
    # (full-archive, server-side). Kept small: title + entities + a summary snippet.
    from datetime import date, timedelta
    today = date.today()
    cutoff = today - timedelta(days=args.search_days)
    by_recent = sorted(archive, key=lambda h: h.get("published", ""), reverse=True)
    corpus = []
    for h in by_recent:
        d = store.parse_date(h.get("published"))
        if not d or d < cutoff:
            continue
        a = an.get(h["id"], {})
        searchable = f"{h.get('title','')} {h.get('summary','')[:160]} {' '.join(a.get('entities',[]))}".lower()
        corpus.append({"t": h.get("title"), "d": h.get("published"), "o": h.get("outlet"),
                       "r": h.get("region"), "th": a.get("theme"),
                       "e": a.get("entities", [])[:6], "l": h.get("link"), "s": searchable})
        if len(corpus) >= args.search_cap:
            break
    (Path(data_dir) / "search_corpus.json").write_text(json.dumps({
        "_comment": "Bounded recent-article corpus for the dashboard's client-side "
                    "search/discovery box. Older/deeper searches use search_archive.py "
                    "(full archive, server-side). Fields shortened to keep it small.",
        "generated": datetime.now(timezone.utc).date().isoformat(),
        "window_days": args.search_days, "count": len(corpus), "articles": corpus}))
    print(f"search corpus: {len(corpus)} recent articles (<= {args.search_days}d) -> search_corpus.json")


if __name__ == "__main__":
    main()
