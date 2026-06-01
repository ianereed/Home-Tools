"""Master link tool for the 'Activities — Hikes, Runs & MTB' tab (activity sections only).

Two passes over the activity region (everything ABOVE the '🚵 MOUNTAIN BIKING' header —
the MTB section already carries its own trailhead pins + Trailforks links via
update_activities_mtb.py):

  1. Trailhead pins — make each activity's Trailhead cell a live Google Maps search link
     ('<trailhead>, <area>, <state>'); no geocoding needed, Google resolves it.
  2. Link labels — turn bare URLs/domains in the Link column into clean labeled links
     ('AllTrails ▸', 'TAMBA ▸', …) matching the MTB section's style.

All links are written as native rich-text (always clickable). Idempotent: a Trailhead
cell is just re-linked; a Link cell already showing a label (not a URL) is skipped.

Consolidates the former add_activities_trailhead_links.py + clean_activity_links.py.
"""
import re
import urllib.parse
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

TAB = "Activities — Hikes, Runs & MTB"
LINKC = {"red": 21 / 255, "green": 101 / 255, "blue": 192 / 255}

# area → state, for the maps query (also gates which rows get a trailhead pin)
STATE = {"Boulder": "CO", "Steamboat": "CO", "Crested Butte": "CO",
         "Lake Tahoe": "CA", "Mammoth Lakes": "CA"}

# Link-column domain → label
LABELS = {
    "alltrails.com": "AllTrails ▸", "trailforks.com": "Trailforks ▸",
    "mtbproject.com": "MTB Project ▸", "northstarcalifornia.com": "Northstar ▸",
    "truckeetrails.org": "Truckee Trails ▸", "tamba.org": "TAMBA ▸",
    "mammothmountain.com": "Mammoth Mtn ▸",
    "easternsierramountainbiking.com": "E. Sierra MTB ▸",
}
_URLISH = re.compile(r"^(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?$", re.I)


def label_for(url):
    host = re.sub(r"^https?://", "", url).split("/")[0]
    host = host[4:] if host.startswith("www.") else host
    for dom, lab in LABELS.items():
        if dom in host:
            return lab
    return f"{host.split('.')[0].capitalize()} ▸"


def link_cell(sid, r, c, text, uri):
    return {"updateCells": {
        "rows": [{"values": [{
            "userEnteredValue": {"stringValue": text},
            "textFormatRuns": [{"startIndex": 0, "format": {
                "link": {"uri": uri}, "underline": True, "foregroundColor": LINKC}}],
        }]}],
        "fields": "userEnteredValue,textFormatRuns",
        "start": {"sheetId": sid, "rowIndex": r, "columnIndex": c}}}


ws = sh.worksheet(TAB)
sid = ws._properties["sheetId"]
grid = ws.get_all_values()

# restrict to the activity region (above the MTB master header)
base = len(grid)
for i, row in enumerate(grid):
    if row and row[0] and "🚵" in row[0]:
        base = i
        break

reqs, th_n, link_n = [], 0, 0
th_col = area_col = link_col = None
for i in range(base):
    row = grid[i]
    if "Trailhead" in row:                      # header row → lock column positions
        th_col = row.index("Trailhead")
        area_col = row.index("Area") if "Area" in row else 1
        link_col = row.index("Link") if "Link" in row else None
        continue
    if th_col is None:
        continue

    # 1) trailhead pin
    th = row[th_col].strip() if th_col < len(row) else ""
    area = row[area_col].strip() if area_col < len(row) else ""
    if th and area in STATE:
        q = urllib.parse.quote(f"{th}, {area}, {STATE[area]}")
        reqs.append(link_cell(sid, i, th_col, th,
                    "https://www.google.com/maps/search/?api=1&query=" + q))
        th_n += 1

    # 2) link label
    if link_col is not None and link_col < len(row):
        val = row[link_col].strip()
        if val and val.lower() != "link" and _URLISH.match(val):
            uri = val if val.lower().startswith("http") else "https://" + val
            reqs.append(link_cell(sid, i, link_col, label_for(uri), uri))
            link_n += 1

if reqs:
    sh.batch_update({"requests": reqs})
print(f"{TAB!r}: linked {th_n} trailhead cells + {link_n} Link cells (activity region, rows 1–{base}).")
