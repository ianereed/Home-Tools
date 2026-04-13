import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

ws = sh.add_worksheet(title="Dining Guide", rows=35, cols=9)
sheet_id = ws._properties['sheetId']

# ── HELPERS ───────────────────────────────────────────────────────────────────
def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

def fmt_row(row_i, bg, text_color=None, bold=True, end_col=9):
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

def merge(row_i, end_col=9):
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
TITLE_BG  = rgb(15,  23,  42)     # very dark navy
BOULD_BG  = rgb(0,  105,  92)     # dark teal (Boulder)
MOAB_BG   = rgb(124,  57,   0)    # dark amber-orange (Moab)
STEAM_BG  = rgb(21,  101, 192)    # deep blue (Steamboat)
CB_BG     = rgb(69,   27, 142)    # deep purple (Crested Butte)
COL_HDR   = rgb(230, 230, 230)
DARK_TXT  = rgb(30,  30,  30)
WHITE     = rgb(255, 255, 255)

HEADERS = ["Restaurant / Place", "City", "Type", "Price", "Reservation",
           "Phone", "Website", "Notes / Must-Know", "Dog Friendly?"]

# ── DATA ──────────────────────────────────────────────────────────────────────
boulder_rows = [
    ["Dushanbe Teahouse", "Boulder", "Brunch / Tea", "$$",
     "Walk-in weekdays\nReserve weekend brunch", "", "boulderteahouse.com",
     "19th-century ornate teahouse hand-carved in Tajikistan, gifted to Boulder. Stunning interior + garden patio on Boulder Creek. One of the most architecturally unique dining rooms in Colorado. Great for brunch or afternoon tea. Walk in weekdays; book ahead for weekend brunch.",
     "✅ Creek patio"],
    ["Chautauqua Dining Hall", "Boulder", "American / Brunch", "$$",
     "Recommended — chautauqua.com", "", "chautauqua.com",
     "Historic 1898 lodge dining room with unobstructed Flatiron views from the covered porch. Quintessential Boulder. Best for brunch before or after a hike at Chautauqua Park. Book ahead for summer weekends — fills quickly.",
     "✅ Covered porch"],
    ["Fate Brewing Company", "Boulder", "Craft Beer / Casual", "$",
     "Walk-in", "", "fatebeer.com",
     "Large dog-friendly patio, solid craft beers, food trucks on weekends. Relaxed afternoon stop near the east side of town. Good for a wind-down after a hike.",
     "✅ Large patio"],
    ["Flagstaff Mountain Road", "Boulder", "Scenic Drive / Sunset", "Free",
     "N/A — just drive up", "", "",
     "Not a restaurant — Boulder's best sunset viewpoint. Drive Baseline Rd west, turn left on Flagstaff Rd, wind up to the Summit Rd pullouts. Sweeping views across Boulder and the plains. 10 min from downtown. Best 45 min before sunset. Dog-friendly on leash. Pairs perfectly with a downtown dinner after.",
     "✅ Outdoor overlooks"],
]

moab_rows = [
    ["The Spoke on Center", "Moab", "Small plates / Cocktails", "$$",
     "Walk-in", "", "",
     "Best non-tourist-trap dinner in Moab. Craft cocktails and small plates, local favorite. Better vibe than the Main St chain spots. Good option for the Jul 20 one-night arrival dinner.",
     "❓ Call ahead"],
    ["Moab Brewery", "Moab", "Casual / Brewery", "$",
     "Walk-in", "", "themoabbrewery.com",
     "Large patio on Main St, dogs welcome. Classic burgers and local craft beer. Relaxed arrival dinner after a long drive day. Reliable and easy.",
     "✅ Patio"],
]

steamboat_rows = [
    ["Aurum Food & Wine", "Steamboat", "New American / Upscale", "$$$",
     "Required — book on Tock\n→ aurumsteamboat.com", "(970) 879-9500", "aurumsteamboat.com",
     "Best restaurant in Steamboat. Riverfront outdoor deck on the Yampa River with fire ring seating, curated wine list, locally sourced New American menu. Book well ahead on Tock for August. This is the splurge dinner for Steamboat. Call ahead to confirm dog-friendly patio policy.",
     "❓ Confirm dog patio"],
    ["Laundry Kitchen & Cocktails", "Steamboat", "Small plates / Cocktails", "$$",
     "Walk-in (opens 4:30pm)", "", "rexsfamily.com/the-laundry",
     "Historic 1910 laundry building turned creative cocktail and small-plate spot. Patio along Soda Creek, intimate and shaded. Inventive house-infused drinks. Opens 4:30pm daily. Great for a longer evening out on a non-big-hike day.",
     "✅ Soda Creek patio"],
    ["Ghost Ranch Coffee", "Steamboat", "Coffee / Breakfast", "$",
     "Walk-in", "", "",
     "Best coffee in Steamboat. Locally roasted. Good morning stop before a long bike or hike day. Downtown location, easy to hit on the way out.",
     "✅ Usually patio"],
]

cb_rows = [
    ["Soupçon", "Crested Butte", "French-American / Prix Fixe", "$$$$",
     "REQUIRED — book NOW\nsoupconcb.com (Tock)\nFills 4–6 wks ahead in summer", "", "soupconcb.com",
     "CB's finest and most celebrated restaurant. Hidden down a back alley off Elk Ave in a tiny 8-table historic building. Two nightly seatings: 5:30pm and 7:45pm. Prix fixe tasting menu ~$150–$250/person all-in. Book immediately on Tock — August Saturdays fill weeks out. This is the one blow-out dinner of the whole Colorado trip.",
     "❌ Indoor only"],
    ["Montanya Distillers", "Crested Butte", "Cocktail Bar / Rum Distillery", "$$",
     "Walk-in, no reservation\n(3–9pm daily)", "", "montanyarum.com",
     "Colorado rum distillery with a walk-in tasting room on Elk Ave. House-distilled white and aged rums, craft cocktails, small bites. Open daily 3–9pm, first-come. Distinctive non-generic atmosphere — not your typical mountain bar. Perfect pre-dinner stop. 204 Elk Ave.",
     "✅ Some outdoor seating"],
    ["Secret Stash", "Crested Butte", "Pizza / Eclectic", "$$",
     "Walk-in (busy weekends)", "", "",
     "CB institution. Pizza served inside a funky old Victorian house with multiple themed rooms, cushion seating, walls covered in art. Fun, loud, very local vibe. A different experience from the upscale Elk Ave spots. Good for a lively group evening.",
     "✅ Some outdoor"],
    ["Teocalli Tamale", "Crested Butte", "Mexican / Fast Casual", "$",
     "Walk-in", "", "",
     "CB institution for Mexican food. Fast, cheap, excellent. Great for a quick and satisfying lunch between a morning hike and an afternoon activity. 311 Elk Ave.",
     "✅ Some outdoor"],
]

# ── ROW LAYOUT ────────────────────────────────────────────────────────────────
# 0:  Title
# 1:  blank
# 2:  BOULDER header
# 3:  col headers
# 4-7: Boulder rows (4)
# 8:  blank
# 9:  MOAB header
# 10: col headers
# 11-12: Moab rows (2)
# 13: blank
# 14: STEAMBOAT header
# 15: col headers
# 16-18: Steamboat rows (3)
# 19: blank
# 20: CRESTED BUTTE header
# 21: col headers
# 22-25: CB rows (4)

EMPTY = ["", "", "", "", "", "", "", "", ""]
ALL_ROWS = [
    ["Dining Guide — Colorado 2026"] + [""] * 8,   # 0
    EMPTY,                                          # 1
    ["BOULDER  |  Jul 22–31"] + [""] * 8,          # 2
    HEADERS,                                        # 3
] + boulder_rows + [                               # 4-7
    EMPTY,                                          # 8
    ["MOAB  |  Jul 20 night"] + [""] * 8,          # 9
    HEADERS,                                        # 10
] + moab_rows + [                                  # 11-12
    EMPTY,                                          # 13
    ["STEAMBOAT SPRINGS  |  Aug 1–7"] + [""] * 8,  # 14
    HEADERS,                                        # 15
] + steamboat_rows + [                             # 16-18
    EMPTY,                                          # 19
    ["CRESTED BUTTE  |  Aug 8–11"] + [""] * 8,     # 20
    HEADERS,                                        # 21
] + cb_rows                                        # 22-25

ws.update(range_name="A1", values=ALL_ROWS)

# ── FORMATTING ────────────────────────────────────────────────────────────────
requests = []

# Merge + color section / title headers
for row_i, bg in [
    (0,  TITLE_BG),
    (2,  BOULD_BG),
    (9,  MOAB_BG),
    (14, STEAM_BG),
    (20, CB_BG),
]:
    requests.append(merge(row_i))
    requests.append(fmt_row(row_i, bg, text_color=WHITE, bold=True))

# Column header rows — grey
for row_i in [3, 10, 15, 21]:
    requests.append(fmt_row(row_i, COL_HDR, text_color=DARK_TXT, bold=True))

# Column widths
requests += col_widths([
    (0, 200), (1, 95), (2, 125), (3, 75), (4, 145),
    (5, 130), (6, 155), (7, 310), (8, 95)
])

# Wrap: Notes (H), Reservation (E), Type (C)
requests.append(wrap_col(7, 8, 3, 26))   # Notes
requests.append(wrap_col(4, 5, 3, 26))   # Reservation
requests.append(wrap_col(2, 3, 3, 26))   # Type

sh.batch_update({"requests": requests})
print(f"Done. Dining Guide sheet created. sheet_id={sheet_id}")
