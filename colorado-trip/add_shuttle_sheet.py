import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

# Create new sheet
ws = sh.add_worksheet(title="MTB Shuttles & Guides", rows=60, cols=8)
sheet_id = ws._properties['sheetId']

# ── HELPERS ───────────────────────────────────────────────────────────────────
def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

def fmt_row(row_i, bg, text_color=rgb(255,255,255), bold=True, end_col=8):
    return {"repeatCell": {
        "range": {"sheetId": sheet_id,
                  "startRowIndex": row_i, "endRowIndex": row_i+1,
                  "startColumnIndex": 0, "endColumnIndex": end_col},
        "cell": {"userEnteredFormat": {
            "backgroundColor": bg,
            "textFormat": {"bold": bold, "foregroundColor": text_color},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"
    }}

def merge(row_i, end_col=8):
    return {"mergeCells": {
        "range": {"sheetId": sheet_id,
                  "startRowIndex": row_i, "endRowIndex": row_i+1,
                  "startColumnIndex": 0, "endColumnIndex": end_col},
        "mergeType": "MERGE_ALL"
    }}

def col_widths(widths):
    """widths = list of (col_index, pixels)"""
    return [{"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                  "startIndex": i, "endIndex": i+1},
        "properties": {"pixelSize": px},
        "fields": "pixelSize"
    }} for i, px in widths]

def wrap_col(start_col, end_col, start_row, end_row):
    return {"repeatCell": {
        "range": {"sheetId": sheet_id,
                  "startRowIndex": start_row, "endRowIndex": end_row,
                  "startColumnIndex": start_col, "endColumnIndex": end_col},
        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
        "fields": "userEnteredFormat(wrapStrategy)"
    }}

# ── COLORS ────────────────────────────────────────────────────────────────────
TITLE_BG   = rgb(23, 37, 84)     # very dark navy
CB_BG      = rgb(21, 101, 192)   # deep blue
STEAM_BG   = rgb(2, 119, 189)    # medium blue-teal
BOULD_BG   = rgb(0, 131, 143)    # teal
ACTION_BG  = rgb(183, 28, 28)    # dark red
COL_HDR    = rgb(230, 230, 230)  # light grey
DARK_TXT   = rgb(30, 30, 30)
WHITE      = rgb(255, 255, 255)

# ── DATA ──────────────────────────────────────────────────────────────────────
HEADERS = ["Service / Operator", "Area", "Dates", "Trails Served",
           "Cost", "Phone", "Website", "Notes / How to Book"]

# Row layout:
# 0:  title
# 1:  blank
# 2:  CRESTED BUTTE section header
# 3:  col headers
# 4:  Dolly's Mountain Shuttle
# 5:  Handlebar Bike & Board (backup)
# 6:  Mountain Express Bus
# 7:  blank
# 8:  STEAMBOAT section header
# 9:  col headers
# 10: Ride Workshop (guided Emerald Mtn)
# 11: Steamboat Powdercats
# 12: Flash of Gold self-shuttle note
# 13: blank
# 14: BOULDER section header
# 15: col headers
# 16: Front Range Ride Guides
# 17: No shuttle note
# 18: blank
# 19: PRIORITY ACTIONS section header
# 20: col headers (Action / Who / What to Ask / By When)
# 21: Call Dolly's
# 22: Call Ride Workshop
# 23: Call Front Range Ride Guides (optional)

cb_rows = [
    ["Dolly's Mountain Shuttle", "Crested Butte", "Aug 8–11", "401 Trail / Schofield Pass drop",
     "~$55/person\n($220 min)", "(970) 209-9757", "crestedbutteshuttle.com",
     "PRIMARY OPTION. Holds USFS permit for Schofield corridor. Drop at top of Schofield Pass Rd (FR-317); descend 401 singletrack back to Gothic Rd. Call to confirm they do this specific run + book early — August is peak season."],
    ["Handlebar Bike & Board", "Crested Butte", "Aug 8–11", "Schofield Pass / 401 (reported)",
     "Unknown", "(970) 251-9169", "handlebarcb.com",
     "BACKUP. Past reviews mention Schofield shuttles. Call to confirm if Dolly's is full or unavailable."],
    ["Mountain Express Bus (free)", "Crested Butte", "Aug 8–11", "Gothic Road corridor (not Schofield)",
     "Free", "(970) 349-5616", "mtnexp.org",
     "Carries bikes. Runs Crested Butte → Gothic Townsite 7 days/week, 4x daily, Jun 13–Sep 28. Does NOT reach Schofield Pass — not useful for the 401, but good for Snodgrass / Judd Falls area rides."],
]

steamboat_rows = [
    ["Ride Workshop", "Steamboat", "Aug 2–6", "Emerald Mountain (guided)",
     "$150/person\n(bike rental incl.)", "(970) 367-3517", "rideworkshop.co",
     "Permitted operator on Emerald Mountain. Guided tours Mon–Sat, 10am (or private start). Max 4 riders. Call to ask about Flash of Gold / Buffalo Pass logistics — they know the scene and may advise on self-shuttle setup."],
    ["Steamboat Powdercats", "Steamboat", "Aug 2–6", "Emerald Mountain (guided)",
     "Unknown — call", "N/A", "steamboatpowdercats.com",
     "Second permitted operator on Emerald Mtn. Buffalo Pass ski background — worth asking about Flash of Gold / Grouse Ridge logistics specifically."],
    ["Flash of Gold → Grouse Ridge\n(self-shuttle only)", "Steamboat", "Aug 2–6", "Buffalo Pass → Spring Creek (town)",
     "N/A", "N/A", "N/A",
     "NO COMMERCIAL SHUTTLE EXISTS. USFS has not issued permits for Buffalo Pass Rd. Self-shuttle with 2 vehicles: leave one at Spring Creek trailhead in town, drive second up Buffalo Pass Rd to TH (4WD recommended for upper section, ~25 min). Locals do this regularly."],
]

boulder_rows = [
    ["Front Range Ride Guides", "Boulder", "Jul 22–31", "Custom — Walker Ranch, Hall Ranch possible",
     "From $129/person\n(half-day)", "(720) 470-1627", "frontrangerideguides.com",
     "Guided tours with transport — not a bare shuttle drop. Custom itineraries; call to ask if they'll do Walker Ranch or Hall Ranch during your July window. Not required since both are loops."],
    ["No dedicated MTB shuttle", "Boulder", "Jul 22–31", "Walker Ranch / Hall Ranch",
     "N/A", "N/A", "N/A",
     "Walker Ranch and Hall Ranch are both loops (start = end). No shuttle geography needed. Drive yourself to the TH: Walker Ranch ~15 min via Boulder Canyon; Hall Ranch ~35 min to Lyons on CO-7."],
]

action_headers = ["Priority", "Who to Call", "Phone", "Key Question to Ask", "", "", "", "When"]
action_rows = [
    ["🔴  1 — BOOK NOW", "Dolly's Mountain Shuttle (CB)", "(970) 209-9757",
     "Do you do the Schofield Pass drop for the 401 trail? What's the current price and availability for Aug 8–11?",
     "", "", "", "ASAP — August books up"],
    ["🟡  2 — INFORM", "Ride Workshop (Steamboat)", "(970) 367-3517",
     "Can you advise on self-shuttle logistics for Flash of Gold → Grouse Ridge at Buffalo Pass? Any informal options?",
     "", "", "", "Before Aug 2"],
    ["⚪  3 — OPTIONAL", "Front Range Ride Guides (Boulder)", "(720) 470-1627",
     "Can you do a guided MTB tour to Walker Ranch or Hall Ranch in late July? What's the half-day rate?",
     "", "", "", "If interested"],
]

# ── WRITE DATA ────────────────────────────────────────────────────────────────
all_rows = [
    ["MTB Shuttles & Guided Rides — Colorado 2026", "", "", "", "", "", "", ""],  # 0
    ["", "", "", "", "", "", "", ""],                                               # 1
    ["CRESTED BUTTE  |  Aug 8–11", "", "", "", "", "", "", ""],                    # 2
    HEADERS,                                                                        # 3
] + cb_rows + [                                                                    # 4-6
    ["", "", "", "", "", "", "", ""],                                               # 7
    ["STEAMBOAT SPRINGS  |  Aug 2–6", "", "", "", "", "", "", ""],                 # 8
    HEADERS,                                                                        # 9
] + steamboat_rows + [                                                             # 10-12
    ["", "", "", "", "", "", "", ""],                                               # 13
    ["BOULDER  |  Jul 22–31", "", "", "", "", "", "", ""],                         # 14
    HEADERS,                                                                        # 15
] + boulder_rows + [                                                               # 16-17
    ["", "", "", "", "", "", "", ""],                                               # 18
    ["PRIORITY ACTIONS — Calls to Make", "", "", "", "", "", "", ""],              # 19
    action_headers,                                                                 # 20
] + action_rows                                                                    # 21-23

ws.update(range_name="A1", values=all_rows)

# ── FORMATTING ────────────────────────────────────────────────────────────────
requests = []

# Merge + color all section/title headers
for row_i, bg, is_sub in [
    (0,  TITLE_BG,  False),
    (2,  CB_BG,     False),
    (8,  STEAM_BG,  False),
    (14, BOULD_BG,  False),
    (19, ACTION_BG, False),
]:
    requests.append(merge(row_i))
    requests.append(fmt_row(row_i, bg, text_color=WHITE, bold=True))

# Column header rows (grey)
for row_i in [3, 9, 15, 20]:
    requests.append(fmt_row(row_i, COL_HDR, text_color=DARK_TXT, bold=True))

# Column widths: A=200, B=120, C=100, D=180, E=130, F=140, G=180, H=300
requests += col_widths([
    (0, 200), (1, 110), (2, 90), (3, 185),
    (4, 130), (5, 140), (6, 180), (7, 310)
])

# Wrap text for Notes column (H) and Trails column (D)
requests.append(wrap_col(3, 4, 3, 24))   # Trails col
requests.append(wrap_col(7, 8, 3, 24))   # Notes col
requests.append(wrap_col(4, 5, 3, 24))   # Cost col

sh.batch_update({"requests": requests})
print("Done. MTB Shuttles & Guides sheet created.")
