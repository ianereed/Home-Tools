import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

ws = sh.add_worksheet(title="Scenic Stops & Drives", rows=40, cols=7)
sheet_id = ws._properties['sheetId']

# ── HELPERS ───────────────────────────────────────────────────────────────────
def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

def fmt_row(row_i, bg, text_color=None, bold=True, end_col=7):
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

def merge(row_i, end_col=7):
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
TITLE_BG   = rgb(15,  23,  42)    # very dark navy
NV_UT_BG   = rgb(124,  57,   0)   # dark amber  — Nevada/Utah leg
STEAM_BG   = rgb(21,  101, 192)   # deep blue   — Steamboat area
DRIVE7_BG  = rgb(39,  119,  59)   # forest green — Aug 7 drive
CB_BG      = rgb(69,   27, 142)   # deep purple — CB area
DRIVE12_BG = rgb(140,  54,  18)   # dark rust   — Aug 12 drive
BOULD_BG   = rgb(0,   105,  92)   # dark teal   — Boulder area
COL_HDR    = rgb(230, 230, 230)
DARK_TXT   = rgb(30,  30,  30)
WHITE      = rgb(255, 255, 255)

HEADERS = ["Stop / Place", "When", "Time Needed", "Dog Friendly", "Cost",
           "Why Go", "Notes & Directions"]

# ── DATA ──────────────────────────────────────────────────────────────────────
nv_ut_rows = [
    ["Bonneville Salt Flats", "Jul 19 (Tahoe → Great Basin)",
     "30–60 min", "✅ Open land", "Free",
     "Otherworldly white expanse stretching to the horizon. Nothing else looks like it. Quick and surreal.",
     "Optional add if routing via I-80 instead of US-50. Exit I-80 at Exit 4 near Wendover, NV/UT border. Walk out onto the flats — dogs free to roam. Goes out of your way ~1 hr vs. direct US-50 route, so skip if pushing for Great Basin."],
    ["Great Basin NP — Bristlecone Pine Trail", "Jul 19–20 overnight stop",
     "2 hrs", "✅ Dogs on leash", "Free (no entry fee at Great Basin)",
     "Some of the world's oldest living things — individual trees over 4,000 years old. Short beautiful walk through ancient forest. Also a certified Dark Sky Park; stargazing after dark is extraordinary.",
     "Bristlecone Pine Trail: 2.8 mi, ~1,000 ft gain from Wheeler Peak Campground. Start near the campground trailhead. Lehman Caves tours also available but require advance reservation (recreation.gov) and sell out weeks ahead. Great Basin is genuinely one of America's most underrated NPs."],
    ["Dead Horse Point State Park", "Jul 21 AM (before Moab → Boulder drive)",
     "1.5–2 hrs\n(+45 min drive each way)", "✅ All overlooks + trails on leash",
     "$20/vehicle",
     "One of the Southwest's most spectacular viewpoints. The Colorado River carves a 1,000-ft-deep gooseneck canyon below sheer cliff walls. Genuinely better than Arches for a quick visit with a dog.",
     "Leave Moab by 7:30–8am. Take US-191 north, then UT-313 west (~32 miles, 45 min). Walk to the main overlook (short, paved, Mochi can do it). Keep a tight leash near cliff edges — sheer drops. Leave by 10am and you arrive Boulder by 5–6pm with the full drive. $20/vehicle. Do not skip this."],
]

steam_area_rows = [
    ["Yampa River Botanic Park", "Any morning/afternoon during Steamboat stay",
     "30–45 min", "✅ Dogs on leash", "Free",
     "Beautiful botanical gardens along the Yampa River. Shaded paths, flower displays, benches by the water. Easy dog walk with great scenery right in town.",
     "1000 Pamela Ln, Steamboat Springs. Open dawn to dusk. Short drive or walkable from downtown. Good warm-up walk before a bigger hike day, or a quiet afternoon wind-down."],
    ["Strings Music Festival (outdoor concert)", "Aug 1–6 — check schedule",
     "2–3 hrs (evening)", "✅ Some outdoor venues dog-friendly",
     "$25–$75/ticket",
     "One of Colorado's premier summer music festivals. Chamber and symphony performances at the Strings Pavilion, a beautiful outdoor venue in Steamboat. Higher quality than the free events.",
     "Check schedule at stringsmusicfestival.com for Aug 1–6 dates. The Pavilion is a permanent outdoor tent structure. Some nights are more casual (bluegrass, jazz); some are formal symphony. Pair with dinner at Aurum or Laundry Kitchen before. Worth a look at the calendar."],
]

drive7_rows = [
    ["Glenwood Canyon — I-70", "Aug 7 drive (Steamboat → CB)",
     "30 min (driving through)", "N/A — in the car", "Free",
     "12 miles of I-70 carved into sheer cliff faces alongside the Colorado River — one of the most dramatic highway sections in America. You drive through it automatically on this route.",
     "No action needed. Just drive it on I-70 between Glenwood Springs and Glenwood Canyon (east of Grand Junction). Slow down and look. The engineering alone is remarkable — the road hugs the canyon walls at river level. There are rest area pullouts with river access if you want to stop."],
    ["Redstone Village", "Aug 7 drive (Steamboat → CB) — 20 min south of Carbondale on CO-133",
     "30–45 min", "✅ Outdoor areas", "Free",
     "Beautifully preserved Victorian coal camp town from the 1890s. Distinctive beehive coke ovens right alongside the road, charming main street with galleries and the Crystal River running through.",
     "Take CO-133 south from Glenwood Springs (at the CO-82 junction). Drive through Carbondale then ~15 min to Redstone. Stop at the Coke Ovens Historic District (roadside pull-off — unmissable). Walk the short main street. Penny Hot Springs is 2 miles south of town on CO-133: free primitive hot spring on the Crystal River bank. Mochi can wade but the pools can be crowded. Then continue south over McClure Pass to CB."],
    ["McClure Pass", "Aug 7 drive (Steamboat → CB) — right after Redstone on CO-133",
     "10–15 min", "✅ Roadside pullouts", "Free",
     "8,755-ft pass through lush mountain meadows with sweeping valley views. Wildflowers in August. The drive from the pass to CB through the Gunnison Valley is some of Colorado's best scenery.",
     "Automatic on the CO-133 route. Pull over at the summit for a quick stretch and photo. The descent toward Paonia on the west side and the valley below CB on the east are both beautiful. No special preparation needed."],
]

cb_area_rows = [
    ["Kebler Pass Road", "Aug 8–11 — any afternoon",
     "1.5–2 hrs", "✅ Dogs on leash", "Free",
     "Said to be the largest aspen grove in North America. In August it's lush green with wildflowers — not the golden fall display, but the sheer scale is still extraordinary. A great evening drive from CB.",
     "Take Gothic Rd west out of CB to Kebler Pass Rd (CR-12). Road is well-graded gravel — Sprinter has no issues. Drive as far as you want (pass summit is ~10 miles). You can continue through to Paonia if you want a loop back via CO-133, adding ~1.5 hrs. Best in late afternoon light. Mochi can get out at any pullout along the way."],
    ["Gothic Road / Gothic Research Station", "Any morning during CB stay",
     "30–45 min scenic drive", "✅ Outdoor areas", "Free",
     "Drive up the valley past the town of Gothic — a rustic former silver mining ghost town now occupied by the Rocky Mountain Biological Laboratory. Beautiful alpine valley, historic wooden buildings, wildflowers.",
     "Gothic Rd heads north out of Crested Butte toward Gothic Mountain and the 401 trailhead. Even if Ian isn't riding or doing the 401 shuttle, it's a worthwhile scenic morning drive. The RMBL station is privately operated but the road past it is open. Good dog walk in the meadows near the station."],
]

drive12_rows = [
    ["Colorado National Monument", "Aug 12 drive (CB → SLC) — near Grand Junction",
     "1.5–2 hrs", "✅ All 19 overlooks + paved roads on leash\n(NOT on hiking trails)",
     "Free w/ America the Beautiful pass\n$25/vehicle otherwise",
     "23-mile Rim Rock Drive through dramatic red sandstone canyon country. Genuinely world-class — rival to canyon parks in Utah, with almost no one on it. You drive right past it on the CB-to-SLC route.",
     "From CB, take US-50 west through Gunnison and Montrose to Grand Junction (~2 hrs). Just before Grand Junction, take Hwy 340 west to the east entrance of the monument. Drive 23-mile Rim Rock Drive with pullouts at viewpoints (all Mochi-accessible). Exit the west side, rejoin I-70 west toward SLC. Adds ~1.5–2 hrs but transforms what would be a blank highway slog into a highlight. This is the single best detour of the whole trip."],
]

boulder_rows = [
    ["Flagstaff Mountain Road", "Any evening during Boulder stay (especially Jul 22–31)",
     "45–60 min", "✅ Trails + overlooks on leash", "Free",
     "Boulder's best sunset viewpoint. Sweeping view of the city and plains from above the Flatirons. Easy 10-min drive from downtown. Perfect before a dinner on Pearl St.",
     "Drive Baseline Rd west past Chautauqua Park, turn left on Flagstaff Rd. Wind up switchbacks to Summit Rd. Multiple free pullouts with benches. Best 30–45 min before sunset when the light hits the red rock below and the plains go gold. Short dog-friendly walk at the summit clearing. Pair with dinner afterward at Dushanbe Teahouse or Pearl St."],
    ["Boulder Canyon Scenic Drive", "Any day during Boulder stay",
     "30–45 min (drive only)", "✅ In the car", "Free",
     "Boulder Creek carved a dramatic canyon into the Rockies heading west of town. The canyon drive on CO-119 is beautiful even without stopping — vertical granite walls, the creek alongside, climbers on the walls.",
     "Take Canyon Blvd (CO-119) west out of downtown Boulder. The canyon opens up after ~5 min. Boulder Falls is a 10-min walk from the road at mile marker 45 — worth stopping for (leash required). Continue to Nederland if you want a full day trip (~30 min each way from town). Good to know when driving to Walker Ranch or Brainard Lake anyway."],
]

# ── ROW LAYOUT ────────────────────────────────────────────────────────────────
# 0:  Title
# 1:  blank
# 2:  NEVADA/UTAH LEG header
# 3:  col headers
# 4-6: 3 stops
# 7:  blank
# 8:  STEAMBOAT AREA header
# 9:  col headers
# 10-11: 2 stops
# 12: blank
# 13: AUG 7 DRIVE header
# 14: col headers
# 15-17: 3 stops
# 18: blank
# 19: CRESTED BUTTE AREA header
# 20: col headers
# 21-22: 2 stops
# 23: blank
# 24: AUG 12 DRIVE header
# 25: col headers
# 26: 1 stop
# 27: blank
# 28: BOULDER AREA header
# 29: col headers
# 30-31: 2 stops

EMPTY = [""] * 7
ALL_ROWS = [
    ["Scenic Stops & Drives — Colorado 2026"] + [""] * 6,                 # 0
    EMPTY,                                                                  # 1
    ["NEVADA / UTAH LEG  |  Jul 18–21"] + [""] * 6,                       # 2
    HEADERS,                                                                # 3
] + nv_ut_rows + [                                                         # 4-6
    EMPTY,                                                                  # 7
    ["STEAMBOAT AREA  |  Aug 1–7"] + [""] * 6,                            # 8
    HEADERS,                                                                # 9
] + steam_area_rows + [                                                    # 10-11
    EMPTY,                                                                  # 12
    ["AUG 7 DRIVE  |  Steamboat → Crested Butte via Glenwood + CO-133"] + [""] * 6,  # 13
    HEADERS,                                                                # 14
] + drive7_rows + [                                                        # 15-17
    EMPTY,                                                                  # 18
    ["CRESTED BUTTE AREA  |  Aug 8–11"] + [""] * 6,                       # 19
    HEADERS,                                                                # 20
] + cb_area_rows + [                                                       # 21-22
    EMPTY,                                                                  # 23
    ["AUG 12 DRIVE  |  Crested Butte → SLC via Grand Junction"] + [""] * 6,  # 24
    HEADERS,                                                                # 25
] + drive12_rows + [                                                       # 26
    EMPTY,                                                                  # 27
    ["BOULDER AREA  |  Jul 22–31"] + [""] * 6,                            # 28
    HEADERS,                                                                # 29
] + boulder_rows                                                           # 30-31

ws.update(range_name="A1", values=ALL_ROWS)

# ── FORMATTING ────────────────────────────────────────────────────────────────
requests = []

for row_i, bg in [
    (0,  TITLE_BG),
    (2,  NV_UT_BG),
    (8,  STEAM_BG),
    (13, DRIVE7_BG),
    (19, CB_BG),
    (24, DRIVE12_BG),
    (28, BOULD_BG),
]:
    requests.append(merge(row_i))
    requests.append(fmt_row(row_i, bg, text_color=WHITE, bold=True))

for row_i in [3, 9, 14, 20, 25, 29]:
    requests.append(fmt_row(row_i, COL_HDR, text_color=DARK_TXT, bold=True))

# Column widths: A=185, B=160, C=85, D=90, E=100, F=210, G=320
requests += col_widths([
    (0, 185), (1, 160), (2, 85), (3, 90), (4, 100), (5, 210), (6, 320)
])

# Wrap: Why Go (F), Notes (G), Dog Friendly (D), Cost (E), When (B)
for col_range in [(5, 6), (6, 7), (3, 4), (4, 5), (1, 2)]:
    requests.append(wrap_col(col_range[0], col_range[1], 3, 32))

sh.batch_update({"requests": requests})
print(f"Done. Scenic Stops & Drives sheet created. sheet_id={sheet_id}")
