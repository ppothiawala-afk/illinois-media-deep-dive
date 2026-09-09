# ▶ Quick commands — Illinois Civic Media

Copy-paste these in Terminal. Always start by being in the project folder:

```bash
cd ~/Illinois\ State\ Media\ Deep\ Dive
```

---

## ⭐ Refresh the weekly summary (the one you use most)

```bash
cd ~/Illinois\ State\ Media\ Deep\ Dive
git pull
python3 weekly_summary.py
open weekly_summary_*.html
```

- `git pull` — grab the latest data the GitHub rollup committed
- `python3 weekly_summary.py` — writes `weekly_summary_<date>.md` and `.html` into this folder
- `open weekly_summary_*.html` — opens it in your browser (Cmd-P → Save as PDF to share)

> For the **clean, API-quality** summary, first run the rollup with your key on
> GitHub: **Actions → rollup → Run workflow**, wait for the green check, *then* run
> the three commands above. Without that, themes are the coarse offline version.

---

## See the live dashboard

```bash
cd ~/Illinois\ State\ Media\ Deep\ Dive
git pull
python3 -m http.server 8000
```
Then open **http://localhost:8000/dashboard.html** · Ctrl-C in Terminal to stop.

---

## Confirm / promote feeds (occasionally)

```bash
cd ~/Illinois\ State\ Media\ Deep\ Dive
python3 discover_feeds.py --health-check
python3 apply_feeds_patch.py --dry-run && python3 apply_feeds_patch.py
git add feeds_config.json feeds_patch.json feeds_patch.applied_*.json
git commit -m "Promote validated feeds" && git push
```

## Run the whole pipeline locally (offline themes unless key is set)

```bash
cd ~/Illinois\ State\ Media\ Deep\ Dive
./run_ingest.sh
./run_rollup.sh
python3 weekly_summary.py
```

---

## Data collection (hands-off)

Runs automatically on GitHub: **ingest** daily 10:20 UTC, **rollup** Mondays 11:40 UTC.
To trigger either now: repo → **Actions** → pick the workflow → **Run workflow**.

## Where things go

- Summaries + dashboard data: this folder (`~/Illinois State Media Deep Dive/`)
- Google Drive copies: folder **"Illinois Civic Media Summaries"**
- If `git pull` ever says *"index.lock: File exists"*: run `find .git -name '*.lock' -delete` then retry.
