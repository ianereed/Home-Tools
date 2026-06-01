"""Generate one detailed tab per trip day (flexible + fixed), templated from the
Itinerary. For flexible hub days it lists that hub's day-options; for Ian-MTB days it
suggests a nearby hike for Anny (from the Trailhead Distances pairs). Batched: a few API
calls total. Idempotent: deletes + recreates all generated day tabs.

Tab name = '<Date> (<Dow>)', e.g. 'Jul 23 (Thu)'.
"""
import re, urllib.parse
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
NCOLS = 6

def rgb(r, g, b): return {"red": r/255, "green": g/255, "blue": b/255}
TITLE_BG=rgb(23,37,84); SUB_BG=rgb(40,60,110); NAVY=rgb(40,60,110)
IAN=rgb(2,119,189); ANNY=rgb(46,125,50); MOCHI=rgb(0,131,143); TOG=rgb(106,27,154)
GREY=rgb(97,97,97); LABEL_BG=rgb(238,240,243); WHITE=rgb(255,255,255); DARK=rgb(33,33,33)
LINKC=rgb(21,101,192); WARN=rgb(255,243,205); GREEN=rgb(232,245,233); FLEXBG=rgb(225,240,250)
TRAVEL=rgb(253,242,222)

# hub config: base addr + short label + day-options menu + MTB→nearby-hike pairs
HUBS = {
 "Boulder": {"base":"582 Locust Place, Boulder, CO 80304","blabel":"582 Locust Pl",
   "menu":[("BLD-A","Together: Green Mountain + dinner"),("BLD-B","Separate: Ian runs Sanitas / Anny+Mochi valley"),
           ("BLD-C","Big day: Indian Peaks alpine lakes"),("BLD-D","Day trip: RMNP Bear Lake + Trail Ridge"),
           ("BLD-E","Separate: Ian Valmont / Anny+Mochi foothills"),("BLD-F","Together: Eldorado or Mesa Trail"),
           ("BLD-G","Town day: Pearl St + climbing + breweries"),("BLD-H","Day trip: Golden"),
           ("BLD-I","Separate: Ian Walker Ranch / Anny+Mochi Flatirons Vista"),("BLD-J","Easy: Reservoir dog beach")],
   "pairs":["Marshall Mesa (ride) ↔ Flatirons Vista hike — 6 min","Marshall Mesa ↔ Chautauqua — 10 min",
            "Marshall Mesa ↔ Eldorado Canyon — 10 min","Marshall Mesa ↔ Gregory Canyon — 12 min"]},
 "Steamboat": {"base":"1036 Lincoln Avenue, Steamboat Springs, CO 80487","blabel":"1036 Lincoln Ave",
   "menu":[("STM-A","Together: Fish Creek Falls"),("STM-B","Separate: Ian bike park / Anny+Mochi Emerald + hot springs"),
           ("STM-C","Big day: Hahns Peak + Fishhook"),("STM-D","Town/rest: Strawberry Park Hot Springs")],
   "pairs":["Spring Creek (ride) ↔ Fish Creek Falls hike — 8 min","Howelsen/Emerald (ride) ↔ Fish Creek Falls — 11 min"]},
 "Crested Butte": {"base":"6 Emmons Road, Crested Butte, CO 81225","blabel":"6 Emmons Rd (Mt CB)",
   "menu":[("CB-A","Separate: Ian Evolution / Anny+Mochi Oh-Be-Joyful + Alpenglow"),
           ("CB-B","Separate: Ian Evolution day 2 / Anny+Mochi Three Lakes"),
           ("CB-C","Big day: West Maroon Pass → Aspen")],
   "pairs":["Brush Creek (ride) ↔ Oh-Be-Joyful hike — 6 min","Lower Loop (ride) ↔ Emerald Lake hike — 6 min",
            "Lower Loop ↔ Oh-Be-Joyful — 10 min","Brush Creek ↔ Emerald Lake — 11 min"]},
 "Mammoth": {"base":None,"blabel":None,"menu":[],"pairs":[]},
}
GID = {w.title: w.id for w in sh.worksheets()}
def ref(title):  # full-URL internal link to another tab (native-link friendly)
    return f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid={GID.get(title,0)}"
def mapsearch(q):
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(q)
def dirfrom(base, dest):
    return ("https://www.google.com/maps/dir/?api=1&origin=" + urllib.parse.quote(base)
            + "&destination=" + urllib.parse.quote(dest))

def hub_of(text):
    t = text.lower()
    for h in ("Boulder","Steamboat","Mammoth"):
        if h.lower() in t: return h
    if "crested butte" in t or t.strip()=="cb": return "Crested Butte"
    return None

# ── read itinerary day rows ──────────────────────────────────────────────────────
iv = sh.worksheet("Itinerary").get_all_values()
def cell(r,i): return r[i].strip() if i < len(r) else ""
days = [r for r in iv if re.match(r"^(Jul|Aug) \d+$", r[0].strip())]

LANES = [(10,"🚵 Ian",IAN),(11,"🥾 Anny",ANNY),(12,"🐕 Mochi",MOCHI),
         (13,"👫 Ian + Anny",TOG),(14,"👥 Everyone",TOG),(15,"☔ Backup",GREY)]
POINTERS = [("→ Activities","Activities — Hikes, Runs & MTB"),("→ Day Options","DAY OPTIONS"),
            ("→ Trailhead Distances","Trailhead Distances"),("→ Dining","Dining Guide"),
            ("→ Dog Daycare","Dog Daycare Options")]

# ── build content per day ────────────────────────────────────────────────────────
content = {}  # title -> dict(values, fmts, merges, heights, links)
order = []
for r in days:
    date=cell(r,0); dow=cell(r,1); wake=cell(r,2); miles=cell(r,3); hrs=cell(r,4)
    sleep=cell(r,5); plan=cell(r,6); notes=cell(r,7); todo=cell(r,8); opp=cell(r,9)
    moreinfo=cell(r,16); daycare=cell(r,17)
    if not (plan or wake or sleep): continue   # skip blank trailing rows
    title=f"{date} ({dow})"
    hub = hub_of(sleep) or hub_of(wake)
    try: mi=float(miles or 0)
    except: mi=0
    is_travel = ("drive" in plan.lower()) or mi >= 80
    same = hub and hub.lower() in (wake+sleep).lower() and ("drive" not in plan.lower())
    is_flexible = bool(same and hub in HUBS and HUBS[hub]["menu"] and mi < 60)
    daytype = "TRAVEL DAY" if is_travel else ("FLEXIBLE — pick from the menu" if is_flexible else "FIXED / SET")
    mtb_day = any(k in cell(r,10).lower() for k in ("bike","mtb","ride","bike park"))

    V=[]; F=[]; M=[]; H=[]; L=[]
    def row(cells):
        V.append(list(cells)+[""]*(NCOLS-len(cells))); return len(V)-1
    def fmt(ri,c0,c1,bg=None,fg=None,bold=False,size=None,align=None,valign="MIDDLE",wrap=True,italic=False):
        cf={}
        if bg is not None: cf["backgroundColor"]=bg
        tf={"bold":bold,"italic":italic}
        if fg is not None: tf["foregroundColor"]=fg
        if size is not None: tf["fontSize"]=size
        cf["textFormat"]=tf
        if align: cf["horizontalAlignment"]=align
        cf["verticalAlignment"]=valign; cf["wrapStrategy"]="WRAP" if wrap else "OVERFLOW_CELL"
        F.append((ri,c0,c1,cf))
    def mg(ri,c0=0,c1=NCOLS): M.append((ri,c0,c1))
    def link(ri,ci,label,url): L.append((ri,ci,label,url))
    def section(label,bg):
        ri=row([label]); mg(ri); fmt(ri,0,NCOLS,bg=bg,fg=WHITE,bold=True,size=11,align="CENTER"); H.append((ri,24))
    def kv(label,value,h=28,linkurl=None):
        ri=row([label,"",value]); mg(ri,0,2); mg(ri,2,NCOLS)
        fmt(ri,0,2,bg=LABEL_BG,fg=DARK,bold=True,align="LEFT",valign="TOP")
        fmt(ri,2,NCOLS,bg=WHITE,fg=(LINKC if linkurl else DARK),align="LEFT",valign="TOP")
        if linkurl: link(ri,2,value,linkurl)
        H.append((ri,h))

    # title
    ri=row([f"{date} · {dow}"]); mg(ri); fmt(ri,0,NCOLS,bg=TITLE_BG,fg=WHITE,bold=True,size=15,align="CENTER"); H.append((ri,32))
    ri=row([plan or "(no plan yet — pick from the menu)"]); mg(ri); fmt(ri,0,NCOLS,bg=SUB_BG,fg=WHITE,italic=True,size=10,align="CENTER"); H.append((ri,30))
    # daytype banner
    tbg = TRAVEL if is_travel else (FLEXBG if is_flexible else LABEL_BG)
    ri=row([daytype]); mg(ri); fmt(ri,0,NCOLS,bg=tbg,fg=DARK,bold=True,size=9,align="CENTER"); H.append((ri,20))
    row([""])

    # at a glance
    section("AT A GLANCE", NAVY)
    kv("Wake up", wake or "—"); kv("Sleep", sleep or "—")
    kv("Driving", (f"{miles} mi · ~{hrs} hr" if miles or hrs else "—"))
    if hub and HUBS.get(hub,{}).get("base"):
        kv("Home base", HUBS[hub]["blabel"], linkurl=mapsearch(HUBS[hub]["base"]))
    if todo: kv("Reservations / to-do", todo, h=34)
    if notes: kv("Notes", notes, h=34)
    row([""])

    # flexible menu
    if is_flexible:
        section(f"PICK YOUR DAY — {hub} menu", IAN)
        for oid,label in HUBS[hub]["menu"]:
            ri=row([oid,"",label]); mg(ri,0,2); mg(ri,2,NCOLS)
            fmt(ri,0,2,bg=FLEXBG,fg=DARK,bold=True,align="CENTER"); fmt(ri,2,NCOLS,bg=WHITE,fg=DARK,align="LEFT")
            H.append((ri,20))
        ri=row(["Full detail + drive times + checkboxes →","","DAY OPTIONS tab"]); mg(ri,0,2); mg(ri,2,NCOLS)
        fmt(ri,0,2,bg=WHITE,fg=GREY,italic=True,align="LEFT"); fmt(ri,2,NCOLS,bg=WHITE,fg=LINKC,align="LEFT"); link(ri,2,"DAY OPTIONS tab",ref("DAY OPTIONS")); H.append((ri,20))
        row([""])

    # plan by person
    lanes=[(idx,lbl,col) for idx,lbl,col in LANES if cell(r,idx)]
    if lanes:
        section("THE PLAN", NAVY)
        for idx,lbl,col in lanes:
            ri=row([lbl,"",cell(r,idx)]); mg(ri,0,2); mg(ri,2,NCOLS)
            fmt(ri,0,2,bg=col,fg=WHITE,bold=True,align="LEFT",valign="TOP"); fmt(ri,2,NCOLS,bg=WHITE,fg=DARK,align="LEFT",valign="TOP")
            H.append((ri,38))
        row([""])

    # MTB → nearby hike pairing
    if mtb_day and hub in HUBS and HUBS[hub]["pairs"]:
        section("💡 IAN RIDES → ANNY HIKES NEARBY  (≤15 min between trailheads)", MOCHI)
        for p in HUBS[hub]["pairs"]:
            ri=row([f"   {p}"]); mg(ri); fmt(ri,0,NCOLS,bg=GREEN,fg=DARK,align="LEFT"); H.append((ri,18))
        ri=row(["Ride details → Activities (MTB section)","","verify times → Trailhead Distances"]); mg(ri,0,3); mg(ri,3,NCOLS)
        fmt(ri,0,3,bg=WHITE,fg=LINKC,align="LEFT"); fmt(ri,3,NCOLS,bg=WHITE,fg=LINKC,align="LEFT")
        link(ri,0,"Activities (MTB section)",ref("Activities — Hikes, Runs & MTB")); link(ri,3,"Trailhead Distances",ref("Trailhead Distances")); H.append((ri,18))
        row([""])

    # opportunities / pointers
    if opp: kv("Opportunities", opp, h=34)
    if moreinfo: kv("More info", moreinfo, h=24)
    if daycare: kv("Dog daycare", daycare, h=24)
    # tab pointers
    ri=row(["See also:"]); mg(ri); fmt(ri,0,NCOLS,bg=LABEL_BG,fg=DARK,bold=True,align="LEFT"); H.append((ri,18))
    for lbl,tt in POINTERS:
        if tt in GID:
            ri=row([lbl]); mg(ri); fmt(ri,0,NCOLS,bg=WHITE,fg=LINKC,align="LEFT"); link(ri,0,lbl,ref(tt)); H.append((ri,18))

    content[title]={"V":V,"F":F,"M":M,"H":H,"L":L}; order.append(title)

# ── delete existing generated day tabs, then create all ──────────────────────────
existing=[w for w in sh.worksheets() if re.match(r"^(Jul|Aug) \d+ \(", w.title)]
if existing:
    sh.batch_update({"requests":[{"deleteSheet":{"sheetId":w.id}} for w in existing]})
add_reqs=[{"addSheet":{"properties":{"title":t,"gridProperties":{"rowCount":max(len(content[t]["V"])+3,30),"columnCount":NCOLS,"hideGridlines":True,"frozenRowCount":1}}}} for t in order]
resp=sh.batch_update({"requests":add_reqs})
title2sid={}
for rep in resp["replies"]:
    p=rep["addSheet"]["properties"]; title2sid[p["title"]]=p["sheetId"]

# values (one batch)
value_data=[{"range":f"'{t}'!A1","values":content[t]["V"]} for t in order]
sh.values_batch_update({"valueInputOption":"USER_ENTERED","data":value_data})

# formatting + merges + dims + native links (chunked batch_update)
WIDTHS=[70,120,130,130,130,210]
reqs=[]
for t in order:
    sid=title2sid[t]; c=content[t]
    for (ri,c0,c1,cf) in c["F"]:
        reqs.append({"repeatCell":{"range":{"sheetId":sid,"startRowIndex":ri,"endRowIndex":ri+1,"startColumnIndex":c0,"endColumnIndex":c1},
            "cell":{"userEnteredFormat":cf},"fields":"userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)"}})
    for (ri,c0,c1) in c["M"]:
        reqs.append({"mergeCells":{"range":{"sheetId":sid,"startRowIndex":ri,"endRowIndex":ri+1,"startColumnIndex":c0,"endColumnIndex":c1},"mergeType":"MERGE_ALL"}})
    for i,px in enumerate(WIDTHS):
        reqs.append({"updateDimensionProperties":{"range":{"sheetId":sid,"dimension":"COLUMNS","startIndex":i,"endIndex":i+1},"properties":{"pixelSize":px},"fields":"pixelSize"}})
    for (ri,px) in c["H"]:
        reqs.append({"updateDimensionProperties":{"range":{"sheetId":sid,"dimension":"ROWS","startIndex":ri,"endIndex":ri+1},"properties":{"pixelSize":px},"fields":"pixelSize"}})
    for (ri,ci,label,url) in c["L"]:
        reqs.append({"updateCells":{"rows":[{"values":[{"userEnteredValue":{"stringValue":label},
            "textFormatRuns":[{"startIndex":0,"format":{"link":{"uri":url},"underline":True,"foregroundColor":LINKC}}]}]}],
            "fields":"userEnteredValue,textFormatRuns","start":{"sheetId":sid,"rowIndex":ri,"columnIndex":ci}}})
for k in range(0,len(reqs),400):
    sh.batch_update({"requests":reqs[k:k+400]})

print(f"OK: built {len(order)} day tabs ({order[0]} … {order[-1]}); {len(reqs)} format/link requests.")
