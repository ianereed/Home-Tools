import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
SSID = SPREADSHEET_ID

# ══════════════════════════════════════════════════════════════════════════════
# 1. CREATE "Dog Daycare Options" SHEET
# ══════════════════════════════════════════════════════════════════════════════
ws = sh.add_worksheet(title="Dog Daycare Options", rows=55, cols=8)
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

# ── COLORS (matching city colors used in other sheets) ────────────────────────
TITLE_BG   = rgb(15,  23,  42)    # very dark navy
WARN_BG    = rgb(230, 119,   0)   # amber — vaccine reminder
PRIORITY_BG= rgb(183,  28,  28)   # dark red — booking priority
BOULD_BG   = rgb(0,  105,  92)    # dark teal — Boulder
MOAB_BG    = rgb(124,  57,   0)   # dark amber — Moab
TAHOE_BG   = rgb(0,   77,  64)    # very dark teal — Tahoe/Truckee
STEAM_BG   = rgb(21, 101, 192)    # deep blue — Steamboat
CB_BG      = rgb(69,  27, 142)    # deep purple — Crested Butte
MAMM_BG    = rgb(180,  40,   0)   # volcanic red — Mammoth (PRIORITY)
BISH_BG    = rgb(55,  71,  79)    # dark slate — Bishop
COL_HDR    = rgb(230, 230, 230)
DARK_TXT   = rgb(30,  30,  30)
WHITE      = rgb(255, 255, 255)

# ── HEADERS ───────────────────────────────────────────────────────────────────
FAC_HEADERS = ["Facility", "Type", "Address", "Phone", "Website",
               "~Cost/Day", "Book Ahead?", "Notes & Hours"]
PRI_HEADERS = ["Priority", "City / When", "Facility", "Phone",
               "Key Action", "Book By", "", ""]

# ── PRIORITY DATA ─────────────────────────────────────────────────────────────
priority_rows = [
    ["🔴  1", "Mammoth  |  Aug 15–17",
     "PUP Hiking Co + Sierra Dog Ventures", "(760) 582-2176 / (714) 609-8510",
     "Call both — small operations at peak summer. Ian is solo with Mochi all 3 days.",
     "NOW", "", ""],
    ["🔴  2", "Steamboat  |  Aug 1–7",
     "Red Rover Resort", "(970) 879-3647",
     "Ask about split-hour pickup policy. Confirm full daycare day is possible.",
     "Before trip", "", ""],
    ["🔴  3", "Crested Butte  |  Aug 8–11",
     "Oh Be Dogful Pet Ranch", "(970) 349-5047",
     "⚠️ NO WEEKEND DAYCARE. Call to confirm weekday availability Aug 8-11.",
     "2–4 weeks out", "", ""],
    ["🟡  4", "Truckee / Lake Tahoe  |  Jul 18",
     "Truckee-Tahoe Pet Lodge", "(530) 582-7268",
     "Complete new-client registration online before Jul 18. One-day drop-off.",
     "1+ week ahead (online)", "", ""],
    ["🟡  5", "Moab  |  Jul 20",
     "Wanderlust Mutts", "(435) 258-9494",
     "Book via Google Form at wanderlustmuttsmoab.com. One-night stop.",
     "1–2 weeks ahead", "", ""],
    ["🟡  6", "Boulder  |  Jul 22–31",
     "Camp Bow Wow (interview req'd first)", "(720) 605-4733",
     "Schedule interview first 1–2 days of Boulder stay (Jul 22–23). Drop-in after.",
     "Interview Day 1–2", "", ""],
    ["⚪  7", "Bishop  |  backup for Mammoth",
     "Donna the Dog Lady", "(760) 387-2331",
     "~50 min from Mammoth. Best if PUP Hiking is full. Free-range boarding.",
     "Call for availability", "", ""],
]

# ── FACILITY DATA ─────────────────────────────────────────────────────────────
boulder_rows = [
    ["Camp Bow Wow Boulder", "Indoor/outdoor open-play",
     "3631 Pearl St, Boulder, CO 80301", "(720) 605-4733", "campbowwow.com/boulder",
     "$41/day (full)\n$35/day (half)",
     "Interview required first; no reservation after",
     "Requires: Rabies, DHPP, Bordetella (every 6 months). Live webcams. Separate size yards. Mon–Fri 6:30am–7pm, Sat 7am–7pm, Sun limited hours. Most convenient for spontaneous drop-offs after interview."],
    ["Cottonwood Kennels", "Farm-style, outdoor acreage",
     "7275 Valmont Rd, Boulder, CO 80301", "(720) 230-2044", "cottonwoodkennels.com",
     "$45/day (large dog)",
     "Book 1+ week ahead",
     "50+ year old farm-style facility. All-inclusive with enrichment, group play, outdoor time. More personal feel than a chain. Well-reviewed. Mon–Sun 7:30am–6pm. Good fit for an active golden."],
    ["Rogue's Farm", "Small open-play groups",
     "7019 County Road 5, Erie, CO 80516\n(~15 min from Boulder)", "(303) 651-2834", "rogues.farm",
     "$32/day (full)\n$22/day (half)",
     "Call/email ahead",
     "Best price of the three. Smaller operation with 2–3 groups split by size/temperament. Bar-free kennels. Leash walks + enrichment included. Mon–Fri 6:30am–7:30pm, Sat–Sun 6:30am–7pm. Short drive to Erie."],
]

moab_rows = [
    ["Wanderlust Mutts", "Adventure trail hike + camp",
     "Moab, UT (mobile — no fixed address)", "(435) 258-9494", "wanderlustmuttsmoab.com",
     "$90/day\n($180 solo adventure)",
     "Google Form booking — book ahead",
     "Takes dogs on actual Moab dog-friendly trails all day. Best fit for an active golden. Solo option ($180) if Mochi doesn't do well with unknown pack. Pickup/drop-off 8am–5pm. Extended hours +$20. Most appropriate for a one-day Moab stop."],
    ["Moab National Bark", "Traditional kennel daycare",
     "2781 Roberts Dr, Moab, UT 84532", "(435) 259-7922", "moabnationalbark.com",
     "$35–48/day",
     "Call ahead",
     "Small family-owned facility. 8am–5pm daily. More affordable than Wanderlust Mutts if you want a straightforward drop-off. 19 reviews as of 2026 — limited info available. Backup if Wanderlust Mutts is booked."],
]

tahoe_rows = [
    ["Truckee-Tahoe Pet Lodge", "Indoor/outdoor open-play",
     "10960 W River St, Truckee, CA 96161\n(~5 min from Northstar)", "(530) 582-7268", "truckeetahoepetlodge.com",
     "~$60/day",
     "Register online as new client 1+ week ahead",
     "Purpose-built facility since 2010. 4,000+ sq ft outdoor + 1,500 sq ft indoor. Separate small/large dog groups. 7 days/week 7:30am–6pm (Fri until 7:30pm). Best documented option in Truckee. Complete new-client form at truckeetahoepetlodge.com BEFORE Jul 18."],
]

steamboat_rows = [
    ["Red Rover Resort", "Pet resort — boarding + daycare",
     "37700 RCR 49, Steamboat Springs, CO 80477", "(970) 879-3647", "redroverresort.com",
     "Call for rates",
     "Book before trip",
     "⚠️ SPLIT HOURS: Drop-off 8–12pm, pickup 4–8pm. Call to confirm full daycare day logistics. Summer Pup Plunge swim pond — great for an active golden. Only well-documented dedicated facility in Steamboat. Operating since 1995."],
    ["Rocky Mountain Pet Resort", "Traditional kennel",
     "27150 Watson Creek Trail, Yampa, CO 80483\n(~35–40 min from Steamboat)", "(970) 638-0242", "rockymountainpetresort.com",
     "$54–69/night boarding",
     "30 days ahead",
     "Better as an overnight option than day-drop. Located in Yampa, not Steamboat — factor in the drive. Call to ask about single-day daycare without overnight. 8am–6pm daily."],
    ["Peace Love Petcare", "In-town, small operation",
     "Steamboat Springs, CO (in town)", "(970) 879-5683", "Yelp: Peace Love Petcare",
     "Call for rates",
     "Call directly",
     "Convenient location. Limited online info — call directly to understand their daycare format and availability. Good backup if Red Rover is full."],
]

cb_rows = [
    ["Oh Be Dogful Pet Ranch", "Pet ranch — daycare + boarding",
     "336 Buckley Dr, Crested Butte, CO 81224", "(970) 349-5047", "ohbedogful.com",
     "Call for rates",
     "Book 2–4 weeks ahead",
     "⚠️ NO WEEKEND DAYCARE — weekends (Sat/Sun) are boarding pickup/drop only, not daycare. WEEKDAY DAYCARE ONLY: Mon–Fri 7:30am–6pm. Plan bike park days on weekdays (Aug 8 Sat, Aug 9 Sun are excluded — use these for trail rides or schedule boarding overnight). Only dedicated in-town CB option."],
    ["Gunnison Critter Sitters", "Open-play daycare + vet on-site",
     "98 County Road 17, Gunnison, CO 81230\n(28 miles / ~35 min from CB)", "(970) 641-0460", "gunnisoncrittersitters.com",
     "$22/day",
     "Call ahead — small operation",
     "Best price of the trip ($22/day). Split hours: drop 8:30–12:30pm, pickup 2:30–5:30pm (Sat pickup 8:30–10:30am). 5 designated play areas. Vet clinic on-site. The drive from CB is the tradeoff. Good weekday option with a flexible CB schedule."],
]

mammoth_rows = [
    ["PUP Hiking Company", "Off-leash pack hiking daycare",
     "126 Old Mammoth Rd #106, Mammoth Lakes, CA 93546", "(760) 582-2176", "puphikingcompany.com",
     "Call for rates",
     "BOOK NOW — August fills fast",
     "🏆 TOP PICK for Mochi. Off-leash pack hikes on actual trails, groups of 7–15 dogs, Garmin tracking collars. Drop-off 7:30–8am, hike all day, pickup 4–4:30pm. Perfect for an active 2-yr-old golden. Ian drops Mochi, spends full day at bike park. Book as far ahead as possible."],
    ["Sierra Dog Ventures", "Off-leash pack adventure daycare",
     "Mammoth Lakes, CA (mobile)", "(714) 609-8510", "sierradogventures.com",
     "Call for rates",
     "Book ahead — small operation",
     "Similar concept to PUP Hiking. Group/pack hikes with socialization focus. Dog CPR + first aid certified. Mammoth Lakes Recreation partnership. 34 Yelp reviews as of 2026. Best backup if PUP Hiking is full for any of the 3 days (Aug 15–17). Call both at the same time."],
    ["Donna the Dog Lady", "Free-range home boarding",
     "1215 Birchim Lane, Round Valley\n(Bishop, CA — ~50 min from Mammoth)", "(760) 387-2331 or (760) 873-8405", "bishopvisitor.com/places/donna-the-dog-lady",
     "Call for rates",
     "Book well ahead",
     "Free-range boarding on a rural property — no crates, low stress. Many Mammoth visitors specifically use Donna. ~50 min drive from Mammoth Lakes makes this better for overnight (drop day before, pick up next morning) than single-day daycare. Use as fallback if PUP + Sierra Dog are both full."],
]

bishop_rows = [
    ["Donna the Dog Lady", "Free-range home boarding",
     "1215 Birchim Lane, Round Valley (near Bishop)", "(760) 387-2331 or (760) 873-8405", "bishopvisitor.com/places/donna-the-dog-lady",
     "Call for rates",
     "Book well ahead",
     "Primary Bishop option. Located between Mammoth and Bishop in Round Valley. Free-range boarding (not kenneled). Well-regarded by repeat Eastern Sierra visitors. Also useful if driving Bishop→Mammoth — could drop Mochi, continue up, pick up on the way back through."],
    ["Pampered Pooches", "Traditional kennel",
     "200 Sawmill Rd, Bishop, CA 93514", "(760) 872-7387", "N/A — call directly",
     "Call for rates",
     "Call ahead",
     "Traditional boarding and grooming in Bishop. Fallback if Donna is unavailable. Limited online info — call directly."],
]

# ── ROW LAYOUT (0-indexed) ────────────────────────────────────────────────────
# 0:  Title
# 1:  blank
# 2:  VACCINE REMINDER (amber warning)
# 3:  blank
# 4:  BOOKING PRIORITY header (red)
# 5:  priority col headers
# 6-12: priority rows (7)
# 13: blank
# 14: BOULDER header
# 15: col headers
# 16-18: Boulder rows (3)
# 19: blank
# 20: MOAB header
# 21: col headers
# 22-23: Moab rows (2)
# 24: blank
# 25: TRUCKEE / LAKE TAHOE header
# 26: col headers
# 27: Tahoe row (1)
# 28: blank
# 29: STEAMBOAT SPRINGS header
# 30: col headers
# 31-33: Steamboat rows (3)
# 34: blank
# 35: CRESTED BUTTE header
# 36: col headers
# 37-38: CB rows (2)
# 39: blank
# 40: MAMMOTH LAKES header ⭐
# 41: col headers
# 42-44: Mammoth rows (3)
# 45: blank
# 46: BISHOP, CA header
# 47: col headers
# 48-49: Bishop rows (2)

E = [""] * 8
vaccine_note = ("⚠️  VACCINE REMINDER — Confirm Mochi is current on: "
                "Rabies | DHPP | Bordetella (often required every 6 months at daycare, not just annually — "
                "most common gotcha). Bring printed records to every drop-off.")

ALL_ROWS = [
    ["Dog Daycare Options — Mochi 🐾"] + [""] * 7,                          # 0
    E,                                                                         # 1
    [vaccine_note] + [""] * 7,                                                # 2
    E,                                                                         # 3
    ["BOOKING PRIORITY — Make These Calls First"] + [""] * 7,               # 4
    PRI_HEADERS,                                                               # 5
] + priority_rows + [                                                          # 6-12
    E,                                                                         # 13
    ["BOULDER, CO  |  Jul 22–31  (10 nights)"] + [""] * 7,                  # 14
    FAC_HEADERS,                                                               # 15
] + boulder_rows + [                                                           # 16-18
    E,                                                                         # 19
    ["MOAB, UT  |  Jul 20  (one night)"] + [""] * 7,                        # 20
    FAC_HEADERS,                                                               # 21
] + moab_rows + [                                                              # 22-23
    E,                                                                         # 24
    ["TRUCKEE / LAKE TAHOE, CA  |  Jul 18  (one day)"] + [""] * 7,         # 25
    FAC_HEADERS,                                                               # 26
] + tahoe_rows + [                                                             # 27
    E,                                                                         # 28
    ["STEAMBOAT SPRINGS, CO  |  Aug 1–7  (6 nights)"] + [""] * 7,          # 29
    FAC_HEADERS,                                                               # 30
] + steamboat_rows + [                                                         # 31-33
    E,                                                                         # 34
    ["CRESTED BUTTE, CO  |  Aug 8–11  ⚠️ No weekend daycare at Oh Be Dogful"] + [""] * 7,  # 35
    FAC_HEADERS,                                                               # 36
] + cb_rows + [                                                                # 37-38
    E,                                                                         # 39
    ["MAMMOTH LAKES, CA  |  Aug 15–17  ⭐ Ian solo with Mochi — daycare needed daily"] + [""] * 7,  # 40
    FAC_HEADERS,                                                               # 41
] + mammoth_rows + [                                                           # 42-44
    E,                                                                         # 45
    ["BISHOP, CA  |  backup / en route option"] + [""] * 7,                 # 46
    FAC_HEADERS,                                                               # 47
] + bishop_rows                                                                # 48-49

ws.update(range_name="A1", values=ALL_ROWS)

# ── FORMATTING ────────────────────────────────────────────────────────────────
requests = []

# Title + vaccine warning
requests += [merge(0), fmt_row(0, TITLE_BG, text_color=WHITE, bold=True)]
requests += [merge(2), fmt_row(2, WARN_BG, text_color=WHITE, bold=True)]

# Priority section
requests += [merge(4), fmt_row(4, PRIORITY_BG, text_color=WHITE, bold=True)]
requests.append(fmt_row(5, COL_HDR, text_color=DARK_TXT, bold=True))

# City sections
for row_i, bg in [
    (14, BOULD_BG),
    (20, MOAB_BG),
    (25, TAHOE_BG),
    (29, STEAM_BG),
    (35, CB_BG),
    (40, MAMM_BG),
    (46, BISH_BG),
]:
    requests += [merge(row_i), fmt_row(row_i, bg, text_color=WHITE, bold=True)]

# Col header rows for city sections
for row_i in [15, 21, 26, 30, 36, 41, 47]:
    requests.append(fmt_row(row_i, COL_HDR, text_color=DARK_TXT, bold=True))

# Column widths: A=185, B=120, C=175, D=130, E=155, F=90, G=115, H=295
requests += col_widths([
    (0, 185), (1, 120), (2, 175), (3, 130), (4, 155), (5, 90), (6, 115), (7, 295)
])

# Wrap Notes (H), Address (C), Cost (F), Book Ahead (G) for all data rows
for col in [7, 2, 5, 6]:
    requests.append(wrap_col(col, col+1, 5, 50))

# Also wrap priority section columns B, D, E
for col in [1, 3, 4]:
    requests.append(wrap_col(col, col+1, 5, 13))

sh.batch_update({"requests": requests})
print(f"Dog Daycare Options sheet created. sheet_id={sheet_id}")

# ══════════════════════════════════════════════════════════════════════════════
# 2. ADD "Dog Daycare" COLUMN Q TO MAIN ITINERARY + FIX MAMMOTH ROWS
# ══════════════════════════════════════════════════════════════════════════════
gids = {w.title: w._properties['sheetId'] for w in sh.worksheets()}
daycare_gid = gids["Dog Daycare Options"]
wsi = sh.worksheets()[0]   # main itinerary tab (whatever it's named)
itinerary_sid = wsi._properties['sheetId']
wsi.resize(rows=100, cols=17)   # add column Q
UE = 'USER_ENTERED'

def dc_link(label="→ Dog Daycare"):
    return f'=HYPERLINK("https://docs.google.com/spreadsheets/d/{SSID}/edit#gid={daycare_gid}","{label}")'

# Header for column Q
wsi.update(range_name="Q22", values=[["Dog Daycare"]], value_input_option=UE)

# Format Q22 header
requests_main = [
    {"repeatCell": {
        "range": {"sheetId": itinerary_sid,
                  "startRowIndex": 21, "endRowIndex": 22,
                  "startColumnIndex": 16, "endColumnIndex": 17},
        "cell": {"userEnteredFormat": {
            "backgroundColor": {"red": 230/255, "green": 230/255, "blue": 230/255},
            "textFormat": {"bold": True, "foregroundColor": {"red": 30/255, "green": 30/255, "blue": 30/255}},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"
    }},
    {"updateDimensionProperties": {
        "range": {"sheetId": itinerary_sid, "dimension": "COLUMNS",
                  "startIndex": 16, "endIndex": 17},
        "properties": {"pixelSize": 180},
        "fields": "pixelSize"
    }},
    {"repeatCell": {
        "range": {"sheetId": itinerary_sid,
                  "startRowIndex": 21, "endRowIndex": 60,
                  "startColumnIndex": 16, "endColumnIndex": 17},
        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
        "fields": "userEnteredFormat(wrapStrategy)"
    }},
]
sh.batch_update({"requests": requests_main})

# Dog daycare links in column Q for relevant rows
daycare_links = [
    ("Q25", "→ Dog Daycare (Truckee)"),    # Jul 18 Tahoe
    ("Q27", "→ Dog Daycare (Moab)"),        # Jul 20 Moab
    ("Q29", "→ Dog Daycare (Boulder)"),     # Jul 22 Boulder
    ("Q39", "→ Dog Daycare (Steamboat)"),   # Aug 1 Steamboat
    ("Q46", "→ Dog Daycare (CB)"),          # Aug 8 CB
    ("Q53", "→ Dog Daycare (Mammoth)"),     # Aug 15
    ("Q54", "→ Dog Daycare (Mammoth)"),     # Aug 16
    ("Q55", "→ Dog Daycare (Mammoth)"),     # Aug 17
]
for cell, label in daycare_links:
    wsi.update(range_name=cell, values=[[dc_link(label)]], value_input_option=UE)

# Fix Mammoth rows (Aug 15-17) to reflect Ian is SOLO with Mochi
# Row 53 (Aug 15)
wsi.update(range_name="J53", values=[[
    "Ian solo with Mochi. Drop Mochi at PUP Hiking Co (7:30am) → full day at Mammoth Mountain Bike Park (gondola, Kamikaze / Bullet Downhill / Skid Marks). Ikon Pass: check 2 free days. ~$65-80 otherwise. Pickup Mochi 4–4:30pm."
]], value_input_option=UE)
wsi.update(range_name="K53", values=[["Anny: Emily Bach Party (unavailable)"]], value_input_option=UE)
wsi.update(range_name="L53", values=[[
    "PUP Hiking Company — drop off 7:30–8am, pickup 4–4:30pm. (760) 582-2176. BOOK IN ADVANCE. Backup: Sierra Dog Ventures (714) 609-8510."
]], value_input_option=UE)
wsi.update(range_name="N53", values=[["Ian + Mochi dinner — pick up Mochi from daycare, settle in for the evening."]], value_input_option=UE)

# Row 54 (Aug 16)
wsi.update(range_name="J54", values=[[
    "Ian solo with Mochi. Drop Mochi at daycare → Lower Rock Creek Canyon Trail (35 min drive to Tom's Place, US-395). 8-9 mi, 1,900 ft descent through aspen canyon — best trail ride in Eastern Sierra. Full day out."
]], value_input_option=UE)
wsi.update(range_name="K54", values=[["Anny: Emily Bach Party (unavailable)"]], value_input_option=UE)
wsi.update(range_name="L54", values=[[
    "PUP Hiking or Sierra Dog Ventures — same drop/pickup as Aug 15. Or Donna the Dog Lady in Round Valley if in-town options full (50 min drive)."
]], value_input_option=UE)
wsi.update(range_name="N54", values=[["Ian + Mochi — evening walk, Convict Lake area if energy allows."]], value_input_option=UE)

# Row 55 (Aug 17)
wsi.update(range_name="J55", values=[[
    "Ian solo with Mochi. Lighter day — Mammoth Rock / Sherwin Ridge trail AM (10 min, warm-up ride, 4 mi). Afternoon: Hot Creek Geological Site with Mochi (15 min drive, free, stunning thermal pools), then Convict Lake loop (2 mi easy, Mochi can swim)."
]], value_input_option=UE)
wsi.update(range_name="K55", values=[["Anny: Emily Bach Party winding down. May be free PM."]], value_input_option=UE)
wsi.update(range_name="L55", values=[[
    "Ian has Mochi — no daycare needed today (lighter activity day). Mochi welcome at Hot Creek overlook trail + Convict Lake."
]], value_input_option=UE)
wsi.update(range_name="N55", values=[["If Anny free: Hot Creek + Convict Lake together with Mochi. Easy, beautiful, dog-friendly."]], value_input_option=UE)

print("Done. Main itinerary Mammoth rows updated and Dog Daycare column Q added.")
