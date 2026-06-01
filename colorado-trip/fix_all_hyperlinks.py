"""Document-wide: convert EVERY =HYPERLINK formula in the whole spreadsheet into a
native rich-text link (clickable, incl. long Maps URLs that Sheets won't linkify from
a formula). Idempotent — already-native links have no formula and are skipped.

Reports per tab and flags any =HYPERLINK it can't statically parse (e.g. cell-reference
forms), which would need manual handling.
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
TWO = re.compile(r'^=HYPERLINK\("(.+?)",\s*"(.*)"\)$', re.S | re.I)   # =HYPERLINK("url","label")
ONE = re.compile(r'^=HYPERLINK\("([^"]+)"\)$', re.S | re.I)            # =HYPERLINK("url")

reqs = []
report = []
skipped = []

for ws in sh.worksheets():
    sid = ws.id
    try:
        grid = ws.get(value_render_option="FORMULA")
    except Exception as e:
        report.append((ws.title, "ERR", str(e)[:60]))
        continue
    n = 0
    for i, frow in enumerate(grid):
        for j, cv in enumerate(frow):
            if not (isinstance(cv, str) and cv[:11].upper().startswith("=HYPERLINK(")):
                continue
            m = TWO.match(cv) or ONE.match(cv)
            if not m:
                skipped.append((ws.title, i + 1, j + 1, cv[:70]))
                continue
            url = m.group(1)
            label = m.group(2) if m.re is TWO else url
            reqs.append({"updateCells": {
                "rows": [{"values": [{
                    "userEnteredValue": {"stringValue": label},
                    "textFormatRuns": [{"startIndex": 0, "format": {
                        "link": {"uri": url}, "underline": True, "foregroundColor": LINKC}}],
                }]}],
                "fields": "userEnteredValue,textFormatRuns",
                "start": {"sheetId": sid, "rowIndex": i, "columnIndex": j}}})
            n += 1
    report.append((ws.title, n, ""))

print("Per-tab =HYPERLINK conversions:")
for title, n, note in report:
    print(f"  {title:32} {n}  {note}")
print(f"TOTAL native-link conversions queued: {len(reqs)}")

if skipped:
    print(f"\n⚠ {len(skipped)} =HYPERLINK cells could NOT be auto-parsed (left as-is):")
    for t, r, c, txt in skipped:
        print(f"  [{t}] R{r}C{c}: {txt}")

if reqs:
    # batch in chunks to stay well under request limits
    CHUNK = 500
    for k in range(0, len(reqs), CHUNK):
        sh.batch_update({"requests": reqs[k:k + CHUNK]})
    print(f"\nApplied {len(reqs)} native links across the document.")
else:
    print("\nNothing to convert (all links already native).")
