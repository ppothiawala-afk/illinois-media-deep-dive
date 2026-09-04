# Illinois Deep-Build — Scope

**Date:** 2026-09-03 · **Status:** Scoping only, no build until you say go.
**Purpose:** Turn the broad-but-shallow 50-state tracker into a *deep* single-state
instrument for Illinois — many diverse sources, one geography — so "coverage"
means something close to the state's real media landscape instead of one
nonprofit network's footprint. Same pipeline machine; richer registry + new
analytical views.

---

## Why Illinois, and why "deep beats wide"

The 50-state build measures one publisher network (States Newsroom) across every
state. Its ceiling is the single-network sourcing — you can't claim it reflects a
state's real press. Going *wide on sources* is only tractable when the geography
is *narrow*: you can realistically curate 15–25 diverse Illinois outlets, but not
25× that for all 50 states. Illinois is a good target: a major metro media market
(Chicago) plus a genuine downstate ecosystem, politically interesting, and — for
you — a market you'd plausibly sell into.

The payoff is analysis the 50-state version structurally cannot produce:
**outlet divergence** (which outlets attend to which topics) and **story
penetration** (does a story saturate the whole state press or stay siloed).

---

## Sourcing reality — validated 2026-09-03 (the honest part)

I live-checked candidate feeds. The key finding: **nonprofit and public-media
outlets are freely ingestible; the legacy for-profit chains are walled.** This
shapes everything.

### Confirmed live (RSS returned, current content) — 9

| Outlet | Type | Geography | Feed |
|---|---|---|---|
| Capitol News Illinois | Nonprofit, statehouse | Statewide | `capitolnewsillinois.com/feed/` |
| Illinois Answers Project | Nonprofit, investigative | Chicago/statewide | `illinoisanswers.org/feed/` |
| Injustice Watch | Nonprofit, courts/justice | Chicago | `injusticewatch.org/feed/` |
| The TRiiBE | Nonprofit, Black Chicago | Chicago | `thetriibe.com/feed/` |
| WTTW News | PBS / public TV | Chicago metro | `news.wttw.com/feed` |
| WBEZ | NPR / public radio | Chicago metro | `wbez.org/rss` |
| Chicago Sun-Times | Commercial daily | Chicago metro | `chicago.suntimes.com/feed` |
| Daily Herald | Commercial daily | Suburban (collar counties) | `dailyherald.com/feed/` |
| Illinois Times | Alt-weekly | Springfield / Central (downstate) | `illinoistimes.com/feed/` |

That's already strong type diversity: statehouse, investigative, community, PBS,
public radio, two commercial dailies, and a downstate alt-weekly.

### Strong candidates — need a live re-check (bot-blocked in my sandbox, likely fine)

Real outlets, standard feed patterns; my fetch tool returned empty (bot
protection), not confirmed dead. `discover_feeds.py --health-check` settles these
on the first live run:

- Block Club Chicago — `blockclubchicago.org/feed/` (nonprofit, neighborhoods)
- Chicago Reader — `chicagoreader.com/feed/` (alt-weekly)
- Chalkbeat Chicago — education nonprofit (Arc feed path)
- Crain's Chicago Business — `chicagobusiness.com` (business/trade; Arc feed)
- NPR Illinois (Springfield) & Illinois Public Media/WILL (Urbana) — downstate public radio
- **Illinois Policy Institute — `illinoispolicy.org/feed/`** and **The Center Square Illinois** — free-market / right-leaning. These matter: they're the ideological counterweight that makes outlet-divergence analysis meaningful rather than measuring one side of the spectrum agreeing with itself.
- Borderless Magazine (immigration), Cicero Independiente (bilingual, suburban), Chicago Defender (Black press)

### Walled / not freely ingestible (design around these)

- **Lee Enterprises downstate** (The Pantagraph/Bloomington, The Southern/Carbondale, Quad-City Times): RSS now sits behind a **TollBit** AI-licensing token wall — confirmed, returns an auth error, not usable without a paid license.
- **Gannett downstate** (State Journal-Register/Springfield, Peoria Journal Star, Rockford Register Star): RSS effectively discontinued.
- **Chicago Tribune**: on the fetch blocklist and paywalled; Arc feed not accessible here.

**Implication for downstate:** you can't lean on the legacy dailies. Downstate
coverage has to come from public radio (NPR Illinois, WILL), Capitol News
Illinois (statehouse, covers the whole state), and downstate nonprofits/alt-
weeklies (Illinois Times). That's a real but thinner downstate layer — worth
stating honestly in the dashboard, exactly as the 50-state build discloses its
single-network limit.

**Realistic target registry: ~15–20 working feeds** (9 confirmed + ~6–10 that
pass the live re-check), spanning nonprofit / public-media / commercial /
alt-weekly, Chicago metro / suburban / downstate, and — critically — across the
ideological spectrum.

---

## What this unlocks — three analyses the 50-state build can't do

1. **State agenda mix.** With a real cross-section of Illinois outlets, the topic
   share becomes a credible read on *the state's media agenda* this week, not one
   network's. "Environmental legislation is 8% of Illinois coverage this week, up
   from 3%."

2. **Outlet divergence (the headline feature).** For a given topic, which outlets
   are covering it heavily and which are ignoring it — measured empirically as
   differential attention, no sentiment needed. "On the utility-rate bill, Capitol
   News and WBEZ went heavy; the commercial dailies barely touched it; Illinois
   Policy framed it as economic development, the nonprofits as environmental
   legislation." That topic-assignment split across outlets *is* the story, and
   it's defensible because it's just counts.

3. **Story penetration / propagation.** Using the sightings log (already built):
   did a story appear across many outlets (saturation) or stay in one (siloed)?
   Penetration is a proxy for what broke through the state's whole press vs. what
   one newsroom cared about — and, tracked over days, how fast it spread.

Plus **Chicago vs. downstate** as a first-class split: a `region` tag on each
feed lets every view compare metro and downstate agendas, which is politically
meaningful in a state defined by that divide.

---

## Pipeline changes (small — the machine already fits)

The architecture doesn't change; this is mostly config + views:

- **New scoped registry** `feeds_config.illinois.json`: the outlets above, each
  tagged `region` (chicago / suburban / downstate) and `outlet_type` (nonprofit /
  public / commercial / altweekly / advocacy). No `state` rollup needed — the unit
  becomes the *outlet*, not the state.
- **Rollup keys by outlet + region** instead of by state. `append_history.py`
  gains an outlet/region grouping; topic-share math is unchanged.
- **Daily granularity for the momentum layer.** Ingestion is already daily; expose
  daily rolling metrics (7-day share, trailing baseline, week-over-week
  acceleration, breadth = # outlets carrying a topic) so the "emerging-topic
  radar" has something to read. Framed as acceleration + breadth alerts with the
  numbers shown — never "prediction."
- **New dashboard views:** (a) state agenda mix + trend; (b) outlet-divergence
  matrix (outlets × topics, cell = that outlet's share on the topic); (c) story-
  penetration list (stories ranked by # outlets carrying them); (d) Chicago vs.
  downstate toggle. Same static-HTML + JSON pattern; add an outlet-composition
  disclosure panel like the existing source-composition one.
- **Verification carries over** unchanged (archive integrity, de-circularized
  cross-check, no-sentiment guard, feed-health). The dead-feed check is *more*
  useful here since a walled/broken downstate feed should surface loudly.

---

## Effort & phasing

- **Phase 1 — sourcing (the real work, ~1–2 sessions):** finalize the registry;
  run `discover_feeds.py --health-check` to confirm the candidate feeds and prune
  the walled ones; handle non-WordPress feed paths (Arc, public-radio). Expect to
  land ~15–20 usable feeds and to *lose* the legacy dailies — that's fine.
- **Phase 2 — rollup by outlet/region (~1 session):** registry tags, grouping
  change, snapshots.
- **Phase 3 — dashboard views (~1 session):** divergence matrix, penetration,
  region toggle, composition disclosure.
- **Phase 4 — momentum radar (after ~6–8 weeks of history):** daily rolling
  metrics + acceleration/breadth alerts. Honest early-signal, not forecasting.

Phases 1–3 give you a genuinely differentiated Illinois media-intelligence
dashboard; Phase 4 adds the "about-to-trend" radar once there's enough history to
set baselines.

---

## Honest limitations (state them on the dashboard)

- Still **media coverage, not public opinion.**
- **Downstate is thinner than Chicago** because the legacy downstate dailies are
  walled — the metro/downstate comparison is real but asymmetric in source depth,
  and should be labeled.
- **No sentiment** — divergence is *attention*, not favorability.
- Momentum alerts are **leading indicators with shown math, not predictions.**

---

## How it serves both goals

- **Media-intelligence instrument:** Illinois deep-dive is the flagship — real
  landscape, outlet divergence, penetration. Clone the template to swing states
  later (one at a time).
- **Billable SoV service:** the outlet-divergence + momentum machinery is exactly
  what a client wants applied to *their* category (competitor blogs, trade press,
  brand mentions). Illinois is the credible, richer demo that de-risks the pitch;
  the client deployment is the same code with a different registry.
