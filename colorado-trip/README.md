# colorado-trip

A toolbox of one-shot Python scripts that built up a Google Sheet itinerary for a Colorado trip. Each script populated a specific section (backpacking, dining, dog daycare, scenic stops, shuttle, trail rides). The Sheet is the deliverable; the scripts are the journey to get there.

## What it is

```
  research-context.md ──┐
  trail-rides JSON   ──┐│
  scenic stops       ──┼┴──▶ add_<section>.py ──▶ Google Sheet (itinerary)
  dining guide       ──┘
```

Each script is independent. They were run in roughly the order of the file listing.

## Audience

You — personal travel planning. Trip-specific, not a generic trip-planner.

## Status

Trip-specific artifact. Functional but most useful as a reference if you build similar trip-planning tooling for a future trip.

## Layout

- `research-context.md` — narrative doc; the only prose. Read this first if you're picking up where things left off.
- 14+ `add_*.py` scripts — each populated one Sheet section. The Activities-tab builders
  (`add_activities.py`, `add_backpacking_sheet.py`, `add_tahoe_mammoth_content.py`,
  `add_trail_rides.py`) are now **legacy/historical** — see the MTB + styling pipeline below.
- `add_dining_guide.py` — **owns the `Dining Guide` tab**, data-driven + idempotent.
  Edit the `SECTIONS`/`*_rows` lists + the `ADDRESSES` dict and re-run; it deletes and
  rebuilds the tab (8 trip-ordered sections incl. day-trip towns Golden/Nederland/Estes
  + Aspen + Tahoe; color bars; `⭐` splurge marks). 12 columns, three computed at build
  time: **From Airbnb** (walk/bike/drive + miles via Distance Matrix from the per-town
  Airbnb in `BASES`, Mochi-aware — biking only flagged useful for non-dog-friendly
  spots), **Come As You Are?** (dress/dirt level from type+price rules + `DRESS_OVERRIDE`),
  and **Address** (native Google-Maps link). Distances are cached in
  `dining_distances.json` (gitignored) so re-runs don't re-hit the Maps API — delete the
  cache to force a recompute. Website + Address cells are native clickable links.
  Re-running moves the tab to the end of the order and assigns a new sheetId.
- `read_itinerary.py` / `read_backpacking.py` — fetch current Sheet state (debug utilities)
- `genmeta.py` + `audit_contacts.py` — reliability + data-quality; see **Reliability + tab ownership** below
- `retired/` — superseded one-shots + drafts, kept for reference; not run (see `retired/README.md`)

### Activities tab — current pipeline (Hikes / Runs / MTB)

All MTB rides live in the `Activities — Hikes, Runs & MTB` tab. The look + the MTB
content are maintained by these (idempotent, safe to re-run):

- `sheet_style.py` — shared palette + column widths (single source of truth for the look).
- `restyle_activities.py` — reads the tab and unifies **formatting only** (section/sub-area/
  column-header bars, zebra striping, widths). Content-safe; never rewrites trip data.
  Styles the activity sections above the `🚵 MOUNTAIN BIKING` header.
- `update_activities_mtb.py` — owns the **MTB section** (Boulder / Steamboat / Crested Butte
  ride data + per-row Google Maps trailhead pins, Trailforks deep-links, `***` must-do
  marks, hype badges). Rewrites only that section; preserves everything above it.
- `mtb_tab.py` — builder used by `update_activities_mtb.py`.
- `activities_links.py` — Activities-tab link master: adds Google Maps trailhead pins to
  each activity's Trailhead cell **and** labels bare URLs in the Link column
  (`AllTrails ▸`, `TAMBA ▸`, …). Activity region only; the MTB section carries its own
  links. (Consolidates the former `add_activities_trailhead_links.py` + `clean_activity_links.py`.)
- `linkutil.py` — turns `=HYPERLINK(...)` cells into native (always-clickable) links.

To change a ride: edit `update_activities_mtb.py` then run it. To re-polish formatting:
run `restyle_activities.py`. To (re)apply trailhead pins + Link labels: run
`activities_links.py`. All pull colors from `sheet_style.py`.

**Not Activities-specific** (left as-is): `fix_all_hyperlinks.py` / `fix_bare_url_cells.py`
are document-wide link maintenance; `rebuild_trip_tabs.py` builds the day/option tabs and
`add_trailhead_distances.py` builds the Trailhead Distances tab (see below).

## Reliability + tab ownership

**Who owns which tab** (the canonical builder — edit its in-script data and re-run):

| Tab(s) | Canonical builder |
|---|---|
| Day tabs (`Jul 16 (Thu)` …), option tabs (`BLD-A`…`CB-C`), Itinerary date links | `rebuild_trip_tabs.py` |
| `Dining Guide` | `add_dining_guide.py` |
| `Activities — Hikes, Runs & MTB` | `update_activities_mtb.py` (+ `restyle_activities.py`, `activities_links.py`, `mtb_tab.py`, `sheet_style.py`); hikes/runs seeded by legacy `add_activities.py` |
| `Trailhead Distances` | `add_trailhead_distances.py` |
| `Dog Daycare Options` / `Scenic Stops & Drives` / `MTB Shuttles & Guides` | `add_dog_daycare_sheet.py` / `add_scenic_stops.py` / `add_shuttle_sheet.py` (seeded once; live tab is authoritative) |
| `Reservations` (single tracker) | hand-maintained; seeded by `consolidate_reservations.py` (retired). Reconcile with Todoist by hand. |
| `_genmeta` (hidden) | `genmeta.py` bookkeeping — do not edit |

**Manual-edit detection — generators won't clobber your hand edits.** Every tab
`rebuild_trip_tabs.py` writes is fingerprinted (sha256 of its values) in the hidden
`_genmeta` tab. Before overwriting a tab it re-reads the live version and compares; if you
(or Anny) edited it in the sheet, the rebuild **skips that tab and reports it** instead of
destroying your work. Each generated tab also carries a visible `🤖 Auto-generated …`
footer. To intentionally replace an edited tab: `python rebuild_trip_tabs.py --force "Aug 1 (Sat)"`
(or `--force-all`). Helper: `genmeta.py`. If you do an ad-hoc single-tab build from a REPL,
call `rebuild_trip_tabs.save_genmeta()` afterward so the baseline doesn't go stale.

**Crash-safe + single-writer.** `rebuild_trip_tabs.py` builds each tab into a temp sheet and
only then atomically deletes the old + renames — a crash never leaves the sheet blank. It
also takes a lockfile + `pgrep` check so two sessions can't rebuild at once. (Before running
any builder, confirm none is live: `pgrep -fl 'rebuild''_trip_tabs'`.)

**`audit_contacts.py`** (read-only) flags the same business carrying different phone numbers
across tabs, and dining/daycare/shuttle entries missing a phone+website. Run it after any
contact edit; reconcile flags in the owning builder's data.

## Setup

```bash
cd colorado-trip
python -m venv .venv
source .venv/bin/activate
pip install gspread google-auth google-api-python-client  # ad-hoc, no pinned reqs
```

Each script reads/writes a Google Sheet via gspread; you'll need a service-account JSON to authenticate.

## Future

Probably retire after the trip, OR — if you take more trips — extract the stable patterns (Sheet bootstrap, multi-tab section adds, scenic-stop schema) into a reusable trip-planner sketch. Don't generalize prematurely.

## Out of scope

- Generic trip-planning library
- Cross-trip reuse without intent
- Real-time travel updates

## Reference

This is preserved as a reference for one specific trip. If you read this in 6 months and don't remember the trip, it's probably safe to retire (after archiving the Sheet PDF).
