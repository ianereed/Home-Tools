import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

# ══════════════════════════════════════════════════════════════════════════════
# 1. ACTIVITIES SHEET — add Tahoe + Mammoth sections (starting at row 52)
# ══════════════════════════════════════════════════════════════════════════════
ws_act = sh.worksheet("Activities — Hikes, Runs & MTB")
ws_act.resize(rows=80, cols=11)
act_sid = ws_act._properties['sheetId']

HEADERS = ["Activity", "Area", "Date Window", "Type", "Distance", "Elevation Gain",
           "", "Extra Driving (RT)", "Trailhead", "Link", "Notes"]
E = [""] * 11   # empty row

tahoe_mtb = [
    ["Northstar California Bike Park", "Lake Tahoe", "Jul 18", "Lift-Served DH/Enduro",
     "—", "~2,000 ft vertical", "", "15 min (Northstar Dr, Truckee)",
     "Northstar Drive, Truckee", "northstarcalifornia.com",
     "~100 miles of trails, 2 lifts. Technical DH black + double-black lines (TNT, River Sticks, Livewire). Can get crowded on Bay Area summer weekends — go early. ~$85–90/day; check Ikon Pass discounts. Dogs NOT allowed."],
    ["Hole in the Ground Loop", "Lake Tahoe", "Jul 18", "Trail Ride (loop)",
     "16 mi", "~2,200 ft", "", "15 min (Donner Summit/I-80)",
     "Donner Summit trailhead off I-80", "truckeetrails.org",
     "Most technical trail ride near Truckee. Alpine granite slabs, rooty sections, rocky chunky terrain with real consequence. Best enduro option in the area. Dry by mid-July at elevation. Dogs on leash."],
    ["Glass Mountain + Painted Rock", "Lake Tahoe", "Jul 18", "Trail Ride (loop)",
     "9 mi (14 mi w/ extension)", "~1,200 ft", "", "5 min (Tahoe City, off Hwy 28)",
     "Tahoe XC Center, Dollar Point, Tahoe City", "tamba.org",
     "Best mix of tech + flow near Tahoe City — fast flowy stretches, rocky technical sections, lake views. Add Painted Rock extension (+5 mi, +1,200 ft) for a full enduro day. Closest trail to town. Ask at Olympic Bike Shop on Hwy 89 for current conditions."],
]

tahoe_hike = [
    ["Page Meadows", "Lake Tahoe", "Jul 18", "Day Hike (Anny + Mochi)",
     "5–8 mi", "~700 ft", "", "15 min (Alpine Meadows Rd)",
     "Snow Crest Rd off Alpine Meadows Rd, Tahoe City area", "",
     "Rolling singletrack through forest opening into a beautiful alpine wildflower meadow. Mid-July is peak bloom. Local favorite, not a tourist destination — stays relatively quiet. Dogs on leash. Flexible distance."],
    ["Donner Lake Rim Trail", "Lake Tahoe", "Jul 18", "Day Hike (Anny + Mochi)",
     "4–8 mi", "~800 ft", "", "10 min (Castle Valley Rd, Truckee)",
     "Castle Valley Road, Truckee", "",
     "High ridge walk with panoramic views of Donner Lake and the Sierra. Less crowded than the lakeside Tahoe trails. Choose any segment as an out-and-back. Dogs on leash; good shade in the forested lower sections."],
]

mammoth_mtb = [
    ["Mammoth Mountain Bike Park", "Mammoth Lakes", "Aug 15–17", "Lift-Served DH/Enduro",
     "—", "3,100 ft vertical", "", "5 min (resort gondola base)",
     "Mammoth Mountain Resort — gondola from Main Lodge", "mammothmountain.com",
     "80+ miles, 3,100 ft descent. Expert lines: Kamikaze Downhill, Bullet Downhill, Skid Marks (one of longest descents), Off the Top. High altitude riding (9,000–11,000 ft) — acclimate first day. Ikon Pass: 2 free days. ~$65–80/day. Solid expert park; not Whistler but worth a full day. Go Monday (Aug 17) to avoid weekend crowds. Dogs NOT allowed."],
    ["Lower Rock Creek Canyon", "Mammoth Lakes", "Aug 15–17", "Trail Ride (point-to-point)",
     "8–9 mi", "~1,200 ft gain\n~1,900 ft descent", "", "35 min (Tom's Place, US-395)",
     "Rock Creek Rd off US-395, Tom's Place area", "easternsierramountainbiking.com",
     "Best trail ride in the Eastern Sierra, full stop. Dramatic canyon with aspen groves, fast singletrack, rock gardens. Often shuttled downhill for maximum descent. Well worth the drive. Plan a full day with the drive. Dogs on leash."],
    ["Mammoth Rock / Sherwin Ridge", "Mammoth Lakes", "Aug 15–17", "Trail Ride (loop)",
     "~4 mi", "~400 ft", "", "10 min (Sherwin Creek Rd)",
     "Sherwin Creek Rd, past the Borrow Pit", "",
     "Close-to-town trail traversing the volcanic ridge above Snowcreek Meadow. More intermediate than expert, but views are great and it's a no-fuss morning warm-up or add-on. Shared with hikers."],
]

mammoth_hike = [
    ["Convict Lake Loop", "Mammoth Lakes", "Aug 15–17", "Day Hike (Anny + Mochi)",
     "2 mi", "~200 ft", "", "20 min (US-395 south)",
     "Convict Lake Rd exit off US-395", "",
     "Easy loop around a stunning deep alpine lake surrounded by dramatic 12,000-ft peaks. One of the most scenic quick walks near Mammoth. Dogs on leash. Mochi can access the shoreline for a swim. Good chill morning activity for Anny between bach party events."],
]

# Row 52–72 (1-indexed). Data array indices 0–20 correspond to rows 52–72.
act_data = (
    [E] +                                                                # row 52
    [["LAKE TAHOE  |  Jul 18 stop"] + [""] * 10] +                      # row 53
    [HEADERS] +                                                          # row 54
    tahoe_mtb +                                                          # rows 55-57
    [E] +                                                                # row 58
    [["LAKE TAHOE HIKES  |  Anny + Mochi"] + [""] * 10] +              # row 59
    [HEADERS] +                                                          # row 60
    tahoe_hike +                                                         # rows 61-62
    [E] +                                                                # row 63
    [["MAMMOTH LAKES  |  Aug 15–17  (Ian free — Anny at bach party)"] + [""] * 10] +  # row 64
    [HEADERS] +                                                          # row 65
    mammoth_mtb +                                                        # rows 66-68
    [E] +                                                                # row 69
    [["MAMMOTH HIKES  |  Anny + Mochi"] + [""] * 10] +                 # row 70
    [HEADERS] +                                                          # row 71
    mammoth_hike                                                         # row 72
)
ws_act.update(range_name="A52", values=act_data)

# Colors
TAHOE_MAIN = rgb(0,   77,  64)   # very dark teal
TAHOE_SUB  = rgb(38, 166, 154)   # medium teal
MAMM_MAIN  = rgb(180,  40,   0)  # volcanic brick red
MAMM_SUB   = rgb(230,  81,   0)  # medium orange

# 0-based row indices for formatting (sheet row N → index N-1):
# row 53 → idx 52, row 59 → idx 58, row 64 → idx 63, row 70 → idx 69
# col headers: row 54 → idx 53, row 60 → idx 59, row 65 → idx 64, row 71 → idx 70

requests_act = []
for row_i, bg, is_sub in [
    (52, TAHOE_MAIN, False),
    (58, TAHOE_SUB,  True),
    (63, MAMM_MAIN,  False),
    (69, MAMM_SUB,   True),
]:
    WHITE_TXT = {"red": 1.0, "green": 1.0, "blue": 1.0}
    DARK_TXT  = {"red": 30/255, "green": 30/255, "blue": 30/255}
    tc = DARK_TXT if is_sub else WHITE_TXT
    requests_act += [
        {"mergeCells": {
            "range": {"sheetId": act_sid,
                      "startRowIndex": row_i, "endRowIndex": row_i+1,
                      "startColumnIndex": 0, "endColumnIndex": 11},
            "mergeType": "MERGE_ALL"
        }},
        {"repeatCell": {
            "range": {"sheetId": act_sid,
                      "startRowIndex": row_i, "endRowIndex": row_i+1,
                      "startColumnIndex": 0, "endColumnIndex": 11},
            "cell": {"userEnteredFormat": {
                "backgroundColor": bg,
                "textFormat": {"bold": True, "foregroundColor": tc},
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"
        }},
    ]

COL_HDR = {"red": 230/255, "green": 230/255, "blue": 230/255}
for row_i in [53, 59, 64, 70]:
    requests_act.append({"repeatCell": {
        "range": {"sheetId": act_sid,
                  "startRowIndex": row_i, "endRowIndex": row_i+1,
                  "startColumnIndex": 0, "endColumnIndex": 11},
        "cell": {"userEnteredFormat": {
            "backgroundColor": COL_HDR,
            "textFormat": {"bold": True, "foregroundColor": {"red": 30/255, "green": 30/255, "blue": 30/255}},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"
    }})

sh.batch_update({"requests": requests_act})
print("Activities sheet updated.")

# ══════════════════════════════════════════════════════════════════════════════
# 2. DINING GUIDE — add Lake Tahoe section (starting at row 27)
# ══════════════════════════════════════════════════════════════════════════════
ws_din = sh.worksheet("Dining Guide")
ws_din.resize(rows=42, cols=9)
din_sid = ws_din._properties['sheetId']

TAHOE_BG = rgb(0, 77, 64)   # dark teal
COL_HDR9 = rgb(230, 230, 230)
DARK9    = rgb(30, 30, 30)
WHITE9   = rgb(255, 255, 255)
DIN_HEADERS = ["Restaurant / Place", "City", "Type", "Price", "Reservation",
               "Phone", "Website", "Notes / Must-Know", "Dog Friendly?"]

tahoe_dining = [
    ["Moody's Bistro Bar & Beats", "Truckee", "New American / Bistro", "$$$",
     "Recommended — call or OpenTable", "", "moodysbistroandbeyond.com",
     "Best dinner in the North Tahoe area. Set inside the historic Truckee Hotel. Fried chicken roulade, smoked pork chops, creative cocktails. Confirmed dog-friendly patio with water bowls. Local institution, not a tourist trap.",
     "✅ Patio (dogs welcome)"],
    ["Alibi Ale Works (Public House)", "Truckee", "Craft Brewery / Pub", "$$",
     "Walk-in", "", "alibialeworks.com",
     "Named Best Dog-Friendly Restaurant in the region. Serious craft brewery with real food — not just pub grub. Outdoor seating in downtown Truckee. Lively and casual. Great for lunch or dinner after a ride.",
     "✅ Confirmed dog-friendly"],
    ["Wolfdale's Cuisine Unique", "Tahoe City", "Asian-European Fusion / Fine Dining", "$$$",
     "Reservations recommended", "", "wolfdales.com",
     "Operating since 1978. Sushi, ahi poke, duck spring rolls, excellent wine. Lakeside location in Tahoe City. Best special-occasion dinner option on the north shore. Call ahead re: dog patio.",
     "❓ Call re: dog patio"],
    ["Bridgetender Tavern & Grill", "Tahoe City", "Burgers / Casual", "$$",
     "Walk-in", "", "",
     "Riverfront local landmark at the base of the Truckee River. Excellent burgers, cold beer, great outdoor patio. A Tahoe City institution. Best casual lunch option. Gets busy in July — go at 2–4pm for a quieter sit.",
     "✅ Outdoor patio"],
    ["Spindleshanks", "Kings Beach", "American / Upscale-Casual", "$$$",
     "Recommended (call ahead)", "", "",
     "Set on the historic Old Brockway Golf Course. Eclectic menu — oysters, Berkshire pork, seafood linguini, spring rolls. Three large wooden decks for outdoor dining. Classiest option in Kings Beach. Less touristy than lakefront spots. ~10 min from Tahoe City.",
     "❓ Call re: dog deck"],
]

ED = [""] * 9
din_data = (
    [ED] +                                                         # row 27: blank
    [["LAKE TAHOE  |  Jul 18 stop"] + [""] * 8] +                # row 28: header
    [DIN_HEADERS] +                                               # row 29: col headers
    tahoe_dining                                                  # rows 30-34
)
ws_din.update(range_name="A27", values=din_data)

# Formatting: header at index 27 (row 28), col headers at index 28 (row 29)
requests_din = [
    {"mergeCells": {
        "range": {"sheetId": din_sid,
                  "startRowIndex": 27, "endRowIndex": 28,
                  "startColumnIndex": 0, "endColumnIndex": 9},
        "mergeType": "MERGE_ALL"
    }},
    {"repeatCell": {
        "range": {"sheetId": din_sid,
                  "startRowIndex": 27, "endRowIndex": 28,
                  "startColumnIndex": 0, "endColumnIndex": 9},
        "cell": {"userEnteredFormat": {
            "backgroundColor": TAHOE_BG,
            "textFormat": {"bold": True, "foregroundColor": WHITE9},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"
    }},
    {"repeatCell": {
        "range": {"sheetId": din_sid,
                  "startRowIndex": 28, "endRowIndex": 29,
                  "startColumnIndex": 0, "endColumnIndex": 9},
        "cell": {"userEnteredFormat": {
            "backgroundColor": COL_HDR9,
            "textFormat": {"bold": True, "foregroundColor": DARK9},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"
    }},
    # Wrap notes + reservation columns for new rows
    {"repeatCell": {
        "range": {"sheetId": din_sid,
                  "startRowIndex": 28, "endRowIndex": 35,
                  "startColumnIndex": 7, "endColumnIndex": 8},
        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
        "fields": "userEnteredFormat(wrapStrategy)"
    }},
]
sh.batch_update({"requests": requests_din})
print("Dining Guide updated with Lake Tahoe section.")

# ══════════════════════════════════════════════════════════════════════════════
# 3. SCENIC STOPS — add Mammoth Area + Nevada/Ely sections (starting at row 33)
# ══════════════════════════════════════════════════════════════════════════════
ws_sc = sh.worksheet("Scenic Stops & Drives")
ws_sc.resize(rows=48, cols=7)
sc_sid = ws_sc._properties['sheetId']

MAMM_SC_BG = rgb(191,  54,  12)  # volcanic brick orange
ELY_SC_BG  = rgb(55,   71,  79)  # dark blue-grey (Nevada high desert)
COL_HDR7   = rgb(230, 230, 230)
WHITE7     = rgb(255, 255, 255)
DARK7      = rgb(30,  30,  30)
SC_HEADERS = ["Stop / Place", "When", "Time Needed", "Dog Friendly", "Cost",
              "Why Go", "Notes & Directions"]
ES7 = [""] * 7

mammoth_scenic = [
    ["Hot Creek Geological Site", "Aug 15–17 (any morning)",
     "45–60 min", "✅ Overlook trail on leash", "Free",
     "Natural hydrothermal vents emerge in a cold creek — visible bubbling turquoise pools in a volcanic canyon. One of the most unusual geological sights in California and genuinely unlike anything else on the trip.",
     "~15 min from Mammoth Lakes on Hot Creek Hatchery Rd off Hwy 203. Short walk to the overlook. Note: swimming is banned and dangerous (scalding vents). The overlook trail has great views of the thermal activity. Dogs allowed on leash on the trail."],
]

ely_scenic = [
    ["Nevada Northern Railway Museum", "Aug 13 evening or Aug 14 AM (Ely overnight)",
     "1–2 hrs", "✅ Outdoor areas on leash", "~$10–15/person museum admission",
     "One of the most intact surviving short-line railroad operations in America. Original 1906 depot, steam locomotives, machine shop, and a full operating roundhouse — all in working condition. Unique Western railroad history.",
     "1100 Ave A, Ely, NV 89301. Museum typically open Mon–Sat 8am–5pm (verify at nnry.com). EXCURSION TRAINS (steam locomotive ride) run Sat–Sun and some holidays only — on this trip you arrive Thu Aug 13, so only the static museum is available. If you ever return on a weekend, the 90-min steam excursion is the real experience. Visit briefly on Aug 14 AM before the drive to Mammoth (4.5 hrs, manageable if you leave by 9am)."],
]

sc_data = (
    [ES7] +                                                              # row 33: blank
    [["MAMMOTH LAKES AREA  |  Aug 15–17"] + [""] * 6] +                # row 34: header
    [SC_HEADERS] +                                                       # row 35: col headers
    mammoth_scenic +                                                     # row 36
    [ES7] +                                                              # row 37: blank
    [["NEVADA OVERNIGHT  |  Ely, NV  — Aug 13–14"] + [""] * 6] +      # row 38: header
    [SC_HEADERS] +                                                       # row 39: col headers
    ely_scenic                                                           # row 40
)
ws_sc.update(range_name="A33", values=sc_data)

# 0-based indices: row 34 → idx 33, row 35 → idx 34, row 38 → idx 37, row 39 → idx 38
requests_sc = []
for row_i, bg in [(33, MAMM_SC_BG), (37, ELY_SC_BG)]:
    requests_sc += [
        {"mergeCells": {
            "range": {"sheetId": sc_sid,
                      "startRowIndex": row_i, "endRowIndex": row_i+1,
                      "startColumnIndex": 0, "endColumnIndex": 7},
            "mergeType": "MERGE_ALL"
        }},
        {"repeatCell": {
            "range": {"sheetId": sc_sid,
                      "startRowIndex": row_i, "endRowIndex": row_i+1,
                      "startColumnIndex": 0, "endColumnIndex": 7},
            "cell": {"userEnteredFormat": {
                "backgroundColor": bg,
                "textFormat": {"bold": True, "foregroundColor": WHITE7},
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"
        }},
    ]

for row_i in [34, 38]:
    requests_sc.append({"repeatCell": {
        "range": {"sheetId": sc_sid,
                  "startRowIndex": row_i, "endRowIndex": row_i+1,
                  "startColumnIndex": 0, "endColumnIndex": 7},
        "cell": {"userEnteredFormat": {
            "backgroundColor": COL_HDR7,
            "textFormat": {"bold": True, "foregroundColor": DARK7},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"
    }})

# Wrap Why Go + Notes columns for new rows
for col in [5, 6]:
    requests_sc.append({"repeatCell": {
        "range": {"sheetId": sc_sid,
                  "startRowIndex": 33, "endRowIndex": 41,
                  "startColumnIndex": col, "endColumnIndex": col+1},
        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
        "fields": "userEnteredFormat(wrapStrategy)"
    }})

sh.batch_update({"requests": requests_sc})
print("Scenic Stops updated with Mammoth + Ely sections.")
print("Done.")
