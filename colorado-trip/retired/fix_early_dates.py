"""
Fix date references that became stale after the user moved early-trip dates:
  - Moab arrival: Jul 20 → Jul 21
  - Moab → Boulder drive: Jul 21 → Jul 22
  - Red Rocks: stale Jul 18 in Todo → Jul 30 (matches main Itinerary)
"""

import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
UE = 'USER_ENTERED'

def u(ws, cell, value):
    ws.update(range_name=cell, values=[[value]], value_input_option=UE)
    print(f"  {cell}: {repr(value[:80]) if len(value) > 80 else repr(value)}")

# ── Dining Guide ────────────────────────────────────────────────────────────
print("=== Dining Guide ===")
dg = sh.worksheet('Dining Guide')
u(dg, 'A10', 'MOAB  |  Jul 21 night')
u(dg, 'H12', 'Best non-tourist-trap dinner in Moab. Craft cocktails and small plates, local favorite. Better vibe than the Main St chain spots. Good option for the Jul 21 one-night arrival dinner.')

# ── Scenic Stops & Drives ────────────────────────────────────────────────────
print("\n=== Scenic Stops & Drives ===")
sc = sh.worksheet('Scenic Stops & Drives')
u(sc, 'A3', 'NEVADA / UTAH LEG  |  Jul 18–22')
u(sc, 'B7', 'Jul 22 AM (before Moab → Boulder drive)')

# ── Itinerary — Boulder Airbnb reservation note ──────────────────────────────
print("\n=== Itinerary ===")
it = sh.worksheets()[0]
u(it, 'E82', 'Jul 22 - Aug 1 (10 nights)')

# ── Todo sheet ───────────────────────────────────────────────────────────────
print("\n=== Todo — Todoist ===")
td = sh.worksheet('Todo — Todoist')
u(td, 'B15', 'Book Wanderlust Mutts — Moab dog adventure daycare (Jul 21) !!2 Apr 25')
u(td, 'B18', 'Buy Red Rocks Killer Queen tickets — Jul 30 !!2 Apr 30')
u(td, 'F18', 'Killer Queen tribute show at Red Rocks. Jul 30 Thu evening, from Golden (25 min drive).')

print("\nDone.")
