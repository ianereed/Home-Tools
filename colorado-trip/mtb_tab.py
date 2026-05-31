"""Shared builder for the MTB ride tabs (Boulder / Steamboat / Crested Butte).

Each tab gets:
  - a dark-purple title, an info banner (optionally a link),
  - a combined "open ALL trailheads on one Google Map" link (route in table order),
  - numbered ride rows,
  - a per-row '📍 Trailhead Map' column linking to that trailhead's Google Maps pin,
  - zebra striping, frozen header, and native (clickable) links via linkutil.

Row data passed in is 10 columns:
  [Ride, Best For, Miles, Elev Gain, Trailhead, Min from Airbnb,
   Difficulty, <Access|Shuttle>, Nearest Hike-Friendly Trail, Dogs?]
The builder inserts the '📍 Trailhead Map' column (index 5) and a leading
number on the Ride cell, so callers don't repeat that boilerplate.
"""
from urllib.parse import quote
import gspread
import linkutil

_SEARCH = "https://www.google.com/maps/search/?api=1&query="
_DIR = "https://www.google.com/maps/dir/"


def gmap_pin(query, label="📍 Open in Maps"):
    return f'=HYPERLINK("{_SEARCH}{quote(query, safe="")}","{label}")'


def gmap_all(origin, queries, label):
    parts = [origin] + list(queries)
    url = _DIR + "/".join(quote(p, safe="") for p in parts)
    return f'=HYPERLINK("{url}","{label}")'


def tf_link(url, label="Trailforks ▸"):
    return f'=HYPERLINK("{url}","{label}")' if url else ""


def _rgb(r, g, b):
    return {"red": r / 255, "green": g / 255, "blue": b / 255}


def _ride_rows(rides, queries, hype, trailforks, must_do):
    """Transform 10-col ride rows into 12-col display rows (number, ***, hype, map, TF)."""
    hype = hype or [""] * len(rides)
    trailforks = trailforks or [""] * len(rides)
    must_do = set(must_do or ())
    out = []
    for i, (row, q, h, tfurl) in enumerate(zip(rides, queries, hype, trailforks), start=1):
        rr = list(row)
        star = " ***" if rr[0] in must_do else ""
        rr[0] = f"{i}. {rr[0]}{star}"
        if h:
            rr[1] = f"{h} — {rr[1]}" if rr[1] else h
        rr.insert(5, gmap_pin(q))
        rr.insert(6, tf_link(tfurl))
        out.append(rr)
    return out


_MTB_FOOTER = [
    "Notes & sources:",
    "• *** after a ride name = Ian's personal must-do pick (hand-marked). 🌟 MUST-RIDE = community-consensus must-do; ⭐ Highly rated = strong ratings / frequently recommended.",
    "• 📍 Trailhead Map (per row) opens that trailhead's Google Maps pin; the link atop each town opens ALL its trailheads on one map (table order = row order).",
    "• Trailforks column deep-links the specific trail where one exists, else the area/region page. Map pins are geocoded from the trailhead name — sanity-check before a long drive.",
    "• Drive times approximate. Difficulty: 🟢 green = easy · 🔵 blue = intermediate · 🔴 black = advanced · ⚫ = expert.",
    "• Sources: Trailforks, local guides (Steamboat Chamber/Lodging/Loam Wolf, travelcrestedbutte/gunnisoncrestedbutte/Pinkbike/Two Wheeled Wanderer/AllTrails, evo, bouldermountainbike), r/MTB + r/boulder + r/steamboat.",
]


def build_activities_mtb(sh, tab_title, master_header, towns):
    """Rewrite ONLY the MTB section (from the '🚵 MOUNTAIN BIKING' header down) inside
    an existing tab, preserving every row above it. `towns` is a list of dicts with keys:
    title, banner_text, banner_url, col8_header, rides, queries, origin, hype,
    trailforks, must_do.
    """
    NCOLS = 12
    ws = sh.worksheet(tab_title)
    grid = ws.get_all_values()
    old_total = len(grid)

    base = None  # 0-based row index of the MTB master header
    for i, row in enumerate(grid):
        if row and row[0] and ("🚵" in row[0] or "MOUNTAIN BIKING" in row[0].upper()):
            base = i
            break
    if base is None:                      # no section yet → append after a blank
        base = old_total + 1

    def pad(cells):
        return list(cells) + [""] * (NCOLS - len(cells))

    rows, kinds = [], []

    def add(cells, kind):
        rows.append(pad(cells))
        kinds.append(kind)

    add([master_header], "master")
    for t in towns:
        add([""], "blank")
        add([t["title"]], "title")
        banner_cell = (f'=HYPERLINK("{t["banner_url"]}","{t["banner_text"]}")'
                       if t.get("banner_url") else t["banner_text"])
        add([banner_cell], "banner")
        n = len(t["rides"])
        combined = gmap_all(t["origin"], t["queries"],
                            f"📍 Open ALL {n} trailheads on one Google Map  "
                            f"(route is in table order — stop 1 = row 1) →")
        add([combined], "map")
        add(["Ride", "Best For", "Miles", "Elev Gain", "Trailhead", "📍 Trailhead Map",
             "Trailforks", "Min from Airbnb", "Difficulty", t["col8_header"],
             "Nearest Hike-Friendly Trail", "Dogs?"], "header")
        for rr in _ride_rows(t["rides"], t["queries"], t.get("hype"),
                             t.get("trailforks"), t.get("must_do")):
            add(rr, "data")
    add([""], "blank")
    for line in _MTB_FOOTER:
        add([line], "note")

    n_rows = len(rows)
    new_total = base + n_rows
    max_total = max(old_total, new_total)

    # Grow first so the clear/unmerge range is valid.
    if ws.row_count < max_total or ws.col_count < NCOLS:
        ws.resize(rows=max(ws.row_count, max_total), cols=max(ws.col_count, NCOLS))
    sid = ws._properties["sheetId"]

    WHITE = _rgb(255, 255, 255)
    # 1) wipe old merges + formatting only within the MTB region.
    sh.batch_update({"requests": [
        {"unmergeCells": {"range": {"sheetId": sid, "startRowIndex": base, "endRowIndex": max_total,
                                    "startColumnIndex": 0, "endColumnIndex": NCOLS}}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": base, "endRowIndex": max_total,
                                  "startColumnIndex": 0, "endColumnIndex": NCOLS},
                        "cell": {"userEnteredFormat": {"backgroundColor": WHITE,
                                 "textFormat": {"bold": False, "foregroundColor": _rgb(0, 0, 0)},
                                 "wrapStrategy": "OVERFLOW_CELL", "verticalAlignment": "BOTTOM"}},
                        "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"}},
    ]})

    # 2) write values, then clear any rows the old section had beyond the new end.
    ws.update(range_name=f"A{base + 1}", values=rows, value_input_option="USER_ENTERED")
    if new_total < old_total:
        ws.batch_clear([f"A{new_total + 1}:L{old_total}"])

    # 3) formatting — palette shared with restyle_activities.py via sheet_style
    import sheet_style as S
    MTB_DARK, SUBAREA, BANNER_BG, MAP_BG, COL_HDR, ZEBRA = (
        _rgb(*S.SECTION_BG), _rgb(*S.SUBAREA_BG), _rgb(*S.BANNER_BG),
        _rgb(*S.MAPLINK_BG), _rgb(*S.COLHDR_BG), _rgb(*S.ZEBRA_BG))

    def merge_color(s, bg, fg=(30, 30, 30), size=None, wrap=True):
        tf = {"bold": True, "foregroundColor": _rgb(*fg)}
        if size:
            tf["fontSize"] = size
        a = base + s
        return [
            {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": a, "endRowIndex": a + 1,
                                      "startColumnIndex": 0, "endColumnIndex": NCOLS}, "mergeType": "MERGE_ALL"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": a, "endRowIndex": a + 1,
                                      "startColumnIndex": 0, "endColumnIndex": NCOLS},
                            "cell": {"userEnteredFormat": {"backgroundColor": bg, "textFormat": tf,
                                     "wrapStrategy": "WRAP" if wrap else "OVERFLOW_CELL",
                                     "verticalAlignment": "MIDDLE"}},
                            "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"}},
        ]

    reqs = []
    zebra_i = 0
    for s, kind in enumerate(kinds):
        a = base + s
        if kind == "master":
            reqs += merge_color(s, MTB_DARK, fg=(255, 255, 255), size=14)
        elif kind == "title":
            reqs += merge_color(s, SUBAREA, fg=(255, 255, 255), size=12)
        elif kind == "banner":
            reqs += merge_color(s, BANNER_BG)
        elif kind == "map":
            reqs += merge_color(s, MAP_BG)
        elif kind == "header":
            zebra_i = 0
            reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": a, "endRowIndex": a + 1,
                                        "startColumnIndex": 0, "endColumnIndex": NCOLS},
                         "cell": {"userEnteredFormat": {"backgroundColor": COL_HDR,
                                  "textFormat": {"bold": True, "foregroundColor": _rgb(30, 30, 30)},
                                  "wrapStrategy": "WRAP"}},
                         "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)"}})
        elif kind == "data":
            reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": a, "endRowIndex": a + 1,
                                        "startColumnIndex": 0, "endColumnIndex": NCOLS},
                         "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP",
                                  "backgroundColor": ZEBRA if zebra_i % 2 else WHITE}},
                         "fields": "userEnteredFormat(wrapStrategy,verticalAlignment,backgroundColor)"}})
            zebra_i += 1
    sh.batch_update({"requests": reqs})

    # 4) trim trailing rows + linkify the freshly written =HYPERLINK cells.
    if ws.row_count != new_total:
        ws.resize(rows=new_total)
    n = linkutil.nativize(sh, ws, sid, new_total, NCOLS)
    print(f"Updated MTB section in {tab_title!r}: rows {base + 1}–{new_total}, "
          f"{sum(k == 'data' for k in kinds)} rides, {n} links nativized. "
          f"Preserved rows 1–{base}.")


def build_mtb_tab(sh, tab, title, banner_text, banner_url,
                  col8_header, rides, queries, origin, hype=None, trailforks=None,
                  must_do=None):
    # must_do: set of base Ride names the USER hand-marked as personal must-dos.
    # We re-append their '***' on every rebuild so manual marks are never lost.
    must_do = set(must_do or ())
    headers = ["Ride", "Best For", "Miles", "Elev Gain", "Trailhead",
               "📍 Trailhead Map", "Trailforks", "Min from Airbnb", "Difficulty",
               col8_header, "Nearest Hike-Friendly Trail", "Dogs?"]
    NCOLS = len(headers)  # 12
    blank = [""] * NCOLS
    hype = hype or [""] * len(rides)
    trailforks = trailforks or [""] * len(rides)

    # Transform rides: number the Ride cell, prepend any hype badge to Best For,
    # and insert the map-pin + Trailforks columns (after the Trailhead column).
    trows = []
    for i, (row, q, h, tfurl) in enumerate(zip(rides, queries, hype, trailforks), start=1):
        rr = list(row)
        star = " ***" if rr[0] in must_do else ""
        rr[0] = f"{i}. {rr[0]}{star}"
        if h:
            rr[1] = f"{h} — {rr[1]}" if rr[1] else h
        rr.insert(5, gmap_pin(q))
        rr.insert(6, tf_link(tfurl))
        trows.append(rr)

    combined_label = (f"📍 Open ALL {len(rides)} trailheads on one Google Map  "
                      f"(route is in table order — stop 1 = row 1) →")
    combined_row = [gmap_all(origin, queries, combined_label)] + [""] * (NCOLS - 1)

    title_row = [title] + [""] * (NCOLS - 1)
    banner_cell = (f'=HYPERLINK("{banner_url}","{banner_text}")'
                   if banner_url else banner_text)
    banner_row = [banner_cell] + [""] * (NCOLS - 1)

    footer = [
        blank,
        ["Notes & sources:"] + [""] * (NCOLS - 1),
        ["• *** after a ride name = Ian's personal must-do pick (hand-marked). 🌟 MUST-RIDE = community-consensus must-do (top Trailforks rating + repeatedly named by locals/forums). ⭐ Highly rated = strong ratings, frequently recommended."] + [""] * (NCOLS - 1),
        ["• 📍 Trailhead Map (per row) opens that trailhead's Google Maps pin. The link up top opens all trailheads on one map, in table order."] + [""] * (NCOLS - 1),
        ["• Drive times approximate. Difficulty: 🟢 green = easy · 🔵 blue = intermediate · 🔴 black = advanced · ⚫ = expert."] + [""] * (NCOLS - 1),
        ["• 'Trailforks' column deep-links the specific trail where one exists, else the area/region page. Map pins are geocoded from the trailhead name — sanity-check before a long drive."] + [""] * (NCOLS - 1),
        ["• Sources: Trailforks, local MTB guides (chamber/lodging/Loam Wolf/travelcrestedbutte/gunnisoncrestedbutte/Pinkbike/Two Wheeled Wanderer/AllTrails/evo/bouldermountainbike), r/MTB + r/boulder + r/steamboat."] + [""] * (NCOLS - 1),
    ]

    data = [title_row, banner_row, combined_row, blank, headers] + trows + footer
    nrows = len(data)

    try:
        sh.del_worksheet(sh.worksheet(tab))
        print(f"Removed existing tab {tab!r}, recreating.")
    except gspread.WorksheetNotFound:
        pass

    ws = sh.add_worksheet(title=tab, rows=nrows + 4, cols=NCOLS)
    ws.update(range_name="A1", values=data, value_input_option="USER_ENTERED")
    sid = ws._properties["sheetId"]

    MTB_DARK = _rgb(69, 39, 160)
    BANNER_BG = _rgb(255, 243, 205)
    MAP_BG = _rgb(209, 233, 255)   # light blue for the combined-map link
    COL_HDR = _rgb(230, 230, 230)
    ZEBRA = _rgb(245, 243, 250)

    hdr_idx = 4
    first_data = hdr_idx + 1
    last_data = first_data + len(trows)

    def merge_style(row_idx, bg, fg=(30, 30, 30), size=None):
        tf = {"bold": True, "foregroundColor": _rgb(*fg)}
        if size:
            tf["fontSize"] = size
        return [
            {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                                      "startColumnIndex": 0, "endColumnIndex": NCOLS}, "mergeType": "MERGE_ALL"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                                      "startColumnIndex": 0, "endColumnIndex": NCOLS},
                            "cell": {"userEnteredFormat": {"backgroundColor": bg, "textFormat": tf,
                                     "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"}},
                            "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"}},
        ]

    reqs = []
    reqs += merge_style(0, MTB_DARK, fg=(255, 255, 255), size=13)
    reqs += merge_style(1, BANNER_BG)
    reqs += merge_style(2, MAP_BG)
    reqs += [
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": hdr_idx, "endRowIndex": hdr_idx + 1,
                                  "startColumnIndex": 0, "endColumnIndex": NCOLS},
                        "cell": {"userEnteredFormat": {"backgroundColor": COL_HDR,
                                 "textFormat": {"bold": True, "foregroundColor": _rgb(30, 30, 30)},
                                 "wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": first_data, "endRowIndex": last_data,
                                  "startColumnIndex": 0, "endColumnIndex": NCOLS},
                        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
                        "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)"}},
        {"updateSheetProperties": {"properties": {"sheetId": sid,
            "gridProperties": {"frozenRowCount": hdr_idx + 1}},
            "fields": "gridProperties.frozenRowCount"}},
    ]
    for r in range(first_data, last_data):
        if (r - first_data) % 2 == 1:
            reqs.append({"repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": r, "endRowIndex": r + 1,
                          "startColumnIndex": 0, "endColumnIndex": NCOLS},
                "cell": {"userEnteredFormat": {"backgroundColor": ZEBRA}},
                "fields": "userEnteredFormat.backgroundColor"}})

    widths = [225, 225, 135, 110, 200, 140, 105, 110, 155, 150, 200, 130]
    for j, w in enumerate(widths):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": j, "endIndex": j + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})

    sh.batch_update({"requests": reqs})
    n = linkutil.nativize(sh, ws, sid, nrows, NCOLS)
    print(f"Done. Tab {tab!r}: {len(rides)} rides; nativized {n} links.")
