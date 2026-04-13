import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

ws = sh.add_worksheet(title="Todo — Todoist", rows=60, cols=5)
sheet_id = ws._properties['sheetId']

def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

WHITE  = rgb(255, 255, 255)
BLACK  = rgb(20, 20, 20)
LGRAY  = rgb(245, 245, 245)

# Section header colors (dark enough for white text)
C_WEEK    = rgb(183, 28,  28)   # urgent red
C_APRIL   = rgb(230, 81,   0)   # orange
C_MAY     = rgb(245, 127,  23)  # amber — dark text
C_JUNE    = rgb(27,  94,  32)   # forest green
C_JULY    = rgb(13,  71, 161)   # navy blue
C_ROLLING = rgb(74,  20, 140)   # purple
C_MAMMOTH = rgb(62,  39,  35)   # dark brown

def fmt_row(row_i, bg, text_color=None, bold=True, end_col=5, italic=False):
    if text_color is None:
        text_color = WHITE
    return {"repeatCell": {
        "range": {"sheetId": sheet_id,
                  "startRowIndex": row_i, "endRowIndex": row_i+1,
                  "startColumnIndex": 0, "endColumnIndex": end_col},
        "cell": {"userEnteredFormat": {
            "backgroundColor": bg,
            "textFormat": {"bold": bold, "italic": italic,
                           "foregroundColor": text_color},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"
    }}

def merge(row_i, end_col=5):
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

def wrap_range(start_col, end_col, start_row, end_row):
    return {"repeatCell": {
        "range": {"sheetId": sheet_id,
                  "startRowIndex": start_row, "endRowIndex": end_row,
                  "startColumnIndex": start_col, "endColumnIndex": end_col},
        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
        "fields": "userEnteredFormat(wrapStrategy)"
    }}

# ── DATA ─────────────────────────────────────────────────────────────────────
# Columns: A=Todoist Task (copy this) | B=Category | C=Due By | D=Contact/Site | E=Notes

COLS = ["Paste column A directly into Todoist — each row is one task",
        "Category", "Due By", "Contact / Website", "Notes"]

# Format: [todoist_task, category, due_by, contact, notes]
# todoist_task must end with  !!N DueDate  for Todoist to parse it

ROWS = [
    # ── THIS WEEK ────────────────────────────────────────────────────────────
    ["THIS WEEK — Do before Apr 13", "", "", "", ""],  # header

    ["Book Boulder Airbnb (Jul 22–31, ~9 nights) !!1 Apr 9",
     "Housing", "Apr 9", "airbnb.com",
     "Home with dog yard preferred. 2 adults + golden retriever. Near Pearl St or Mapleton Hill area."],

    ["Book Steamboat Springs Airbnb (Aug 1–6, 5 nights) !!1 Apr 9",
     "Housing", "Apr 9", "airbnb.com",
     "Dog-friendly, near Old Town / Yampa River area. 2 adults + golden retriever."],

    ["Book Crested Butte Airbnb (Aug 7–12, 5 nights) !!1 Apr 9",
     "Housing", "Apr 9", "airbnb.com",
     "Dog-friendly. Can be in town or CB South. 2 adults + golden retriever."],

    ["Book Soupçon dinner — Crested Butte !!1 Apr 10",
     "Dining", "Apr 10", "soupconcb.com",
     "Prix fixe tasting menu, only 8 tables, fills 4–6 weeks out in Aug. Book on Tock. 5:30pm or 7:45pm seating, one night Aug 8–11."],

    ["Call PUP Hiking Company — Mammoth dog daycare (Aug 15–17) !!1 Apr 10",
     "Mochi / Daycare", "Apr 10", "(760) 582-2176 / puphikingcompany.com",
     "Off-leash pack hiking daycare 8am–4:30pm. Need 3 days: Aug 15, 16, and possibly 17. August fills 6–8 weeks out — book NOW."],

    ["Call Sierra Dog Ventures — Mammoth daycare backup !!1 Apr 10",
     "Mochi / Daycare", "Apr 10", "sierradogventures.com",
     "Book as backup if PUP Hiking is full. Same dates: Aug 15–17."],

    ["Call Red Rover Resort — Steamboat dog daycare !!1 Apr 10",
     "Mochi / Daycare", "Apr 10", "(970) 871-0888",
     "Split hours: drop-off 8am–12pm, pick-up 4–8pm. Confirm Aug 2–6 availability and whether split hours work for bike rides."],

    ["Call Oh Be Dogful — Crested Butte daycare !!1 Apr 10",
     "Mochi / Daycare", "Apr 10", "(970) 349-5155",
     "⚠️ WEEKDAYS ONLY (Mon–Fri). Ian's bike park days are Sat Aug 8 + Sun Aug 9 — daycare NOT available those days. Call to confirm Aug 11 (Mon) availability at minimum."],

    # ── BY END OF APRIL ───────────────────────────────────────────────────────
    ["BY END OF APRIL", "", "", "", ""],  # header

    ["Call Elaine's Pet Resorts — Fresno dog boarding (Aug 19–22) !!1 Apr 25",
     "Mochi / Boarding", "Apr 25", "(559) 227-5959",
     "Top pick for Rae Lakes Loop stay. Family-owned since 1989, two doggy water parks. 3 nights Aug 19–22 while Ian + Anny backpack Kings Canyon."],

    ["Book Wanderlust Mutts — Moab dog adventure daycare (Jul 20–21) !!2 Apr 25",
     "Mochi / Daycare", "Apr 25", "wanderlustmutts.com",
     "Off-leash trail adventure daycare, $90/day. Ideal for Moab days when Ian + Anny want to hike Arches or do canyon activities. Call to confirm availability."],

    ["Buy America the Beautiful Annual Pass !!2 Apr 25",
     "Logistics", "Apr 25", "store.usgs.gov/store/product/annual-pass",
     "$80/year. Covers: Arches NP, Dead Horse Point, Colorado Natl Monument, Kings Canyon, Yosemite (Tioga Pass). Pays for itself on this trip."],

    ["Call Dolly's Mountain Shuttle — Crested Butte MTB shuttle !!2 Apr 30",
     "Activities / Ian", "Apr 30", "(970) 209-3368",
     "Shuttle for Teocalli Ridge and other point-to-point rides out of CB. Book Aug 8–11 dates. Fills up in summer."],

    ["Buy Red Rocks Killer Queen tickets — Jul 18 !!2 Apr 30",
     "Events", "Apr 30", "axs.com / redrocksonline.com",
     "Killer Queen tribute show at Red Rocks. Jul 18 evening after driving to Truckee. Check exact date — confirm it's Jul 18."],

    ["Buy Lake Tahoe Shakespeare Festival tickets — Jul 18 !!2 Apr 30",
     "Events", "Apr 30", "tahoeshakespeare.com",
     "Outdoor Shakespeare at Sand Harbor, Lake Tahoe. Jul 18 evening — confirm show date aligns with arrival day. PM show."],

    # ── BY END OF MAY ─────────────────────────────────────────────────────────
    ["BY END OF MAY", "", "", "", ""],  # header

    ["Check Colorado Shakespeare Festival dates (Boulder, late Jul) !!2 May 15",
     "Events", "May 15", "coloradoshakes.org",
     "Boulder outdoor Shakespeare. Jul 22–Jul 31 stay. See what's playing and whether it's worth booking."],

    ["Check Colorado Music Festival schedule (Boulder, late Jul) !!2 May 15",
     "Events", "May 15", "coloradomusicfest.org",
     "Boulder classical/pop festival. Check late-July schedule — free lawn tickets available. Chautauqua Park venue."],

    ["Check Strings Music Festival schedule (Steamboat, Aug 1–6) !!2 May 15",
     "Events", "May 15", "stringsmusicfestival.com",
     "Popular Steamboat summer music series. See what's on Aug 1–6 and buy tickets if something good lines up."],

    ["Call Donna the Dog Lady — Mammoth/Bishop backup daycare !!2 May 15",
     "Mochi / Daycare", "May 15", "(760) 387-2331 or (760) 873-8405",
     "Free-range boarding near Bishop (~50 min from Mammoth). Book as tertiary backup for Aug 15–17 if PUP Hiking + Sierra Dog are both full."],

    ["Book Maroon Bells timed entry at recreation.gov (Aug 8–11) !!1 May 20",
     "Activities", "May 20", "recreation.gov",
     "⚠️ MANDATORY Jun–Oct. Parking entry fills weeks out. Select morning window, one day Aug 8–11 (day trip from CB, 1.5 hr drive). Dogs allowed on leash at Maroon Lake + Crater Lake trail."],

    ["Register Mochi at Truckee-Tahoe Pet Lodge — Jul 17–18 !!2 May 20",
     "Mochi / Daycare", "May 20", "truckeetahoe.com",
     "Register online ahead of time — required before first day. ~$60/day. For Jul 17 or 18 Truckee stopover days."],

    ["Confirm Mochi's Bordetella vaccine is current !!2 May 20",
     "Mochi / Health", "May 20", "your vet",
     "Required by most daycare and boarding facilities. Vaccine valid for 6–12 months depending on type. Confirm before June to rebook if needed."],

    ["Book Aurum Food & Wine dinner — Steamboat !!2 May 20",
     "Dining", "May 20", "aurumsteamboat.com",
     "Best restaurant in Steamboat. Riverfront deck on the Yampa. Book 1–2 weeks ahead via Tock for Aug 2–6. Ask about dog-friendly patio."],

    ["Buy Steamboat Pro Rodeo Series tickets !!3 May 25",
     "Events", "May 25", "steamboatrodeo.com",
     "Runs every Fri + Sat summer. Classic western experience — worth one evening Aug 1–6. Check schedule."],

    # ── BY JUNE ───────────────────────────────────────────────────────────────
    ["BY JUNE", "", "", "", ""],  # header

    ["Complete Camp Bow Wow interview — Boulder (plan Jul 22–23 arrival) !!2 Jun 1",
     "Mochi / Daycare", "Jun 1", "campbowwow.com/boulder",
     "Interview required before first drop. $41/day. Book interview for Jul 22 or 23 (first Boulder days) so Mochi is cleared for Jul 24+. Call ahead to schedule."],

    ["Call Ride Workshop — Steamboat MTB bike rental !!2 Jun 1",
     "Activities / Ian", "Jun 1", "rideworkshop.com / (970) 871-0880",
     "Book high-end MTB rental for Aug 1–6 Steamboat days. Full-sus preferred. Reserve early for August peak."],

    ["Call Front Range Guides — optional guided hike !!3 Jun 15",
     "Activities / Optional", "Jun 15", "frontrangeguides.com",
     "Optional guided hike option for Boulder (Jul 22–31). Check Eldorado Canyon or Indian Peaks Wilderness options. Nice if Ian + Anny want a guided day together."],

    # ── JULY ──────────────────────────────────────────────────────────────────
    ["JULY TASKS", "", "", "", ""],  # header

    ["Book Strawberry Park Hot Springs — Steamboat (evening) !!2 Jul 1",
     "Activities", "Jul 1", "strawberryhotsprings.com",
     "Dog-friendly hot springs north of Steamboat. No dogs after dark. Reservations recommended for Aug peak. Plan one evening Aug 1–6."],

    ["Confirm van A/C is working before departure !!1 Jul 1",
     "Logistics / Vehicle", "Jul 1", "your mechanic",
     "Multi-week summer trip through Nevada and Utah desert. Departing Jul 17. Get A/C serviced if not confirmed since last summer."],

    ["Rent or buy bear canisters — for Kings Canyon !!1 Jul 15",
     "Gear / Rae Lakes", "Jul 15", "recreation.gov or Roads End permit station",
     "NPS-required for all Kings Canyon overnight trips — no hanging food. 2 canisters for Ian + Anny. Rent at Roads End Permit Station (Cedar Grove) or buy Garcia/BearVault BV500 ahead."],

    ["Check Ikon Pass MTB benefits — Mammoth + Northstar !!2 Jul 10",
     "Activities / Ian", "Jul 10", "ikonpass.com",
     "Ikon Pass includes 2 free days at Mammoth Mountain Bike Park ($65–80/day value) and discounts at Northstar (Jul 18). Confirm which tier you have and what's covered."],

    ["Buy Mammoth Mountain Bike Park lift tickets !!2 Jul 15",
     "Activities / Ian", "Jul 15", "mammothmountain.com",
     "If Ikon Pass days are used up or don't cover MTB, buy individual tickets ahead. Aug 15–16 (possibly Aug 17). Online cheaper than at the window."],

    # ── ROLLING 15-DAY ────────────────────────────────────────────────────────
    ["ROLLING 15-DAY — Book close to the date on recreation.gov", "", "", "", ""],  # header

    ["Book Eldorado Canyon State Park entry (~Jul 12, 15-day window) !!2 Jul 12",
     "Activities / Boulder", "Jul 12", "cpw.state.co.us / recreation.gov",
     "Timed entry required in summer. Opens 15 days out at 8am. Target Jul 26–28 visit during Boulder stay. Dog-friendly on leash on most trails."],

    ["Book Brainard Lake Recreation Area entry (~Jul 16, 15-day window) !!2 Jul 16",
     "Activities / Boulder", "Jul 16", "recreation.gov",
     "Timed entry required summer weekends. 15-day rolling window. Target a weekday Jul 28–31. Dogs on leash. Indian Peaks Wilderness trailhead — Mitchell Lake + Blue Lake are dog-friendly."],

    # ── BEFORE MAMMOTH ────────────────────────────────────────────────────────
    ["BEFORE MAMMOTH (Aug 1–14) — Final prep", "", "", "", ""],  # header

    ["Confirm PUP Hiking Co bookings are confirmed (Aug 15–17) !!1 Aug 1",
     "Mochi / Daycare", "Aug 1", "(760) 582-2176",
     "Call to reconfirm all 3 days are locked in. Get confirmation in writing / text. Have Sierra Dog Ventures as backup."],

    ["Look up Lower Rock Creek Canyon trailhead directions !!3 Aug 10",
     "Activities / Ian", "Aug 10", "alltrails.com — Lower Rock Creek Canyon",
     "35 min drive from Mammoth Lakes to Tom's Place on US-395. 8–9 mi, 1,900 ft descent. Best plan for Aug 16 (second Mammoth day)."],
]

# ── SEPARATE ROWS: which are headers vs data ──────────────────────────────
HEADER_LABELS = {
    "THIS WEEK — Do before Apr 13",
    "BY END OF APRIL",
    "BY END OF MAY",
    "BY JUNE",
    "JULY TASKS",
    "ROLLING 15-DAY — Book close to the date on recreation.gov",
    "BEFORE MAMMOTH (Aug 1–14) — Final prep",
}

HEADER_COLORS = {
    "THIS WEEK — Do before Apr 13":                           C_WEEK,
    "BY END OF APRIL":                                        C_APRIL,
    "BY END OF MAY":                                          C_MAY,
    "BY JUNE":                                                C_JUNE,
    "JULY TASKS":                                             C_JULY,
    "ROLLING 15-DAY — Book close to the date on recreation.gov": C_ROLLING,
    "BEFORE MAMMOTH (Aug 1–14) — Final prep":                 C_MAMMOTH,
}

HEADER_TEXT_COLOR = {
    "BY END OF MAY": BLACK,   # amber bg needs dark text
}

# ── WRITE DATA ────────────────────────────────────────────────────────────────
# Row 0 = title, Row 1 = instructions, Row 2 = col headers, Row 3+ = data
TITLE_ROW   = ["Colorado 2026 — Master Todo List (Paste column A into Todoist)"] + [""] * 4
INSTRUCT_ROW = ["↓ Copy any task from column A and paste into Todoist. Priority + date are embedded."] + [""] * 4

all_rows = [TITLE_ROW, INSTRUCT_ROW, COLS] + ROWS
ws.update(range_name="A1", values=all_rows, value_input_option='USER_ENTERED')

# ── FORMATTING ────────────────────────────────────────────────────────────────
requests = []

# Title row
requests.append(merge(0))
requests.append(fmt_row(0, rgb(15, 23, 42), WHITE, bold=True))

# Instructions row
requests.append(merge(1))
requests.append(fmt_row(1, rgb(40, 40, 40), rgb(200, 230, 200), bold=False, italic=True))

# Column headers row (row index 2)
requests.append(fmt_row(2, rgb(230, 230, 230), BLACK, bold=True))

# Section header rows + data row alternating shading
for i, row in enumerate(ROWS):
    sheet_row_i = i + 3   # offset: title + instruct + col headers = 3 rows
    label = row[0]
    if label in HEADER_LABELS:
        requests.append(merge(sheet_row_i))
        bg = HEADER_COLORS[label]
        txt = HEADER_TEXT_COLOR.get(label, WHITE)
        requests.append(fmt_row(sheet_row_i, bg, txt, bold=True))
    else:
        # Alternating light shading for readability
        bg_color = rgb(250, 250, 250) if (i % 2 == 0) else rgb(240, 240, 240)
        requests.append(fmt_row(sheet_row_i, bg_color, BLACK, bold=False))

# Column widths: A=480, B=120, C=100, D=200, E=280
requests += col_widths([(0, 480), (1, 120), (2, 100), (3, 200), (4, 280)])

# Wrap all columns for data rows (rows 3 onward)
total_rows = len(all_rows)
for col in range(5):
    requests.append(wrap_range(col, col+1, 2, total_rows))

# Freeze top 3 rows
requests.append({"updateSheetProperties": {
    "properties": {
        "sheetId": sheet_id,
        "gridProperties": {"frozenRowCount": 3}
    },
    "fields": "gridProperties.frozenRowCount"
}})

sh.batch_update({"requests": requests})
print(f"Done. 'Todo — Todoist' sheet created with {len(ROWS)} rows. sheet_id={sheet_id}")
