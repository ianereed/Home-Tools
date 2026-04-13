import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
ws = sh.worksheet("Activities — Hikes, Runs & MTB")

# Current data ends at row 42. Add trail rides below.
# Row 43: blank gap
# Row 44: subsection header
# Row 45: col headers
# Rows 46–51: 6 trail rides

ws.resize(rows=55, cols=11)

HEADERS = ["Activity", "Area", "Date Window", "Type", "Distance", "Elevation Gain",
           "", "Extra Driving (RT)", "Trailhead", "Link", "Notes"]

trail_rides = [
    # Boulder
    ["Walker Ranch Loop", "Boulder", "Jul 22–31", "Trail Ride (loop)", "8 mi", "1,560 ft", "",
     "15 min", "Walker Ranch Park TH (Boulder Canyon)",
     "https://www.trailforks.com/region/boulder/",
     "Classic Boulder enduro — fast loose descents, steep punchy climbs; also Ian's trail run day (can double-dip or pick one)"],
    ["Hall Ranch", "Boulder", "Jul 22–31", "Trail Ride (loop)", "9–10 mi", "1,500 ft", "",
     "35 min (near Lyons)", "Hall Ranch TH (CO-7 past Lyons)",
     "https://www.trailforks.com/region/boulder/",
     "Technical singletrack with zippy descents; less crowded than in-town trails; great for an AM push"],

    # Steamboat
    ["Flash of Gold → Grouse Ridge", "Steamboat", "Aug 2–6", "Trail Ride (point-to-point/loop)", "~21 mi", "~2,300 ft", "",
     "~25 min (Buffalo Pass Rd)",
     "Buffalo Pass TH",
     "https://www.trailforks.com/region/steamboat-springs/",
     "Steamboat's premier enduro ride; Grouse Ridge descent = 1,500 ft in 3.5 mi of technical rock features; may need shuttle or early start for half-day"],
    ["Emerald Mountain (Beall / Ridge / Rotary)", "Steamboat", "Aug 2–6", "Trail Ride (loop)", "~16 mi", "~2,170 ft", "",
     "walkable / 5 min",
     "Howelsen Hill / Emerald Mtn TH",
     "https://www.trailforks.com/region/steamboat-springs/",
     "Accessible from town; interconnected network; flowy alpine terrain with good views — solid AM loop option"],

    # Crested Butte
    ["401 Trail Loop", "Crested Butte", "Aug 8–11", "Trail Ride (loop)", "~14 mi", "~2,300 ft", "",
     "~20 min (Schofield Pass / Gothic Rd)",
     "Copper Creek / Gothic TH",
     "https://www.trailforks.com/region/crested-butte/",
     "CB's most iconic ride; sustained gravel climb up Schofield then 10mi flowing singletrack descent through wildflowers + aspen; pure enduro classic"],
    ["Reno–Flag–Bear–Deadman Loop", "Crested Butte", "Aug 8–11", "Trail Ride (loop)", "~13 mi", "~2,400 ft", "",
     "~15 min (Cement Creek Rd)",
     "Deadman's Gulch TH (Cement Creek Rd, 7.5mi east of Hwy 135)",
     "https://www.trailforks.com/region/crested-butte/",
     "CB classic with 3 climbs + 3 descents; reaches 11,121 ft; Flag Creek descent is fast + flowy; stream crossings"],
]

# Write blank separator + subsection header + headers + data
rows_to_write = (
    [["", "", "", "", "", "", "", "", "", "", ""]],       # row 43: blank
    [["TRAIL RIDES — Singletrack & Enduro Loops",         # row 44: subsection header
      "", "", "", "", "", "", "", "", "", ""]],
    [HEADERS],                                            # row 45: col headers
)
flat = [r[0] if isinstance(r[0], list) else r for r in rows_to_write]
# Flatten properly
data = (
    [["", "", "", "", "", "", "", "", "", "", ""]] +
    [["TRAIL RIDES — Singletrack & Enduro Loops", "", "", "", "", "", "", "", "", "", ""]] +
    [HEADERS] +
    trail_rides
)
ws.update(range_name="A43", values=data)

# ── FORMATTING ────────────────────────────────────────────────────────────────
sheet_id = ws._properties['sheetId']

def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

MTB_MID   = rgb(103, 58, 183)   # medium purple for trail rides sub-header
COL_HDR   = rgb(230, 230, 230)  # grey col headers

requests = [
    # Merge trail rides sub-header row (row 44 = index 43)
    {
        "mergeCells": {
            "range": {"sheetId": sheet_id,
                      "startRowIndex": 43, "endRowIndex": 44,
                      "startColumnIndex": 0, "endColumnIndex": 11},
            "mergeType": "MERGE_ALL"
        }
    },
    # Color trail rides sub-header
    {
        "repeatCell": {
            "range": {"sheetId": sheet_id,
                      "startRowIndex": 43, "endRowIndex": 44,
                      "startColumnIndex": 0, "endColumnIndex": 11},
            "cell": {"userEnteredFormat": {
                "backgroundColor": MTB_MID,
                "textFormat": {"bold": True, "foregroundColor": rgb(255, 255, 255)},
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"
        }
    },
    # Color col header row (row 45 = index 44)
    {
        "repeatCell": {
            "range": {"sheetId": sheet_id,
                      "startRowIndex": 44, "endRowIndex": 45,
                      "startColumnIndex": 0, "endColumnIndex": 11},
            "cell": {"userEnteredFormat": {
                "backgroundColor": COL_HDR,
                "textFormat": {"bold": True, "foregroundColor": rgb(30, 30, 30)},
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"
        }
    },
]

sh.batch_update({"requests": requests})
print("Done. Trail rides added.")
