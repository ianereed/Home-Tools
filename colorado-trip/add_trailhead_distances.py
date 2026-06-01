"""Build a 'Trailhead Distances' tab: per-hub driving-time matrix between every
trailhead (STARRED MTB rides + that hub's hiking trailheads), via the Google Distance
Matrix API, plus a '< 15 min apart' pairs list (same-day MTB + hike candidates).

Phase 1 = starred (***) MTB rides only; expand later. Re-runnable.
"""
import json, time, urllib.parse, urllib.request
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE, MAPS_API_KEY
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
TAB = "Trailhead Distances"
THRESHOLD = 15  # minutes

# (short name, kind MTB/HIKE, geocode/Distance-Matrix query string)
HUBS = {
 "BOULDER": {"base": "582 Locust Place, Boulder, CO 80304", "th": [
   ("Walker Ranch", "MTB", "Walker Ranch Trailhead, Flagstaff Road, Boulder, CO"),
   ("Marshall Mesa", "MTB", "Marshall Mesa Trailhead, Boulder, CO"),
   ("West Magnolia", "MTB", "West Magnolia Trailhead, Nederland, CO"),
   ("Chautauqua", "HIKE", "Chautauqua Park, Boulder, CO"),
   ("Gregory Canyon", "HIKE", "Gregory Canyon Trailhead, Boulder, CO"),
   ("Mt Sanitas", "HIKE", "Mount Sanitas Trailhead, Boulder, CO"),
   ("Eldorado Canyon", "HIKE", "Eldorado Canyon State Park, Eldorado Springs, CO"),
   ("Flatirons Vista", "HIKE", "Flatirons Vista Trailhead, Boulder, CO"),
   ("Long Lake (Brainard)", "HIKE", "Long Lake Trailhead, Brainard Lake Recreation Area, Ward, CO"),
   ("East Portal (Moffat)", "HIKE", "East Portal Trailhead, Rollinsville, CO"),
 ]},
 "STEAMBOAT": {"base": "1036 Lincoln Avenue, Steamboat Springs, CO 80487", "th": [
   ("Dry Lake (Buffalo Pass)", "MTB", "Dry Lake Campground, Buffalo Pass Road, Steamboat Springs, CO"),
   ("Summit Lake (Buffalo Pass)", "MTB", "Summit Lake Campground, Buffalo Pass Road, Steamboat Springs, CO"),
   ("Howelsen / Emerald", "MTB", "Howelsen Hill, Steamboat Springs, CO"),
   ("Spring Creek", "MTB", "Spring Creek Trailhead, Amethyst Drive, Steamboat Springs, CO"),
   ("Fish Creek Falls", "HIKE", "Fish Creek Falls Trailhead, Steamboat Springs, CO"),
   ("Hahns Peak", "HIKE", "Hahns Peak Trailhead, Clark, CO"),
   ("Red Dirt", "HIKE", "Red Dirt Trailhead, Steamboat Springs, CO"),
   ("Slavonia (Clark)", "HIKE", "Slavonia Trailhead, Forest Road 400, Clark, CO"),
   ("Mandall (Yampa)", "HIKE", "Mandall Lakes Trailhead, Forest Road 900, Yampa, CO 80483"),
 ]},
 "CRESTED BUTTE": {"base": "6 Emmons Road, Crested Butte, CO 81225", "th": [
   ("Doctor Park", "MTB", "Doctor Park Trailhead, Spring Creek Road, Almont, CO"),
   ("Judd Falls (Trail 401)", "MTB", "Judd Falls Trailhead, Gothic, CO 81224"),
   ("Brush Creek (Teocalli)", "MTB", "Brush Creek Trailhead, Brush Creek Road, Crested Butte, CO 81224"),
   ("Lower Loop", "MTB", "Lower Loop Trailhead, Peanut Lake Road, Crested Butte, CO"),
   ("Emerald Lake (Gothic)", "HIKE", "Emerald Lake, Gothic Road, Crested Butte, CO"),
   ("Oh-Be-Joyful", "HIKE", "Oh Be Joyful Trailhead, Slate River Road, Crested Butte, CO 81224"),
   ("Three Lakes (Kebler)", "HIKE", "Lake Irwin Campground, Kebler Pass Road, Crested Butte, CO 81224"),
   ("Dark Canyon (Kebler)", "HIKE", "Erickson Springs Campground, Kebler Pass Road, Somerset, CO 81434"),
 ]},
}

def http(url):
    return json.load(urllib.request.urlopen(url, timeout=30))

def geocode(q):
    u = "https://maps.googleapis.com/maps/api/geocode/json?key=" + MAPS_API_KEY + "&address=" + urllib.parse.quote(q)
    d = http(u)
    if d["status"] == "OK":
        r = d["results"][0]
        loc = r["geometry"]["location"]
        return (loc["lat"], loc["lng"], r["formatted_address"], r["geometry"].get("location_type", "?"))
    return (None, None, f"[{d['status']}]", "FAIL")

def matrix_row(origin, dests):
    u = ("https://maps.googleapis.com/maps/api/distancematrix/json?key=" + MAPS_API_KEY
         + "&mode=driving&origins=" + urllib.parse.quote(origin)
         + "&destinations=" + urllib.parse.quote("|".join(dests)))
    d = http(u)
    out = []
    if d["status"] != "OK":
        return [None] * len(dests)
    for el in d["rows"][0]["elements"]:
        out.append(round(el["duration"]["value"] / 60) if el.get("status") == "OK" else None)
    return out

# ── compute ──────────────────────────────────────────────────────────────────────
data = {}
for hub, info in HUBS.items():
    names = [t[0] for t in info["th"]]
    kinds = [t[1] for t in info["th"]]
    queries = [t[2] for t in info["th"]]
    print(f"\n=== {hub} — geocoding {len(queries)} trailheads ===")
    geo = []
    for q in queries:
        g = geocode(q)
        geo.append(g)
        print(f"  {g[3]:14} {q.split(',')[0]:26} -> {g[2][:50]}")
        time.sleep(0.12)
    print(f"--- distance matrix ({len(queries)}x{len(queries)}) ---")
    mat = []
    for q in queries:
        mat.append(matrix_row(q, queries))
        time.sleep(0.12)
    data[hub] = {"names": names, "kinds": kinds, "geo": geo, "mat": mat}
    # a trailhead whose geocode is only a town/area centroid can't be trusted for a
    # ≤15-min claim — exclude those endpoints from the pairs list (but keep in matrix).
    UNRELIABLE = {"APPROXIMATE", "FAIL"}
    unreliable = [names[i] for i in range(len(names)) if geo[i][3] in UNRELIABLE]
    data[hub]["unreliable"] = unreliable
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if geo[i][3] in UNRELIABLE or geo[j][3] in UNRELIABLE:
                continue
            a, b = mat[i][j], mat[j][i]
            vals = [v for v in (a, b) if v is not None]
            if not vals:
                continue
            mins = round(sum(vals) / len(vals))
            if mins <= THRESHOLD:
                pairs.append((mins, i, j))
    pairs.sort()
    data[hub]["pairs"] = pairs
    mtbhike = [p for p in pairs if kinds[p[1]] != kinds[p[2]]]
    print(f"  < {THRESHOLD} min pairs: {len(pairs)} total, {len(mtbhike)} MTB↔HIKE")

# ── build sheet ────────────────────────────────────────────────────────────────
def rgb(r, g, b): return {"red": r/255, "green": g/255, "blue": b/255}
TITLE_BG=rgb(23,37,84); SECT_BG=rgb(21,101,192); COL_HDR=rgb(225,228,234)
WHITE=rgb(255,255,255); DARK=rgb(33,33,33); GREY=rgb(120,120,120)
GREEN=rgb(198,239,206); YELLOW=rgb(255,235,156); REDISH=rgb(255,224,224)
MTB_BG=rgb(225,240,250); HIKE_BG=rgb(232,245,233); NOTE_BG=rgb(255,243,205)
NCOLS = max(len(v["names"]) for v in data.values()) + 2  # label + N + slack

values, fmts, merges, heights, notes = [], [], [], [], []
def row(cells):
    values.append(list(cells) + [""] * (NCOLS - len(cells))); return len(values) - 1
def fmt(r, c0, c1, bg=None, fg=None, bold=False, size=None, align=None, wrap=False, valign="MIDDLE"):
    cell={}
    if bg is not None: cell["backgroundColor"]=bg
    tf={"bold":bold}
    if fg is not None: tf["foregroundColor"]=fg
    if size is not None: tf["fontSize"]=size
    cell["textFormat"]=tf
    if align: cell["horizontalAlignment"]=align
    cell["verticalAlignment"]=valign
    cell["wrapStrategy"]="WRAP" if wrap else "OVERFLOW_CELL"
    fmts.append({"repeatCell":{"range":{"startRowIndex":r,"endRowIndex":r+1,"startColumnIndex":c0,"endColumnIndex":c1},
        "cell":{"userEnteredFormat":cell},"fields":"userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)"}})
def mergerow(r, c0=0, c1=NCOLS): merges.append((r,c0,c1))

r=row(["TRAILHEAD DRIVING DISTANCES  —  minutes between trailheads (Google driving times)"])
mergerow(r); fmt(r,0,NCOLS,bg=TITLE_BG,fg=WHITE,bold=True,size=14,align="CENTER"); heights.append((r,34))
r=row([f"Cells = one-way driving minutes. GREEN ≤ {THRESHOLD} min = same-day-able (Ian MTB + Anny hike). Phase 1 = starred (***) MTB rides + each hub's hiking trailheads."])
mergerow(r); fmt(r,0,NCOLS,bg=rgb(40,60,110),fg=WHITE,size=9,align="CENTER"); heights.append((r,28))
row([""])

for hub, info in HUBS.items():
    d = data[hub]; names=d["names"]; kinds=d["kinds"]; N=len(names)
    r=row([f"{hub}"]); mergerow(r); fmt(r,0,NCOLS,bg=SECT_BG,fg=WHITE,bold=True,size=12,align="CENTER"); heights.append((r,26))
    # key (numbered) with geocode verification note
    r=row(["#","Trailhead","Kind","Geocoded as (sanity-check)"]); mergerow(r,3,NCOLS)
    fmt(r,0,NCOLS,bg=COL_HDR,fg=DARK,bold=True,align="LEFT"); heights.append((r,20))
    for i,nm in enumerate(names):
        lat,lng,addr,lt = d["geo"][i]
        r=row([str(i+1), nm, kinds[i], (f"{addr}  ({lt})")]); mergerow(r,3,NCOLS)
        fmt(r,0,1,bg=WHITE,fg=DARK,bold=True,align="CENTER")
        fmt(r,1,2,bg=(MTB_BG if kinds[i]=="MTB" else HIKE_BG),fg=DARK,bold=True,align="LEFT")
        fmt(r,2,3,bg=(MTB_BG if kinds[i]=="MTB" else HIKE_BG),fg=DARK,align="CENTER")
        kbg = NOTE_BG if lt=='APPROXIMATE' else WHITE
        kfg = rgb(200,0,0) if lt=='FAIL' else (rgb(120,70,0) if lt=='APPROXIMATE' else GREY)
        fmt(r,3,NCOLS,bg=kbg,fg=kfg,align="LEFT",wrap=True)
        heights.append((r,18))
    # matrix
    r=row(["min"]+[str(i+1) for i in range(N)]);
    fmt(r,0,1,bg=COL_HDR,fg=DARK,bold=True,align="CENTER")
    for j in range(N):
        fmt(r,1+j,2+j,bg=(MTB_BG if kinds[j]=="MTB" else HIKE_BG),fg=DARK,bold=True,align="CENTER")
    heights.append((r,18))
    for i in range(N):
        cells=[f"{i+1}. {names[i]}"]
        for j in range(N):
            cells.append("—" if i==j else (str(d["mat"][i][j]) if d["mat"][i][j] is not None else "?"))
        r=row(cells)
        fmt(r,0,1,bg=(MTB_BG if kinds[i]=="MTB" else HIKE_BG),fg=DARK,bold=True,align="LEFT")
        for j in range(N):
            v=d["mat"][i][j]
            bg=WHITE
            if i==j: bg=rgb(230,230,230)
            elif v is None: bg=REDISH
            elif v<=THRESHOLD: bg=GREEN
            elif v<=30: bg=YELLOW
            fmt(r,1+j,2+j,bg=bg,fg=DARK,align="CENTER")
        heights.append((r,18))
    row([""])
    # < threshold pairs (separate-day candidates) — MTB↔HIKE first
    r=row([f"⭐ SAME-DAY CANDIDATES — trailheads ≤ {THRESHOLD} min apart"]); mergerow(r); fmt(r,0,NCOLS,bg=rgb(0,131,143),fg=WHITE,bold=True,size=10,align="CENTER"); heights.append((r,22))
    mtbhike=[p for p in d["pairs"] if kinds[p[1]]!=kinds[p[2]]]
    same=[p for p in d["pairs"] if kinds[p[1]]==kinds[p[2]]]
    if mtbhike:
        r=row(["MTB + HIKE (Ian rides, Anny hikes — drop-off works):"]); mergerow(r); fmt(r,0,NCOLS,bg=WHITE,fg=DARK,bold=True,align="LEFT"); heights.append((r,18))
        for mins,i,j in mtbhike:
            mtb = names[i] if kinds[i]=="MTB" else names[j]
            hike = names[j] if kinds[j]=="HIKE" else names[i]
            r=row([f"   🚵 {mtb}   ↔   🥾 {hike}   —   {mins} min"]); mergerow(r)
            fmt(r,0,NCOLS,bg=GREEN,fg=DARK,bold=True,align="LEFT"); heights.append((r,18))
    if same:
        r=row(["Same-type (two rides or two hikes near each other):"]); mergerow(r); fmt(r,0,NCOLS,bg=WHITE,fg=GREY,bold=True,align="LEFT"); heights.append((r,18))
        for mins,i,j in same:
            r=row([f"   {names[i]}   ↔   {names[j]}   —   {mins} min"]); mergerow(r)
            fmt(r,0,NCOLS,bg=rgb(245,245,245),fg=GREY,align="LEFT"); heights.append((r,18))
    if not d["pairs"]:
        r=row([f"   (none ≤ {THRESHOLD} min among reliably-geocoded trailheads)"]); mergerow(r); fmt(r,0,NCOLS,bg=WHITE,fg=GREY,align="LEFT"); heights.append((r,18))
    if d["unreliable"]:
        r=row([f"   ⚠ Excluded from pairs (geocode too coarse — give me exact coords to include): {', '.join(d['unreliable'])}"])
        mergerow(r); fmt(r,0,NCOLS,bg=NOTE_BG,fg=rgb(120,70,0),align="LEFT"); heights.append((r,20))
    row([""]); row([""])

# write
if TAB in [w.title for w in sh.worksheets()]:
    sh.del_worksheet(sh.worksheet(TAB))
ws = sh.add_worksheet(title=TAB, rows=max(len(values)+5, 80), cols=NCOLS)
sid = ws._properties["sheetId"]
ws.update(values, "A1", value_input_option="USER_ENTERED")
reqs=[]
for f in fmts:
    f["repeatCell"]["range"]["sheetId"]=sid; reqs.append(f)
for (r,c0,c1) in merges:
    reqs.append({"mergeCells":{"range":{"sheetId":sid,"startRowIndex":r,"endRowIndex":r+1,"startColumnIndex":c0,"endColumnIndex":c1},"mergeType":"MERGE_ALL"}})
widths=[180]+[46]*(NCOLS-1)
for i,px in enumerate(widths):
    reqs.append({"updateDimensionProperties":{"range":{"sheetId":sid,"dimension":"COLUMNS","startIndex":i,"endIndex":i+1},"properties":{"pixelSize":px},"fields":"pixelSize"}})
for (r,px) in heights:
    reqs.append({"updateDimensionProperties":{"range":{"sheetId":sid,"dimension":"ROWS","startIndex":r,"endIndex":r+1},"properties":{"pixelSize":px},"fields":"pixelSize"}})
reqs.append({"updateSheetProperties":{"properties":{"sheetId":sid,"gridProperties":{"frozenRowCount":1,"hideGridlines":True}},"fields":"gridProperties.frozenRowCount,gridProperties.hideGridlines"}})
sh.batch_update({"requests":reqs})
print(f"\nOK: '{TAB}' built — {len(values)} rows.")
