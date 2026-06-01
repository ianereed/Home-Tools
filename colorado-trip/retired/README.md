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
