#!/usr/bin/env python3
"""
fetch_feeds.py — stage 1: collect ALL items from the Illinois feed registry and
APPEND them to the append-only archive.

Unlike the earlier state tracker, this build ingests EVERYTHING (no topic
pre-filter) — the whole point is to discover what Illinois is focused on rather
than pre-decide the categories. Items are keyed by a content hash and appended to
`items_archive.jsonl`; re-running is idempotent (same content twice adds nothing).

Each item carries its OUTLET, REGION (chicago/suburban/downstate/statewide) and
OUTLET_TYPE, because the unit of analysis here is the outlet, not the state —
that is what powers outlet-divergence and Chicago-vs-downstate views.

Cheap and API-free: designed to run daily (RSS windows expire). Analysis and
rollup are separate weekly stages.

SYNTHETIC GUARD: any item from --fixtures mode, or from a feed whose <generator>
is 'synthetic-test-fixture', is stamped synthetic:true and rides that flag
downstream; verify_pipeline FAILS if synthetic data reaches shipped derived files.

Usage:
    python3 fetch_feeds.py
    python3 fetch_feeds.py --outlets "WBEZ,WTTW News"
    python3 fetch_feeds.py --fixtures tests/fixtures_synthetic/
    python3 fetch_feeds.py --data-dir /tmp/demo --dry-run
"""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import archive_store as store

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "feeds_config.json"
USER_AGENT = "Mozilla/5.0 (IllinoisMediaDeepDive collector)"
SYNTHETIC_GENERATOR = "synthetic-test-fixture"


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def normalize_title(title: str) -> str:
    t = title or ""
    t = re.split(r"\s+[•|]\s+", t)[0]
    t = t.lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def dedup_hash(norm_title: str, date: str) -> str:
    return hashlib.sha1(f"{norm_title}|{date}".encode("utf-8")).hexdigest()[:16]


def _iso_from_struct(tp) -> str:
    return datetime(*tp[:6], tzinfo=timezone.utc).date().isoformat()


def _parse_with_feedparser(source, is_fixture):
    import feedparser
    parsed = (feedparser.parse(str(source)) if is_fixture
              else feedparser.parse(source, agent=USER_AGENT))
    entries = []
    for e in parsed.entries:
        published = ""
        for key in ("published_parsed", "updated_parsed"):
            if e.get(key):
                published = _iso_from_struct(e.get(key)); break
        if not published:
            for key in ("published", "updated", "date"):
                if e.get(key):
                    published = str(e.get(key))[:10]; break
        entries.append({"title": (e.get("title") or "").strip(),
                        "link": (e.get("link") or "").strip(),
                        "summary": e.get("summary", "") or "",
                        "published": published})
    return {"generator": (parsed.feed.get("generator") or "") if parsed.get("feed") else "",
            "entries": entries}


def _stdlib_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).astimezone(timezone.utc).date().isoformat()
    except Exception:  # noqa: BLE001
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except Exception:  # noqa: BLE001
        return raw[:10]


def _parse_with_stdlib(path):
    import xml.etree.ElementTree as ET
    root = ET.parse(str(path)).getroot()
    ns = {"a": "http://www.w3.org/2005/Atom"}

    def text(el, *names):
        for n in names:
            found = el.find(n, ns) if n.startswith("a:") else el.find(n)
            if found is not None and (found.text or "").strip():
                return found.text.strip()
            if found is not None and n in ("link", "a:link"):
                href = found.get("href")
                if href:
                    return href.strip()
        return ""

    channel = root.find("channel")
    if channel is not None:
        gen = text(channel, "generator")
        entries = [{"title": text(it, "title"), "link": text(it, "link"),
                    "summary": text(it, "description"),
                    "published": _stdlib_date(text(it, "pubDate", "date"))}
                   for it in channel.findall("item")]
    else:
        gen = text(root, "a:generator")
        entries = [{"title": text(it, "a:title"), "link": text(it, "a:link"),
                    "summary": text(it, "a:summary"),
                    "published": _stdlib_date(text(it, "a:updated", "a:published"))}
                   for it in root.findall("a:entry", ns)]
    return {"generator": gen, "entries": entries}


def parse_feed_source(source, is_fixture):
    try:
        return _parse_with_feedparser(source, is_fixture)
    except ImportError:
        if not is_fixture:
            raise RuntimeError("feedparser required for live fetch: pip install -r requirements.txt")
        return _parse_with_stdlib(source)


def collect(parsed, feed, max_items, synthetic):
    rows = []
    for e in parsed["entries"][:max_items]:
        title, link = e["title"], e["link"]
        if not title or not link:
            continue
        summary = re.sub(r"<[^>]+>", " ", e.get("summary") or "")
        summary = re.sub(r"\s+", " ", summary).strip()
        norm = normalize_title(title)
        row = {
            "id": dedup_hash(norm, e["published"]),
            "title": title, "norm_title": norm, "summary": summary[:600],
            "link": link, "published": e["published"],
            "outlet": feed["outlet"], "region": feed.get("region", "unknown"),
            "outlet_type": feed.get("outlet_type", "unknown"),
            "feed_url": feed["feed_url"],
        }
        if synthetic:
            row["synthetic"] = True
        rows.append(row)
    return rows


def load_fixture_map(fixtures_dir):
    out = {}
    for p in sorted(Path(fixtures_dir).glob("*.xml")):
        stem = re.sub(r"^synthetic_", "", p.stem)
        out[stem] = p
    return out


def main():
    ap = argparse.ArgumentParser(description="Collect Illinois media items into the append-only archive.")
    ap.add_argument("--outlets", help="comma-separated outlet names to limit to")
    ap.add_argument("--fixtures", help="offline: directory of SYNTHETIC feed XML files")
    ap.add_argument("--max-items", type=int, default=80)
    ap.add_argument("--data-dir")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data_dir = store.resolve_data_dir(args.data_dir)
    config = json.loads(CONFIG_PATH.read_text())
    feeds = config["feeds"]
    if args.outlets:
        want = {o.strip() for o in args.outlets.split(",")}
        feeds = [f for f in feeds if f["outlet"] in want]
    else:
        # skip feeds a human flagged dead/empty; candidates + active still fetched
        feeds = [f for f in feeds if f.get("status") not in ("flagged", "dead")]

    fixtures_dir = Path(args.fixtures) if args.fixtures else None
    fixture_map = load_fixture_map(fixtures_dir) if fixtures_dir else {}
    offline = fixtures_dir is not None
    if offline:
        print(f">> FIXTURE MODE ({fixtures_dir}) — all items stamped synthetic:true (NOT real).")

    all_rows, per_feed, errors = [], [], []
    feeds_ok = feeds_err = 0

    for f in feeds:
        if offline:
            path = fixture_map.get(slugify(f["outlet"]))
            if not path:
                continue
            sources = [(path, True)]
        else:
            sources = [(f["feed_url"], False)]
        for src, is_fix in sources:
            try:
                parsed = parse_feed_source(src, is_fix)
                synthetic = is_fix or SYNTHETIC_GENERATOR in (parsed.get("generator") or "")
                rows = collect(parsed, f, args.max_items, synthetic)
                all_rows.extend(rows)
                per_feed.append({"outlet": f["outlet"], "region": f.get("region"),
                                 "items": len(rows), "synthetic": synthetic})
                feeds_ok += 1
                print(f"  + {f['outlet']} ({f.get('region')}): {len(rows)} items"
                      + ("  [SYNTHETIC]" if synthetic else ""))
                if not offline:
                    time.sleep(0.5)
            except Exception as e:  # noqa: BLE001
                feeds_err += 1
                errors.append({"outlet": f["outlet"], "error": str(e)})
                print(f"  ! {f['outlet']} failed: {e}", file=sys.stderr)

    # in-run dedup: same wire story across outlets -> one archive row, many sightings
    run_seen, run_items, sightings, collapsed = {}, [], [], 0
    for r in all_rows:
        sightings.append({"id": r["id"], "outlet": r["outlet"], "region": r["region"],
                          "outlet_type": r["outlet_type"], "feed_url": r["feed_url"],
                          "published": r["published"], "synthetic": bool(r.get("synthetic"))})
        if r["id"] in run_seen:
            collapsed += 1
            continue
        run_seen[r["id"]] = r
        run_items.append(r)

    known_ids = store.archive_ids(data_dir)
    known_sightings = store.sighting_keys(data_dir)
    now = datetime.now(timezone.utc)
    run_id = now.isoformat(timespec="seconds")

    new_items = []
    for r in run_items:
        if r["id"] in known_ids:
            continue
        row = dict(r)
        row["first_seen"] = now.date().isoformat()
        row["ingest_run"] = run_id
        row["source_mode"] = "fixtures" if offline else "live"
        new_items.append(row)

    new_sightings = []
    for s in sightings:
        key = (s["id"], s["feed_url"])
        if key in known_sightings:
            continue
        known_sightings.add(key)
        s = dict(s); s["seen"] = now.date().isoformat(); s["ingest_run"] = run_id
        new_sightings.append(s)

    total_in = len(all_rows)
    dedup_rate = round(collapsed / total_in, 4) if total_in else 0.0

    if args.dry_run:
        print(f"\n[dry-run] would append {len(new_items)} items, {len(new_sightings)} sightings to {data_dir}")
        return

    appended = store.append_jsonl(store.archive_path(data_dir), new_items)
    store.append_jsonl(store.sightings_path(data_dir), new_sightings)
    lines = store.count_lines(store.archive_path(data_dir))
    wm = store.bump_watermark(data_dir, lines, lines)
    store.append_jsonl(store.runs_path(data_dir), [{
        "run": run_id, "mode": "fixtures" if offline else "live", "synthetic_run": offline,
        "feeds_ok": feeds_ok, "feeds_err": feeds_err, "items_collected": total_in,
        "duplicates_collapsed_in_run": collapsed, "dedup_rate": dedup_rate,
        "items_new_to_archive": appended, "sightings_new": len(new_sightings),
        "archive_lines_after": lines, "errors": errors[:20], "per_feed": per_feed,
    }])

    print(f"\ncollected {total_in} ({collapsed} collapsed, dedup_rate={dedup_rate})")
    print(f"appended {appended} NEW -> {store.ARCHIVE_NAME} now {lines} lines (watermark {wm['archive_lines']})")
    if wm.get("regression_seen"):
        print("  ! archive SHORTER than watermark — possible truncation; run verify_pipeline.py", file=sys.stderr)


if __name__ == "__main__":
    main()
