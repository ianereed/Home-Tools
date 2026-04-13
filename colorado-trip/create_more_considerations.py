import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

ws = sh.add_worksheet(title="More Things to Consider", rows=30, cols=8)
sheet_id = ws._properties['sheetId']

def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

def fmt_row(row_i, bg, text_color=None, bold=True, end_col=8):
    if text_color is None:
        text_color = rgb(255, 255, 255)
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
TITLE_BG    = rgb(15,  23,  42)   # very dark navy
ADDON_BG    = rgb(27,  94,  32)   # dark forest green
PRACTICAL_BG= rgb(109,  76,   0)  # dark amber
CABIN_BG    = rgb(0,   96, 100)   # dark teal
COL_HDR     = rgb(230, 230, 230)
DARK_TXT    = rgb(30,  30,  30)
WHITE       = rgb(255, 255, 255)

# ── DATA ──────────────────────────────────────────────────────────────────────

# Section 1: Possible Add-Ons
# Cols: A=What | B=When / Where | C=Description | D=Action Required | E-H unused
addon_headers = ["What", "When / Where", "Description", "Action Required", "", "", "", ""]

addons = [
    ["Maroon Bells Day Trip",
     "Aug 8–11\n(day trip from CB, ~1.5 hrs via Glenwood Springs + CO-82)",
     "Probably the most iconic mountain view in Colorado — two 14,000-ft peaks reflected in a lake. Dogs allowed on leash. Crater Lake hike: 3.6 mi, dog-friendly, moderate. The classic view from Maroon Lake is one you'll know immediately. Best early morning (light + fewer people).",
     "BOOK TIMED ENTRY NOW at recreation.gov. Mandatory late June–mid-October. Parking reservations fill weeks out in August. Select a date Aug 8–11 for the morning window.", "", "", "", ""],
    ["Arkansas River Half-Day Raft",
     "Aug 8–11\n(~1.5 hrs from CB via US-285, Salida/Buena Vista area)",
     "Colorado's classic whitewater — Browns Canyon National Monument, Class III–IV on the Arkansas River. Several outfitters offer 2-3 hr half-day morning floats. A different kind of adventure that gives Anny something more active to do while Ian has bike park days.",
     "Call outfitters for Aug 8–11 availability: Ark Outfitters (arkoutfitters.com), Browns Canyon Expeditions. Ask if dogs allowed on float trips — some mellow sections permit it.", "", "", "", ""],
    ["Nevada Northern Railway Weekend Excursion",
     "Ely, NV — any future visit on a Sat/Sun",
     "On this trip you arrive Ely on Thursday (Aug 13) so only the static museum is available. But: if you ever route through Ely on a Saturday or Sunday, the 90-minute steam locomotive excursion is worth doing — one of the last fully intact short-line railroad operations in the US running original 1906 equipment through the Nevada desert.",
     "No action for this trip. If you return: book at nnry.com/excursions. Weekend excursions run most Sat/Sun May–Oct.", "", "", "", ""],
    ["Tioga Pass: Olmsted Point + Tenaya Lake",
     "Aug 18\n(already in itinerary — drive Mammoth → Fresno via Yosemite)",
     "The Aug 18 drive already goes through Tioga Pass. Two specific stops worth 30 min total: Olmsted Point (sweeping overhead view of Half Dome from above; no hiking, just pullout) and Tenaya Lake (stunning alpine lake, dogs allowed at the shoreline for a quick swim). Both are direct pullouts off Tioga Rd.",
     "Already in the plan. Just don't skip these two pullouts — they're the payoff for the Yosemite entrance fee.", "", "", "", ""],
]

# Section 2: Practical Notes
# Cols: A=Topic | B=When It Applies | C=Details | D=What to Do | E-H unused
practical_headers = ["Topic", "When It Applies", "Details", "What to Do", "", "", "", ""]

practical = [
    ["Altitude Acclimatization",
     "Aug 7–8 (arrival at Crested Butte)",
     "CB town is at 8,909 ft — a big jump from Steamboat (6,732 ft) in one day. Ian has the bike park booked for Aug 8, the morning after arrival. Riding lift-served enduro at 9,000–11,000 ft your first morning can mean headaches, heavier breathing, and slower recovery. Altitude also amplifies alcohol dehydration.",
     "Plan Aug 8 as a lighter bike park day (explore, don't go full-send). Drink extra water Aug 7–8. Skip or limit alcohol on arrival night (Aug 7). The difference in recovery between day 1 and day 2 at altitude is significant."],
    ["Wildfire Smoke",
     "Whole trip — peaks in August",
     "August is peak western wildfire season. CB, Steamboat, and Boulder have all seen significant smoke days in recent summers from California, Oregon, and Wyoming fires. Can arrive fast, last 2–5 days, and make outdoor activity uncomfortable or unhealthy (AQI 150+).",
     "Check AirNow.gov each morning in August. Have an indoor backup plan for each city: Boulder → Dushanbe Teahouse, Pearl St, Movement climbing. Steamboat → Laundry Kitchen, Old Town Hot Springs. CB → Montanya Distillers, Elk Ave gallery crawl, a long lunch at Secret Stash."],
    ["Bear Canisters (Rae Lakes Loop)",
     "Aug 19–22",
     "Kings Canyon NPS requires bear canisters for all overnight trips — no soft-sided containers. You cannot hang food. This is strictly enforced. Note: Mochi is not with you on this trip (boarded in Fresno). Standard party of 2 needs 2 canisters or one large.",
     "Rent or buy bear canisters before Aug 19. Rental available at the Roads End Permit Station (Cedar Grove, Kings Canyon, ~5 mi from Rae Lakes trailhead). Garcia Canister or BearVault BV500 are standard. Check NPS permit conditions for any updates on water sources and campsite rules."],
    ["Mammoth Altitude",
     "Aug 15–17 (Mammoth stays)",
     "Mammoth Lakes sits at 7,880 ft and the bike park reaches 11,053 ft at the summit. Coming from SLC (~4,200 ft), Ian may notice altitude on the first bike park day. Lower Rock Creek Canyon trail is at lower elevation (~6,000–7,000 ft) — a better first-day option than going straight to the park.",
     "If Ian goes to the bike park Aug 15 (first day after SLC), go easy on the early runs and drink water. Consider Lower Rock Creek or Mammoth Rock trail on Aug 15, bike park on Aug 16 after a night of acclimation."],
]

# Section 3: Cabin Evaluation Checklist
# Cols: A=Task | B=Do In... | C=Details | D=Notes | E-H unused
cabin_headers = ["Task", "Do In...", "How / Details", "Notes", "", "", "", ""]

cabin = [
    ["Grocery + basics comparison",
     "Both towns",
     "Visit the local Safeway or City Market on a random weekday. Look at: produce freshness and selection, meat/fish quality, prepared food options, prices vs. your Bay Area baseline.",
     "Steamboat has a full City Market and a Natural Grocers. CB has a Clarks Market (limited, expensive) and a local co-op. The gap is significant — factor into 'how often would we drive to a bigger town?'"],
    ["Drive the neighborhoods",
     "Both towns",
     "Steamboat: drive South Valley / Elk River Road area (west of downtown, more residential, horse farms). CB: drive Crested Butte South (~3 mi from town on CO-135) — more affordable housing, different community feel, 5 min to town but a different life.",
     "First impressions of a place from a tourist trail are not the same as living in it. Driving the surrounding neighborhoods for 30 min gives you a better read."],
    ["Test internet + cell coverage",
     "Both towns",
     "Open Speedtest.net in multiple locations: downtown, your Airbnb, on the road out of town toward Gunnison (CB) or SLC (Steamboat). This matters a lot for part-time remote work.",
     "Steamboat reportedly has better connectivity (Xfinity + rural broadband). CB has limited options. Cell coverage on the road between CB and Gunnison (US-135) is spotty in spots."],
    ["Ask a local about shoulder season",
     "Both towns",
     "At the coffee shop or a local bar, ask: 'What's it actually like here in November or March?' This is the single best question for understanding whether a place would work for you outside peak season.",
     "Some CB locals say October–November is quiet + beautiful. Others find the 4-hr drive to Denver limiting in winter. Steamboat has a stronger year-round economy (ski resort town, summer rodeo, music festivals)."],
    ["Airport comparison — important",
     "Both towns",
     "Steamboat has its own regional airport (SBS) with United service to Denver and Houston. This changes the calculus significantly for part-time ownership. CB requires a 90-min drive to Gunnison (GUC) with limited service, or 4+ hrs to Denver.",
     "If you'd be splitting time between Bay Area and a CO cabin, airport access is a bigger quality-of-life factor than it sounds. Steamboat has a major advantage here."],
    ["Walk around on a weekday (non-peak)",
     "Both towns",
     "Try to spend one morning midweek not doing a hike or activity — just walking around, getting coffee, running an errand. See what the energy is like when it's not a peak Saturday.",
     "This matters more for evaluating a place to live than all the activities combined."],
]

# ── ROW LAYOUT ────────────────────────────────────────────────────────────────
# 0:  Title
# 1:  blank
# 2:  POSSIBLE ADD-ONS header
# 3:  col headers
# 4-7: add-on rows (4)
# 8:  blank
# 9:  PRACTICAL NOTES header
# 10: col headers
# 11-14: practical rows (4)
# 15: blank
# 16: CABIN EVALUATION CHECKLIST header
# 17: col headers
# 18-23: cabin rows (6)

EMPTY = [""] * 8
ALL_ROWS = [
    ["More Things to Consider — Colorado 2026"] + [""] * 7,   # 0
    EMPTY,                                                      # 1
    ["POSSIBLE ADD-ONS"] + [""] * 7,                           # 2
    addon_headers,                                              # 3
] + addons + [                                                 # 4-7
    EMPTY,                                                      # 8
    ["PRACTICAL NOTES"] + [""] * 7,                            # 9
    practical_headers,                                          # 10
] + practical + [                                              # 11-14
    EMPTY,                                                      # 15
    ["CABIN EVALUATION CHECKLIST"] + [""] * 7,                 # 16
    cabin_headers,                                              # 17
] + cabin                                                      # 18-23

ws.update(range_name="A1", values=ALL_ROWS)

# ── FORMATTING ────────────────────────────────────────────────────────────────
requests = []

for row_i, bg in [
    (0,  TITLE_BG),
    (2,  ADDON_BG),
    (9,  PRACTICAL_BG),
    (16, CABIN_BG),
]:
    requests.append(merge(row_i))
    requests.append(fmt_row(row_i, bg, text_color=WHITE, bold=True))

for row_i in [3, 10, 17]:
    requests.append(fmt_row(row_i, COL_HDR, text_color=DARK_TXT, bold=True))

# Column widths: A=190, B=160, C=360, D=220, E-H=20
requests += col_widths([
    (0, 190), (1, 160), (2, 360), (3, 220),
    (4, 20), (5, 20), (6, 20), (7, 20)
])

# Wrap columns A–D for data rows
for col in range(4):
    requests.append(wrap_col(col, col+1, 3, 24))

sh.batch_update({"requests": requests})
print(f"Done. 'More Things to Consider' sheet created. sheet_id={sheet_id}")
