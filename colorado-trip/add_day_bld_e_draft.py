"""DRAFT: a fully-detailed single-day tab for option BLD-E (separate day:
Ian bikes Valmont / Anny + Mochi hike Wonderland Lake). Demonstrates what a
per-day tab looks like — real AllTrails (hike) + Trailforks (MTB) links, clickable
map addresses, parking, phone numbers, one-car choreography. Non-destructive;
re-runnable (deletes + recreates the tab).
"""
import re
import urllib.parse
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

TAB = "Day BLD-E — Boulder (DRAFT)"
NCOLS = 6  # A..F
BASE_ADDR = "582 Locust Place, Boulder, CO 80304"  # Boulder Airbnb

def dirlink(dest, label):
    u = ("https://www.google.com/maps/dir/?api=1&origin="
         + urllib.parse.quote(BASE_ADDR) + "&destination=" + urllib.parse.quote(dest))
    return f'=HYPERLINK("{u}","{label}")'

# Public screenshot of the full-day Google Map (user-authorized publish to the public repo)
MAP_IMG_URL = "https://raw.githubusercontent.com/ianereed/Home-Tools/main/colorado-trip/maps/bld-e-day-map-v3.png"

def day_map_url():
    base = urllib.parse.quote(BASE_ADDR)
    wp = ["Wonderland Lake Trailhead, 4201 N Broadway, Boulder, CO",
          "Valmont Bike Park, 3160 Airport Road, Boulder, CO",
          "Avery Brewing, 4910 Nautilus Ct N, Boulder, CO",
          "Chautauqua Auditorium, 900 Baseline Rd, Boulder, CO"]
    return ("https://www.google.com/maps/dir/?api=1&origin=" + base + "&destination=" + base
            + "&waypoints=" + "%7C".join(urllib.parse.quote(w) for w in wp) + "&travelmode=driving")

img_block = None  # (start_row, end_row) for the embedded map, merged in the final section

def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

TITLE_BG = rgb(23, 37, 84)
SUB_BG   = rgb(40, 60, 110)
NAVY     = rgb(40, 60, 110)
BLUE     = rgb(2, 119, 189)      # Ian / MTB
GREEN    = rgb(46, 125, 50)      # Anny+Mochi / hike
ORANGE   = rgb(230, 145, 30)     # lunch
TEAL     = rgb(0, 131, 143)      # evening
GREY     = rgb(97, 97, 97)       # logistics
LABEL_BG = rgb(238, 240, 243)
WHITE    = rgb(255, 255, 255)
DARK     = rgb(33, 33, 33)
LINKC    = rgb(21, 101, 192)
WARN_BG  = rgb(255, 248, 225)

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

def link(url, label):
    return f'=HYPERLINK("{url}","{label.replace(chr(34), chr(39))}")'

def gmap(addr):
    return link("https://maps.google.com/?q=" + urllib.parse.quote(addr), addr)

def blank(px=8):
    heights.append((row([""]), px))

def section(label, bg):
    r = row([label]); merge(r); fmt(r, bg=bg, fg=WHITE, bold=True, size=12, align="CENTER"); heights.append((r, 28))

def kv(label, value, h=30, link_value=False):
    r = row([label, "", value]); merge(r, 0, 2); merge(r, 2, NCOLS)
    fmt(r, bg=LABEL_BG, fg=DARK, bold=True, c0=0, c1=2, align="LEFT", valign="TOP")
    fmt(r, bg=WHITE, fg=(LINKC if link_value else DARK), c0=2, c1=NCOLS, align="LEFT", valign="TOP")
    heights.append((r, h))

def tline(time, what, h=30):
    r = row([time, "", what]); merge(r, 0, 2); merge(r, 2, NCOLS)
    fmt(r, bg=rgb(232, 240, 254), fg=DARK, bold=True, c0=0, c1=2, align="LEFT", valign="TOP")
    fmt(r, bg=WHITE, fg=DARK, c0=2, c1=NCOLS, align="LEFT", valign="TOP")
    heights.append((r, h))

# ── TITLE ───────────────────────────────────────────────────────────────────────
r = row(["BLD-E · SEPARATE DAY"])
merge(r); fmt(r, bg=TITLE_BG, fg=WHITE, bold=True, size=15, align="CENTER"); heights.append((r, 34))
r = row(["Ian bikes Valmont Bike Park  ·  Anny + Mochi hike Wonderland Lake  ·  lunch together  ·  Music Festival evening"])
merge(r); fmt(r, bg=SUB_BG, fg=WHITE, italic=True, size=10, align="CENTER"); heights.append((r, 30))
r = row(["Boulder · a flexible day (pick from the menu). One car between us. Best on a dry morning — Valmont closes when wet."])
merge(r); fmt(r, bg=WHITE, fg=GREY, italic=True, size=9, align="CENTER"); heights.append((r, 24))
blank()

# ── MAP OF THE DAY ───────────────────────────────────────────────────────────────
section("🗺️  MAP OF THE DAY  ·  all 5 stops + the driving loop", NAVY)
r = row([link(day_map_url(), "▶ Open the live Google Map — full-day route (≈54 min / 22 mi loop)")])
merge(r); fmt(r, bg=WHITE, fg=LINKC, bold=True, align="CENTER"); heights.append((r, 24))
img_top = row([f'=IMAGE("{MAP_IMG_URL}", 1)'])
IMG_ROWS = 17
for _ in range(IMG_ROWS - 1):
    row([""])
for rr in range(img_top, img_top + IMG_ROWS):
    heights.append((rr, 38))  # ~646px tall so the landscape map fills the content width
fmt(img_top, bg=WHITE, c0=0, c1=NCOLS, align="CENTER", valign="MIDDLE")
img_block = (img_top, img_top + IMG_ROWS)
r = row(["Stops:  A 582 Locust Pl (home)  ·  B Wonderland Lake  ·  C Valmont Bike Park  ·  D Avery Brewing  ·  E Chautauqua  ·  back home"])
merge(r); fmt(r, bg=rgb(247, 247, 247), fg=GREY, italic=True, size=9, align="CENTER"); heights.append((r, 22))
blank()

# ── AT A GLANCE ──────────────────────────────────────────────────────────────────
section("AT A GLANCE", NAVY)
kv("Home base", gmap(BASE_ADDR), link_value=True)
kv("Type", "Separate day — split AM, together for lunch + evening")
kv("Reservations", "None needed (both are free, no timed entry)")
kv("Mochi", "Stays with Anny all morning (leashed). Home at the Airbnb for the evening concert.")
kv("One car", "Drop Ian at Valmont, Anny keeps the car — the two spots are ~10 min / 4 mi apart. Full plan at the bottom.")
kv("Weather backup", "Valmont wet/closed → Ian rope-climbs at Movement. Storms → Anny shortens to the Wonderland Lake loop only.")
blank()

# ── TIMELINE ─────────────────────────────────────────────────────────────────────
section("THE DAY", NAVY)
tline("8:00 AM", "Drive out together. Drop Ian + bike at Valmont Bike Park (3160 Airport Rd).")
tline("8:15 AM", "Anny + Mochi drive ~10 min to the Wonderland Lake trailhead (4201 N Broadway) and start hiking.")
tline("8:15–11:30", "Ian sessions Valmont — pump tracks, dirt jumps, slopestyle, dual slalom, XC singletrack.")
tline("8:30–10:00", "Anny + Mochi: Wonderland Lake loop (add the Foothills extension if feeling good).")
tline("~11:45", "Anny + Mochi swing back and pick Ian up at Valmont (Avery Brewing is ~5 min away).")
tline("12:00 PM", "Lunch at Avery Brewing — dog-friendly patio, all three of us.")
tline("Afternoon", "Easy: Pearl St / errands / Airbnb downtime through the heat of the day. Mochi naps.")
tline("6:00 PM", "Colorado Music Festival at Chautauqua Auditorium (Mochi stays at the Airbnb — no pets at the venue).")
blank()

# ── IAN: VALMONT ─────────────────────────────────────────────────────────────────
section("🚵  IAN — VALMONT BIKE PARK", BLUE)
kv("Where", gmap("Valmont Bike Park, 3160 Airport Road, Boulder, CO 80301"), link_value=True)
kv("Drive from base", dirlink("Valmont Bike Park, 3160 Airport Road, Boulder, CO 80301", "~12 min from 582 Locust Pl (tap for live time)"), link_value=True)
kv("Cost / hours", "Free. Open daily dawn–dusk. CLOSES when wet — check the Valmont Bike Park Facebook/X feed the morning of.")
kv("Trail map (MTB)", link("https://www.trailforks.com/region/valmont-bike-park/", "Trailforks — Valmont Bike Park (trails, status, conditions)"), link_value=True)
kv("Official map", link("https://bouldercolorado.gov/valmont-bike-park-trail-map", "City of Boulder — interactive Valmont trail map"), link_value=True)
kv("What it is", "42-acre terrain park: progression pump tracks, dirt-jump lines, slopestyle, dual slalom, cyclocross + XC singletrack. Something for warm-up to send.")
kv("Heads-up", "In-town + free = no daycare or shuttle logistics. Bring your own water; limited shade.")
blank()

# ── ANNY + MOCHI: WONDERLAND LAKE ────────────────────────────────────────────────
section("🥾  ANNY + MOCHI — WONDERLAND LAKE", GREEN)
kv("Trailhead", gmap("Wonderland Lake Trailhead, 4201 North Broadway, Boulder, CO 80304"), link_value=True)
kv("Drive from base", dirlink("Wonderland Lake Trailhead, 4201 North Broadway, Boulder, CO 80304", "~5 min from 582 Locust Pl (tap for live time)"), link_value=True)
kv("Parking", "Free paved lot at the trailhead (adjacent to the Foothills Nature Center). Arrive early on a summer Sat.")
kv("Route — easy", link("https://www.alltrails.com/trail/us/colorado/wonderland-lake-trail", "AllTrails — Wonderland Lake Trail (2.0 mi loop, ~90 ft, easy)"), link_value=True)
kv("Route — a bit more", link("https://www.alltrails.com/trail/us/colorado/wonderland-lake-trail-and-foothills-loop", "AllTrails — Wonderland Lake + Foothills Loop (2.5 mi, moderate)"), link_value=True)
kv("Dog rules", "Leashed dogs OK (Boulder OSMP). Wonderland Lake is a protected wildlife area — keep Mochi on the trail, out of the lake.")
kv("Why this one", "Flat, fast, shaded stretches, foothills + reservoir views, and 4 min from Valmont so the pickup is easy.")
blank()

# ── LUNCH ────────────────────────────────────────────────────────────────────────
section("🍔  LUNCH — AVERY BREWING CO.", ORANGE)
kv("Where", gmap("Avery Brewing Co, 4910 Nautilus Ct N, Boulder, CO 80301"), link_value=True)
kv("Drive from base", dirlink("Avery Brewing Co, 4910 Nautilus Ct N, Boulder, CO 80301", "~15 min from 582 Locust Pl (tap for live time)"), link_value=True)
kv("Phone", "(303) 440-4324")
kv("Why", "Big dog-friendly patio, full food menu, 30 taps. ~5 min from Valmont — natural regroup spot. First-come seating.")
kv("More options", "See the Dining Guide tab for other dog-friendly Boulder spots.")
blank()

# ── EVENING ──────────────────────────────────────────────────────────────────────
section("🎶  EVENING — COLORADO MUSIC FESTIVAL", TEAL)
kv("Where", gmap("Chautauqua Auditorium, 900 Baseline Road, Boulder, CO 80302"), link_value=True)
kv("Drive from base", dirlink("Chautauqua Auditorium, 900 Baseline Road, Boulder, CO 80302", "~13 min from 582 Locust Pl (tap for live time)"), link_value=True)
kv("Tickets", link("https://coloradomusicfestival.org", "coloradomusicfestival.org — check the night's program + book ahead"), link_value=True)
kv("Mochi", "Stays at the Airbnb (A/C) — no pets at the venue. Evening is cool, easy walk to the car.")
blank()

# ── ONE-CAR CHOREOGRAPHY ─────────────────────────────────────────────────────────
section("🚗  ONE-CAR PLAN (the logistics)", GREY)
steps = [
    "Leave the Airbnb together ~8:00. First stop Valmont (3160 Airport Rd) — Ian + bike out.",
    "Anny + Mochi keep the car, drive ~10 min / 4 mi to Wonderland Lake (4201 N Broadway), hike.",
    "Anny finishes first (shorter), drives back toward Valmont. Text Ian a pickup time (~11:45).",
    "Pick Ian up at Valmont → 5 min to Avery Brewing for lunch (dog patio).",
    "Afternoon back at the Airbnb. Evening: drive to Chautauqua for the concert; Mochi stays home.",
]
for i, s in enumerate(steps, 1):
    r = row([str(i), "", s]); merge(r, 0, 2); merge(r, 2, NCOLS)
    fmt(r, bg=LABEL_BG, fg=DARK, bold=True, c0=0, c1=2, align="CENTER", valign="TOP")
    fmt(r, bg=WHITE, fg=DARK, c0=2, c1=NCOLS, align="LEFT", valign="TOP")
    heights.append((r, 30))
blank()

r = row(["← This is the detail behind one menu line. Back to the menu: 'DAY OPTIONS (DRAFT)' tab, option BLD-E."])
merge(r); fmt(r, bg=WARN_BG, fg=rgb(120, 70, 0), italic=True, align="LEFT"); heights.append((r, 28))

# ════════════════════════════════════════════════════════════════════════════════
if TAB in [w.title for w in sh.worksheets()]:
    sh.del_worksheet(sh.worksheet(TAB))
ws = sh.add_worksheet(title=TAB, rows=max(len(values)+5, 60), cols=NCOLS)
sid = ws._properties['sheetId']
ws.update(values, "A1", value_input_option="USER_ENTERED")

reqs = []
for f in fmts:
    f["repeatCell"]["range"]["sheetId"] = sid
    reqs.append(f)
for (r, c0, c1) in merges:
    reqs.append({"mergeCells": {"range": {"sheetId": sid, "startRowIndex": r, "endRowIndex": r+1,
        "startColumnIndex": c0, "endColumnIndex": c1}, "mergeType": "MERGE_ALL"}})
if img_block:  # rectangular merge holding the embedded map image
    reqs.append({"mergeCells": {"range": {"sheetId": sid,
        "startRowIndex": img_block[0], "endRowIndex": img_block[1],
        "startColumnIndex": 0, "endColumnIndex": NCOLS}, "mergeType": "MERGE_ALL"}})
widths = [70, 120, 130, 130, 130, 210]
for i, px in enumerate(widths):
    reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS",
        "startIndex": i, "endIndex": i+1}, "properties": {"pixelSize": px}, "fields": "pixelSize"}})
for (r, px) in heights:
    reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS",
        "startIndex": r, "endIndex": r+1}, "properties": {"pixelSize": px}, "fields": "pixelSize"}})
reqs.append({"updateSheetProperties": {"properties": {"sheetId": sid,
    "gridProperties": {"frozenRowCount": 1, "hideGridlines": True}},
    "fields": "gridProperties.frozenRowCount,gridProperties.hideGridlines"}})

sh.batch_update({"requests": reqs})

# Convert every =HYPERLINK formula into a NATIVE rich-text link (textFormatRuns +
# link.uri). Sheets won't linkify long Maps `dir` URLs from a HYPERLINK formula
# (renders as plain text), so native links are the reliable, always-clickable form.
formulas = ws.get("A1:H{}".format(len(values) + 1), value_render_option="FORMULA")
pat = re.compile(r'^=HYPERLINK\("(.+?)","(.*)"\)$', re.S)
link_reqs = []
for i, frow in enumerate(formulas):
    for j, cellv in enumerate(frow):
        if isinstance(cellv, str) and cellv.startswith("=HYPERLINK("):
            m = pat.match(cellv)
            if not m:
                continue
            url, label = m.group(1), m.group(2)
            link_reqs.append({"updateCells": {
                "rows": [{"values": [{
                    "userEnteredValue": {"stringValue": label},
                    "textFormatRuns": [{"startIndex": 0, "format": {
                        "link": {"uri": url}, "underline": True, "foregroundColor": LINKC}}],
                }]}],
                "fields": "userEnteredValue,textFormatRuns",
                "start": {"sheetId": sid, "rowIndex": i, "columnIndex": j}}})
if link_reqs:
    sh.batch_update({"requests": link_reqs})
print(f"OK: '{TAB}' built — {len(values)} rows, {len(reqs)} requests, {len(link_reqs)} native links.")
