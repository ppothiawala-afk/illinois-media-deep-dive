#!/usr/bin/env python3
"""
analyze.py — stage 2: emergent analysis of the archive. NO fixed policy topics,
NO sentiment. Two layers:

  LAYER 1 — entities (purely emergent, deterministic, always-on).
    Extract proper-noun / keyphrase mentions per article. Aggregated later, this
    is the raw "what/who is Illinois focused on" signal — nothing is predefined.

  LAYER 2 — theme (normalized emergent, for trend comparability).
    Assign each article ONE canonical theme from themes_taxonomy.json. A pure
    free-text label fragments over time (the same story named five ways destroys
    a surge baseline), so raw labels are normalized to the frozen canonical set.
    * API mode (ANTHROPIC_API_KEY): the model returns a short free-text theme;
      it is normalized via the taxonomy's aliases. A label that maps to nothing
      is PROPOSED to themes_pending.json for human review (never auto-added) and
      the item is themed "other" until a human freezes it in.
    * --offline: the theme is chosen deterministically by alias keyword match
      against title+summary. Same normalizer, no API.

Writes items_analyzed.json — a DERIVED, git-IGNORED file (regenerable via
--rebuild). Downstream stages read it; nothing commits it (storage rule).

HARD RULE: outputs are countable facts only — entity mentions and a theme label.
No sentiment, tone, stance, or judgment score anywhere.

Usage:
    python3 analyze.py --offline
    python3 analyze.py                     # API mode (needs ANTHROPIC_API_KEY)
    python3 analyze.py --rebuild --offline
    python3 analyze.py --data-dir /tmp/demo --offline
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import archive_store as store

HERE = Path(__file__).resolve().parent
TAXONOMY_PATH = HERE / "themes_taxonomy.json"
ANALYZED_NAME = "items_analyzed.json"        # DERIVED, gitignored
PENDING_NAME = "themes_pending.json"         # committed, small (human-gated)
OTHER_THEME = "other"

STOPWORD_CAPS = {"The", "A", "An", "This", "That", "It", "He", "She", "They",
                 "In", "On", "At", "For", "And", "But", "Or", "Of", "To", "As",
                 "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday", "January", "February", "March", "April",
                 "May", "June", "July", "August", "September", "October",
                 "November", "December", "Illinois", "Chicago"}


def load_taxonomy():
    tax = json.loads(TAXONOMY_PATH.read_text())
    canon = tax["canonical"]
    # alias -> canonical (longer aliases first so multi-word wins)
    alias_index = []
    for theme, aliases in canon.items():
        for a in aliases:
            alias_index.append((a.lower(), theme))
    alias_index.sort(key=lambda x: -len(x[0]))
    return tax, canon, alias_index


def theme_from_text(text, alias_index):
    """Deterministic: canonical theme whose aliases best match the text.
    Returns (theme, hit_count). Ties broken by the alias ordering (specific
    first)."""
    low = f" {text.lower()} "
    scores = Counter()
    for alias, theme in alias_index:
        # word-ish containment
        if alias in low:
            scores[theme] += 1
    if not scores:
        return OTHER_THEME, 0
    best, n = scores.most_common(1)[0]
    return best, n


def normalize_label(label, alias_index):
    """Map a short free-text theme LABEL to a canonical theme, or None."""
    low = f" {label.lower()} "
    for alias, theme in alias_index:
        if alias in low:
            return theme
    return None


def extract_entities(text):
    ents = set()
    for m in re.finditer(r"\b([A-Z][a-zA-Z.&']+(?:\s+[A-Z][a-zA-Z.&']+){0,3})\b", text):
        phrase = m.group(1).strip(".")
        first = phrase.split()[0]
        if first in STOPWORD_CAPS and " " not in phrase:
            continue
        if len(phrase) < 3:
            continue
        ents.add(phrase)
    return sorted(ents)[:12]


def theme_batch_api(client, model, batch):
    lines = [f"[{i}] {it['title']} — {it.get('summary','')[:200]}" for i, it in enumerate(batch)]
    prompt = (
        "For each numbered Illinois news item, give a SHORT theme label (2-4 words, "
        "lowercase) describing what it is ABOUT. Objective topic only — NO sentiment, "
        "tone, or judgment. Return STRICT JSON: {\"themes\":[{\"i\":0,\"theme\":\"...\"}]}\n\n"
        + "\n".join(lines))
    msg = client.messages.create(model=model, max_tokens=1500,
                                 messages=[{"role": "user", "content": prompt}])
    txt = re.sub(r"^```(json)?|```$", "", msg.content[0].text.strip(), flags=re.MULTILINE).strip()
    data = json.loads(txt)
    return {r["i"]: (r.get("theme") or "").strip() for r in data.get("themes", [])}


def build_entry(it, entities, theme, theme_raw):
    e = {"id": it["id"], "title": it["title"], "link": it["link"],
         "published": it["published"], "outlet": it["outlet"],
         "region": it.get("region", "unknown"), "outlet_type": it.get("outlet_type", "unknown"),
         "feed_url": it.get("feed_url", ""), "entities": entities,
         "theme": theme, "theme_raw": theme_raw}
    if it.get("synthetic"):
        e["synthetic"] = True
    return e


def main():
    ap = argparse.ArgumentParser(description="Emergent entity + theme analysis (no sentiment).")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--max-api-items", type=int, default=1500)
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--data-dir")
    args = ap.parse_args()

    data_dir = store.resolve_data_dir(args.data_dir)
    tax, canon, alias_index = load_taxonomy()
    archive = store.load_archive(data_dir)

    analyzed_path = Path(data_dir) / ANALYZED_NAME
    cached = {}
    if analyzed_path.exists() and not args.rebuild:
        try:
            for it in json.loads(analyzed_path.read_text()).get("items", []):
                cached[it["id"]] = it
        except Exception:  # noqa: BLE001
            cached = {}

    todo = [it for it in archive if it["id"] not in cached]
    out_items = [cached[it["id"]] for it in archive if it["id"] in cached]

    proposals = Counter()          # unmapped raw label -> count
    proposal_examples = {}
    api_calls = api_items = 0

    if not todo:
        pass
    elif args.offline:
        for it in todo:
            text = f"{it.get('title','')} {it.get('summary','')}"
            theme, _ = theme_from_text(text, alias_index)
            out_items.append(build_entry(it, extract_entities(text), theme, theme))
    else:
        try:
            import anthropic
        except ImportError:
            print("anthropic required for API mode; use --offline.", file=sys.stderr); sys.exit(2)
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            print("ANTHROPIC_API_KEY not set; use --offline.", file=sys.stderr); sys.exit(2)
        client = anthropic.Anthropic(api_key=key)
        send, overflow = todo[:args.max_api_items], todo[args.max_api_items:]
        for start in range(0, len(send), args.batch_size):
            batch = send[start:start + args.batch_size]
            try:
                labels = theme_batch_api(client, args.model, batch)
            except Exception as e:  # noqa: BLE001
                print(f"  ! theme batch failed ({e}); offline fallback for this batch", file=sys.stderr)
                labels = {}
            api_calls += 1; api_items += len(batch)
            for i, it in enumerate(batch):
                text = f"{it.get('title','')} {it.get('summary','')}"
                raw = labels.get(i, "")
                canon_theme = normalize_label(raw, alias_index) if raw else None
                if canon_theme is None:
                    # try text-based fallback before giving up to 'other'
                    canon_theme, hits = theme_from_text(text, alias_index)
                    if raw and canon_theme == OTHER_THEME:
                        proposals[raw.lower()] += 1
                        proposal_examples.setdefault(raw.lower(), it["title"])
                out_items.append(build_entry(it, extract_entities(text), canon_theme, raw or canon_theme))
        for it in overflow:  # beyond cap: offline-theme, never drop
            text = f"{it.get('title','')} {it.get('summary','')}"
            theme, _ = theme_from_text(text, alias_index)
            out_items.append(build_entry(it, extract_entities(text), theme, theme))

    out_items.sort(key=lambda x: (x.get("published", ""), x["id"]))
    synthetic_n = sum(1 for x in out_items if x.get("synthetic"))

    out = {
        "_comment": "DERIVED from items_archive.jsonl (git-ignored, regenerable via "
                    "analyze.py --rebuild). Counts, not judgments: entity mentions + a "
                    "canonical theme; NO sentiment. Themes normalized to themes_taxonomy.json.",
        "generated": datetime.now(timezone.utc).date().isoformat(),
        "backend": "offline_keyword" if args.offline else f"anthropic:{args.model}",
        "taxonomy_version": tax.get("version"),
        "stats": {"archive_items": len(archive), "reused_cached": len(cached),
                  "api_calls": api_calls, "api_items": api_items,
                  "analyzed": len(out_items), "synthetic_items": synthetic_n,
                  "other_theme": sum(1 for x in out_items if x["theme"] == OTHER_THEME)},
        "items": out_items,
    }
    analyzed_path.write_text(json.dumps(out, indent=2))

    # human-gated theme proposals (merge with any existing pending)
    if proposals:
        pending_path = Path(data_dir) / PENDING_NAME
        existing = {}
        if pending_path.exists():
            try:
                for p in json.loads(pending_path.read_text()).get("proposals", []):
                    existing[p["raw_theme"]] = p
            except Exception:  # noqa: BLE001
                existing = {}
        for raw, n in proposals.items():
            if raw in existing:
                existing[raw]["count"] += n
            else:
                existing[raw] = {"raw_theme": raw, "count": n,
                                 "example": proposal_examples.get(raw, ""),
                                 "proposed_on": datetime.now(timezone.utc).date().isoformat()}
        pending_path.write_text(json.dumps({
            "_comment": "Emergent theme labels the model produced that map to NO "
                        "canonical theme. Human review: add real ones to "
                        "themes_taxonomy.json (as a new canonical theme or alias). "
                        "Never auto-added — this is the human-gated taxonomy growth.",
            "proposals": sorted(existing.values(), key=lambda p: -p["count"])}, indent=2))

    print(f"analyzed {len(out_items)} items via {out['backend']} "
          f"(archive={len(archive)}, api_calls={api_calls}, other={out['stats']['other_theme']}, "
          f"synthetic={synthetic_n}) -> {ANALYZED_NAME}")
    if proposals:
        print(f"  {len(proposals)} unmapped theme label(s) proposed -> {PENDING_NAME} (human review)")


if __name__ == "__main__":
    main()
