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

# Create new sheet
ws = sh.add_worksheet(title="Overnight Backpacking Options", rows=20, cols=10)
time.sleep(0.5)

# Headers
headers = [
    ["Overnight Backpacking Options — Dog Friendly, No Permits Required"],
    [],
    [
        "Trip Name",
        "Area",
        "Trip Dates Window",
        "Type",
        "Total Distance",
        "Elevation Gain",
        "Daily Mileage",
        "Extra Driving (RT)",
        "Trailhead",
        "Link",
    ],
]

# Data rows
rows = [
    [
        "Heart Lake + Rogers Pass Lake",
        "Boulder",
        "Jul 22-31",
        "Out & Back",
        "~9 mi",
        "~2,100 ft",
        "~4.5 mi/day",
        "~2 hrs (1hr each way via Nederland)",
        "East Portal / Moffat Tunnel TH",
        "https://www.alltrails.com/trail/us/colorado/james-peak-via-rogers-pass-trail",
    ],
    [
        "Zirkel Circle (Gilpin + Gold Creek Lakes)",
        "Steamboat",
        "Aug 2-6",
        "Loop",
        "~11 mi",
        "~2,400 ft",
        "~5.5 mi/day",
        "~1 hr (30min each way via Clark)",
        "Slavonia TH",
        "https://www.alltrails.com/trail/us/colorado/gilpin-lake-trail",
    ],
    [
        "Oh-Be-Joyful to Blue Lake",
        "Crested Butte",
        "Aug 8-11",
        "Out & Back",
        "~13 mi",
        "~2,160 ft",
        "~6.5 mi/day",
        "~20 min (5mi from town)",
        "Oh-Be-Joyful TH",
        "https://www.alltrails.com/trail/us/colorado/oh-be-joyful--3",
    ],
]

all_data = headers + rows
ws.update(range_name="A1", values=all_data)
time.sleep(0.5)

# Formatting
sheet_id = ws.id

requests = [
    # Bold title row
    {
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 10},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}},
            "fields": "userEnteredFormat.textFormat",
        }
    },
    # Bold header row
    {
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": 10},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.85, "green": 0.92, "blue": 0.83},
            }},
            "fields": "userEnteredFormat(textFormat,backgroundColor)",
        }
    },
    # Merge title row
    {
        "mergeCells": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 10},
            "mergeType": "MERGE_ALL",
        }
    },
    # Auto-resize columns
    {
        "autoResizeDimensions": {
            "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 10}
        }
    },
]

sh.batch_update({"requests": requests})
print("Done! New sheet 'Overnight Backpacking Options' created.")
