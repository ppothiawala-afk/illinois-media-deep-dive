#!/usr/bin/env python3
"""
track_events.py — track named events/campaigns from the watchlist across the WHOLE
archive, and measure their MEDIA virality over time. Retroactive by design: a new
watchlist entry gets its full history because the archive keeps every article.

For each watched event it computes, from the archive (matched on title+summary+
entities) and — where available — the analyzed corpus (for co-occurring themes/
entities):

  * a weekly time series: mentions, distinct OUTLETS (breadth), distinct regions
  * VIRALITY, shown as transparent components (no black-box score):
      - breadth  = how many distinct outlets carried it (spread = viral)
      - velocity = latest week vs the trailing weeks (accelerating vs fading)
      - peak week, first/last seen, total mentions
  * CO-OCCURRING themes + entities — what the event travels with (this is how the
    "economic-impact" or "legal-challenge" angle surfaces)
  * recent linked articles for review

Writes events_report.json (committed, small/bounded). Counts, not sentiment; this
is MEDIA coverage attention, not public opinion or the event's real-world scale.

Usage:
    python3 track_events.py
    python3 track_events.py --data-dir /tmp/demo --recent 8
"""

import argparse
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import archive_store as store

HERE = Path(__file__).resolve().parent
WATCHLIST = HERE / "watchlist.json"
ANALYZED_NAME = "items_analyzed.json"
REPORT_NAME = "events_report.json"


def week_of(d: date) -> str:
    """Monday of that date's week, as an iso string (the weekly bucket key)."""
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def matches(text, match_terms, exclude_terms):
    low = f" {text.lower()} "
    if any(x in low for x in exclude_terms):
        return False
    return any(m in low for m in match_terms)


def main():
    ap = argparse.ArgumentParser(description="Track watchlist events across the archive.")
    ap.add_argument("--recent", type=int, default=8, help="weeks used for the velocity/trend read")
    ap.add_argument("--examples", type=int, default=6, help="linked example articles per event")
    ap.add_argument("--data-dir")
    args = ap.parse_args()

    data_dir = store.resolve_data_dir(args.data_dir)
    watch = json.loads(WATCHLIST.read_text())
    archive = store.load_archive(data_dir)

    # optional join to analyzed for theme/entity co-occurrence
    an = {}
    ap_path = Path(data_dir) / ANALYZED_NAME
    if ap_path.exists():
        try:
            for it in json.loads(ap_path.read_text()).get("items", []):
                an[it["id"]] = it
        except Exception:  # noqa: BLE001
            an = {}

    events_out = []
    for ev in watch.get("events", []):
        mt = [m.lower() for m in ev.get("match", [])]
        ex = [x.lower() for x in ev.get("exclude", [])]
        hits = []
        for it in archive:
            text = f"{it.get('title','')} {it.get('summary','')} {' '.join((an.get(it['id'],{}) or {}).get('entities',[]))}"
            if matches(text, mt, ex):
                hits.append(it)

        if not hits:
            events_out.append({"name": ev["name"], "slug": ev["slug"], "total_mentions": 0,
                               "note": "no mentions in the archive yet"})
            continue

        dated = [(store.parse_date(h.get("published")), h) for h in hits]
        dated = [(d, h) for d, h in dated if d]
        dated.sort(key=lambda x: x[0])
        first_seen = dated[0][0].isoformat() if dated else None
        last_seen = dated[-1][0].isoformat() if dated else None

        # weekly series
        wk_mentions = Counter()
        wk_outlets = defaultdict(set)
        wk_regions = defaultdict(set)
        for d, h in dated:
            wk = week_of(d)
            wk_mentions[wk] += 1
            wk_outlets[wk].add(h.get("outlet"))
            wk_regions[wk].add(h.get("region"))
        weeks = sorted(wk_mentions)
        series = [{"week": w, "mentions": wk_mentions[w],
                   "outlets": len(wk_outlets[w]), "regions": len(wk_regions[w])} for w in weeks]

        # virality components
        breadth_total = len({h.get("outlet") for _, h in dated})
        breadth_peak = max((s["outlets"] for s in series), default=0)
        peak = max(series, key=lambda s: s["mentions"]) if series else None
        recent = series[-args.recent:]
        last_wk = series[-1]["mentions"] if series else 0
        prior = [s["mentions"] for s in recent[:-1]]
        prior_avg = round(sum(prior) / len(prior), 2) if prior else 0.0
        if not prior:
            trend = "new"
        elif last_wk == 0:
            trend = "dormant"
        elif prior_avg > 0 and last_wk >= 1.5 * prior_avg:
            trend = "rising"
        elif prior_avg > 0 and last_wk <= 0.5 * prior_avg:
            trend = "fading"
        else:
            trend = "steady"
        broadening = series[-1]["outlets"] > (sum(s["outlets"] for s in recent[:-1]) / len(recent[:-1])) if len(recent) > 1 else False

        # co-occurring themes + entities (what it travels with)
        co_theme = Counter()
        co_entity = Counter()
        for _, h in dated:
            a = an.get(h["id"])
            if not a:
                continue
            if a.get("theme"):
                co_theme[a["theme"]] += 1
            for e in a.get("entities", []):
                el = e.lower()
                if any(m in el or el in m for m in mt):  # skip the event's own name
                    continue
                co_entity[e] += 1

        # recent linked articles for review
        top_articles = [{"title": h.get("title"), "link": h.get("link"),
                         "outlet": h.get("outlet"), "region": h.get("region"),
                         "published": h.get("published"),
                         "theme": (an.get(h["id"], {}) or {}).get("theme")}
                        for _, h in reversed(dated)][:args.examples]

        events_out.append({
            "name": ev["name"], "slug": ev["slug"], "notes": ev.get("notes", ""),
            "total_mentions": len(hits), "first_seen": first_seen, "last_seen": last_seen,
            "weeks_active": len(weeks),
            "virality": {
                "breadth_total_outlets": breadth_total,
                "breadth_peak_week_outlets": breadth_peak,
                "peak_week": peak["week"] if peak else None,
                "peak_week_mentions": peak["mentions"] if peak else 0,
                "latest_week_mentions": last_wk,
                "prior_weeks_avg_mentions": prior_avg,
                "trend": trend, "broadening": bool(broadening),
            },
            "series": series,
            "co_themes": co_theme.most_common(6),
            "co_entities": co_entity.most_common(10),
            "recent_articles": top_articles,
        })

    report = {
        "_comment": "Media-attention tracking for watchlist events. VIRALITY is shown "
                    "as transparent components (breadth = distinct outlets, velocity = "
                    "latest vs trailing weeks), not a black-box score. Counts, not "
                    "sentiment; measures COVERAGE attention, not public opinion or real "
                    "scale. Retroactive: history reconstructed from the full archive.",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "watchlist_version": watch.get("version"),
        "events": events_out,
    }
    (Path(data_dir) / REPORT_NAME).write_text(json.dumps(report, indent=2))
    for e in events_out:
        v = e.get("virality", {})
        print(f"  {e['name']}: {e['total_mentions']} mentions across "
              f"{v.get('breadth_total_outlets',0)} outlets, trend={v.get('trend','-')}, "
              f"peak {v.get('peak_week','-')}")
    print(f"-> {REPORT_NAME}")


if __name__ == "__main__":
    main()
