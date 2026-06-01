"""Pass 2: linkify cells whose ENTIRE content is a bare URL or domain (e.g. the Todo
tab's 'airbnb.com'). These were plain text, never clickable. Prose cells that merely
mention a domain inline are intentionally left alone (blanket-linking a sentence is wrong).
"""
import re
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

LINKC = {"red": 21 / 255, "green": 101 / 255, "blue": 192 / 255}
# whole-cell URL or bare domain with a real TLD (no surrounding prose / spaces)
WHOLE = re.compile(
    r'^\s*(?:https?://\S+|(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|org|net|gov|io|co|edu|us)(?:/\S*)?)\s*$',
    re.I)

def to_uri(text):
    t = text.strip()
    if t.lower().startswith("http"):
        return t
    if t.lower().startswith("www."):
        return "https://" + t
    return "https://" + t

titles = [ws.title for ws in sh.worksheets()]
meta = sh.fetch_sheet_metadata({"includeGridData": True, "ranges": titles})

reqs = []
report = []
for s in meta["sheets"]:
    sid = s["properties"]["sheetId"]
    title = s["properties"]["title"]
    n = 0
    samples = []
    for d in s.get("data", []):
        for ri, row in enumerate(d.get("rowData", [])):
            for ci, c in enumerate(row.get("values", []) or []):
                uev = c.get("userEnteredValue", {})
                if "formulaValue" in uev:           # skip formulas (=IMAGE etc.)
                    continue
                if c.get("hyperlink"):              # already a link
                    continue
                fr = c.get("textFormatRuns")
                if fr and any("link" in (r.get("format") or {}) for r in fr):
                    continue
                ev = c.get("effectiveValue", {})
                txt = ev.get("stringValue") if isinstance(ev, dict) else None
                if not txt or not WHOLE.match(txt):
                    continue
                reqs.append({"updateCells": {
                    "rows": [{"values": [{
                        "userEnteredValue": {"stringValue": txt},
                        "textFormatRuns": [{"startIndex": 0, "format": {
                            "link": {"uri": to_uri(txt)}, "underline": True, "foregroundColor": LINKC}}],
                    }]}],
                    "fields": "userEnteredValue,textFormatRuns",
                    "start": {"sheetId": sid, "rowIndex": ri, "columnIndex": ci}}})
                n += 1
                if len(samples) < 4:
                    samples.append(txt.strip()[:40])
    if n:
        report.append((title, n, samples))

print("Whole-cell bare URLs linkified:")
for title, n, samples in report:
    print(f"  {title:32} {n}  e.g. {samples}")
print(f"TOTAL: {len(reqs)}")

if reqs:
    CHUNK = 500
    for k in range(0, len(reqs), CHUNK):
        sh.batch_update({"requests": reqs[k:k + CHUNK]})
    print(f"Applied {len(reqs)} bare-URL links.")
else:
    print("None found.")
