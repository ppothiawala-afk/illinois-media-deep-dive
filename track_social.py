#!/usr/bin/env python3
"""
track_social.py — PHASE TWO: public-attention signals for watchlist events, sitting
BESIDE media coverage (never blended into it). Two independent, gracefully-failing
providers:

  * Google Trends (search interest, geo=US-IL) — the closest proxy for "are people
    looking this up." Access is the UNOFFICIAL pytrends library: free but flaky and
    rate-limited, and the numbers are RELATIVE (0-100), not absolute. If it fails,
    that signal is marked unavailable and the rest continues.

  * Reddit (public discussion) — posts/upvotes/comments per week. Official API via
    praw; needs REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_USER_AGENT env
    (create an app at reddit.com/prefs/apps). Reliable when configured.

Design honesty (see README): each platform is a DIFFERENT, non-representative
population — Reddit isn't the public, search interest isn't the public, your
outlets aren't the public. So signals are reported and drawn SEPARATELY with their
own units. No averaging, no composite "virality score." Counts/indices, no sentiment.

Writes social_report.json (committed, small). Best-effort: a provider failure is
recorded per-event and never breaks the pipeline.

Usage:
    python3 track_social.py                 # live (needs Reddit creds; Trends best-effort)
    python3 track_social.py --mock          # synthetic data, for testing the plumbing
    python3 track_social.py --months 12 --data-dir /tmp/demo
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import archive_store as store
from build_explorer import week_of  # Monday-of-week bucket, shared with the rest

HERE = Path(__file__).resolve().parent
WATCHLIST = HERE / "watchlist.json"
REPORT_NAME = "social_report.json"


# ── Google Trends (best-effort, unofficial) ─────────────────────────────────
def google_trends(term, months):
    try:
        from pytrends.request import TrendReq
    except ImportError:
        return {"status": "unavailable", "error": "pytrends not installed", "series": []}
    try:
        tf = f"today {max(1, months)}-m"
        py = TrendReq(hl="en-US", tz=360)
        py.build_payload([term], timeframe=tf, geo="US-IL")  # Illinois-scoped interest
        df = py.interest_over_time()
        if df is None or df.empty:
            return {"status": "no_data", "series": []}
        series = [{"week": week_of(idx.date()), "interest": int(row[term])}
                  for idx, row in df.iterrows() if term in row]
        # collapse to weekly max (Trends may be daily/weekly depending on window)
        wk = {}
        for s in series:
            wk[s["week"]] = max(wk.get(s["week"], 0), s["interest"])
        return {"status": "ok", "unit": "relative_interest_0_100",
                "series": [{"week": w, "interest": wk[w]} for w in sorted(wk)]}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)[:200], "series": []}


# ── Reddit (official API via praw) ──────────────────────────────────────────
def reddit_search(term, months):
    cid = os.environ.get("REDDIT_CLIENT_ID")
    csec = os.environ.get("REDDIT_CLIENT_SECRET")
    ua = os.environ.get("REDDIT_USER_AGENT", "illinois-media-deep-dive/0.1")
    if not (cid and csec):
        return {"status": "not_configured",
                "error": "set REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET", "series": []}
    try:
        import praw
    except ImportError:
        return {"status": "unavailable", "error": "praw not installed", "series": []}
    try:
        reddit = praw.Reddit(client_id=cid, client_secret=csec, user_agent=ua,
                             check_for_async=False)
        cutoff = datetime.now(timezone.utc) - timedelta(days=30 * months)
        wk_posts, wk_score, wk_comments = {}, {}, {}
        subs = {}
        for s in reddit.subreddit("all").search(f'"{term}"', sort="new", time_filter="year", limit=250):
            created = datetime.fromtimestamp(s.created_utc, tz=timezone.utc)
            if created < cutoff:
                continue
            w = week_of(created.date())
            wk_posts[w] = wk_posts.get(w, 0) + 1
            wk_score[w] = wk_score.get(w, 0) + int(getattr(s, "score", 0))
            wk_comments[w] = wk_comments.get(w, 0) + int(getattr(s, "num_comments", 0))
            sub = str(getattr(s, "subreddit", ""))
            subs[sub] = subs.get(sub, 0) + 1
        series = [{"week": w, "posts": wk_posts[w], "upvotes": wk_score[w],
                   "comments": wk_comments[w]} for w in sorted(wk_posts)]
        top_subs = sorted(subs.items(), key=lambda kv: -kv[1])[:6]
        return {"status": "ok", "unit": "posts/upvotes/comments per week",
                "series": series, "top_subreddits": top_subs,
                "note": "Reddit search caps ~250 results; high-volume terms undercounted"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)[:200], "series": []}


# ── mock (for testing the plumbing without network/creds) ───────────────────
def mock_signal(term, months, kind):
    import random
    random.seed(hash(term) & 0xffff)
    today = datetime.now(timezone.utc).date()
    weeks = [week_of(today - timedelta(days=7 * i)) for i in range(min(months * 4, 12))][::-1]
    if kind == "trends":
        return {"status": "ok", "unit": "relative_interest_0_100", "mock": True,
                "series": [{"week": w, "interest": random.randint(5, 100)} for w in weeks]}
    return {"status": "ok", "unit": "posts/upvotes/comments per week", "mock": True,
            "series": [{"week": w, "posts": random.randint(0, 8),
                        "upvotes": random.randint(0, 400), "comments": random.randint(0, 120)} for w in weeks],
            "top_subreddits": [["chicago", 5], ["illinois", 2]]}


def main():
    ap = argparse.ArgumentParser(description="Public-attention signals for watchlist events.")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--mock", action="store_true", help="synthetic data (no network/creds)")
    ap.add_argument("--data-dir")
    args = ap.parse_args()

    data_dir = store.resolve_data_dir(args.data_dir)
    watch = json.loads(WATCHLIST.read_text())
    out = []
    for ev in watch.get("events", []):
        term = ev.get("match", [ev["name"]])[0] if ev.get("match") else ev["name"]
        if args.mock:
            gt, rd = mock_signal(term, args.months, "trends"), mock_signal(term, args.months, "reddit")
        else:
            gt, rd = google_trends(ev["name"], args.months), reddit_search(term, args.months)
        out.append({"name": ev["name"], "slug": ev["slug"], "query": term,
                    "google_trends": gt, "reddit": rd})
        print(f"  {ev['name']}: trends={gt['status']} reddit={rd['status']}")

    report = {
        "_comment": "PUBLIC-ATTENTION signals (Google Trends search interest + Reddit "
                    "discussion) for watchlist events, shown SEPARATELY from media "
                    "coverage — different populations, different units, never blended. "
                    "Best-effort: providers fail independently. Counts/indices, no sentiment.",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mock": bool(args.mock),
        "events": out,
    }
    (Path(data_dir) / REPORT_NAME).write_text(json.dumps(report, indent=2))
    print(f"-> {REPORT_NAME}")


if __name__ == "__main__":
    main()
