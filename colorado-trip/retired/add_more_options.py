import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials
import time

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
ws = sh.worksheet("Overnight Backpacking Options")

new_rows = [
    [
        "Forest Lakes (Upper + Lower)",
        "Boulder",
        "Jul 22-31",
        "Out & Back",
        "~9 mi",
        "~1,800 ft",
        "~4.5 mi/day",
        "~2 hrs (1hr each way via Nederland)",
        "East Portal / Moffat Tunnel TH",
        "https://www.alltrails.com/trail/us/colorado/forest-lakes-trail",
    ],
    [
        "Mandall Lakes (Slide + Black Mandall)",
        "Steamboat",
        "Aug 2-6",
        "Out & Back",
        "~8.5 mi",
        "~1,700 ft",
        "~4.3 mi/day",
        "~2 hrs (1hr each way via Yampa)",
        "Mandall TH (CR 7 / FR 900)",
        "https://www.alltrails.com/trail/us/colorado/slide-mandall-lake-and-black-mandall-lake",
    ],
    [
        "Dark Canyon (Raggeds Wilderness)",
        "Crested Butte",
        "Aug 8-11",
        "Out & Back",
        "~10-14 mi",
        "~2,500-3,500 ft",
        "~5-7 mi/day",
        "~40 min (via Kebler Pass)",
        "Dark Canyon TH (Kebler Pass Rd)",
        "https://www.alltrails.com/trail/us/colorado/dark-canyon-trail--2",
    ],
]

# Append after existing data (row 6 = last existing data row)
ws.update(range_name="A7", values=new_rows)
time.sleep(0.5)

print("Done! Added 3 new backpacking options.")
