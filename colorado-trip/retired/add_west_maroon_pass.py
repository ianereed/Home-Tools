"""Add a 'West Maroon Pass' tab describing the Crested Butte -> Aspen hike option,
with a vertical route map, trail stats, the three shuttle/relocation services,
a reservations checklist, dog notes, and the Aug 10-vs-11 tradeoff.

Also stamps the option onto the Itinerary tab (Aug 10 & Aug 11) and adds the
three new bookings to the ADVANCE RESERVATIONS section.

Idempotent: deletes and recreates the tab if it already exists.
"""
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

TAB = "West Maroon Pass"
NCOLS = 7  # A..G

# ── colors (match existing tabs) ───────────────────────────────────────────────
def rgb(r, g, b):
    return {"red": r / 255, "green": g / 255, "blue": b / 255}

TITLE_BG  = rgb(23, 37, 84)     # very dark navy
SUB_BG    = rgb(40, 60, 110)    # navy
SECT_BG   = rgb(21, 101, 192)   # deep blue
COL_HDR   = rgb(225, 228, 234)  # light grey
NODE_BG   = rgb(55, 71, 79)     # slate (map nodes)
HIKE_BG   = rgb(46, 125, 50)    # green  (hiking legs)
HIKE_LT   = rgb(232, 245, 233)  # light green
BUS_BG    = rgb(2, 119, 189)    # blue   (public shuttle / bus)
BUS_LT    = rgb(225, 240, 250)
CAR_BG    = rgb(230, 145, 30)   # orange (your car / relocation / drive)
CAR_LT    = rgb(253, 242, 222)
NOTE_BG   = rgb(247, 247, 247)  # near-white
WARN_BG   = rgb(255, 248, 225)  # pale amber
WHITE     = rgb(255, 255, 255)
DARK      = rgb(33, 33, 33)

# ── builder ────────────────────────────────────────────────────────────────────
values = []   # list of rows (each a list of <=NCOLS cells)
fmts = []     # list of formatting request dicts
merges = []   # list of (row, c0, c1) merges
heights = []  # list of (row, px)

def row(cells):
    r = list(cells) + [""] * (NCOLS - len(cells))
    values.append(r)
    return len(values) - 1  # 0-based row index

def fmt(r, bg=None, fg=None, bold=False, size=None, c0=0, c1=NCOLS,
        align=None, valign="MIDDLE", wrap=True, italic=False):
    cell = {}
    if bg is not None:
        cell["backgroundColor"] = bg
    tf = {}
    if fg is not None:
        tf["foregroundColor"] = fg
    tf["bold"] = bold
    tf["italic"] = italic
    if size is not None:
        tf["fontSize"] = size
    cell["textFormat"] = tf
    if align:
        cell["horizontalAlignment"] = align
    cell["verticalAlignment"] = valign
    cell["wrapStrategy"] = "WRAP" if wrap else "OVERFLOW_CELL"
    fmts.append({"repeatCell": {
        "range": {"startRowIndex": r, "endRowIndex": r + 1,
                  "startColumnIndex": c0, "endColumnIndex": c1},
        "cell": {"userEnteredFormat": cell},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)"
    }})

def merge(r, c0=0, c1=NCOLS):
    merges.append((r, c0, c1))

def link(url, label):
    safe = label.replace('"', "'")
    return f'=HYPERLINK("{url}","{safe}")'

def blank(px=8):
    r = row([""])
    heights.append((r, px))
    return r

# ════════════════════════════════════════════════════════════════════════════════
# TITLE
r = row(["CRESTED BUTTE  →  ASPEN   via West Maroon Pass"])
merge(r); fmt(r, bg=TITLE_BG, fg=WHITE, bold=True, size=16, align="CENTER")
heights.append((r, 40))

r = row(["A full-day point-to-point epic over the Elk Mountains. Hike one way; "
         "two shuttles + a car-relocation service handle the logistics. "
         "Option for ONE Crested Butte day (Aug 10 or 11) — 4 people + Mochi."])
merge(r); fmt(r, bg=SUB_BG, fg=WHITE, italic=True, size=10, align="CENTER")
heights.append((r, 38))

blank()

# ════════════════════════════════════════════════════════════════════════════════
# ROUTE MAP (vertical timeline-map)
r = row(["ROUTE MAP  —  the loop  (read top to bottom)"])
merge(r); fmt(r, bg=SECT_BG, fg=WHITE, bold=True, size=12, align="CENTER")
heights.append((r, 28))

# node row: dark chip in A + merged label B:G ; leg row: colored bar across A:G
def node(icon, label):
    r = row([icon, label])
    fmt(r, bg=NODE_BG, fg=WHITE, bold=True, size=11, c0=0, c1=1, align="CENTER")
    merge(r, 1, NCOLS); fmt(r, bg=rgb(236, 239, 241), fg=DARK, bold=True, size=11, c0=1, c1=NCOLS, align="LEFT")
    heights.append((r, 30))

def leg(kind, label):
    bg = {"hike": HIKE_BG, "bus": BUS_BG, "car": CAR_BG}[kind]
    r = row(["", label])
    fmt(r, bg=bg, fg=WHITE, c0=0, c1=1, align="CENTER")
    merge(r, 1, NCOLS); fmt(r, bg=bg, fg=WHITE, italic=True, size=10, c0=1, c1=NCOLS, align="LEFT")
    heights.append((r, 24))

node("🚐", "CRESTED BUTTE  ·  town, ~8,900 ft  ·  morning start")
leg("car",  "↘  Meanwhile, your car is driven CB → Aspen by road (Maroon Bells Shuttles relocation) so it's waiting when you finish")
leg("bus",  "↓  Dolly's Mountain Shuttle  ·  CB → West Maroon Trailhead  ·  ~40 min over Schofield Pass  ·  $55/seat (Mochi needs a seat too)")
node("🥾", "WEST MAROON TRAILHEAD  ·  10,432 ft  ·  start hiking (go EARLY — afternoon thunderstorms)")
leg("hike", "↓  HIKE up the valley along the Crystal River  ·  wildflower fields  ·  steady climb")
node("⛰️", "WEST MAROON PASS  ·  12,490 ft  ·  HIGH POINT  ·  last ¼ mi is steep — big views both sides")
leg("hike", "↓  HIKE down (steep + rocky at first)  ·  3 Maroon Creek crossings  ·  past Crater Lake")
node("🏞️", "MAROON LAKE TRAILHEAD  ·  ~9,580 ft  ·  the Maroon Bells! (most-photographed peaks in N. America)")
leg("bus",  "↓  Maroon Bells RFTA shuttle bus  ·  Maroon Lake → Aspen Highlands  ·  15 min  ·  $10 'One-Way Return' ticket  ·  last bus down 5:00 PM")
node("🚌", "ASPEN HIGHLANDS  ·  Maroon Bells Welcome Center, 75 Boomerang Rd")
leg("car",  "↓  Reach your relocated car (RFTA local bus Aspen Highlands ↔ Aspen, or arrange drop point with the relocation service)")
node("🍽️", "ASPEN  ·  pick up your car  ·  dinner downtown")
leg("car",  "↓  DRIVE back to Crested Butte  ·  ~2.5 hr loop (CO-82 → Carbondale → McClure & Kebler Pass)")
node("🏠", "CRESTED BUTTE  ·  home for the night")

blank()

# LEGEND
r = row(["LEGEND"]); merge(r); fmt(r, bg=COL_HDR, fg=DARK, bold=True, size=10, align="CENTER")
r = row(["🟩  on foot (hike)", "", "🟦  public shuttle / bus", "", "🟧  your car (relocation + drive)", "", ""])
fmt(r, bg=HIKE_LT, fg=DARK, bold=True, c0=0, c1=2, align="CENTER")
fmt(r, bg=BUS_LT, fg=DARK, bold=True, c0=2, c1=4, align="CENTER")
fmt(r, bg=CAR_LT, fg=DARK, bold=True, c0=4, c1=NCOLS, align="CENTER")
merge(r, 0, 2); merge(r, 2, 4); merge(r, 4, NCOLS)
heights.append((r, 26))

blank()

# ════════════════════════════════════════════════════════════════════════════════
# TRAIL STATS
r = row(["TRAIL  —  Crested Butte → Aspen via West Maroon Pass"])
merge(r); fmt(r, bg=SECT_BG, fg=WHITE, bold=True, size=12, align="CENTER")
heights.append((r, 26))

stats = [
    ("Distance",      "~10.5 miles one-way (point to point)"),
    ("Elevation gain", "2,357 ft of climbing  (CB → Aspen direction)"),
    ("High point",    "West Maroon Pass — 12,490 ft"),
    ("Difficulty",    "Strenuous"),
    ("Time on trail", "6–10 hours (Dolly's quotes ~6 hr avg — you're only as fast as the slowest hiker)"),
    ("Season",        "Trail passable late June–mid July depending on snow; August is prime + wildflowers"),
    ("Trailhead access", "West Maroon TH is 13–14 mi / ~40 min from CB over Schofield Pass (past Emerald Lake). 4x4 SUV + tiny lot — this is why you take Dolly's instead of self-driving."),
]
for k, v in stats:
    r = row([k, "", v]); merge(r, 0, 2); merge(r, 2, NCOLS)
    fmt(r, bg=NOTE_BG, fg=DARK, bold=True, c0=0, c1=2, align="LEFT")
    fmt(r, bg=WHITE, fg=DARK, c0=2, c1=NCOLS, align="LEFT")
    heights.append((r, 30))

blank()

# ════════════════════════════════════════════════════════════════════════════════
# SERVICES & BOOKING
r = row(["SERVICES & BOOKING  (costs shown for 4 people + Mochi)"])
merge(r); fmt(r, bg=SECT_BG, fg=WHITE, bold=True, size=12, align="CENTER")
heights.append((r, 26))

# Service rows use 7 cols: 0 leg | 1 name | 2-3 what (merged) | 4 cost | 5 phone | 6 link
r = row(["Leg", "Service", "What it does", "", "Cost (4+dog)", "Phone", "Book / Link"])
merge(r, 2, 4)
fmt(r, bg=COL_HDR, fg=DARK, bold=True, align="CENTER")
heights.append((r, 24))

def service2(legcolor, leg_lbl, name, what, cost, phone, url, booklabel):
    bg = {"car": CAR_LT, "bus": BUS_LT}[legcolor]
    r = row([leg_lbl, name, what, "", cost, phone, link(url, booklabel)])
    merge(r, 2, 4)
    fmt(r, bg=bg, fg=DARK, c0=0, c1=NCOLS, align="LEFT")
    fmt(r, bg=bg, fg=DARK, bold=True, c0=1, c1=2, align="LEFT")
    heights.append((r, 58))

service2("car", "🟧 relocate",
         "Maroon Bells Shuttles",
         "Drives YOUR car CB → Aspen by road while you hike, so it's waiting at the finish. Operating since 2012; they email final logistics after you book.",
         "Request quote (per vehicle)",
         "—",
         "https://maroonbellsshuttles.com/reservations/",
         "maroonbellsshuttles.com")

service2("bus", "🟦 to TH",
         "Dolly's Mountain Shuttle",
         "Drives the group (+ dog) from CB to the West Maroon Trailhead. Dogs OK but must be leashed AND have their own reserved van seat. Books up fast, esp. weekends — reserve early. Cancel 48 hr (trailheads).",
         "$55 × 5 = $275  ($220 min)",
         "970-209-1568",
         "https://www.crestedbutteshuttle.com/summer",
         "crestedbutteshuttle.com")

service2("bus", "🟦 from TH",
         "Maroon Bells Shuttle (RFTA)",
         "Bus from Maroon Lake Trailhead down to Aspen Highlands Welcome Center (15 min). CB hikers buy the 'One-Way Return Only' ticket. Last bus down is 5:00 PM. Confirm leashed-dog policy when booking.",
         "$10 × 4 = $40  (+ dog?)",
         "—",
         "https://www.visitmaroonbells.com/maroon-bells-shuttle-reservations/",
         "visitmaroonbells.com")

blank()

# ════════════════════════════════════════════════════════════════════════════════
# RESERVATIONS CHECKLIST
r = row(["RESERVATIONS — book all three, in this order"])
merge(r); fmt(r, bg=SECT_BG, fg=WHITE, bold=True, size=12, align="CENTER")
heights.append((r, 26))

r = row(["#", "Book", "When", "", "Notes", "", ""])
merge(r, 2, 4); merge(r, 4, NCOLS)
fmt(r, bg=COL_HDR, fg=DARK, bold=True, align="CENTER")
heights.append((r, 24))

checklist = [
    ("1", "Maroon Bells Shuttles — car relocation", "Well in advance",
     "Reserve the vehicle move CB→Aspen. They send final logistics + tell you exactly where the car will be in Aspen. Coordinate that drop point with where the RFTA bus drops you (Aspen Highlands)."),
    ("2", "Dolly's Mountain Shuttle — ride to trailhead", "Early — books up, esp. weekends",
     "Reserve 5 seats: 4 people + Mochi. Book online (FareHarbor) or call 970-209-1568 / 970-209-9759. Cancellation 48 hr for trailheads."),
    ("3", "Maroon Bells RFTA bus — 'One-Way Return Only'", "2026 shuttle res open now",
     "$10 return ticket per hiker at visitmaroonbells.com. Pick a departure time at booking; the down-bus is first-come. Return portion is usable days later if plans slip."),
]
for n, item, when, note in checklist:
    r = row([n, item, when, "", note, "", ""])
    merge(r, 2, 4); merge(r, 4, NCOLS)
    fmt(r, bg=WHITE, fg=DARK, c0=0, c1=1, align="CENTER", bold=True)
    fmt(r, bg=NOTE_BG, fg=DARK, bold=True, c0=1, c1=2, align="LEFT")
    fmt(r, bg=WHITE, fg=DARK, c0=2, c1=4, align="LEFT")
    fmt(r, bg=WHITE, fg=DARK, c0=4, c1=NCOLS, align="LEFT")
    heights.append((r, 58))

blank()

# ════════════════════════════════════════════════════════════════════════════════
# DOG NOTES
r = row(["🐕  MOCHI NOTES"])
merge(r); fmt(r, bg=rgb(0, 131, 143), fg=WHITE, bold=True, size=11, align="CENTER")
heights.append((r, 24))
dog_notes = [
    "Trail: leashed dogs ARE allowed — this is USFS wilderness (Maroon Bells–Snowmass), not a National Park.",
    "Dolly's van: dog is welcome but needs its own reserved (paid) seat — count Mochi as a 5th seat.",
    "Maroon Bells RFTA bus: confirm the leashed-dog policy when you book the $10 return ticket.",
    "Fitness: 10.5 mi / +2,357 ft / 12,490 ft pass is a BIG day for a 2-yr-old golden. Doable for a fit dog — bring extra water + check paws on the rocky Aspen-side descent.",
]
for n in dog_notes:
    r = row(["•", n]); merge(r, 1, NCOLS)
    fmt(r, bg=WARN_BG, fg=DARK, c0=0, c1=1, align="CENTER", bold=True)
    fmt(r, bg=WARN_BG, fg=DARK, c0=1, c1=NCOLS, align="LEFT")
    heights.append((r, 32))

blank()

# ════════════════════════════════════════════════════════════════════════════════
# WHICH DAY
r = row(["WHICH CRESTED BUTTE DAY?  (this is a full-day commitment — pick one)"])
merge(r); fmt(r, bg=SECT_BG, fg=WHITE, bold=True, size=12, align="CENTER")
heights.append((r, 26))

day_notes = [
    ("Aug 10 (Mon)", "Currently Ian's bike-park day 1 + free Alpenglow Concert (5:30pm, CB Town Park). The hike replaces both, and a late Aspen dinner CONFLICTS with the concert. Upside: leaves Aug 11 free to pack."),
    ("Aug 11 (Tue)", "Currently bike-park day 2 + pack-up + last Elk Ave dinner. The hike turns 'last dinner' into dinner in Aspen and pushes packing to the morning of Aug 12 — tight before the ~6.5 hr drive to SLC."),
    ("Either way", "Aug 9 (arrive ~noon) and Aug 12 (drive out) are not options. Whichever day you choose, this displaces a bike-park day. Go EARLY to beat afternoon storms on the pass."),
]
for d, note in day_notes:
    r = row([d, "", note]); merge(r, 0, 2); merge(r, 2, NCOLS)
    fmt(r, bg=NOTE_BG, fg=DARK, bold=True, c0=0, c1=2, align="LEFT")
    fmt(r, bg=WHITE, fg=DARK, c0=2, c1=NCOLS, align="LEFT")
    heights.append((r, 48))

blank()

# ════════════════════════════════════════════════════════════════════════════════
# SOURCES
r = row(["SOURCES"]); merge(r); fmt(r, bg=COL_HDR, fg=DARK, bold=True, size=10, align="CENTER")
sources = [
    ("Dolly's Mountain Shuttle — summer / hike info", "https://www.crestedbutteshuttle.com/summer"),
    ("Maroon Bells Shuttles — car relocation reservations", "https://maroonbellsshuttles.com/reservations/"),
    ("Maroon Bells RFTA shuttle reservations", "https://www.visitmaroonbells.com/maroon-bells-shuttle-reservations/"),
    ("Travel Crested Butte — hike guide (stats + route)", "https://travelcrestedbutte.com/hike-crested-butte-to-aspen/"),
    ("AllTrails — CB to Aspen via West Maroon Pass", "https://www.alltrails.com/trail/us/colorado/crested-butte-to-aspen-via-west-maroon-pass"),
]
for label, url in sources:
    r = row([link(url, label)]); merge(r)
    fmt(r, bg=WHITE, fg=rgb(21, 101, 192), c0=0, c1=NCOLS, align="LEFT")
    heights.append((r, 22))

# ════════════════════════════════════════════════════════════════════════════════
# WRITE IT
if TAB in [w.title for w in sh.worksheets()]:
    sh.del_worksheet(sh.worksheet(TAB))
ws = sh.add_worksheet(title=TAB, rows=max(len(values) + 5, 60), cols=NCOLS)
sid = ws._properties['sheetId']

ws.update(values, "A1", value_input_option="USER_ENTERED")

# inject sheetId into every formatting range, plus merges, widths, heights
reqs = []
for f in fmts:
    f["repeatCell"]["range"]["sheetId"] = sid
    reqs.append(f)
for (r, c0, c1) in merges:
    reqs.append({"mergeCells": {
        "range": {"sheetId": sid, "startRowIndex": r, "endRowIndex": r + 1,
                  "startColumnIndex": c0, "endColumnIndex": c1},
        "mergeType": "MERGE_ALL"}})
widths = [120, 120, 120, 120, 120, 130, 190]
for i, px in enumerate(widths):
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
        "properties": {"pixelSize": px}, "fields": "pixelSize"}})
for (r, px) in heights:
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": r, "endIndex": r + 1},
        "properties": {"pixelSize": px}, "fields": "pixelSize"}})
# freeze the title row
reqs.append({"updateSheetProperties": {
    "properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}},
    "fields": "gridProperties.frozenRowCount"}})
# hide gridlines for a cleaner "map" look
reqs.append({"updateSheetProperties": {
    "properties": {"sheetId": sid, "gridProperties": {"hideGridlines": True}},
    "fields": "gridProperties.hideGridlines"}})

sh.batch_update({"requests": reqs})
from linkutil import nativize
n = nativize(sh, ws, sid, len(values), NCOLS)
print(f"OK: '{TAB}' tab built — {len(values)} rows, {len(reqs)} format/merge/dim requests, {n} native links.")
