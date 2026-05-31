"""Tidy the 'Link' column (J) of the activity sections into clean labeled links.

Converts bare URLs / bare domains (e.g. 'https://www.alltrails.com/...', 'tamba.org')
into native labeled hyperlinks like 'AllTrails ▸' — matching the MTB section's style.
Operates only on the activity region (above the '🚵 MOUNTAIN BIKING' header); the MTB
section already uses labeled links. Idempotent: once a cell shows a label (not a URL),
re-running skips it.
"""
import re
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials
import linkutil

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets'],
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

TAB = "Activities — Hikes, Runs & MTB"
LINK_COL = 9  # column J (0-based)

# domain → label
LABELS = {
    "alltrails.com": "AllTrails ▸",
    "trailforks.com": "Trailforks ▸",
    "mtbproject.com": "MTB Project ▸",
    "northstarcalifornia.com": "Northstar ▸",
    "truckeetrails.org": "Truckee Trails ▸",
    "tamba.org": "TAMBA ▸",
    "mammothmountain.com": "Mammoth Mtn ▸",
    "easternsierramountainbiking.com": "E. Sierra MTB ▸",
}
_URLISH = re.compile(r"^(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?$", re.I)


def label_for(url):
    host = re.sub(r"^https?://", "", url).split("/")[0].lstrip("www.")
    host = host[4:] if host.startswith("www.") else host
    for dom, lab in LABELS.items():
        if dom in host:
            return lab
    root = host.split(".")[0]
    return f"{root.capitalize()} ▸"


ws = sh.worksheet(TAB)
grid = ws.get_all_values()
base = len(grid)
for i, row in enumerate(grid):
    if row and row[0] and "🚵" in row[0]:
        base = i
        break

updates = []
for i in range(base):
    row = grid[i]
    val = (row[LINK_COL] if len(row) > LINK_COL else "").strip()
    if not val or val.lower() == "link":
        continue
    if not _URLISH.match(val):          # already a label / not a URL → skip
        continue
    url = val if val.lower().startswith("http") else "https://" + val
    formula = f'=HYPERLINK("{url}","{label_for(url)}")'
    updates.append({"range": f"J{i + 1}", "values": [[formula]]})

if updates:
    ws.batch_update(updates, value_input_option="USER_ENTERED")
    n = linkutil.nativize(sh, ws, ws._properties["sheetId"], len(grid), 12)
    print(f"Cleaned {len(updates)} link cell(s) in {TAB!r}; nativized {n} links total.")
else:
    print("No bare URLs found — links already clean.")
