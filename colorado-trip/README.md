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
- 14+ `add_*.py` scripts — each populates one Sheet section
- `restructure_itinerary.py` — periodic reorg sweeps
- `read_itinerary.py` — fetch current state of the Sheet
- `fix_backpacking_stats.py` — corrections to specific sections
- Tahoe / Mammoth content also added (`add_tahoe_mammoth_content.py`) — the toolbox got repurposed for adjacent trips

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
