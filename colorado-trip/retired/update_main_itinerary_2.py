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

# Get sheet GIDs
gids = {ws.title: ws._properties['sheetId'] for ws in sh.worksheets()}
activities_gid = gids["Activities — Hikes, Runs & MTB"]
dining_gid     = gids["Dining Guide"]
scenic_gid     = gids["Scenic Stops & Drives"]
more_gid       = gids["More Things to Consider"]

def link(label, gid):
    return f'=HYPERLINK("https://docs.google.com/spreadsheets/d/{SSID}/edit#gid={gid}","{label}")'

ws = sh.worksheets()[0]   # "Steamboat" — main itinerary
UE = 'USER_ENTERED'

# ── ROW 25 (Jul 18 — Lake Tahoe day) ─────────────────────────────────────────
# G25: Plan
ws.update(range_name="G25",
          values=[["Drive to Lake Tahoe  |  AM: split activities (see plans) + PM Shakespeare Festival"]],
          value_input_option=UE)

# J25: Ian Plan
ws.update(range_name="J25",
          values=[["AM: MTB — Northstar Bike Park (15 min from Truckee, ~$85-90, Ikon Pass discounts) OR Hole in the Ground Loop (16 mi, 2,200 ft technical trail ride, 15 min from Truckee). → See Activities tab."]],
          value_input_option=UE)

# K25: Anny Plan
ws.update(range_name="K25",
          values=[["AM hike with Mochi: Page Meadows (wildflower meadow loop, 5-8 mi, 15 min from Tahoe City) or Donner Lake Rim Trail (ridge walk, Donner Lake views, 10 min from Truckee). → See Activities tab."]],
          value_input_option=UE)

# L25: Mochi Plan
ws.update(range_name="L25",
          values=[["Hikes with Anny — Page Meadows or Donner Lake Rim Trail"]],
          value_input_option=UE)

# M25: Ian + Anny Plan
ws.update(range_name="M25",
          values=[["Lunch together after AM activities — Bridgetender Tavern (riverfront, dog patio, Tahoe City) or Alibi Ale Works (Truckee, confirmed dog-friendly). → Dining Guide tab."]],
          value_input_option=UE)

# N25: Everyone Together Plan — already has Shakespeare Festival info, leave as-is

# P25: More Info links
ws.update(range_name="P25",
          values=[[link("→ Activities (Tahoe)", activities_gid)]],
          value_input_option=UE)

# ── ROW 51 (Aug 13 — SLC → Ely) ──────────────────────────────────────────────
current_h51 = ws.acell('H51').value or ""
ws.update(range_name="H51",
          values=[[current_h51 + "\n\nELY OVERNIGHT: Nevada Northern Railway Museum (1100 Ave A) — original 1906 steam depot + roundhouse. Museum open Mon–Sat ~8am–5pm. Visit Aug 14 AM before driving to Mammoth. EXCURSION TRAIN (the real experience) runs Sat–Sun only — not available this trip (Thu arrival). → Scenic Stops tab."]],
          value_input_option=UE)

ws.update(range_name="P51",
          values=[[link("→ Scenic Stops (NNR)", scenic_gid)]],
          value_input_option=UE)

# ── ROWS 53–55 (Aug 15–17 — Mammoth, Anny at bach party, Ian free) ──────────

# Aug 15 (row 53)
ws.update(range_name="J53",
          values=[["AM: Mammoth Mountain Bike Park — first full day (gondola, expert terrain: Kamikaze, Bullet Downhill, Skid Marks). ~$65-80/day. Ikon Pass: 2 free days. Go Monday Aug 17 if wanting lighter weekend crowds."]],
          value_input_option=UE)
ws.update(range_name="K53",
          values=[["Anny: Emily Bach Party"]],
          value_input_option=UE)
ws.update(range_name="L53",
          values=[["DOG DAYCARE — PUP Hiking Company, Mammoth Lakes: (760) 582-2176 / puphikingcompany.com. Off-leash pack hikes, 8am–4:30pm. BOOK NOW — August fills fast. 6-8 weeks min."]],
          value_input_option=UE)
ws.update(range_name="P53",
          values=[[link("→ Activities (Mammoth MTB)", activities_gid)]],
          value_input_option=UE)

# Aug 16 (row 54)
ws.update(range_name="J54",
          values=[["AM: Lower Rock Creek Canyon Trail (35 min drive to Tom's Place on US-395). Best trail ride in Eastern Sierra — 8-9 mi, 1,900 ft descent, fast singletrack + rock gardens through aspen canyon. Worth the drive."]],
          value_input_option=UE)
ws.update(range_name="K54",
          values=[["Anny: Emily Bach Party"]],
          value_input_option=UE)
ws.update(range_name="L54",
          values=[["Dog daycare: PUP Hiking Co or Donna the Dog Lady (Round Valley, ~50 min, free-range boarding, (760) 387-2331)"]],
          value_input_option=UE)
ws.update(range_name="P54",
          values=[[link("→ Activities (Mammoth MTB)", activities_gid)]],
          value_input_option=UE)

# Aug 17 (row 55)
ws.update(range_name="J55",
          values=[["AM: Mammoth Mountain Bike Park day 2 (Monday — lighter crowds). OR morning trail ride at Mammoth Rock / Sherwin Ridge (10 min, warm-up loop). Afternoon: Hot Creek Geological Site + Convict Lake loop with Mochi (if Anny back from bach party)."]],
          value_input_option=UE)
ws.update(range_name="K55",
          values=[["Anny: Emily Bach Party (winding down). Convict Lake loop with Mochi if free in PM — easy 2-mi, stunning alpine lake, 20 min south on US-395."]],
          value_input_option=UE)
ws.update(range_name="L55",
          values=[["Daycare morning if needed. Pick up Mochi for afternoon Convict Lake walk if both Ian + Anny are free."]],
          value_input_option=UE)
ws.update(range_name="P55",
          values=[[link("→ Scenic Stops (Hot Creek)", scenic_gid)]],
          value_input_option=UE)

# ── ADVANCE RESERVATIONS — dog daycare entries ────────────────────────────────
# Add after existing reservation rows. Looking at current state:
# Row 84: Soupçon (added previously)
# Row 85: Aurum Food & Wine (added previously)
# Boarding section moved to row 87+
# Add daycare entries at rows ~86

ws.update(range_name="A86:E86", values=[[
    "PUP Hiking Company — Mammoth dog daycare",
    "Book 6–8 weeks ahead (June for Aug dates)",
    "puphikingcompany.com",
    "",
    "Off-leash pack hiking daycare. (760) 582-2176. Needed Aug 15-17 while Ian rides and Anny is at bach party. Off-leash + Garmin-tracked — best option for an active young golden in Mammoth."
]], value_input_option=UE)

ws.update(range_name="A87:E87", values=[[
    "Donna the Dog Lady — backup daycare (Bishop area)",
    "Book well ahead — small operation, high August demand",
    "bishopvisitor.com/places/donna-the-dog-lady/",
    "",
    "(760) 387-2331 or (760) 873-8405. Free-range boarding near Bishop (~50 min from Mammoth). Fallback if PUP Hiking is full. Also good for single-day drop if driving through Bishop."
]], value_input_option=UE)

print("Done. Main itinerary updated for Jul 18, Aug 13, Aug 15-17 + reservations.")
