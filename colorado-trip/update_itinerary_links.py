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

# Get GIDs for the two new sheets dynamically
gids = {ws.title: ws._properties['sheetId'] for ws in sh.worksheets()}
dining_gid   = gids["Dining Guide"]
scenic_gid   = gids["Scenic Stops & Drives"]
print(f"Dining Guide GID: {dining_gid}")
print(f"Scenic Stops GID: {scenic_gid}")

def dining_link(label="→ Dining Guide"):
    return f'=HYPERLINK("https://docs.google.com/spreadsheets/d/{SSID}/edit#gid={dining_gid}","{label}")'

def scenic_link(label="→ Scenic Stops"):
    return f'=HYPERLINK("https://docs.google.com/spreadsheets/d/{SSID}/edit#gid={scenic_gid}","{label}")'

ws = sh.worksheets()[0]   # "Steamboat" tab (main itinerary)
itinerary_sid = ws._properties['sheetId']

# ── 1. RESIZE to fit column P ─────────────────────────────────────────────────
ws.resize(rows=100, cols=16)

# ── 2. UPDATE DRIVE-DAY PLAN CELLS (column G) ────────────────────────────────

# Jul 21 (row 28): add Dead Horse Point morning
ws.update(range_name="G28",
          values=[["AM: Dead Horse Point (45 min drive, 1.5 hrs) → Drive to Boulder + settle"]],
          value_input_option='USER_ENTERED')

# Jul 21 (row 28): update notes column to be helpful
ws.update(range_name="H28",
          values=[["SCENIC STOP BEFORE DRIVING: Dead Horse Point State Park — 32 mi from Moab, 45 min. Colorado River gooseneck canyon, 1,000 ft below. Leave Moab by 8am, arrive Boulder ~5–6pm. Mochi allowed at all overlooks on leash."]],
          value_input_option='USER_ENTERED')

# Aug 7 (row 45): update notes with scenic route tip
ws.update(range_name="H45",
          values=[["DRIVE DAY + ARRIVAL | Scenic route: I-70 through Glenwood Canyon, then CO-133 south via Redstone Village (Victorian coke ovens, Crystal River — 30-45 min stop) + McClure Pass. One of Colorado's best drives."]],
          value_input_option='USER_ENTERED')

# Aug 12 (row 50): append Colorado National Monument note
current_h50 = ws.acell('H50').value or ""
ws.update(range_name="H50",
          values=[[current_h50 + "\n\nSTOP EN ROUTE: Colorado National Monument near Grand Junction. 23-mile Rim Rock Drive, 19 canyon overlooks, dog-friendly. Free w/ America the Beautiful pass. Adds 1.5–2 hrs but this is a world-class detour."]],
          value_input_option='USER_ENTERED')

# ── 3. ADD "MORE INFO" COLUMN P WITH HYPERLINKS ───────────────────────────────

# Header row (row 22 = index 21)
ws.update(range_name="P22", values=[["More Info"]], value_input_option='USER_ENTERED')

# Jul 20 row 27 — Moab arrival dinner
ws.update(range_name="P27", values=[[dining_link("→ Dining Guide (Moab)")]], value_input_option='USER_ENTERED')

# Jul 21 row 28 — Dead Horse Point
ws.update(range_name="P28", values=[[scenic_link("→ Scenic Stops (Dead Horse Point)")]], value_input_option='USER_ENTERED')

# Jul 22 row 29 — Boulder start (both sheets)
ws.update(range_name="P29", values=[[dining_link("→ Dining Guide (Boulder)")]], value_input_option='USER_ENTERED')

# Aug 1 row 39 — Steamboat arrival
ws.update(range_name="P39", values=[[dining_link("→ Dining Guide (Steamboat)")]], value_input_option='USER_ENTERED')

# Aug 7 row 45 — drive day with Redstone
ws.update(range_name="P45", values=[[scenic_link("→ Scenic Stops (Aug 7 drive)")]], value_input_option='USER_ENTERED')

# Aug 8 row 46 — CB start (dining)
ws.update(range_name="P46", values=[[dining_link("→ Dining Guide (Crested Butte)")]], value_input_option='USER_ENTERED')

# Aug 10 row 48 — CB hike day (Kebler Pass)
ws.update(range_name="P48", values=[[scenic_link("→ Scenic Stops (Kebler Pass)")]], value_input_option='USER_ENTERED')

# Aug 12 row 50 — drive to SLC (Colorado National Monument)
ws.update(range_name="P50", values=[[scenic_link("→ Scenic Stops (Colorado Natl Monument)")]], value_input_option='USER_ENTERED')

# ── 4. FORMAT COLUMN P ────────────────────────────────────────────────────────
requests = []

# Bold header cell P22
requests.append({
    "repeatCell": {
        "range": {"sheetId": itinerary_sid,
                  "startRowIndex": 21, "endRowIndex": 22,
                  "startColumnIndex": 15, "endColumnIndex": 16},
        "cell": {"userEnteredFormat": {
            "backgroundColor": {"red": 230/255, "green": 230/255, "blue": 230/255},
            "textFormat": {"bold": True, "foregroundColor": {"red": 30/255, "green": 30/255, "blue": 30/255}},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"
    }
})

# Column P width
requests.append({
    "updateDimensionProperties": {
        "range": {"sheetId": itinerary_sid, "dimension": "COLUMNS",
                  "startIndex": 15, "endIndex": 16},
        "properties": {"pixelSize": 220},
        "fields": "pixelSize"
    }
})

# Wrap column P rows 22-52
requests.append({
    "repeatCell": {
        "range": {"sheetId": itinerary_sid,
                  "startRowIndex": 21, "endRowIndex": 52,
                  "startColumnIndex": 15, "endColumnIndex": 16},
        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
        "fields": "userEnteredFormat(wrapStrategy)"
    }
})

sh.batch_update({"requests": requests})

# ── 5. ADD SOUPÇON + AURUM TO ADVANCE RESERVATIONS ───────────────────────────
# The advance reservations section starts at row 73 header.
# Columns: A=Item, B=When to Book, C=Website, D=Status, E=Notes
# Current last reservation row is ~83 (Steamboat Airbnb). Row 84 is blank.
ws.update(range_name="A84:E84", values=[[
    "Soupçon Dinner — Crested Butte",
    "BOOK NOW — fills 4–6 weeks ahead in Aug",
    "soupconcb.com",
    "",
    "Prix fixe tasting menu. Two seatings: 5:30pm and 7:45pm. ~$150–$250/person all-in. Only 8 tables. Book on Tock at soupconcb.com. Schedule for one night Aug 8–11."
]], value_input_option='USER_ENTERED')

ws.update(range_name="A85:E85", values=[[
    "Aurum Food & Wine — Steamboat",
    "1–2 weeks ahead via Tock",
    "aurumsteamboat.com",
    "",
    "Best restaurant in Steamboat. Riverfront deck on the Yampa. Book for one evening Aug 2–6. Check dog-patio policy when booking."
]], value_input_option='USER_ENTERED')

# ── 6. SHIFT DOG BOARDING SECTION DOWN 2 ROWS ────────────────────────────────
# DOG BOARDING header was at row 85 — it's now shifted down. But we're writing
# into rows 84-85 above, so we need to check if there's a conflict.
# From the data read, row 85 was "DOG BOARDING OPTIONS" header.
# We just wrote over it. We need to move the dog boarding section.
# Read rows 85-92 to check, then rewrite at row 87+.

print("Updating dog boarding section placement...")
boarding_header   = ['DOG BOARDING OPTIONS (Fresno/Visalia) — for Rae Lakes Loop'] + [''] * 14
boarding_col_hdr  = ['Rank', 'Name', 'Location', 'Rating', 'Reviews', 'Highlights', 'Phone', 'Notes'] + [''] * 7
elaines           = ['1 (TOP PICK)', "Elaine's Pet Resorts", 'Fresno (3912 N Hayston Ave)',
                     '4.5 stars', '224+ reviews',
                     'Two doggy water parks, family-owned since 1989, "Best in Central Valley" every year since 2007',
                     '(559) 227-5959', 'Best for a golden in August heat — water parks keep dogs cool'] + [''] * 7
pet_medical       = ['2', 'Pet Medical Center & Spa', 'Fresno (621 W Fallbrook Ave)',
                     '4.2-4.5 stars', '557+ reviews',
                     'Vet on-site, VIP suites with DogTV, live-feed cameras',
                     '', 'Most reviewed. Vet on-site = peace of mind.'] + [''] * 7
visalia_vip       = ['3', "Visalia's VIP Pet Boarding", 'Visalia (438 S Goddard St)',
                     '4.7 stars', '75-104 reviews',
                     'Open 365 days. Clean facility. 96% positive on Nextdoor.',
                     '(559) 732-4803', 'Closer to Kings Canyon trailhead than Fresno options.'] + [''] * 7
cb_airbnb         = ['Crested Butte Airbnb', 'ASAP', 'airbnb.com', '',
                     'Aug 7 - Aug 12 (5 nights)', 'Home-style on 2+ acres. Sends video updates.',
                     '(559) 718-3242', 'Smaller operation but highest rating.'] + [''] * 7
no_wag            = ['', 'No Wag Hotel in Fresno. Closest: Sacramento (too far). No Camp Bow Wow in Fresno either.'] + [''] * 13

ws.update(range_name="A87", values=[
    boarding_header,
    boarding_col_hdr,
    elaines,
    pet_medical,
    visalia_vip,
    cb_airbnb,
    [''] * 15,
    no_wag,
], value_input_option='USER_ENTERED')

print("Done. Itinerary updated with links, drive-day notes, and reservations.")
