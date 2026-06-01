"""Append the 3 MTB-rides tabs (Boulder / Steamboat / Crested Butte) into the
'Activities — Hikes, Runs & MTB' tab as a 'MOUNTAIN BIKING' section, preserving every
cell + link (trailhead pins, Trailforks deep-links, the all-trailheads map link,
warnings, notes). Does NOT delete the MTB tabs (verify first, delete separately).
Idempotent-ish: removes any prior appended MTB block (marker row) before re-appending.
"""
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

ACT = "Activities — Hikes, Runs & MTB"
MTB_TABS = ["Boulder MTB Rides", "Steamboat MTB Rides", "Crested Butte MTB Rides"]
NCOLS = 12
MARKER = "🚵 MOUNTAIN BIKING — Colorado  (consolidated from the MTB tabs)"

def rgb(r, g, b): return {"red": r/255, "green": g/255, "blue": b/255}
TITLE_BG=rgb(23,37,84); HUB_BG=rgb(21,101,192); COL_HDR=rgb(225,228,234)
WHITE=rgb(255,255,255); DARK=rgb(33,33,33); GREY=rgb(110,110,110)
WARN=rgb(255,243,205); LINKC=rgb(21,101,192); ALT=rgb(247,250,252)

# ── read MTB tabs as (text, link) per cell ───────────────────────────────────────
meta = sh.fetch_sheet_metadata({"includeGridData": True, "ranges": MTB_TABS})
def cell_tl(c):
    ev = c.get("effectiveValue", {})
    t = ev.get("stringValue") if isinstance(ev, dict) else None
    t = t.strip() if t else ""
    link = c.get("hyperlink")
    if not link:
        for fr in (c.get("textFormatRuns") or []):
            u = (fr.get("format") or {}).get("link", {}).get("uri")
            if u: link = u; break
    return (t, link)

mtb = {}  # title -> list of rows; row = list of (text,link)
for s in meta["sheets"]:
    title = s["properties"]["title"]
    rows = []
    for d in s.get("data", []):
        for rd in d.get("rowData", []):
            cells = [cell_tl(c) for c in (rd.get("values", []) or [])]
            rows.append(cells)
    mtb[title] = rows

# ── read Activities: find append point + sheetId + current grid size ─────────────
act_ws = sh.worksheet(ACT)
sid = act_ws.id
av = act_ws.get_all_values()
# if a previous MTB block exists, truncate at its marker so we re-append cleanly
marker_row = next((i for i, r in enumerate(av) if r and r[0].strip() == MARKER), None)
if marker_row is not None:
    last_keep = marker_row
else:
    last_keep = max((i for i, r in enumerate(av) if any(c.strip() for c in r)), default=len(av)-1) + 1
start = last_keep + 1  # leave one blank row

# ── build appended block ─────────────────────────────────────────────────────────
V, F, M, H, L = [], [], [], [], []
def row(cells):  # cells = list of (text,link) or plain str
    norm = []
    for c in cells:
        norm.append(c if isinstance(c, tuple) else (c, None))
    r = start + len(V)
    V.append([t for t, _ in norm] + [""] * (NCOLS - len(norm)))
    for ci, (t, link) in enumerate(norm):
        if link and t:
            L.append((r, ci, t, link))
    return r
def fmt(r, c0, c1, bg=None, fg=None, bold=False, size=None, align=None, wrap=True, valign="MIDDLE"):
    cf = {}
    if bg is not None: cf["backgroundColor"] = bg
    tf = {"bold": bold}
    if fg is not None: tf["foregroundColor"] = fg
    if size is not None: tf["fontSize"] = size
    cf["textFormat"] = tf
    if align: cf["horizontalAlignment"] = align
    cf["verticalAlignment"] = valign; cf["wrapStrategy"] = "WRAP" if wrap else "OVERFLOW_CELL"
    F.append((r, c0, c1, cf))
def mg(r, c0=0, c1=NCOLS): M.append((r, c0, c1))

r = row([MARKER]); mg(r); fmt(r, 0, NCOLS, bg=TITLE_BG, fg=WHITE, bold=True, size=13, align="CENTER"); H.append((r, 30))

for title in MTB_TABS:
    rows = mtb[title]
    for cells in rows:
        if not any(t for t, _ in cells):
            continue
        c0t = cells[0][0]
        role = "other"
        if any(t == "Ride" for t, _ in cells): role = "header"
        elif c0t and c0t[0].isdigit() and "." in c0t[:4]: role = "ride"
        elif c0t.startswith("📍 Open ALL"): role = "combined"
        elif c0t.startswith("Notes & sources") or c0t.startswith("•"): role = "notes"
        elif cells is rows[0]: role = "hubtitle"
        else: role = "warn"

        if role == "header":
            r = row(cells)
            fmt(r, 0, NCOLS, bg=COL_HDR, fg=DARK, bold=True, size=9, align="CENTER", wrap=True); H.append((r, 30))
        elif role == "ride":
            r = row(cells)
            star = "***" in c0t
            fmt(r, 0, NCOLS, bg=(rgb(255,250,230) if star else WHITE), fg=DARK, align="LEFT", wrap=True, valign="TOP")
            fmt(r, 0, 1, bg=(rgb(255,243,205) if star else ALT), fg=DARK, bold=True, align="LEFT", valign="TOP")
            H.append((r, 56))
        elif role == "combined":
            r = row(cells); mg(r); fmt(r, 0, NCOLS, bg=rgb(232,240,254), fg=LINKC, bold=True, align="LEFT", wrap=True); H.append((r, 30))
        elif role == "notes":
            r = row(cells); mg(r); fmt(r, 0, NCOLS, bg=WHITE, fg=GREY, size=9, align="LEFT", wrap=True); H.append((r, 28))
        elif role == "hubtitle":
            r = row(cells); mg(r); fmt(r, 0, NCOLS, bg=HUB_BG, fg=WHITE, bold=True, size=12, align="CENTER"); H.append((r, 26))
        else:  # warn / intro
            r = row(cells); mg(r); fmt(r, 0, NCOLS, bg=WARN, fg=rgb(120,70,0), align="LEFT", wrap=True); H.append((r, 40))
    row([""])  # spacer between hubs

# ── ensure 12 cols, then write ───────────────────────────────────────────────────
reqs = []
if act_ws.col_count < NCOLS:
    reqs.append({"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"columnCount": NCOLS}}, "fields": "gridProperties.columnCount"}})
# clear any stale rows from a prior append (from `start` down to old end)
old_end = len(av)
if old_end >= start:
    reqs.append({"updateCells": {"range": {"sheetId": sid, "startRowIndex": start-1, "startColumnIndex": 0, "endColumnIndex": NCOLS}, "fields": "userEnteredValue,userEnteredFormat,textFormatRuns"}})
if reqs:
    sh.batch_update({"requests": reqs})

act_ws.update(V, f"A{start+1}", value_input_option="USER_ENTERED")

reqs = []
for (r, c0, c1, cf) in F:
    reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r, "endRowIndex": r+1, "startColumnIndex": c0, "endColumnIndex": c1},
        "cell": {"userEnteredFormat": cf}, "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)"}})
for (r, c0, c1) in M:
    reqs.append({"mergeCells": {"range": {"sheetId": sid, "startRowIndex": r, "endRowIndex": r+1, "startColumnIndex": c0, "endColumnIndex": c1}, "mergeType": "MERGE_ALL"}})
for (r, px) in H:
    reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": r, "endIndex": r+1}, "properties": {"pixelSize": px}, "fields": "pixelSize"}})
for (r, ci, label, url) in L:
    reqs.append({"updateCells": {"rows": [{"values": [{"userEnteredValue": {"stringValue": label},
        "textFormatRuns": [{"startIndex": 0, "format": {"link": {"uri": url}, "underline": True, "foregroundColor": LINKC}}]}]}],
        "fields": "userEnteredValue,textFormatRuns", "start": {"sheetId": sid, "rowIndex": r, "columnIndex": ci}}})
for k in range(0, len(reqs), 400):
    sh.batch_update({"requests": reqs[k:k+400]})

rides = sum(1 for t in MTB_TABS for cl in mtb[t] if cl and cl[0][0][:2] and cl[0][0][0].isdigit() and "." in cl[0][0][:4])
print(f"OK: appended MTB block to '{ACT}' starting row {start+1}; {len(V)} rows, {rides} rides, {len(L)} links. (MTB tabs NOT deleted yet.)")
