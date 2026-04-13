import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
ws = sh.worksheet("Overnight Backpacking Options")

# Expand sheet to 11 columns
ws.resize(rows=20, cols=11)

# Add Notes header in K3
ws.update(range_name="K3", values=[["Notes"]])

# Row 4: Heart Lake / Rogers Pass — add note about link mismatch
ws.update(range_name="K4", values=[["AllTrails link shows full James Peak summit route (13.3 mi / 4,261 ft). This trip stops at Rogers Pass Lake (~9 mi / ~2,100 ft total)."]])

# Row 5: Gilpin Lake — correct stats (AllTrails: 9.2 mi, ~2,000 ft)
ws.update(range_name="E5:G5", values=[["9.2 mi", "~2,000 ft", "~4.6 mi/day"]])

# Row 7: Forest Lakes — correct stats (AllTrails upper lakes: ~8 mi, ~1,800 ft)
ws.update(range_name="E7:G7", values=[["~8 mi", "~1,800 ft", "~4 mi/day"]])

print("Done.")
