# retired/

Superseded one-shot scripts, kept for reference. Not maintained; don't run.

- `consolidate_mtb_into_activities.py` — one-time migration that appended the three
  standalone MTB tabs into the Activities tab. The standalone tabs were since deleted,
  so it can no longer run. The MTB section is now owned by `../update_activities_mtb.py`
  (regenerates from in-script data + preserves the rows above it).

- `add_activities_trailhead_links.py` — added Google Maps trailhead pins to activity
  Trailhead cells. Merged into `../activities_links.py`, which now does trailhead pins
  **and** Link-column labeling for the Activities tab.

- `add_west_maroon_pass.py` — built the standalone `West Maroon Pass` tab (route map,
  shuttle/booking tables, reservations, Mochi notes). That tab was folded into the
  **CB-C** option tab and deleted; the logistics now live in `../rebuild_trip_tabs.py`
  (the `wmp_route` / `wmp_stats` / `wmp_services` / `wmp_reservations` / `wmp_mochi` /
  `wmp_sources` fields on the CB-C `OPTIONS` dict, rendered in `build_option`).

## Retired 2026-05-31 (overhaul: reliability + reservations consolidation)

- `add_day_tabs.py` — earlier per-day tab generator (one tab per calendar day, incl.
  flexible days). Superseded by `../rebuild_trip_tabs.py`, which builds fixed-day tabs +
  per-option tabs and wires the Itinerary/DAY OPTIONS links (flexible days have no per-day
  tab in the current model).
- `add_day_options_draft.py`, `add_day_bld_e_draft.py` — drafts/POCs for the day-option
  tabs; `../rebuild_trip_tabs.py` now owns the `OPTIONS` tabs directly.
- `workshop_tab.py` — experimental single-tab scratch; never integrated.
- `create_todo_sheet.py` — built the old `Todo — Todoist` tab (column-A paste format).
  That tab was consolidated + renamed to `Reservations`; see `consolidate_reservations.py`.
- `consolidate_reservations.py` — one-shot migration that merged the Itinerary "Advance
  Reservations" block into the `Todo — Todoist` tab and renamed it `Reservations`
  (added the West Maroon RFTA bus + car-relocation rows). Done; the tab is now hand-maintained.
- `add_west_maroon_to_itinerary.py` — injected the West Maroon block into the Itinerary;
  that block was removed during the reservations consolidation (logistics live on CB-C).
- `fix_early_dates.py`, `restructure_itinerary.py`, `update_main_itinerary_2.py`,
  `add_more_options.py`, `create_more_considerations.py` — completed one-shot Itinerary/
  options edits; their results are baked into the live Sheet + current builders.
- `restructure_back_half.py` — one-shot for the 2026-07-14 back-half cancellation
  (Steamboat / Twin Lakes / CB / SLC / Ely → Aug 1–5 Boulder→Redwood City drive home):
  deleted the STM/CB DAY OPTIONS sections, rewrote Itinerary constraint + Aug 1–13 day
  rows, inserted the OUT-OF-SCOPE divider above Aug 14, marked 15 Reservations rows
  CANCELLED. Ran once before the same-day full rebuild (commit 2899ddd).
