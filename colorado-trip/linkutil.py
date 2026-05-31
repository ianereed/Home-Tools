"""Convert =HYPERLINK formulas on a worksheet into NATIVE rich-text links.

Google Sheets will not linkify a long Maps `dir/?api=1&waypoints=...` URL from a
=HYPERLINK formula (it renders the label as plain, non-clickable text). A native
link (textFormatRuns -> format.link.uri) is the reliable, always-clickable form;
Sheets stores a whole-cell link as the cell-level `hyperlink`.

Call after the sheet's values + formatting have been written.
"""
import re

_PAT = re.compile(r'^=HYPERLINK\("(.+?)","(.*)"\)$', re.S)
_DEFAULT_LINKC = {"red": 21 / 255, "green": 101 / 255, "blue": 192 / 255}


def nativize(sh, ws, sid, nrows, ncols, linkc=None):
    linkc = linkc or _DEFAULT_LINKC
    end_col = chr(ord("A") + ncols - 1)
    formulas = ws.get(f"A1:{end_col}{nrows + 1}", value_render_option="FORMULA")
    reqs = []
    for i, frow in enumerate(formulas):
        for j, cv in enumerate(frow):
            if isinstance(cv, str) and cv.startswith("=HYPERLINK("):
                m = _PAT.match(cv)
                if not m:
                    continue
                url, label = m.group(1), m.group(2)
                reqs.append({"updateCells": {
                    "rows": [{"values": [{
                        "userEnteredValue": {"stringValue": label},
                        "textFormatRuns": [{"startIndex": 0, "format": {
                            "link": {"uri": url}, "underline": True, "foregroundColor": linkc}}],
                    }]}],
                    "fields": "userEnteredValue,textFormatRuns",
                    "start": {"sheetId": sid, "rowIndex": i, "columnIndex": j}}})
    if reqs:
        sh.batch_update({"requests": reqs})
    return len(reqs)
