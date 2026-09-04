# Illinois Media Deep Dive

A scheduled pipeline that ingests **all** coverage from a curated cross-section of
Illinois news outlets and surfaces **what the state is focused on** — discovered
from the coverage rather than sorted into predefined buckets. It tracks emerging
themes, what's *surging* relative to its own normal, which outlets diverge on what,
and how stories penetrate across the state's press.

It captures and counts coverage. **It infers no sentiment, tone, or bias** — every
number is a countable fact traceable to source articles.

## Status

Ships with the pipeline, a curated Illinois feed registry, tests, and an honest
empty-state dashboard — **no real data collected yet**. Week one is week one.

## What makes this different from a fixed-topic tracker

- **Emergent, not prejudged.** There are no fixed policy categories. **Entities**
  (people/orgs/places) are raw mentions — the purest "what is Illinois focused on"
  signal. **Themes** are a normalized overlay (`themes_taxonomy.json`) so trends stay
  comparable week to week instead of fragmenting; unmapped themes the model proposes
  land in `themes_pending.json` for human review (the taxonomy grows human-gated).
- **Unit is the outlet, not the state.** Every item carries `outlet`, `region`
  (chicago/suburban/downstate/statewide) and `outlet_type`, which powers
  outlet-divergence and Chicago-vs-downstate views.
- **Surge relative to normal.** See below.

## "Surging relative to normal" — the definition

`surge.py` flags a theme when its coverage rises above **its own** recent baseline:

- The metric is **share** (fraction of coverage), not raw volume, so a busy news day
  doesn't create a fake surge.
- **Normal** = a theme's trailing mean share over the prior `--baseline-weeks`
  snapshots, measured against **its own** standard deviation (a z-score). "Unusual for
  this theme," not merely "high."
- **Emerging** (was ~absent, just appeared) is kept separate from **surging** (an
  established theme climbing above its norm).
- **Breadth cross-check:** surging *and* broadening across more outlets is the
  high-confidence signal; a spike in one outlet is often one newsroom's obsession.
- **Confidence by history depth:** a baseline needs history. With few snapshots the
  report labels itself low-confidence; treat early surges as hints. Seasonality (annual
  cycles) needs ~a year to handle honestly.
- A rolling baseline *forgets*: a theme elevated for months becomes the new normal and
  stops surging. That's correct — surge means change, not dominance.

## Architecture — one archive, disposable views

```
feeds_config.json ─▶ fetch_feeds.py ─▶ items_archive.jsonl  (append-only, source of truth)
   (registry)          (daily)              │
                                            ├▶ analyze.py ─▶ items_analyzed.json (DERIVED, git-ignored)
                                            │    entities + normalized theme, no sentiment
                                            ├▶ rollup.py ─▶ media_history.json + theme_history.jsonl
                                            │                + entity_history.jsonl + items_recent.json
                                            └▶ surge.py  ─▶ surge_report.json
                                 verify_pipeline.py (checks that can FAIL)
                                            │
                                      dashboard.html (static; reads the JSON)
```

## Storage rule (enforced in code)

The archive is append-only and git-friendly. The one thing that would bloat git is a
**wholesale-rewritten** derived file, so the rule is enforced three ways:

1. `.gitignore` blocks `items_analyzed.json` and `verification_report.json`.
2. CI stages only an explicit allowlist of bounded/append-only files.
3. **verify check `S7` FAILS the build** if either forbidden file is ever git-tracked.

Committed: the append-only archive (+ sightings/runs/watermark), `theme_history.jsonl`
and `entity_history.jsonl` (append-only time series), `media_history.json` (weekly
snapshots), `items_recent.json` (bounded last-N-days for the dashboard),
`surge_report.json`, and the taxonomy. The full analyzed corpus is always regenerable
with `analyze.py --rebuild`.

## Cadence

- **Daily — `run_ingest.sh`** (`.github/workflows/ingest.yml`): fetch all feeds, append
  to the archive. API-free. Daily because RSS windows expire.
- **Weekly — `run_rollup.sh`** (`.github/workflows/rollup.yml`): analyze → snapshot →
  surge → verify. Resilient: derived data commits even if verify fails; a gate step reds
  the build so failures are visible, never silent.

## Sources & honest limitations

- **A curated cross-section, not the whole press.** Nonprofit (Capitol News Illinois,
  Illinois Answers, Injustice Watch, The TRiiBE, Block Club, Borderless), public
  (WBEZ, WTTW), commercial (Chicago Sun-Times, Daily Herald), alt-weekly (Chicago
  Reader, Illinois Times), and advocacy (Illinois Policy — a right-leaning counterweight
  so divergence isn't one-sided).
- **Downstate is thinner by necessity.** Legacy downstate dailies are walled — Lee
  papers (Pantagraph, Southern Illinoisan) sit behind a TollBit AI-licensing paywall,
  Gannett discontinued RSS, the Tribune is paywalled. Downstate leans on public radio,
  Capitol News, and alt-weeklies. The dashboard discloses this.
- **Wanted but feed not yet found:** The Center Square Illinois, NPR Illinois, WILL,
  Crain's, Chalkbeat — non-standard feed paths; `discover_feeds.py` is how they get
  added once located.
- **Media coverage, not public opinion. No sentiment. Surge is a leading indicator with
  its math shown, not a prediction.**

## Verification (checks that can fail)

Structural (hold regardless of data): S1 no-sentiment, S2 no-synthetic-in-shipped, S3
themes-in-taxonomy, S6 share math, **S7 storage invariant**. Data (WARN until real data):
D1 archive integrity/truncation, D2 sightings resolve, D3 de-circularized (snapshot
total + per-region recomputed from the archive window), D4 dead-outlet detection, D5
feed-health coverage.

## Setup

```bash
pip install -r requirements.txt
python3 -m unittest discover -s tests      # 9 offline tests, no network/key

./run_ingest.sh                            # collect (daily) — real Illinois articles
./run_rollup.sh                            # analyze + snapshot + surge + verify (weekly)
python3 -m http.server 8000                # then open http://localhost:8000/dashboard.html
```

Analysis runs key-free by default (deterministic keyword themes). Set `ANTHROPIC_API_KEY`
for model theme labeling; only theme naming uses it, entities stay deterministic and free.

To confirm the candidate feeds live and promote them:

```bash
python3 discover_feeds.py --health-check
python3 apply_feeds_patch.py --dry-run && python3 apply_feeds_patch.py
```

## Roadmap

Once ~8–12 weeks of history bank: reliable surge baselines; later, day-of-week and
seasonal adjustment; propagation velocity from the sightings log. All framed as
early-signal detection with shown math — never "prediction."
