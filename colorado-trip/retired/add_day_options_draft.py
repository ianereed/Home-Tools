"""DRAFT, for review. Builds the 'DAY OPTIONS (DRAFT)' tab: a per-location menu of
full-day options for the FLEXIBLE days. Pick one each morning, check it off
(strikethrough), drop its one-liner into the itinerary. Locations: Boulder, Steamboat,
Crested Butte (Mammoth pending). Options mirror what's on the Itinerary sheet.
Non-destructive; re-runnable (deletes + recreates the tab).

MTB note: lift-served bike parks (Steamboat Bike Park, Evolution) don't allow dogs, so
the 'Ian + Mochi / Anny solo' pattern (from dog-friendly rides) applies to Boulder
trail rides — see the 'Boulder MTB Rides' tab — not these bike-park days.
"""
import urllib.parse
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

TAB = "DAY OPTIONS"
NCOLS = 10  # A..J

def rtlink(base, stops, label):
    """Round-trip Maps route: base -> stops... -> base (live total time on tap)."""
    b = urllib.parse.quote(base)
    wpts = "|".join(urllib.parse.quote(s) for s in stops)
    u = (f"https://www.google.com/maps/dir/?api=1&origin={b}"
         f"&destination={b}&waypoints={wpts}")
    return f'=HYPERLINK("{u}","{label}")'

def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

TITLE_BG = rgb(23, 37, 84)
SUB_BG   = rgb(40, 60, 110)
SECT_BG  = rgb(21, 101, 192)
EVE_BG   = rgb(0, 131, 143)
COL_HDR  = rgb(225, 228, 234)
WHITE    = rgb(255, 255, 255)
DARK     = rgb(33, 33, 33)
NOTE_BG  = rgb(247, 247, 247)
PREVIEW  = rgb(232, 240, 254)
GREY     = rgb(150, 150, 150)
LINKC    = rgb(21, 101, 192)

TYPES = {
    "Together":  (rgb(46, 125, 50),  rgb(232, 245, 233)),
    "Separate":  (rgb(2, 119, 189),  rgb(225, 240, 250)),
    "Big day":   (rgb(106, 27, 154), rgb(243, 229, 245)),
    "Day trip":  (rgb(230, 145, 30), rgb(253, 242, 222)),
    "Town/rest": (rgb(97, 97, 97),   rgb(238, 238, 238)),
}

values, fmts, merges, heights = [], [], [], []

def row(cells):
    values.append(list(cells) + [""] * (NCOLS - len(cells)))
    return len(values) - 1

def fmt(r, bg=None, fg=None, bold=False, size=None, c0=0, c1=NCOLS,
        align=None, valign="MIDDLE", wrap=True, italic=False):
    cell = {}
    if bg is not None:
        cell["backgroundColor"] = bg
    tf = {"bold": bold, "italic": italic}
    if fg is not None:
        tf["foregroundColor"] = fg
    if size is not None:
        tf["fontSize"] = size
    cell["textFormat"] = tf
    if align:
        cell["horizontalAlignment"] = align
    cell["verticalAlignment"] = valign
    cell["wrapStrategy"] = "WRAP" if wrap else "OVERFLOW_CELL"
    fmts.append({"repeatCell": {
        "range": {"startRowIndex": r, "endRowIndex": r+1, "startColumnIndex": c0, "endColumnIndex": c1},
        "cell": {"userEnteredFormat": cell},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)"}})

def merge(r, c0=0, c1=NCOLS):
    merges.append((r, c0, c1))

def blank(px=8):
    heights.append((row([""]), px))

# ════════════════════════════════════════════════════════════════════════════════
# LOCATION DATA — options mirror the Itinerary sheet (phase 1). drive = (label, [stops]).
# opt = (id, type, label, ian, anny, mochi, reservations, weather-backup)
BOULDER = {
 "name": "BOULDER", "dates": "Jul 22–31", "base": "582 Locust Place, Boulder, CO 80304",
 "base_label": "582 Locust Pl",
 "opts": [
  ("BLD-A","Together","Together: Green Mountain hike + downtown dinner",
    "Green Mtn via Gregory Canyon (6 mi, 2,400 ft)","Same hike","Comes (leashed)",
    "$5 Gregory Canyon parking; start ~7am","Flagstaff Mtn (shorter, no res)"),
  ("BLD-B","Separate","Separate AM: Ian runs Sanitas / Anny+Mochi valley walk, PM together",
    "Mt Sanitas loop run (3.2 mi, 1,270 ft)","Sanitas Valley Trail (2 mi, easy)","With Anny (same trailhead)",
    "None","Wonderland Lake loop (1.3 mi)"),
  ("BLD-C","Big day","Big alpine day: Indian Peaks — Lake Isabelle + Blue Lake",
    "6–8 mi alpine lakes + waterfalls","Same hike","Comes (leashed)",
    "Brainard Lake timed entry (recreation.gov, 15-day rolling)","Golden Gate Canyon SP — Mountain Lion Trail (no res)"),
  ("BLD-D","Day trip","Day trip: RMNP — Bear Lake→Dream Lake + Trail Ridge Road",
    "Bear Lake shuttle + 2.2 mi hike; drive Trail Ridge (12,183 ft)","Same","RESTRICTED in RMNP (lots only) — Airbnb (A/C after Jul 30) or daycare",
    "Arrive before 9am; check timed entry (nps.gov/romo)","Nederland day (Carousel, town, coffee)"),
  ("BLD-E","Separate","Separate: Ian Valmont bike park / Anny+Mochi foothills",
    "Valmont Bike Park (free, in town)","Wonderland Lake + Foothills Trail","With Anny",
    "None","The Spot bouldering (Mochi home, shade/fans)"),
  ("BLD-F","Together","Together: Eldorado Canyon or Mesa Trail",
    "Eldorado Canyon (6.7 mi) or Mesa Trail (7 mi)","Same","Comes (leashed)",
    "Eldorado timed entry $10 (cpw.state.co.us)","Mesa Trail from Chautauqua (no res)"),
  ("BLD-G","Town/rest","Town day: brunch, Pearl St, climbing gym, breweries",
    "Rope climbing at Movement or BRC","Brunch + neighborhoods + Trident Books","Airbnb (A/C after Jul 30) or Reservoir dog beach",
    "None","Boulder Creek Path walk"),
  ("BLD-H","Day trip","Day trip: Golden — Coors tour, Clear Creek, North Table hike",
    "North Table Mtn (5.9 mi) + tubing + brewery","Same","Comes (most dog-friendly day trip)",
    "None","Betasso Preserve or Flagstaff Mtn"),
  ("BLD-I","Separate","Separate: Ian Walker Ranch ride/run / Anny+Mochi Flatirons Vista",
    "Walker Ranch Loop (7.8 mi MTB or run) — see Boulder MTB Rides tab","Flatirons Vista / Doudy Draw (3.4 mi)","With Anny",
    "None — Anny drops Ian at the trailhead","Gross Reservoir hike"),
  ("BLD-J","Town/rest","Easy day: Reservoir dog beach + Chautauqua meadow + downtown",
    "Easy / recovery","Easy / recovery","OFF-LEASH swim at Boulder Reservoir dog beach!",
    "None","Flatirons Vista easy loop"),
 ],
 "drive": {
  "BLD-A": ("~30 min", ["Gregory Canyon Trailhead, Baseline Rd, Boulder, CO 80302", "Pearl Street Mall, Boulder, CO 80302"]),
  "BLD-B": ("~20 min", ["Mount Sanitas Trailhead, Boulder, CO 80304"]),
  "BLD-C": ("~1h50",   ["Brainard Lake Recreation Area, Ward, CO 80481"]),
  "BLD-D": ("~2h40",   ["Bear Lake Trailhead, Rocky Mountain National Park, CO"]),
  "BLD-E": ("~20 min", ["Valmont Bike Park, 3160 Airport Road, Boulder, CO 80301", "Avery Brewing Co, 4910 Nautilus Ct N, Boulder, CO 80301"]),
  "BLD-F": ("~45 min", ["Eldorado Canyon State Park, Eldorado Springs, CO 80025"]),
  "BLD-G": ("~20 min", ["Pearl Street Mall, Boulder, CO 80302"]),
  "BLD-H": ("~1h40",   ["North Table Mountain Park, Golden, CO 80403"]),
  "BLD-I": ("~1h",     ["Walker Ranch Loop Trailhead, Flagstaff Rd, Boulder, CO 80302"]),
  "BLD-J": ("~25 min", ["Boulder Reservoir, 5100 N 51st St, Boulder, CO 80301"]),
 },
 "evenings_title": "BOULDER — EVENINGS (date-pinned: glance here when you pick a day)",
 "evenings": [
  ("Most nights", "Colorado Music Festival @ Chautauqua — book coloradomusicfestival.org"),
  ("Through Aug 1", "Colorado Shakespeare Festival (Twelfth Night / Shakespeare in Love) — cupresents.org"),
  ("Wed eves", "Bands on the Bricks, Pearl St (free, 5:30–9) — season finale Jul 29"),
  ("Thu Jul 30", "Red Rocks: Killer Queen tribute (pairs with the Golden day trip) — redrocksonline.com"),
  ("Sat AM", "Boulder Farmers Market, 13th & Canyon, 8am–2pm"),
 ],
}

STEAMBOAT = {
 "name": "STEAMBOAT", "dates": "Aug 2–5", "base": "1036 Lincoln Avenue, Steamboat Springs, CO 80487",
 "base_label": "1036 Lincoln Ave",
 "opts": [
  ("STM-A","Together","Together: Fish Creek Falls — lower + upper falls",
    "Upper Falls (5 mi RT, moderate)","Same hike","Comes — OFF-LEASH past 0.25 mi!",
    "$5 parking; start early (popular)","Emerald Mtn Blackmere (3.7 mi, from town)"),
  ("STM-B","Separate","Separate: Ian Steamboat Bike Park / Anny+Mochi Emerald Mtn, PM Old Town Hot Springs together",
    "Steamboat Bike Park — lift DH/enduro, 2,200 ft ($50–70; check Ikon 2 free days). Back by lunch","Emerald Mtn Blackmere Trail (3.7 mi, 938 ft), walkable from Howelsen","With Anny on Emerald (dog-friendly); at Airbnb (A/C) during PM hot springs",
    "Bike-park ticket / Ikon perk","Park closed/wet → Ian trail-runs Emerald (6–8 mi)"),
  ("STM-C","Big day","Big day: Hahns Peak summit + Fishhook Lake (or Red Dirt)",
    "Hahns Peak fire-lookout (3 mi RT) + Fishhook Lake (6 mi)","Same","Comes (dog-friendly)",
    "None — ~40 min drive N to Hahns Peak Village","Yampa River Core Trail (7 mi paved) if weather turns"),
  ("STM-D","Town/rest","Town/rest: Strawberry Park Hot Springs + Yampa River + downtown",
    "Soak + downtown / Yampa River walk","Soak + downtown","At Airbnb (A/C) — no dogs at the springs",
    "Strawberry Park: book ~30 days ahead, CASH $20/person","Old Town Hot Springs (downtown, no res)"),
 ],
 "drive": {
  "STM-A": ("~20 min", ["Fish Creek Falls Trailhead, Steamboat Springs, CO 80487"]),
  "STM-B": ("~20 min", ["Steamboat Resort, 2305 Mount Werner Cir, Steamboat Springs, CO 80487", "Old Town Hot Springs, 136 Lincoln Ave, Steamboat Springs, CO 80487"]),
  "STM-C": ("~1h30",   ["Hahns Peak, CO 80428"]),
  "STM-D": ("~40 min", ["Strawberry Park Hot Springs, 44200 County Road 36, Steamboat Springs, CO 80487"]),
 },
 "evenings_title": "STEAMBOAT — EVENINGS (date-pinned)",
 "evenings": [
  ("Fri + Sat", "Steamboat Pro Rodeo — BBQ 6pm, rodeo 7:30, Romick Arena (steamboatprorodeo.com)"),
  ("One eve Aug 2–6", "Aurum Food & Wine — riverfront on the Yampa; book on Tock (aurumsteamboat.com)"),
  ("Evenings", "Strawberry Park Hot Springs soak (reserve ~30 days ahead, cash only)"),
  ("Nightly", "Movies on the Mountain — Gondola Square, free, sunset (no dogs)"),
 ],
}

CRESTED_BUTTE = {
 "name": "CRESTED BUTTE", "dates": "Aug 10–11", "base": "6 Emmons Road, Crested Butte, CO 81225",
 "base_label": "6 Emmons Rd (Mt CB)",
 "opts": [
  ("CB-A","Separate","Separate: Ian Evolution Bike Park / Anny+Mochi Oh-Be-Joyful (or Judd Falls), Alpenglow Concert eve",
    "Evolution Bike Park — lift DH, world-class ($60–70). WALK from the Airbnb. Back ~4:30 for the concert","Oh-Be-Joyful (9.6 mi, hard, 4.8★) OR Judd Falls / Copper Creek (moderate, dog-friendly)","With Anny",
    "Bike-park ticket — get the 2-day pass (Aug 10 + 11)","Lower Loop / Slate River (easy–moderate, river + meadow)"),
  ("CB-B","Separate","Separate: Ian Evolution day 2 / Anny+Mochi Three Lakes, pack up + last Elk Ave dinner",
    "Evolution Bike Park — second session (2-day pass). Walk from the Airbnb","Three Lakes Loop (3 mi, easy — 3 alpine lakes + waterfall detour)","With Anny",
    "Use the 2-day pass from Aug 10","Lower Loop or Woods Walk (easy, dog-friendly)"),
  ("CB-C","Big day","Big day: Crested Butte → Aspen via West Maroon Pass (4 + Mochi)",
    "Hike 10.5 mi over West Maroon Pass (12,490 ft)","Same hike","Comes (leashed; USFS wilderness)",
    "Dolly's + Maroon Bells bus + car relocation — see 'West Maroon Pass' tab","Full-day commitment; replaces a bike-park day"),
 ],
 "drive": {
  "CB-A": ("~50 min", ["Oh-Be-Joyful Trailhead, Crested Butte, CO 81224", "Crested Butte Town Park, Crested Butte, CO 81224"]),
  "CB-B": ("~45 min", ["Three Lakes Trailhead, Crested Butte, CO", "Elk Avenue, Crested Butte, CO 81224"]),
  "CB-C": ("~2.5 hr", ["Aspen, CO 81611"]),
 },
 "evenings_title": "CRESTED BUTTE — EVENINGS (date-pinned)",
 "evenings": [
  ("Mon Aug 10", "Alpenglow Concert — free, 5:30pm, CB Town Park (no pets inside)"),
  ("One eve Aug 10–11", "Soupçon — prix-fixe tasting; book NOW on Tock (soupconcb.com), fills 4–6 wks ahead"),
  ("Wed", "Music on the Mountain — CBMR base, free 5:30–8pm (needs an extra night)"),
  ("Nightly", "Elk Avenue stroll + dinner — The Breadery, The Public House"),
 ],
}

LOCATIONS = [BOULDER, STEAMBOAT, CRESTED_BUTTE]

# ── TITLE ───────────────────────────────────────────────────────────────────────
r = row(["DAY OPTIONS  —  a menu-driven way to run the flexible days   (DRAFT for review)"])
merge(r); fmt(r, bg=TITLE_BG, fg=WHITE, bold=True, size=15, align="CENTER"); heights.append((r, 38))
r = row(["The trip is long — don't feel beholden to a script. Travel/anchor days stay fixed; "
         "in-location days become a MENU. Each morning (or the night before) pick a day that fits "
         "the weather + mood, check it off, and drop its one-liner into the itinerary."])
merge(r); fmt(r, bg=SUB_BG, fg=WHITE, italic=True, size=10, align="CENTER"); heights.append((r, 40))
blank()

# ── HOW IT WORKS ─────────────────────────────────────────────────────────────────
r = row(["HOW IT WORKS"]); merge(r); fmt(r, bg=SECT_BG, fg=WHITE, bold=True, size=12, align="CENTER"); heights.append((r, 26))
how = [
    ("1 · Fixed vs flexible", "Travel days, arrivals, the van handoff, Geotrek, the drives — stay specific. The in-location days (Boulder, Steamboat, Crested Butte, Mammoth) become a menu."),
    ("2 · A menu per location", "Each base has its own menu below. Phase 1 mirrors the Itinerary; we can add MORE options than days so there's always genuine choice + a rain backup."),
    ("3 · Pick + cross off", "Check the box on the option you used → the row strikes through + greys out, so it won't get picked twice. (Or type the date if you'd rather keep a log.)"),
    ("4 · Itinerary simplifies", "Each flexible day's row becomes just: the chosen one-liner (blank until you pick) + a pointer here. The detail lives in the menu, not the grid."),
]
for k, v in how:
    r = row([k, "", v]); merge(r, 0, 2); merge(r, 2, NCOLS)
    fmt(r, bg=NOTE_BG, fg=DARK, bold=True, c0=0, c1=2, align="LEFT")
    fmt(r, bg=WHITE, fg=DARK, c0=2, c1=NCOLS, align="LEFT"); heights.append((r, 36))
blank()

# ── LEGEND ───────────────────────────────────────────────────────────────────────
r = row(["DAY TYPES"]); merge(r); fmt(r, bg=COL_HDR, fg=DARK, bold=True, size=10, align="CENTER"); heights.append((r,22))
chip_specs = [("Together",(0,2)),("Separate",(2,4)),("Big day",(4,5)),("Day trip",(5,7)),("Town/rest",(7,NCOLS))]
r = row(["Together","","Separate","","Big day","Day trip","","Town/rest",""])
for lbl,(a,b) in chip_specs:
    merge(r, a, b); fmt(r, bg=TYPES[lbl][1], fg=DARK, bold=True, c0=a, c1=b, align="CENTER")
heights.append((r, 24))
blank()

# ── PER-LOCATION MENUS ───────────────────────────────────────────────────────────
menu_ranges = []   # (first_opt_row, last_opt_row) per location for checkbox + cond-format
for loc in LOCATIONS:
    r = row([f"{loc['name']}  —  DAY MENU   ({loc['dates']} · base = {loc['base_label']} · "
             f"'Drive' = approx TOTAL driving for the day, round trip · tap for the live route)"])
    merge(r); fmt(r, bg=SECT_BG, fg=WHITE, bold=True, size=11, align="CENTER"); heights.append((r, 30))
    r = row(["Done", "ID", "Type", "Drive", "Day label (drops into the itinerary)", "Ian", "Anny", "Mochi", "Reservations / heads-up", "Weather backup"])
    fmt(r, bg=COL_HDR, fg=DARK, bold=True, align="CENTER"); heights.append((r, 30))

    opt_rows = []
    for oid, typ, label, ian, anny, mochi, res, backup in loc["opts"]:
        dmin, dstops = loc["drive"][oid]
        r = row(["", oid, typ, rtlink(loc["base"], dstops, dmin), label, ian, anny, mochi, res, backup])
        opt_rows.append(r)
        chip, tint = TYPES[typ]
        fmt(r, bg=WHITE, fg=DARK, c0=0, c1=NCOLS, align="LEFT", valign="TOP")
        fmt(r, bg=tint, fg=DARK, bold=True, c0=1, c1=2, align="CENTER", valign="TOP")
        fmt(r, bg=chip, fg=WHITE, bold=True, size=9, c0=2, c1=3, align="CENTER", valign="TOP")
        fmt(r, bg=WHITE, fg=LINKC, bold=True, c0=3, c1=4, align="CENTER", valign="TOP")
        fmt(r, bg=tint, fg=DARK, bold=True, c0=4, c1=5, align="LEFT", valign="TOP")
        heights.append((r, 58))
    menu_ranges.append((opt_rows[0], opt_rows[-1]))
    blank(4)

    # evenings strip
    r = row([loc["evenings_title"]]); merge(r); fmt(r, bg=EVE_BG, fg=WHITE, bold=True, size=10, align="CENTER"); heights.append((r, 22))
    for when, what in loc["evenings"]:
        r = row([when, "", what]); merge(r, 0, 2); merge(r, 2, NCOLS)
        fmt(r, bg=rgb(224,242,241), fg=DARK, bold=True, c0=0, c1=2, align="LEFT")
        fmt(r, bg=WHITE, fg=DARK, c0=2, c1=NCOLS, align="LEFT"); heights.append((r, 24))
    blank()

# ── NOTES ────────────────────────────────────────────────────────────────────────
for note in [
    "PHASE 1 = options that mirror the Itinerary. Next: add MORE options than days (extra choices + rain backups), then refine each to be ideal.",
    "MTB: Steamboat Bike Park + Evolution are lift-served (NO dogs). The 'Boulder MTB Rides' tab is the ride source of truth + has dog-friendly trails — so 'Ian + Mochi ride / Anny solo day' fits BOULDER. Want a Steamboat/CB dog-friendly MTB list too?",
    "Still to build: Mammoth (Aug 15–17). Drive estimates are approximate — tap a Drive cell for the live route time.",
]:
    r = row([note]); merge(r); fmt(r, bg=rgb(255,243,205), fg=rgb(120,70,0), italic=True, align="LEFT"); heights.append((r, 34))

# ════════════════════════════════════════════════════════════════════════════════
if TAB in [w.title for w in sh.worksheets()]:
    sh.del_worksheet(sh.worksheet(TAB))
ws = sh.add_worksheet(title=TAB, rows=max(len(values)+5, 80), cols=NCOLS)
sid = ws._properties['sheetId']
ws.update(values, "A1", value_input_option="USER_ENTERED")

reqs = []
for f in fmts:
    f["repeatCell"]["range"]["sheetId"] = sid
    reqs.append(f)
for (r, c0, c1) in merges:
    reqs.append({"mergeCells": {"range": {"sheetId": sid, "startRowIndex": r, "endRowIndex": r+1,
        "startColumnIndex": c0, "endColumnIndex": c1}, "mergeType": "MERGE_ALL"}})
widths = [54, 56, 96, 78, 210, 150, 150, 130, 165, 140]
for i, px in enumerate(widths):
    reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS",
        "startIndex": i, "endIndex": i+1}, "properties": {"pixelSize": px}, "fields": "pixelSize"}})
for (r, px) in heights:
    reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS",
        "startIndex": r, "endIndex": r+1}, "properties": {"pixelSize": px}, "fields": "pixelSize"}})

# per-location: checkboxes on Done + strike-through-when-checked conditional format
for (first, last) in menu_ranges:
    reqs.append({"setDataValidation": {
        "range": {"sheetId": sid, "startRowIndex": first, "endRowIndex": last+1, "startColumnIndex": 0, "endColumnIndex": 1},
        "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True, "strict": True}}})
    reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": sid, "startRowIndex": first, "endRowIndex": last+1, "startColumnIndex": 0, "endColumnIndex": NCOLS}],
        "booleanRule": {
            "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": f"=$A{first+1}=TRUE"}]},
            "format": {"textFormat": {"strikethrough": True, "foregroundColor": GREY}, "backgroundColor": rgb(245,245,245)}}}}})

reqs.append({"updateSheetProperties": {"properties": {"sheetId": sid,
    "gridProperties": {"frozenRowCount": 1, "hideGridlines": True}},
    "fields": "gridProperties.frozenRowCount,gridProperties.hideGridlines"}})

sh.batch_update({"requests": reqs})
from linkutil import nativize
n = nativize(sh, ws, sid, len(values), NCOLS, LINKC)
total_opts = sum(len(l["opts"]) for l in LOCATIONS)
print(f"OK: '{TAB}' built — {len(values)} rows, {total_opts} options across {len(LOCATIONS)} locations, {n} native links.")
