#!/usr/bin/env python3
"""
search_archive.py — the backend discovery tool. Search the WHOLE archive for any
term(s) and get the same full-history read the event tracker produces: mention
timeline, virality (breadth + trend), co-occurring entities/themes, and linked
articles — plus a ready-to-paste watchlist.json line to start tracking it
permanently.

This is the deep counterpart to the dashboard's client-side search box (which
only covers the recent shipped corpus). Use it to investigate anything, back to
the first mention, and to decide what's worth adding to the watchlist.

Counts, not sentiment; media coverage attention. Retroactive from the full archive.

Usage:
    python3 search_archive.py "operation midway blitz"
    python3 search_archive.py --terms "data center,data centers" --exclude "sports"
    python3 search_archive.py "teachers strike" --json
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import archive_store as store
from build_explorer import compute_topic

HERE = Path(__file__).resolve().parent
ANALYZED_NAME = "items_analyzed.json"


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def main():
    ap = argparse.ArgumentParser(description="Full-archive discovery search.")
    ap.add_argument("query", nargs="*", help="a phrase to search (e.g. teachers strike)")
    ap.add_argument("--terms", help="comma-separated match terms (OR); overrides positional")
    ap.add_argument("--exclude", help="comma-separated exclude terms")
    ap.add_argument("--examples", type=int, default=10)
    ap.add_argument("--json", action="store_true", help="also write search_<slug>.json")
    ap.add_argument("--data-dir")
    args = ap.parse_args()

    terms = ([t.strip().lower() for t in args.terms.split(",")] if args.terms
             else ([" ".join(args.query).lower()] if args.query else []))
    if not terms or not terms[0]:
        ap.error("give a query phrase or --terms")
    excludes = [x.strip().lower() for x in args.exclude.split(",")] if args.exclude else []
    label = args.terms or " ".join(args.query)

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

    hits = []
    for h in archive:
        a = an.get(h["id"], {})
        text = f"{h.get('title','')} {h.get('summary','')} {' '.join(a.get('entities',[]))}".lower()
        if any(x in text for x in excludes):
            continue
        if any(t in text for t in terms):
            hits.append({"title": h.get("title"), "link": h.get("link"), "outlet": h.get("outlet"),
                         "region": h.get("region"), "published": h.get("published"),
                         "entities": a.get("entities", []), "theme": a.get("theme")})

    rec = compute_topic(label, "search", hits, recent_weeks=8, examples=args.examples)
    if not rec:
        print(f'No dated mentions of "{label}" in the archive.')
        return

    v = rec["virality"]
    print(f'\n== "{label}" ==  ({rec["total_mentions"]} mentions, {rec["first_seen"]} → {rec["last_seen"]})')
    print(f'virality: {v["breadth_total_outlets"]} outlets · trend {v["trend"]} · '
          f'peak {v["peak_week"]} ({v["peak_week_mentions"]}) · latest week {v["latest_week_mentions"]}')
    print("weekly:  " + "  ".join(f'{s["week"]}:{s["mentions"]}m/{s["outlets"]}o' for s in rec["series"]))
    if rec["co_entities"]:
        print("travels with: " + ", ".join(f"{c[0]}({c[1]})" for c in rec["co_entities"][:10]))
    if rec["co_themes"]:
        print("themes: " + ", ".join(f"{c[0]}({c[1]})" for c in rec["co_themes"][:6]))
    print("\narticles:")
    for a in rec["recent_articles"]:
        print(f'  {a["published"]}  {a["outlet"]:22.22}  {a["title"][:70]}')
        print(f'      {a["link"]}')

    slug = slugify(label)
    print("\n→ to track permanently, add to watchlist.json events[]:")
    print(f'  {{"name":"{label}","slug":"{slug}","match":{json.dumps(terms)},'
          f'"exclude":{json.dumps(excludes)},"added":"{datetime.now(timezone.utc).date().isoformat()}"}}')

    if args.json:
        out = Path(data_dir) / f"search_{slug}.json"
        out.write_text(json.dumps({"query": label, "terms": terms, "excludes": excludes,
                                   "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   **rec}, indent=2))
        print(f"\nwrote {out.name}")


if __name__ == "__main__":
    main()
