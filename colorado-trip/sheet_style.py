"""Shared visual palette + column widths for the Activities tab.

Single source of truth so the MTB updater (update_activities_mtb.py via mtb_tab.py)
and the activity restyler (restyle_activities.py) produce a consistent look.

Hierarchy:
  SECTION  — top-level bar (Backpacking, HIKES, TRAIL RUNS, LAKE TAHOE, MAMMOTH, 🚵 MTB master)
  SUBAREA  — second-level bar (BOULDER / STEAMBOAT / CRESTED BUTTE; MTB town titles)
  COLHDR   — column-header row (grey)
  BANNER   — MTB info/warning banner (amber)
  MAPLINK  — MTB "open all trailheads" link row (light blue)
  ZEBRA    — alternating data-row shade
"""

# (r, g, b) 0-255
SECTION_BG = (69, 39, 160)      # deep indigo  #4527A0
SUBAREA_BG = (126, 87, 194)     # medium purple #7E57C2
COLHDR_BG  = (230, 230, 230)    # grey
BANNER_BG  = (255, 243, 205)    # amber
MAPLINK_BG = (209, 233, 255)    # light blue
ZEBRA_BG   = (245, 243, 250)    # faint lavender
WHITE      = (255, 255, 255)
DARK_TEXT  = (30, 30, 30)
WHITE_TEXT = (255, 255, 255)

# One width set for the whole tab (cols A..L). Chosen as a compromise that reads
# well for BOTH the activity schema (Name|Area|Date|Type|Dist|Elev|DailyMi|Drive|TH|Link|Notes)
# and the MTB schema (Ride|BestFor|Miles|Elev|TH|Map|Trailforks|Min|Diff|Access|Hike|Dogs).
COL_WIDTHS = [225, 205, 115, 130, 165, 135, 120, 140, 165, 160, 200, 120]


def rgb(t):
    return {"red": t[0] / 255, "green": t[1] / 255, "blue": t[2] / 255}


def bar_format(bg, fg, size=None, wrap=True):
    """userEnteredFormat dict for a merged section/sub-area/banner/map bar."""
    tf = {"bold": True, "foregroundColor": rgb(fg)}
    if size:
        tf["fontSize"] = size
    return {"backgroundColor": rgb(bg), "textFormat": tf,
            "wrapStrategy": "WRAP" if wrap else "OVERFLOW_CELL",
            "verticalAlignment": "MIDDLE"}
