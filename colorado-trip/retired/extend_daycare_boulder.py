"""
extend_daycare_boulder.py — one-shot (2026-07-15): extend the (hand-maintained) Dog
Daycare Options tab with the newly researched Boulder-area options + hygiene fixes.

Sources: agent research 2026-07-15 (journal-208.md) — all facts verified on official
sites. Changes:
  1. Insert 8 rows into the BOULDER facility section (after Rogue's Farm): Bowhaus
     (new top pick — 500 ft from Geotrek), Hike Doggie, Updog, Dogtopia Lafayette,
     The Pet Spot (ex-"Dog Spot", rebranded), Gunbarrel Vet (walk-in), Leader of the
     Pack, and a "locals-only — skip" info row.
  2. Refresh the Boulder BOOKING PRIORITY row (#6) → Bowhaus-first plan.
  3. Mark the Steamboat / Truckee / Moab priority rows ❌ LEG CANCELLED (those legs
     were cut Jul 14 — the "make these calls first" instruction was stale + harmful).
  4. Append ❌ LEG CANCELLED to the MOAB / TAHOE / STEAMBOAT section headers.
  5. Extend the amber vaccine-reminder row with the influenza/lepto + spay/neuter +
     48-hour findings (time-critical: departure is Jul 17).
  6. Reservations: add the Bowhaus Trial-Day booking row; mark the Camp Bow Wow row
     as the 2nd choice.

Idempotent (sentinel per step), throttled ~2 s/write, rows located by content.
NOTE: do NOT run add_dog_daycare_sheet.py to "regenerate" this tab — its section 2
rewrites a long-deleted Itinerary layout (cols J–R) at hardcoded rows.
"""
import time

import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE

gc = gspread.service_account(filename=CREDENTIALS_FILE)
sh = gc.open_by_key(SPREADSHEET_ID)

def W(fn, *a, **k):
    time.sleep(2.0)
    return fn(*a, **k)

ws = sh.worksheet("Dog Daycare Options")
sid = ws.id

NEW_ROWS = [
    ["🥇 Bowhaus Boulder  (NEW TOP PICK — next door to Geotrek)",
     "Indoor/outdoor daycare + boarding",
     "6560 O'Dell Pl, Boulder, CO 80301 (Gunbarrel)\n~500 ft from the van shop (6420 Odell Pl)",
     "(720) 961-7466\ntext (303) 802-7790", "bowhausco.com/locations/boulder",
     "$40 full / $32 half\n🎁 first 30 days unlimited $99 (summer 2026 promo)",
     "FREE Trial Day required first; email vaccine records ≥48 h before arrival",
     "M–F 6:30am–7pm; Sat–Sun 9am–5pm (lobby windows 6:30–10a + 4–7p). Requires Rabies, "
     "DHPP, Bordetella + CANINE INFLUENZA; spay/neuter over 12 mo; reservations required "
     "for all services. Same corner as Geotrek → drop the van AND Mochi in one stop on "
     "Mon Jul 27. Out-of-town clients OK (verified traveler review)."],
    ["⭐ Hike Doggie — Boulder Flatirons  (adventure hikes — they come to YOU)",
     "Van pickup → leashed small-group trail hikes",
     "Mobile — picks up at the Airbnb (ZIP 80304 in service area; all Boulder + "
     "Louisville/Lafayette/Erie/Broomfield)",
     "(720) 773-8200 / (720) 909-3353\nMatt.Gray@HikeDoggieLove.com", "hikedoggie.com",
     "$110 per hike\n(multi-hike/dog discounts)",
     "Calendly inquiry call → 30-min in-home meet & greet; 'hiking as early as this week'",
     "The PUP-Hiking-Co analog: door pickup ('Zen Den' van crates), leashed pack trail "
     "hike, post-hike rinse + photo report. LEASHED = no Boulder V&S tag needed. Vaccines: "
     "Rabies, DHPP, Bordetella + CANINE INFLUENZA + LEPTOSPIROSIS (check Mochi's records!); "
     "spay/neuter ≥6 mo. Bonded/insured, pet first aid certified. Pairs perfectly with the "
     "car-free days + an RMNP/Eldorado day."],
    ["Updog  (the Sunday option)", "Boutique small-group daycare",
     "5155 Arapahoe Ave, Boulder, CO 80303\n(park on Range St)",
     "(303) 444-1451\nmelody@updogdaycare.com", "updogdaycare.com",
     "$42 full / $34 half\n10-day $400",
     "Gingr registration + trial day — ⚠️ ~1 week trial-day wait; start before arrival",
     "OPEN 7 DAYS (only verified Sunday daycare): M–Sat drop-off 6:30–11:30am, pickup "
     "2–6:30pm; Sun drop 7–11:30am, pickup 2–6pm; all dogs kenneled for naptime 12–2pm. "
     "Small groups (max 15 dogs/handler). Rabies, DHPP, Bordetella; spay/neuter over 6 mo; "
     "allow 24 h for vaccine-record review."],
    ["Dogtopia of Lafayette  (nearest Dogtopia — none exists in Boulder)",
     "Chain daycare with webcams",
     "300 W South Boulder Rd, Lafayette, CO 80026\n(~10 mi / ~20 min SE)",
     "(720) 263-4583\nLafayette@dogtopia.com", "dogtopia.com/lafayette",
     "$45 full / $35 half\nweekly plans from $38",
     "20–30 min Meet & Greet evaluation first; ≥48 h since most recent vaccination",
     "M–F 6:30am–7pm; Sat–Sun 9am–5pm. Rabies, DHPP, Bordetella; spay/neuter over 7 mo. "
     "Explicitly serves Boulder; live webcams to peek at her."],
    ["The Pet Spot  (= the old 'Dog Spot Boulder' — rebranded, NOT closed)",
     "Facility daycare",
     "3640 Walnut St, Suite D, Boulder, CO 80301\n(~3.5 mi / ~10 min SE)",
     "(720) 564-6280\ninfo@thepetspotco.com", "thepetspotco.com",
     "Call — rates unpublished",
     "Comprehensive Meet & Greet evaluation required; 24-h cancellation policy",
     "M–F 7am–6:30pm; Sat–Sun 8am–5pm. Booking via Gingr (portal is still "
     "'dogspotboulder'). Spay/neuter + vaccine specifics unpublished — ask when calling."],
    ["Gunbarrel Veterinary Hospital daycare  (zero-lead-time fallback)",
     "Vet-run daycare — WALK-IN",
     "4636 55th St, Boulder, CO 80301\n(~5 mi / ~13 min E; near Geotrek)",
     "(303) 530-2500\nrecords → clientservices@gunbarrelvet.com", "gunbarrelvet.com",
     "$53.49 full / $34.99 half",
     "NONE — walk-in, no reservation; new dogs get an on-the-spot intro (no interview day)",
     "M–F ONLY 7:45am–5:45pm — no weekend daycare. Rabies, DHPP + Bordetella given within "
     "the LAST 6 MONTHS (stricter than annual). Spay/neuter over 9 mo. Dogs not separated "
     "by size; vet on-site if anything goes wrong."],
    ["Leader of the Pack  (budget pack outings)", "Home pickup → pack hikes/walks",
     "Central Boulder (mobile; one-man operation since 2009)",
     "packwalking.com contact form\n(no phone published)", "packwalking.com",
     "$36 / 90-min trail hike\n$24 / 60-min leashed walk",
     "Message via the site; M–F only, no federal holidays; short-term-visitor acceptance UNVERIFIED",
     "'Adventure Pack' hikes ~11am–12:30pm (off-leash portions REQUIRE the Boulder Voice & "
     "Sight tag — visitors can get one: $75, free online course, ~2 weeks lead); 'Variety "
     "Pack' leashed walks ~2–3pm need no tag. Blog active as of 7/14/2026."],
    ["ℹ️ Look great in searches but LOCALS-ONLY — skip",
     "Off-leash hike outfits (not available to visitors)",
     "Boulder Doggie Adventures  ·  Off Leash Dog Walks", "—", "—", "—", "—",
     "Both require dogs to LIVE within Boulder city limits AND hold a V&S tag — not viable "
     "on a 10-day visit; don't waste the calls. (Also: 'Rocky Mountain K9' search hits are "
     "a Utah chain, not Boulder. Doggie Depot's site still looks alive but they DECLINED "
     "new clients Jun 2026.)"],
]

# ── 1. insert the new Boulder facility rows (after Rogue's Farm) ──────────────────
vals = ws.get_all_values()
if any("Bowhaus" in (r[0] if r else "") for r in vals):
    print("Boulder rows already present (Bowhaus found) — skip insert.")
    start = next(i for i, r in enumerate(vals) if r and "Bowhaus" in r[0])
else:
    rogues = next(i for i, r in enumerate(vals) if r and r[0].startswith("⭐ Rogue's Farm"))
    start = rogues + 1                       # 0-based insertion index
    W(sh.batch_update, {"requests": [{"insertDimension": {
        "range": {"sheetId": sid, "dimension": "ROWS",
                  "startIndex": start, "endIndex": start + len(NEW_ROWS)},
        "inheritFromBefore": True}}]})
    W(sh.batch_update, {"requests": [{"unmergeCells": {"range": {"sheetId": sid,
        "startRowIndex": start, "endRowIndex": start + len(NEW_ROWS),
        "startColumnIndex": 0, "endColumnIndex": 8}}}]})
    W(ws.update, range_name=f"A{start+1}",
      values=[(r + [""] * (8 - len(r)))[:8] for r in NEW_ROWS],
      value_input_option="USER_ENTERED")
    W(sh.batch_update, {"requests": [
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": start,
            "endRowIndex": start + len(NEW_ROWS), "startColumnIndex": 0, "endColumnIndex": 8},
            "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1, "green": 1, "blue": 1},
                     "textFormat": {"bold": False},
                     "wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"}},
    ]})
    print(f"Inserted {len(NEW_ROWS)} Boulder rows at r{start+1}–r{start+len(NEW_ROWS)}.")

# ── 2–5. refetch, then content-located updates ────────────────────────────────────
vals = ws.get_all_values()

def row_where(pred):
    return next(i for i, r in enumerate(vals) if r and pred(r))

# 2. Boulder priority row refresh
i = row_where(lambda r: len(r) > 2 and r[1].startswith("Boulder  |"))
if "Bowhaus" in vals[i][2]:
    print("Priority row #6 already refreshed — skip.")
else:
    W(ws.update, range_name=f"C{i+1}:F{i+1}", values=[[
        "🥇 Bowhaus (Gunbarrel) → Camp Bow Wow / Updog / Gunbarrel Vet backups",
        "(720) 961-7466  (Bowhaus)",
        "NEW PLAN (researched 7/15): book Bowhaus's FREE Trial Day for Jul 22–23 and email "
        "vaccine records ≥48 h ahead. $99 first-30-days promo covers the whole stay; 500 ft "
        "from Geotrek → one-stop van + dog drop on Mon Jul 27. VERIFY FIRST: spay/neuter "
        "status + CANINE INFLUENZA shot (Bowhaus) / +LEPTO (Hike Doggie) — any missing shot "
        "must happen BEFORE the Jul 17 departure (48-h rules). Backups: Camp Bow Wow "
        "(interview day), Updog (Sundays; ~1 wk trial wait), Gunbarrel Vet (walk-in, M–F).",
        "NOW — before Jul 17 departure"]],
      value_input_option="USER_ENTERED")
    print(f"Priority row #6 (r{i+1}) refreshed → Bowhaus-first plan.")

# 3. cancelled-leg priority rows
for facility, city in [("Red Rover Resort", "Steamboat"),
                       ("Truckee-Tahoe Pet Lodge", "Truckee"),
                       ("Wanderlust Mutts", "Moab")]:
    i = row_where(lambda r, f=facility: len(r) > 4 and f in r[2] and "|" in r[1])
    if vals[i][4].startswith("❌"):
        print(f"Priority row {city} already marked — skip.")
        continue
    W(ws.update, range_name=f"A{i+1}", values=[["⚫  —"]], value_input_option="USER_ENTERED")
    W(ws.update, range_name=f"E{i+1}",
      values=[["❌ LEG CANCELLED 2026-07-14 — do NOT book. | " + vals[i][4]]],
      value_input_option="USER_ENTERED")
    print(f"Priority row {city} (r{i+1}) marked ❌ leg cancelled.")

# 4. section headers for cancelled legs
for prefix in ("MOAB, UT", "TRUCKEE / LAKE TAHOE", "STEAMBOAT SPRINGS"):
    i = row_where(lambda r, p=prefix: r[0].startswith(p))
    if "LEG CANCELLED" in vals[i][0]:
        print(f"Header {prefix} already marked — skip.")
        continue
    W(ws.update_cell, i + 1, 1, vals[i][0] + "   —   ❌ LEG CANCELLED Jul 14 (kept for reference)")
    print(f"Header {prefix} (r{i+1}) marked ❌.")

# 5. vaccine reminder extension
i = row_where(lambda r: r[0].startswith("⚠️  VACCINE REMINDER"))
if "INFLUENZA" in vals[i][0]:
    print("Vaccine row already extended — skip.")
else:
    W(ws.update_cell, i + 1, 1, vals[i][0] +
      "   NEW (7/15, Boulder): Bowhaus + Hike Doggie also require CANINE INFLUENZA (Hike "
      "Doggie adds LEPTOSPIROSIS); every Boulder facility requires spay/neuter by age 2; "
      "new-client shots must be ≥48 h before the first visit — i.e., BEFORE the Jul 17 "
      "departure. Verify Mochi's records TODAY.")
    print(f"Vaccine reminder (r{i+1}) extended.")

# ── 6. Reservations: Bowhaus trial row + CBW now 2nd choice ───────────────────────
res = sh.worksheet("Reservations")
rvals = res.get_all_values()
if any(len(r) > 1 and "Bowhaus" in r[1] for r in rvals):
    print("Reservations: Bowhaus row already present — skip.")
else:
    cbw = next(i for i, r in enumerate(rvals) if len(r) > 1 and "Camp Bow Wow Boulder Interview" in r[1])
    empty = next(i for i in range(cbw + 1, len(rvals))
                 if len(rvals[i]) < 2 or not rvals[i][1].strip())
    row = ["FALSE",
           "Book Bowhaus Boulder FREE Trial Day (Jul 22–23) + email vaccine records ≥48 h ahead !!1 Jul 16",
           "Mochi / Daycare", "Jul 16",
           "(720) 961-7466 / bowhausco.com/locations/boulder",
           "🥇 NEW BOULDER PICK — 6560 O'Dell Pl, ~500 ft from Geotrek. $99 first-30-days "
           "unlimited promo (else $40/day); open weekends. Needs CANINE INFLUENZA + "
           "Rabies/DHPP/Bordetella + spay/neuter, records ≥48 h before the visit. Trial day "
           "Jul 22–23 unlocks daycare for the van-free days (Jul 27–29) + any RMNP/Eldorado "
           "trip. If the influenza shot is missing, it must happen BEFORE the Jul 17 departure."]
    W(res.update, range_name=f"A{empty+1}:F{empty+1}", values=[row],
      value_input_option="USER_ENTERED")
    print(f"Reservations: Bowhaus trial row written at r{empty+1}.")
    f_old = rvals[cbw][5] if len(rvals[cbw]) > 5 else ""
    if not f_old.startswith("2nd choice"):
        W(res.update_cell, cbw + 1, 6, "2nd choice — Bowhaus (next row) is closer to the van "
          "shop + cheaper via promo. | " + f_old)
        print(f"Reservations: Camp Bow Wow row (r{cbw+1}) marked 2nd choice.")

print("DONE.")
