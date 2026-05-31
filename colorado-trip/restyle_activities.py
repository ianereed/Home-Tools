"""Restyle the activity sections of the 'Activities — Hikes, Runs & MTB' tab.

Content-safe: this reads the tab and only changes FORMATTING — it never rewrites
your trip data. It unifies every section/sub-area/column header, adds zebra striping,
wraps + top-aligns data rows, and sets one consistent column-width set (A..L) for the
whole tab. The MTB section (from the '🚵 MOUNTAIN BIKING' header down) is owned by
update_activities_mtb.py, which already uses the same sheet_style palette — so the two
halves match. Run order doesn't matter; both are idempotent.
"""
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials
import sheet_style as S

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets'],
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

TAB = "Activities — Hikes, Runs & MTB"
NCOLS = 12
SUBAREA_PREFIXES = ("BOULDER", "STEAMBOAT", "CRESTED BUTTE")

ws = sh.worksheet(TAB)
grid = ws.get_all_values()
sid = ws._properties["sheetId"]

# Find the MTB master header → restyle only the activity region above it.
base = len(grid)
for i, row in enumerate(grid):
    if row and row[0] and ("🚵" in row[0] or "MOUNTAIN BIKING" in row[0].upper()):
        base = i
        break


def classify(row):
    a = (row[0] if row else "").strip()
    rest = any(c.strip() for c in row[1:]) if row else False
    if not a and not rest:
        return "blank"
    if a in ("Trip Name", "Activity", "Ride"):
        return "colhdr"
    if a and not rest:                       # lone label in col A → a header bar
        up = a.upper()
        if up.startswith(SUBAREA_PREFIXES) and "MTB RIDES" not in up:
            return "subarea"
        return "section"
    return "data"


def merge_bar(r, fmt):
    return [
        {"unmergeCells": {"range": {"sheetId": sid, "startRowIndex": r, "endRowIndex": r + 1,
                                    "startColumnIndex": 0, "endColumnIndex": NCOLS}}},
        {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": r, "endRowIndex": r + 1,
                                  "startColumnIndex": 0, "endColumnIndex": NCOLS}, "mergeType": "MERGE_ALL"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r, "endRowIndex": r + 1,
                                  "startColumnIndex": 0, "endColumnIndex": NCOLS},
                        "cell": {"userEnteredFormat": fmt},
                        "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"}},
    ]


def color_row(r, bg, bold, fg, wrap, valign):
    return {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r, "endRowIndex": r + 1,
                           "startColumnIndex": 0, "endColumnIndex": NCOLS},
            "cell": {"userEnteredFormat": {"backgroundColor": S.rgb(bg),
                     "textFormat": {"bold": bold, "foregroundColor": S.rgb(fg)},
                     "wrapStrategy": "WRAP" if wrap else "OVERFLOW_CELL",
                     "verticalAlignment": valign}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"}}


# 1) wipe merges + formatting in the activity region (kills the old mismatched colors).
reqs = [
    {"unmergeCells": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": base,
                                "startColumnIndex": 0, "endColumnIndex": NCOLS}}},
    {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": base,
                    "startColumnIndex": 0, "endColumnIndex": NCOLS},
                    "cell": {"userEnteredFormat": {"backgroundColor": S.rgb(S.WHITE),
                             "textFormat": {"bold": False, "foregroundColor": S.rgb(S.DARK_TEXT)},
                             "wrapStrategy": "OVERFLOW_CELL", "verticalAlignment": "BOTTOM"}},
                    "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"}},
]

# 2) per-row roles
counts = {"section": 0, "subarea": 0, "colhdr": 0, "data": 0, "blank": 0}
zebra_i = 0
for r in range(base):
    kind = classify(grid[r] if r < len(grid) else [])
    counts[kind] += 1
    if kind == "section":
        reqs += merge_bar(r, S.bar_format(S.SECTION_BG, S.WHITE_TEXT, size=12))
        zebra_i = 0
    elif kind == "subarea":
        reqs += merge_bar(r, S.bar_format(S.SUBAREA_BG, S.WHITE_TEXT, size=11))
        zebra_i = 0
    elif kind == "colhdr":
        reqs.append(color_row(r, S.COLHDR_BG, True, S.DARK_TEXT, wrap=True, valign="BOTTOM"))
        zebra_i = 0
    elif kind == "data":
        bg = S.ZEBRA_BG if zebra_i % 2 else S.WHITE
        reqs.append(color_row(r, bg, False, S.DARK_TEXT, wrap=True, valign="TOP"))
        zebra_i += 1
    else:  # blank
        zebra_i = 0

# 3) one consistent column-width set for the whole tab
for j, w in enumerate(S.COL_WIDTHS):
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": j, "endIndex": j + 1},
        "properties": {"pixelSize": w}, "fields": "pixelSize"}})

# 4) freeze nothing (sections self-label); ensure no stale frozen rows
reqs.append({"updateSheetProperties": {"properties": {"sheetId": sid,
            "gridProperties": {"frozenRowCount": 0, "frozenColumnCount": 0}},
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}})

sh.batch_update({"requests": reqs})
print(f"Restyled activity region rows 1–{base} of {TAB!r}: "
      f"{counts['section']} sections, {counts['subarea']} sub-areas, "
      f"{counts['colhdr']} col-headers, {counts['data']} data rows. "
      f"Set column widths A–L. MTB section (rows {base + 1}+) left to update_activities_mtb.py.")
