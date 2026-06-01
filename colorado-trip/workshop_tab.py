"""Rebuild one or more flexible-day OPTION tabs IN PLACE and keep them in order.

Usage:
    python3 workshop_tab.py BLD-A BLD-B ...     # rebuild specific tabs
    python3 workshop_tab.py --all               # rebuild every option tab

Why this exists: rebuild_trip_tabs.flush() deletes-by-title then addSheet, and addSheet
APPENDS the new sheet to the end of the workbook — so a naive single-tab rebuild yanks
that tab to the bottom of the sheet list. This script rebuilds the requested tab(s) via
build_option(), rewires each tab's DAY OPTIONS cell to its new gid, then runs ONE reorder
pass that puts ALL 17 option tabs back into their canonical contiguous block (right after
"Trailhead Distances", in OPTIONS order).

Importing rebuild_trip_tabs only runs its setup + definitions (the `if __name__` guard
means the build phases do NOT fire on import).
"""
import sys
import rebuild_trip_tabs as R

OPTION_IDS = [o["id"] for o in R.OPTIONS]               # canonical order
# Option tabs sit immediately after this anchor tab, contiguously.
ANCHOR_TITLE = "Trailhead Distances"


def _rewire_menu_link(tab_id, new_gid):
    menu = R.sh.worksheet("DAY OPTIONS")
    msid = menu.id
    reqs = []
    for ri, row in enumerate(menu.get_all_values()):
        for ci, cell in enumerate(row):
            if cell.strip() == tab_id:
                reqs.append({"updateCells": {"rows": [{"values": [{
                    "userEnteredValue": {"stringValue": tab_id},
                    "textFormatRuns": [{"startIndex": 0, "format": {
                        "link": {"uri": R.turl(new_gid)}, "underline": True,
                        "foregroundColor": R.LINKC}}]}]}],
                    "fields": "userEnteredValue,textFormatRuns",
                    "start": {"sheetId": msid, "rowIndex": ri, "columnIndex": ci}}})
    if reqs:
        R.sh.batch_update({"requests": reqs})
    return len(reqs)


def _update_menu_drive(tab_id, o):
    """Update the DAY OPTIONS Drive cell (col D) for tab_id: short time + the route link."""
    if not o.get("route_stops"):
        return 0
    menu = R.sh.worksheet("DAY OPTIONS")
    msid = menu.id
    short = o["drive"].split(" · ")[0]                  # e.g. "~31 min", "~1h20"
    url = R.day_route(o["hub"], o["route_stops"])
    reqs = []
    for ri, row in enumerate(menu.get_all_values()):
        if len(row) > 1 and row[1].strip() == tab_id:
            reqs.append({"updateCells": {"rows": [{"values": [{
                "userEnteredValue": {"stringValue": short},
                "textFormatRuns": [{"startIndex": 0, "format": {
                    "link": {"uri": url}, "underline": True,
                    "foregroundColor": R.LINKC}}]}]}],
                "fields": "userEnteredValue,textFormatRuns",
                "start": {"sheetId": msid, "rowIndex": ri, "columnIndex": 3}}})
    if reqs:
        R.sh.batch_update({"requests": reqs})
    return len(reqs)


def _reorder_option_block():
    """Place the 17 option tabs contiguously right after ANCHOR_TITLE, in OPTIONS order.

    Reorder rule that dodges the Sheets move off-by-one: process in final order, and each
    move targets a LOWER index than the sheet's current position, so it lands exactly at
    the target. After a fresh rebuild the option tabs are piled at the end (indices well
    past the anchor), so every target index is lower than the current one.
    """
    meta = R.sh.fetch_sheet_metadata()
    title_idx = {s["properties"]["title"]: s["properties"]["index"] for s in meta["sheets"]}
    title_gid = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    base = title_idx[ANCHOR_TITLE] + 1
    for i, tab_id in enumerate(OPTION_IDS):
        if tab_id not in title_gid:
            continue
        R.sh.batch_update({"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": title_gid[tab_id], "index": base + i},
            "fields": "index"}}]})
    return base


def main(ids):
    for tab_id in ids:
        o = next((x for x in R.OPTIONS if x["id"] == tab_id), None)
        if o is None:
            print(f"!! {tab_id}: not in OPTIONS — skipping")
            continue
        new_gid = R.build_option(o)
        n = _rewire_menu_link(tab_id, new_gid)
        d = _update_menu_drive(tab_id, o)
        print(f"   {tab_id}: rebuilt -> gid {new_gid}, rewired {n} link(s), updated {d} drive cell(s)")
    base = _reorder_option_block()
    print(f"   reordered option block to indices {base}..{base + len(OPTION_IDS) - 1}")
    print("DONE.")


if __name__ == "__main__":
    args = sys.argv[1:]
    ids = OPTION_IDS if (not args or args == ["--all"]) else args
    print(f"Workshopping {len(ids)} tab(s): {', '.join(ids)}")
    main(ids)
