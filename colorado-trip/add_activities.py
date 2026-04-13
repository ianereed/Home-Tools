import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
ws = sh.worksheet("Overnight Backpacking Options")

# Rename sheet
ws.update_title("Activities — Hikes, Runs & MTB")

# Resize to fit all new content (42 rows of data + buffer)
ws.resize(rows=50, cols=11)

# ── DATA ─────────────────────────────────────────────────────────────────────
HEADERS = ["Activity", "Area", "Date", "Type", "Distance", "Elevation Gain",
           "", "Extra Driving (RT)", "Trailhead", "Link", "Notes"]

hikes_boulder = [
    ["Chautauqua Meadow Walk", "Boulder", "Jul 22", "Day Hike", "1–2 mi", "~200 ft", "",
     "in town", "Chautauqua Park TH", "",
     "Easy opening day; dogs on leash"],
    ["Green Mountain via Gregory Canyon", "Boulder", "Jul 23", "Day Hike", "6 mi", "2,400 ft", "",
     "5 min", "Gregory Canyon TH", "",
     "$5 parking; start by 7am; classic Boulder hike"],
    ["Sanitas Valley Trail", "Boulder", "Jul 24", "Day Hike (Anny)", "2 mi", "~400 ft", "",
     "in town", "Mt Sanitas TH", "",
     "Easy; same TH as Ian's run; meet back at car"],
    ["Eldorado Canyon Trail", "Boulder", "Jul 27", "Day Hike", "6.7 mi", "1,978 ft", "",
     "20 min", "Eldorado Canyon SP", "",
     "TIMED ENTRY (cpw.state.co.us); $10/vehicle; watch climbers"],
    ["Flatirons Vista / Doudy Draw", "Boulder", "Jul 29", "Day Hike (Anny)", "3.4 mi", "~500 ft", "",
     "10 min", "Flatirons Vista TH", "",
     "Anny solo while Ian runs Walker Ranch; dog-friendly loop"],
    ["Lake Isabelle + Blue Lake", "Boulder", "Jul 31", "Day Hike", "7 mi", "~1,200 ft", "",
     "45 min", "Long Lake TH (Brainard)", "",
     "TIMED ENTRY (recreation.gov, opens ~Jul 16); best alpine near Boulder; dog-friendly"],
]

hikes_steamboat = [
    ["Fish Creek Falls", "Steamboat", "Aug 2", "Day Hike", "5 mi RT", "~900 ft", "",
     "5 min", "Fish Creek Falls TH", "",
     "Lower falls 0.5mi easy; upper falls 5mi; dogs off-leash past 0.25mi"],
    ["Emerald Mountain Blackmere Trail", "Steamboat", "Aug 3", "Day Hike (Anny)", "3.7 mi", "938 ft", "",
     "walkable", "Howelsen Hill", "",
     "Walkable from downtown; Anny solo while Ian bikes"],
    ["Hahns Peak", "Steamboat", "Aug 4", "Day Hike", "3 mi RT", "~900 ft", "",
     "30 min", "Hahns Peak TH", "",
     "Great summit views; pair with Fishhook Lake as a double"],
    ["Fishhook Lake", "Steamboat", "Aug 4", "Day Hike", "6 mi RT", "~1,200 ft", "",
     "30 min (same drive)", "Hahns Peak area", "",
     "Dog-friendly; combine with Hahns Peak"],
    ["Red Dirt Trail", "Steamboat", "Aug 6", "Day Hike (Anny)", "~8 mi", "gentle", "",
     "20 min", "Red Dirt TH", "",
     "Longest dog-friendly Steamboat trail; creeks + wildflowers"],
]

hikes_cb = [
    ["Emerald Lake", "Crested Butte", "Aug 8", "Day Hike (Anny)", "1.7 mi", "~350 ft", "",
     "5 min", "Gothic Rd TH", "",
     "Easy warm-up; dogs can swim at the lake"],
    ["Cedar Point Nature Trail", "Crested Butte", "Aug 9", "Day Hike", "1.2 mi", "~200 ft", "",
     "1.5 hrs (Black Canyon NP)", "South Rim Visitor Center", "",
     "Dog-friendly overlooks; part of Black Canyon day trip"],
    ["Oh-Be-Joyful Trail", "Crested Butte", "Aug 10", "Day Hike", "9.6 mi", "2,162 ft", "",
     "20 min", "Oh-Be-Joyful TH",
     "https://www.alltrails.com/trail/us/colorado/oh-be-joyful--3",
     "4.8 stars; most popular CB trail; dog-friendly"],
    ["Three Lakes Loop", "Crested Butte", "Aug 11", "Day Hike (Anny)", "3 mi", "~700 ft", "",
     "20 min", "Kebler Pass Rd", "",
     "Anny solo while Ian bikes; 3 alpine lakes + waterfall detour"],
]

trail_runs = [
    ["Mt Sanitas Loop", "Boulder", "Jul 24", "Trail Run (Ian)", "3.2 mi", "1,270 ft", "",
     "in town", "Mt Sanitas TH", "",
     "Ian solo AM; back by lunch; same TH as Anny's hike"],
    ["Walker Ranch Loop", "Boulder", "Jul 29", "Trail Run (Ian)", "7.6 mi", "~1,650 ft", "",
     "15 min", "Walker Ranch TH", "",
     "Ian solo AM; Anny drops at trailhead"],
    ["Emerald Mountain System", "Steamboat", "Aug 6", "Trail Run (Ian)", "6–8 mi", "~1,500 ft", "",
     "walkable", "Howelsen Hill", "",
     "Ian solo AM; back by lunch; flexible distance on network"],
]

mtb = [
    ["Valmont Bike Park", "Boulder", "Jul 26", "Bike Park (no lift)", "—", "—", "",
     "in town", "3100 Valmont Rd", "",
     "Free city park; pump tracks, dirt jumps, skills area"],
    ["Steamboat Bike Park", "Steamboat", "Aug 3", "Lift-Served DH/Enduro", "—", "2,200 ft vertical", "",
     "5 min", "Steamboat Resort", "",
     "$50–70/day; check Ikon Pass (2 free days)"],
    ["Evolution Bike Park", "Crested Butte", "Aug 8 & 11", "Lift-Served DH/Enduro", "—", "—", "",
     "5 min", "CBMR base", "",
     "World-class DH/enduro; ~$60–70/day; consider 2-day pass on Aug 8"],
]

# ── ROW POSITIONS ─────────────────────────────────────────────────────────────
# Backpacking: rows 1–9 (existing)
# Row 10: blank
# Row 11: HIKES section header
# Row 12: Boulder subsection
# Row 13: column headers
# Rows 14–19: Boulder hikes (6)
# Row 20: Steamboat subsection
# Rows 21–25: Steamboat hikes (5)
# Row 26: Crested Butte subsection
# Rows 27–30: CB hikes (4)
# Row 31: blank
# Row 32: TRAIL RUNS section header
# Row 33: column headers
# Rows 34–36: runs (3)
# Row 37: blank
# Row 38: MTB section header
# Row 39: column headers
# Rows 40–42: MTB (3)

ws.update(range_name="A11", values=[
    ["HIKES — Day Hikes by Area", "", "", "", "", "", "", "", "", "", ""],
    ["BOULDER  |  Jul 22–31", "", "", "", "", "", "", "", "", "", ""],
    HEADERS,
] + hikes_boulder + [
    ["STEAMBOAT  |  Aug 2–6", "", "", "", "", "", "", "", "", "", ""],
] + hikes_steamboat + [
    ["CRESTED BUTTE  |  Aug 8–11", "", "", "", "", "", "", "", "", "", ""],
] + hikes_cb + [
    ["", "", "", "", "", "", "", "", "", "", ""],
    ["TRAIL RUNS", "", "", "", "", "", "", "", "", "", ""],
    HEADERS,
] + trail_runs + [
    ["", "", "", "", "", "", "", "", "", "", ""],
    ["MTB / BIKE PARKS", "", "", "", "", "", "", "", "", "", ""],
    HEADERS,
] + mtb
)

# ── FORMATTING ────────────────────────────────────────────────────────────────
sheet_id = ws._properties['sheetId']

def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

def header_row_request(row_idx, bg_color, text_color=None, bold=True):
    """Format an entire row (0-indexed) with background color."""
    if text_color is None:
        text_color = rgb(255, 255, 255)
    return {
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                      "startColumnIndex": 0, "endColumnIndex": 11},
            "cell": {"userEnteredFormat": {
                "backgroundColor": bg_color,
                "textFormat": {"bold": bold, "foregroundColor": text_color},
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"
        }
    }

def merge_row(row_idx):
    return {
        "mergeCells": {
            "range": {"sheetId": sheet_id, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                      "startColumnIndex": 0, "endColumnIndex": 11},
            "mergeType": "MERGE_ALL"
        }
    }

# Colors
HIKE_DARK  = rgb(26,  82, 118)   # dark blue
HIKE_LIGHT = rgb(174, 214, 241)  # light blue (subsections)
RUN_DARK   = rgb(120,  66,   0)  # dark brown-orange
MTB_DARK   = rgb(69,  39, 160)   # dark purple
COL_HDR    = rgb(230, 230, 230)  # light grey for column headers

# Row indices (0-based)
R_HIKE_HDR    = 10   # row 11
R_BOULDER_SUB = 11   # row 12
R_BOULDER_COL = 12   # row 13 (col headers)
R_STEAM_SUB   = 19   # row 20
R_STEAM_COL   = None  # no separate col header for subsections
R_CB_SUB      = 25   # row 26
R_RUN_HDR     = 31   # row 32
R_RUN_COL     = 32   # row 33
R_MTB_HDR     = 37   # row 38
R_MTB_COL     = 38   # row 39

requests = []

# Merge + color section headers
for row_i, bg, is_sub in [
    (R_HIKE_HDR,    HIKE_DARK,  False),
    (R_BOULDER_SUB, HIKE_LIGHT, True),
    (R_STEAM_SUB,   HIKE_LIGHT, True),
    (R_CB_SUB,      HIKE_LIGHT, True),
    (R_RUN_HDR,     RUN_DARK,   False),
    (R_MTB_HDR,     MTB_DARK,   False),
]:
    requests.append(merge_row(row_i))
    text_color = rgb(30, 30, 30) if is_sub else rgb(255, 255, 255)
    requests.append(header_row_request(row_i, bg, text_color=text_color))

# Column header rows — grey + bold
for row_i in [R_BOULDER_COL, R_RUN_COL, R_MTB_COL]:
    requests.append(header_row_request(row_i, COL_HDR, text_color=rgb(30, 30, 30)))

sh.batch_update({"requests": requests})

print("Done. Sheet renamed + all sections added.")
