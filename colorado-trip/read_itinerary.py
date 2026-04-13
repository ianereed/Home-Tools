import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
print("Sheets:", [ws.title for ws in sh.worksheets()])
ws = sh.worksheets()[0]
print("Reading:", ws.title)
data = ws.get_all_values()
for i, row in enumerate(data):
    if any(cell.strip() for cell in row):
        print(f"Row {i+1}: {row}")
