"""Rebuild the trip's day tabs under the new model:

  • FLEXIBLE days (Boulder Jul 23-31, Mammoth Aug 15-17) get NO per-day tab. They are
    run from the DAY OPTIONS menu, which links to one OPTION tab per menu
    row: BLD-A..J, MAM-A..D (14 tabs, rich BLD-E style).
  • FIXED days (everything else) get a date-titled tab ("Jul 16 (Thu)" ...), linked
    from the Itinerary's date cell.

Phases:
  1. delete the 39 old "Mon DD (Dow)" per-day tabs + the old BLD-E draft (idempotent —
     also deletes any of the NEW titles if a prior run created them).
  2. create + populate the 14 option tabs and the 20 fixed-day tabs.
  3. wire links: DAY OPTIONS ID cell -> option tab; Itinerary date cell -> fixed tab
     (or -> DAY OPTIONS for flexible days).

All links are native rich-text links (see memory feedback_gsheets_hyperlink_native).
Re-runnable: it deletes the tabs it owns before recreating, so design tweaks = edit +
re-run. Does NOT touch Activities / Dining / Trailhead Distances / etc.
(2026-07-14 restructure: Steamboat / Twin Lakes / Crested Butte / SLC / Ely legs
cancelled — Aug 1-5 are now Boulder→Redwood City drive-home tabs. Aug 14+ tabs
(Mammoth, Rae Lakes) are kept but OUT OF SCOPE for active planning per the user.)
"""
import re
import os
import sys
import time
import argparse
import subprocess
from urllib.parse import quote as _q
import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

# throttle writes to stay under 60 write-requests/min (Sheets per-user quota)
_obu, _ovu = sh.batch_update, sh.values_update
def _bu(*a, **k): time.sleep(2.0); return _obu(*a, **k)
def _vu(*a, **k): time.sleep(2.0); return _ovu(*a, **k)
sh.batch_update, sh.values_update = _bu, _vu

NCOLS = 12

def rgb(r, g, b): return {"red": r/255, "green": g/255, "blue": b/255}
TITLE_BG=rgb(23,37,84); SUB_BG=rgb(30,58,138); SEC_BG=rgb(225,228,234)
WHITE=rgb(255,255,255); DARK=rgb(33,33,33); GREY=rgb(96,96,96)
KEY_BG=rgb(243,246,250); WARN=rgb(255,243,205); LINKC=rgb(21,101,192)
ALT=rgb(247,250,252); IAN_BG=rgb(232,240,254); ANNY_BG=rgb(244,236,252)
MOCHI_BG=rgb(255,247,230); TOG_BG=rgb(232,245,233); BACK_BG=rgb(238,238,238)
BANNER_FIX=rgb(96,125,139); BANNER_TRAVEL=rgb(120,144,156); BANNER_FLEX=rgb(46,125,50)
MAPBTN_BG=rgb(219,237,255)

def turl(gid): return f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid={gid}"

# ── stable reference tabs (looked up at runtime) ─────────────────────────────────
_meta = sh.fetch_sheet_metadata()
GID = {s["properties"]["title"]: s["properties"]["sheetId"] for s in _meta["sheets"]}
REF = {
    "itin":   GID["Itinerary"],
    "menu":   GID["DAY OPTIONS"],
    "acts":   GID["Activities — Hikes, Runs & MTB"],
    "dining": GID["Dining Guide"],
    "daycare":GID["Dog Daycare Options"],
    "scenic": GID["Scenic Stops & Drives"],
    "thd":    GID["Trailhead Distances"],
    "shuttle":GID["MTB Shuttles & Guides"],
}

# ── manual-edit detection (genmeta) ──────────────────────────────────────────────
# Every tab is regenerated from the Python source below, which would clobber any edit
# made by hand in the live sheet. genmeta fingerprints each tab as we write it and lets
# flush() refuse to overwrite a tab a human has touched. See genmeta.py.
import genmeta
_META = None        # lazy-loaded {title: fingerprint}; persisted to the hidden _genmeta tab
DIRTY = []          # tabs skipped this run because they had manual edits
FORCE = set()       # specific tab titles to overwrite anyway
FORCE_ALL = False   # overwrite every tab regardless of manual edits

def _ensure_meta():
    global _META
    if _META is None:
        _META = genmeta.load(sh)
    return _META

def save_genmeta():
    """Persist fingerprints. The run block calls this automatically; call it yourself
    after an ad-hoc single-tab build (e.g. build_fixed(...) from a REPL) so the baseline
    doesn't go stale and falsely flag the tab dirty on the next full run."""
    if _META is not None:
        genmeta.save(sh, _META)

# ── activity lookup: verbatim links pulled from the Activities tab ───────────────
def pin(q): return f"https://www.google.com/maps/search/?api=1&query={q}"
def maps_route(stops):
    return "https://www.google.com/maps/dir/" + "/".join(s.replace(" ","%20").replace(",","%2C") for s in stops)
ACT = {
 # Boulder hikes/runs
 "chautauqua": dict(name="Chautauqua Meadow Walk", th="Chautauqua Park TH",
   pin=pin("Chautauqua%20Park%20TH%2C%20Boulder%2C%20CO"), stats="1–2 mi · ~200 ft · easy",
   drive="in town", dog="Leash", note="Easy opening-day stroll."),
 "green_mtn": dict(name="Green Mountain via Gregory Canyon → Ranger", th="Gregory Canyon TH",
   pin=pin("Gregory%20Canyon%20Trailhead%2C%20Boulder%2C%20CO"),
   link="https://www.alltrails.com/trail/us/colorado/green-mountain-via-gregory-canyon-and-ranger-trail",
   linklabel="AllTrails ▸ Green Mtn via Gregory Canyon",
   stats="6 mi · 2,400 ft · Hard · 4.8★ (1,200+ reviews)",
   drive="~10 min", dog="Leash (OSMP)",
   note="$5 OSMP permit (ParkMobile zone 24700). Tiny lot — start by 7am. Rocky the whole way; final push is a steep boulder staircase."),
 "sanitas_valley": dict(name="Sanitas Valley Trail (Anny)", th="Mt Sanitas TH",
   pin=pin("Mt%20Sanitas%20TH%2C%20Boulder%2C%20CO"), stats="2 mi · ~400 ft · easy",
   drive="in town", dog="Leash", note="Same trailhead as Ian's run — meet back at the car."),
 "sanitas_run": dict(name="Mt Sanitas Loop (Ian, run)", th="Mt Sanitas TH",
   pin=pin("Mt%20Sanitas%20TH%2C%20Boulder%2C%20CO"), stats="3.2 mi · 1,270 ft",
   drive="in town", dog="With Anny", note="Back by lunch; same trailhead as Anny's hike."),
 "eldorado": dict(name="Eldorado Canyon Trail", th="Eldorado Canyon SP",
   pin=pin("Eldorado%20Canyon%20SP%2C%20Boulder%2C%20CO"), stats="6.7 mi · 1,978 ft",
   drive="~20 min", dog="Leash", note="TIMED ENTRY (cpw.state.co.us), $10/vehicle. Watch climbers."),
 "mesa_trail": dict(name="Mesa Trail (Chautauqua ↔ South Mesa)", th="Chautauqua Park TH",
   pin=pin("Chautauqua%20Park%20TH%2C%20Boulder%2C%20CO"), stats="7 mi · ~2,000 ft rolling",
   drive="~5 min", dog="Leash", note="No reservation needed. Good Eldorado alternative."),
 "flatirons_vista": dict(name="Flatirons Vista / Doudy Draw (Anny)", th="Flatirons Vista TH",
   pin=pin("Flatirons%20Vista%20TH%2C%20Boulder%2C%20CO"), stats="3.4 mi · ~500 ft",
   drive="~10 min", dog="Leash", note="Dog-friendly loop; Anny solo while Ian rides/runs."),
 "lake_isabelle": dict(name="Lake Isabelle + Blue Lake (Indian Peaks)", th="Long Lake TH (Brainard)",
   pin=pin("Long%20Lake%20TH%20%28Brainard%29%2C%20Boulder%2C%20CO"), stats="6–8 mi · ~1,200 ft",
   drive="~45 min", dog="Leash", note="TIMED ENTRY for Brainard parking (recreation.gov, opens ~Jul 16). Best alpine near Boulder."),
 "wonderland": dict(name="Wonderland Lake + Foothills (Anny)", th="Wonderland Lake TH (4201 N Broadway)",
   pin=pin("Wonderland%20Lake%20Trailhead%2C%204201%20N%20Broadway%2C%20Boulder%2C%20CO"),
   stats="1.3–2.5 mi · easy", drive="~5 min", dog="Leash (protected area — stay on trail)",
   note="Flat, fast, foothills + reservoir views."),
 "walker_run": dict(name="Walker Ranch Loop (Ian, run)", th="Walker Ranch TH (Flagstaff Rd)",
   pin=pin("Walker%20Ranch%20TH%2C%20Boulder%2C%20CO"), stats="7.6 mi · ~1,650 ft",
   drive="~15 min", dog="With Anny", note="Anny drops Ian at the trailhead, hikes nearby, picks up."),
 # Boulder MTB
 "valmont": dict(name="Valmont Bike Park (Ian, MTB)", th="3160 Airport Rd (in town)",
   pin=pin("Valmont%20Bike%20Park%2C%20Boulder%2C%20CO"),
   link="https://www.trailforks.com/region/valmont-bike-park/", linklabel="Trailforks ▸ Valmont",
   stats="15 trails · 🟢→⚫", drive="~10 min E", dog="Leash",
   note="Free, in town. Skills / jumps / pump-track. CLOSES when wet — check the morning of.",
   nearhike="Boulder Creek Path"),
 "walker_mtb": dict(name="Walker Ranch Loop (Ian, MTB) ***", th="Walker Ranch TH (Flagstaff Rd)",
   pin=pin("Walker%20Ranch%20Trailhead%2C%20Flagstaff%20Road%2C%20Boulder%2C%20CO"),
   link="https://www.trailforks.com/trails/walker-ranch-loop/", linklabel="Trailforks ▸ Walker Ranch",
   stats="7.8 mi loop · ~1,510 ft · 🔴 Black", drive="~25 min W", dog="Leash",
   note="MUST-RIDE: best descent in the area; marquee technical day.",
   nearhike="Meyers Homestead Trail (same lot — Anny's dog hike)"),
 # Steamboat
 "fish_creek": dict(name="Fish Creek Falls", th="Fish Creek Falls TH",
   pin=pin("Fish%20Creek%20Falls%20TH%2C%20Steamboat%2C%20CO"), stats="lower 0.5 mi / upper 5 mi RT · ~900 ft",
   drive="~5 min", dog="Leash (off-leash claims are unofficial — verify)", note="$5 parking, cash/check; start early (popular)."),
 "emerald_blackmere": dict(name="Emerald Mtn Blackmere Trail (Anny)", th="Howelsen Hill",
   pin=pin("Howelsen%20Hill%2C%20Steamboat%2C%20CO"), stats="3.7 mi · 938 ft",
   drive="walkable from downtown", dog="Leash", note="Anny solo while Ian bikes Emerald/the park."),
 "emerald_run": dict(name="Emerald Mountain System (Ian, run)", th="Howelsen Hill",
   pin=pin("Howelsen%20Hill%2C%20Steamboat%2C%20CO"), stats="6–8 mi · flexible",
   drive="walkable", dog="With Anny", note="Back by lunch; flexible distance on the network."),
 "hahns": dict(name="Hahns Peak summit", th="Hahns Peak TH",
   pin=pin("Hahns%20Peak%20TH%2C%20Steamboat%2C%20CO"), stats="3 mi RT · ~900 ft",
   drive="~30–40 min N", dog="Leash", note="Fire-lookout summit views; pair with Fishhook Lake."),
 "fishhook": dict(name="Fishhook Lake", th="Hahns Peak area",
   pin=pin("Hahns%20Peak%20area%2C%20Steamboat%2C%20CO"), stats="6 mi RT · ~1,200 ft",
   drive="same drive as Hahns", dog="Leash", note="Dog-friendly; combine with Hahns Peak for a full day."),
 "red_dirt": dict(name="Red Dirt Trail (Anny)", th="Red Dirt TH",
   pin=pin("Red%20Dirt%20TH%2C%20Steamboat%2C%20CO"), stats="~8 mi · gentle",
   drive="~20 min", dog="Leash", note="Longest dog-friendly Steamboat trail; creeks + wildflowers."),
 "steamboat_bp": dict(name="Steamboat Bike Park (Ian, MTB)", th="Steamboat Resort base",
   pin=pin("Steamboat%20Resort%2C%20Steamboat%20Springs%2C%20CO"),
   link="https://www.trailforks.com/region/steamboat-bike-park/", linklabel="Trailforks ▸ Steamboat Bike Park",
   stats="2,200 ft vert (lift) · 🟢→🔴", drive="~5 min", dog="No (lift-served)",
   note="Lift-served DH/enduro. $50–70/day; Ikon Pass = 2 free days.",
   nearhike="Yampa River Core Trail / Emerald Mtn"),
 # Crested Butte
 "emerald_lake": dict(name="Emerald Lake", th="Gothic Rd TH",
   pin=pin("Gothic%20Rd%20TH%2C%20Crested%20Butte%2C%20CO"), stats="1.7 mi · ~350 ft · easy",
   drive="~5 min", dog="Leash (can swim at the lake)", note="Easy warm-up; Mochi swims."),
 "oh_be_joyful": dict(name="Oh-Be-Joyful Trail (Anny)", th="Oh-Be-Joyful TH",
   pin=pin("Oh-Be-Joyful%20TH%2C%20Crested%20Butte%2C%20CO"),
   link="https://www.alltrails.com/trail/us/colorado/oh-be-joyful--3", linklabel="AllTrails ▸ Oh-Be-Joyful",
   stats="9.6 mi · 2,162 ft · hard", drive="~20 min", dog="Leash",
   note="4.8★ — most popular CB trail. Big day for Anny while Ian rides."),
 "judd_falls": dict(name="Judd Falls / Copper Creek (Anny)", th="Judd Falls TH (Gothic Rd)",
   pin=pin("Judd%20Falls%20Trailhead%2C%20Gothic%20Road%2C%20Crested%20Butte%2C%20CO"),
   stats="moderate · dog-friendly", drive="~15 min", dog="Leash",
   note="Mellower alternative to Oh-Be-Joyful."),
 "three_lakes": dict(name="Three Lakes Loop (Anny)", th="Kebler Pass Rd",
   pin=pin("Kebler%20Pass%20Rd%2C%20Crested%20Butte%2C%20CO"), stats="3 mi · ~700 ft · easy",
   drive="~20 min", dog="Leash", note="3 alpine lakes + a waterfall detour. Anny solo while Ian rides."),
 "evolution": dict(name="Evolution Bike Park (Ian, MTB)", th="CBMR base (Mt Crested Butte)",
   pin=pin("Evolution%20Bike%20Park%2C%20Mount%20Crested%20Butte%2C%20CO"),
   link="https://www.trailforks.com/region/cbmr-evolution-bike-park/", linklabel="Trailforks ▸ Evolution",
   stats="54 trails · 🟢→⚫", drive="walk from the Airbnb / ~2 min", dog="No (bike park)",
   note="Lift-served DH/enduro, world-class. $60–70/day — get the 2-day pass for Aug 10+11.",
   nearhike="Woods Walk / Lower Loop"),
}

# ════════════════════════════════════════════════════════════════════════════════
#  OPTION TABS  (flexible days — linked from DAY OPTIONS)
# ════════════════════════════════════════════════════════════════════════════════
BASE = {"BLD":"582 Locust Pl, Boulder, CO 80304",
        "STM":"1036 Lincoln Ave, Steamboat Springs, CO 80487",
        "CB":"6 Emmons Rd, Unit 122, Mt Crested Butte, CO 81225",
        "MAM":"Mammoth Lakes, CA 93546"}
HUBNAME = {"BLD":"Boulder","STM":"Steamboat","CB":"Crested Butte","MAM":"Mammoth"}

# each: id, type, oneliner, ctx, drive, ian, anny, mochi, together, acts (detail blocks),
#       res, backup, evening
OPTIONS = [
 dict(id="BLD-A", type="TOGETHER DAY", drive="~31 min · 10 mi round trip",
   oneliner="Together: Green Mountain summit via Gregory Canyon, then a Pearl St evening",
   ctx="The classic Boulder summit — all three of us up Gregory Canyon to the top of Green Mountain (6 mi, 2,400 ft, rated Hard), back by early afternoon, then a dog-friendly lunch and a downtown evening.",
   ian="Green Mtn via Gregory Canyon → Ranger to the summit (6 mi, 2,400 ft).",
   anny="Same hike — fine to turn around at the Ranger/Greenman saddle if it's too much; the canyon itself is the prettiest part.",
   mochi="On leash the whole way (OSMP). Carry 2–3 L of water for him — it's rocky, steep, and warm by midday. He likely can't do the final 8–10 ft summit boulder, so one person holds him while the other scrambles up to the register.",
   together="Down by ~1pm. Dog-friendly lunch in town, then ease into a Pearl St evening — optionally drive up Flagstaff for sunset first.",
   acts=["green_mtn"],
   route_stops=["Gregory Canyon Trailhead, Boulder, CO",
                "Chautauqua Dining Hall, Boulder, CO",
                "Pearl Street Mall, Boulder, CO"],
   res="$5 OSMP daily permit — pay at the kiosk or prepay on the ParkMobile app (zone 24700). Valid 5am–9pm and at every OSMP lot that day. Prepaying does NOT hold a space, so still arrive early.",
   backup="Storm / heat: Flagstaff Mountain trails (shorter, same permit) or the flat Boulder Creek Path. If Mochi overheats, cool him at East Boulder Dog Park's pond (fenced, wadeable).",
   evening="Colorado Music Festival @ Chautauqua, or Bands on the Bricks (free, Pearl St, 5:30–9pm)",
   beta=[
     "Rated Hard, 4.8★ from 1,200+ AllTrails reviews — a consistent rocky incline almost the whole way; the last stretch to the top is a steep, rocky 'staircase.'",
     "Best route (a local who's summited 50+ times): UP Gregory Canyon → Ranger → Green Mtn West Ridge to the summit, DOWN E.M. Greenman → Saddle Rock. Loops it for more scenery than an out-and-back.",
     "Greenman (the loop's descent) is steeper and rockier than Ranger. If knees are complaining at the top, just retrace Ranger back down — easier on the way out.",
     "The summit is boulder-strewn; scramble the biggest boulder (~8–10 ft) to sign the register and get the Indian Peaks / Continental Divide / RMNP views to the west.",
     "Start by 7am: the lot is tiny and fills fast, and the west-side trails stay shaded through the morning (cooler for Mochi).",
     "Heads-up: poison ivy low in the canyon — stay on the trail. There are reports of an aggressive deer/elk on this trail; give wildlife a wide berth (75+ ft). Restrooms are at the trailhead.",
   ],
   lunch="Recommendation: don't haul a picnic up — it's a hot, rocky 2,400 ft climb. Carry light snacks + plenty of water for the summit, then do a real sit-down lunch in town once you're down (~1pm). If you'd rather picnic, buy supplies beforehand and eat in the shaded Chautauqua Meadow or along Boulder Creek after the hike — not at the exposed summit.",
   eat=[
     ("Chautauqua Dining Hall", "🥾 Come as you are (post-hike OK) — closest to the trailhead; 1898 lodge, Flatiron-view porch, dog-friendly. The post-hike lunch; reserve summer weekends.", pin(_q("900 Baseline Rd, Boulder, CO 80302"))),
     ("Dushanbe Teahouse", "🥾 Post-hike OK — dogs on the grapevine-shaded NORTH patio only (order at the bar); creekside Tajik teahouse, weekend brunch from 8am.", pin(_q("1770 13th St, Boulder, CO 80302"))),
     ("Postino Boulder", "🚿 Shower first (clean & casual) — Pearl St Mall wine bar, build-your-own bruschetta boards + happy hour, dog patio.", pin(_q("1468 Pearl St Ste 110, Boulder, CO 80302"))),
     ("River and Woods", "🚿 Shower first (clean & casual) — New American in a restored miner's cabin; leashed dogs in the leafy backyard.", pin(_q("2328 Pearl St, Boulder, CO 80302"))),
     ("Corrida", "👔 Dress up a bit + shower first — 4th-floor rooftop, Flatirons views, wood-fired Spanish steak; no dogs (rooftop). Book the terrace at golden hour. (Frasca is the bigger splurge — book ~a month out.)", pin(_q("1023 Walnut St Ste 400, Boulder, CO 80302"))),
   ],
   after=[
     ("Pearl Street Mall", "Boulder's pedestrian mall — street performers, shops, dog-friendly patios. Note: dogs aren't allowed on the brick mall itself, but the bordering patios welcome them.", pin(_q("Pearl Street Mall, Boulder, CO"))),
     ("East Boulder Dog Park", "Fenced off-leash park with a small pond Mochi can wade in — the reliable cool-down (the Reservoir swim beach restricts dogs in summer).", pin(_q("East Boulder Community Dog Park, Boulder, CO"))),
     ("Boulder Creek Path", "Flat, shaded creekside walk through town — an easy legs-recovery stroll, dog-friendly.", pin(_q("Boulder Creek Path, Boulder, CO"))),
     ("Flagstaff Mountain (sunset)", "Drive up Flagstaff Rd ~45 min before sunset for the best overlook in town, then back down to a Pearl St dinner. Dog-friendly overlooks.", pin(_q("Flagstaff Mountain Summit, Boulder, CO"))),
   ],
   q=[
     "Which evening anchor — a ticketed Colorado Music Festival night at Chautauqua (no dogs inside) or free Bands on the Bricks on Pearl St (Wednesdays only)? Pick the date that fits.",
     "Out-and-back on Ranger, or the full loop down E.M. Greenman → Saddle Rock? Decide at the summit based on how knees + Mochi are doing.",
   ]),
 dict(id="BLD-B", type="SEPARATE AM → TOGETHER PM", drive="~24 min · 8 mi round trip",
   oneliner="Separate AM: Ian runs Sanitas / Anny + Mochi valley walk, PM together",
   ctx="Same trailhead, two efforts. Regroup at the car, then an easy afternoon together.",
   ian="Mt Sanitas loop run (3.2 mi, 1,270 ft)", anny="Sanitas Valley Trail (2 mi, easy)",
   mochi="With Anny on the valley trail (same trailhead as Ian).", together="Lunch in town, then errands / Pearl St.",
   acts=["sanitas_run","sanitas_valley"], res="None.",
   route_stops=["Mt Sanitas Trailhead, Boulder, CO","Boulder Dushanbe Teahouse, Boulder, CO","Pearl Street Mall, Boulder, CO"],
   backup="Wonderland Lake loop (1.3 mi)", evening="Bands on the Bricks (Wed) / festival night",
   beta=[
     "Mt Sanitas loop: rated Hard, 4.7★ (2,800+) — short but steep, with rocky Class-2 footing on the ridge and a chunky-boulder summit push. ~3.2 mi as a summit out-and-back.",
     "Run it clockwise: up the rocky Mt Sanitas ridge, down the gentler Sanitas Valley Trail — saves the loose rock for fresh legs.",
     "Parking: the small Centennial/Sanitas lot on Mapleton fills by 8am; Mapleton street parking is the overflow. Arrive by 7:30.",
     "Fully exposed, west-facing ridge — start by 8am to beat the sun and the noon thunderstorm window.",
     "All on-leash (OSMP), no off-leash anywhere here. Mochi does the flat Sanitas Valley out-and-back from the same lot while Ian runs the loop; the ridge boulders aren't dog terrain.",
   ],
   lunch="Short in-town morning — you both finish at the same Mapleton trailhead, 5 min from Pearl St, so skip the picnic and head downtown. Dushanbe Teahouse (creek patio) for a late brunch, or Postino on Pearl. Pack a picnic only if you want to linger at the trailhead first.",
   eat=[
     ("Moe's Broadway Bagel", '🚵 Grab-and-go before the climb — hot bagels + breakfast sandwiches near the Sanitas/Chautauqua trailheads; order to go.', pin(_q('3267 28th St, Boulder, CO 80304'))),
     ('Santo', "🥾 Post-hike OK — Top Chef's Hosea Rosenberg; real Hatch green chile + blue-corn breakfast, dog patio. Close to N Boulder.", pin(_q('1265 Alpine Ave, Boulder, CO 80304'))),
     ('Dushanbe Teahouse', '🥾 Post-hike OK — dogs on the north patio only; creekside Tajik teahouse, weekend brunch from 8am.', pin(_q('1770 13th St, Boulder, CO 80302'))),
     ('Postino Boulder', '🚿 Shower first — Pearl St wine bar + bruschetta boards, dog patio. Easy dinner before Bands on the Bricks.', pin(_q('1468 Pearl St Ste 110, Boulder, CO 80302'))),
   ],
   after=[
     ("Pearl Street Mall", "Street performers, shops, patios. Dogs aren't allowed on the brick mall itself but the bordering patios welcome them.", pin(_q("Pearl Street Mall, Boulder, CO"))),
     ("Bands on the Bricks", "Free Wednesday concert on the Pearl St bricks (beer garden 5:30, headliner 7–9). Mochi OK on the mall, clear of the beer garden.", pin(_q("Pearl Street Mall, Boulder, CO"))),
     ("Boulder Creek Path", "Flat, shaded creekside walk — easy afternoon leg-stretch, dog-friendly.", pin(_q("Boulder Creek Path, Boulder, CO"))),
     ("Trident Booksellers & Cafe", "Anny's stop — employee-owned indie bookstore + coffee on Pearl, dogs welcome.", pin(_q("Trident Booksellers and Cafe, Boulder, CO"))),
   ],
   q=[
     "Does Ian want the short summit out-and-back (3.2 mi / 1,270 ft) or the full Sanitas loop (~5.3 mi)? Changes timing + the loop-direction advice.",
     "Bands on the Bricks runs Wednesdays only and ends ~Jul 29 — confirm this tab lands on a Wednesday, else the evening is a regular festival/dinner night.",
   ]),
 dict(id="BLD-C", type="BIG ALPINE DAY", drive="~2h · 61 mi round trip",
   oneliner="Big alpine day: Indian Peaks — Lake Isabelle + Blue Lake",
   ctx="The best alpine hiking near Boulder. Start very early — Brainard parking needs a timed entry.",
   ian="6–8 mi alpine lakes + waterfalls", anny="Same hike", mochi="Comes along, leashed.",
   together="One big objective for the whole crew.", acts=["lake_isabelle"],
   route_stops=["Long Lake Trailhead, Ward, CO","Crosscut Pizzeria and Taphouse, Nederland, CO","Carousel of Happiness, Nederland, CO"],
   res="Brainard Lake timed entry — recreation.gov, 15-day rolling (opens ~Jul 16).",
   backup="Golden Gate Canyon SP — Mountain Lion Trail (no reservation)", evening="Easy night in",
   beta=[
     "Late July is peak alpine wildflower bloom in the Long Lake basin (paintbrush, columbine, aster) — prime timing for this window.",
     "Brainard timed entry sells out: book the recreation.gov slot at the 15-day rolling mark (~8am MT). $16/vehicle + $2 reservation; download the QR confirmation before leaving — there's near-zero cell coverage inside.",
     "Long Lake TH (for Isabelle) and Mitchell Lake TH (for Blue Lake) are two separate lots ~0.5 mi apart needing two reservations — simplest is to park one and walk the paved connector. Free Gateway TH (no reservation) is 2 mi outside the gate as overflow.",
     "Starts at 10,500 ft and you feel it. Isabelle is 4–5 mi easy–moderate; Blue Lake via Mitchell is 5.7 mi / ~977 ft. Both = a solid 8–10 mi day. Snow can linger at Blue Lake into late July.",
     "Afternoon storms build by 1–2pm — hike by 7–8am, be back below treeline by early afternoon.",
     "Moose frequent the lakeshores and bogs (signed) — keep Mochi leashed (required) and give moose wide berth. Bear + lion also present.",
   ],
   lunch="A lake at 10,500 ft strongly favors a packed picnic — you'll be miles from food. Pack sandwiches, snacks, and plenty of water + a collapsible bowl for Mochi, and eat at the lake. For a town stop on the way down, Nederland (~40 min) is the gateway: Crosscut Pizzeria's dog patio is the reliable post-hike option.",
   eat=[
     ('Crosscut Pizzeria & Taphouse', '🚵 Come as you are — Nederland wood-fired pizza + taps on the drive down, dog patio. (Often opens 3pm midweek — confirm.)', pin(_q('4 E 1st St, Nederland, CO 80466'))),
     ('Salto Coffee Works', '🚵 Come as you are — beloved Nederland roaster-café, dog patio; coffee + pastry on the way out or back.', pin(_q('112 E 2nd St, Nederland, CO 80466'))),
     ('Dushanbe Teahouse', '🥾 Post-hike OK — back in Boulder, north dog patio; a relaxed late dinner after the alpine day.', pin(_q('1770 13th St, Boulder, CO 80302'))),
     ('River and Woods', '🚿 Shower first — New American comfort food, leafy dog backyard; a calmer dinner for a tired crew.', pin(_q('2328 Pearl St, Boulder, CO 80302'))),
   ],
   after=[
     ("Carousel of Happiness", "Nederland's hand-carved 1910 carousel ($3/ride). Note: no pets inside — one person waits outside with Mochi.", pin(_q("Carousel of Happiness, Nederland, CO"))),
     ("Barker Reservoir / Nederland walk", "Quirky mountain town + lakeside path — a 20-min leg-stretch with Mochi before the drive back.", pin(_q("Barker Reservoir, Nederland, CO"))),
     ("Flagstaff Mountain (sunset)", "On the Flagstaff Rd return into Boulder — Sunrise Amphitheater overlook for a west-facing sunset. Dogs on leash.", pin(_q("Sunrise Amphitheater, Boulder, CO"))),
   ],
   q=[
     "Book both Long Lake + Mitchell Lake reservations, or park one lot and walk the connector? Set a recreation.gov reminder for 15 days before this date (slots open ~8am MT).",
     "Have an America the Beautiful pass? It drops Brainard entry to just the $2 reservation fee.",
     "Heavy-snow year: Blue Lake's upper bowl can hold snow into late July — check Boulder Ranger District conditions ~1 week out.",
   ]),
 dict(id="BLD-D", type="DAY TRIP", drive="~2h30 · 95 mi round trip",
   oneliner="Day trip: RMNP — Bear Lake → Dream Lake + Trail Ridge Road",
   ctx="The one option where Mochi can't really come — RMNP restricts dogs to parking lots. Plan daycare or A/C-van day.",
   ian="Bear Lake shuttle + 2.2 mi hike; drive Trail Ridge Rd (12,183 ft)", anny="Same",
   mochi="RESTRICTED in RMNP (lots only) — Airbnb (A/C after Jul 30) or daycare.",
   together="Scenic NP day; Nederland on the way back.", acts=[],
   route_stops=["Bear Lake Trailhead, Rocky Mountain National Park, CO","Rock Cut Brewing Company, Estes Park, CO","Lake Estes Trail, Estes Park, CO"],
   res="Arrive before 9am; check timed entry at nps.gov/romo.",
   backup="Nederland day (Carousel of Happiness, town, coffee)", evening="Back late — easy dinner",
   beta=[
     "DOG BAN — the dealbreaker: RMNP prohibits dogs on ALL trails, tundra, and meadows. Mochi may only be in parking lots, on paved roads, and in campgrounds. You can't carry her past that. This is a daycare day or a dog-at-Airbnb day (and A/C is only available after Jul 30).",
     "Timed-entry permit: you need the 'Timed Entry + Bear Lake Road' type ($2, recreation.gov). July permits release June 1 at 8am MDT and sell out fast; a sliver re-releases 7pm MDT the night before.",
     "Bear Lake → Dream Lake is 2.2 mi / ~500 ft (extend to Emerald Lake = ~4 mi). Lots fill by 7–8am even WITH a reservation — the free park shuttle is the backup, but it doesn't allow pets.",
     "Trail Ridge Road tops out at 12,183 ft (summit 35–45°F even when Estes is 80s; bring layers). Crest the divide by noon — afternoon storms above treeline are the rule.",
     "Lily Lake (just outside the park on Hwy 7) ALSO bans dogs — it connects to the park trail network. Estes Park town, however, is dog-friendly (patios, Lake Estes Trail).",
   ],
   lunch="No real dining inside the park (Alpine Visitor Center has only a snack bar). Pack a full lunch from Boulder and eat at a Trail Ridge pull-out or Bear Lake picnic area. Save a proper meal for Estes Park on the way home — dogs are welcome on Estes patios, so it's the re-entry reward.",
   eat=[
     ('Rock Cut Brewing Co.', '🚵 Come as you are — Estes Park riverside dog patio + food trucks; casual post-park beer + bite.', pin(_q('390 W Riverside Dr, Estes Park, CO 80517'))),
     ('The Barrel', '🚵 Come as you are — Estes open-air beer garden, self-pour + food trucks, dog-friendly; Mochi settles while you decompress.', pin(_q('251 Moraine Ave, Estes Park, CO 80517'))),
     ('Bird & Jim', '🚿 Shower first — polished New American, mountain-view dog patio; the nicer Estes dinner. Reserve.', pin(_q('915 Moraine Ave, Estes Park, CO 80517'))),
     ('Rock Inn Mountain Tavern', '🚿 Shower first — 1937 log roadhouse, buffalo burgers + Divide views, covered dog patio (CO-66, south side).', pin(_q('1675 CO-66, Estes Park, CO 80517'))),
   ],
   after=[
     ("Lake Estes Trail", "Fully dog-friendly paved 3.75-mi loop around Lake Estes (county, not NPS) — mellow walk for Mochi after a big day.", pin(_q("Lake Estes Trail, Estes Park, CO"))),
     ("Downtown Estes Park (Elkhorn Ave)", "Dogs welcome on the sidewalks — galleries, fudge, outfitters. Easy wind-down before the drive home.", pin(_q("Elkhorn Avenue, Estes Park, CO"))),
     ("Nederland (return route)", "On the Peak-to-Peak drive home — Carousel of Happiness + Salto Coffee break up the drive (dog waits outside the carousel).", pin(_q("Carousel of Happiness, Nederland, CO"))),
   ],
   q=[
     "DECIDE: RMNP bans dogs on every trail, and the Airbnb has no A/C until Jul 30 — keep BLD-D (Ian solo or both with Mochi at Estes daycare), swap to the all-dog-friendly Nederland day, or drop it? It's flagged DROPPED in the Itinerary.",
     "If keeping it: July permits release June 1, 8am MDT (recreation.gov) and sell out same-day — want a reminder set?",
     "If Mochi stays home, this only works after Jul 30 (A/C). Before that, Estes Park daycare (Elena's Barking Lot / Estes Park Pet Lodge) is the safer call — which date are you eyeing?",
   ]),
 dict(id="BLD-E", type="SEPARATE DAY", drive="~47 min · 20 mi round trip",
   oneliner="Separate: Ian Valmont bike park / Anny + Mochi foothills, lunch + festival",
   ctx="Split the morning (one car — drop Ian, Anny keeps it), regroup for lunch, festival at night.",
   ian="Valmont Bike Park (free, in town)", anny="Wonderland Lake + Foothills Trail",
   mochi="With Anny all morning (leashed); home at the Airbnb for the evening concert.",
   together="Lunch at Avery Brewing (dog patio) ~12pm.", acts=["valmont","wonderland"],
   route_stops=["Valmont Bike Park, Boulder, CO","Wonderland Lake Trailhead, Boulder, CO","Avery Brewing Company, Boulder, CO"],
   res="None (both free).", backup="Valmont wet → Ian rope-climbs at Movement; Anny shortens to Wonderland loop.",
   evening="Colorado Music Festival @ Chautauqua (no pets)",
   beta=[
     "Valmont Bike Park (3160 Airport Rd, NE Boulder): free 42-acre city park, dawn–dusk, free parking. Best for skills — 4 pump tracks (1 paved + 3 dirt), graded dirt-jump lines (S→XL), a dual-slalom course, and green→black flow.",
     "Strict wet-closure: the park closes whenever trails are muddy, which can happen overnight. Check the morning of — facebook.com/ValmontBikePark posts same-day status.",
     "Bring a helmet + pads; a hardtail or dirt-jump bike suits the jumps/slalom better than a big trail bike.",
     "Anny + Mochi: Wonderland Lake TH (4201 N Broadway) — flat 1.3–2.5 mi loop, on-leash protected area, reservoir + foothills views, prairie-dog/bird watching. Small lot fills by ~8:30; morning bluff shade.",
   ],
   lunch="Regroup at Avery Brewing (4910 Nautilus Ct, ~10 min from both) — a huge dog-friendly beer garden, 30 taps, full lunch menu, opens 11:30 (CLOSED Mondays). Get there near open for shaded seats. If it's a Monday, Rayback Collective (food-truck yard, dogs, 5 min from Valmont) is the pivot.",
   eat=[
     ('Avery Brewing Co.', '🚵 Come as you are — the planned regroup; huge dog patio + full restaurant menu, 30+ taps. Opens 11:30, CLOSED Mon.', pin(_q('4910 Nautilus Ct N, Boulder, CO 80301'))),
     ('The Rayback Collective', '🚵 Come as you are — food-truck park with a dedicated pup zone, 5 min from Valmont. The Monday-Avery bail option.', pin(_q('2775 Valmont Rd, Boulder, CO 80304'))),
     ('Postino Boulder', '🚿 Shower first — Pearl St wine bar + boards, dog patio; late-afternoon stop before the concert.', pin(_q('1468 Pearl St Ste 110, Boulder, CO 80302'))),
     ('River and Woods', "🚿 Shower first — New American, dog backyard; dinner for Anny + Mochi while Ian's at the concert.", pin(_q('2328 Pearl St, Boulder, CO 80302'))),
   ],
   after=[
     ("Colorado Music Festival @ Chautauqua", "Ian solo — no pets in the Auditorium. 2026 season runs Jul 9–Aug 9; book ahead, seats from ~$18; free Hop shuttle from downtown.", pin(_q("Chautauqua Auditorium, Boulder, CO"))),
     ("Valmont Dog Park", "Fenced off-leash park next to the bike park — Mochi runs while Ian gets extra laps.", pin(_q("Valmont Dog Park, Boulder, CO"))),
     ("Wonderland Lake (evening loop)", "Short enough to repeat at golden hour — west-facing Flatiron light while Ian's at the concert.", pin(_q("Wonderland Lake Trailhead, Boulder, CO"))),
   ],
   q=[
     "Confirm this isn't a Monday — Avery is closed Mondays (pivot lunch to Rayback Collective).",
     "Is Ian bringing his own bike, or renting? A skills/jump park isn't ideal on a rental (University Cycles / Mike's Bikes rent dirt-jump/trail bikes).",
     "Which CMF concert + date is Ian targeting (Jul 9–Aug 9 season)? And does Anny want a solo dog dinner or a sunset hike + Airbnb takeout that evening?",
   ]),
 dict(id="BLD-F", type="TOGETHER DAY", drive="~55 min · 23 mi round trip",
   oneliner="Together: Eldorado Canyon or Mesa Trail",
   ctx="Stunning canyon + climbers (Eldorado, timed entry) or the no-reservation Mesa Trail.",
   ian="Eldorado Canyon (6.7 mi) or Mesa Trail (7 mi)", anny="Same",
   mochi="Comes along, leashed.", together="Pick by whether you snagged the Eldorado entry.",
   acts=["eldorado","mesa_trail"], res="Eldorado timed entry $10 (cpw.state.co.us) — or skip to Mesa Trail.",
   route_stops=["Eldorado Canyon State Park, Eldorado Springs, CO","Southern Sun Pub and Brewery, Boulder, CO","Pearl Street Mall, Boulder, CO"],
   backup="Mesa Trail from Chautauqua (no reservation)", evening="Festival night",
   beta=[
     "Eldorado Canyon SP: timed-entry reservation required on summer weekends/holidays (FREE to reserve, up to 30 days ahead at cpwshop.com, 2-hr arrival window) PLUS a $10/vehicle gate fee. Near-zero cell service — screenshot the confirmation.",
     "Park fills 10am–2pm on weekends and turns cars away — reserve the earliest slot, or ride the FREE weekend Eldo Shuttle from Boulder (skips the timed-entry requirement entirely).",
     "Eldorado Canyon Trail: ~6.8 mi moderate along South Boulder Creek; partly shaded by the creek, exposed up high. Watch for rattlesnakes on warm rocks in July. The shorter Rattlesnake Gulch (3 mi) climbs to the Crags Hotel ruins + divide views; pair with the Fowler Trail to watch climbers on the Bastille.",
     "Mesa Trail (the no-reservation alt): ~7 mi rolling from South Mesa or Chautauqua, mix of shade + exposed ridge. More water than you think in July; tick-check at the car.",
     "Leashed dogs throughout (enforced). Eldorado lots close 6pm nightly for utility work (out by 5:30 if you drove in).",
   ],
   lunch="For Eldorado, pack it and picnic creekside on the flat rocks along South Boulder Creek — shade, water for Mochi, no re-exiting the gate. If you do Mesa Trail instead, you finish at Chautauqua and the Dining Hall is a 3-min walk. Fallback either way: a Whole Foods/Pearl deli run 15 min out.",
   eat=[
     ('Southern Sun Pub & Brewery', '🚵 Come as you are — S Boulder brewpub closest to the canyon trailheads, lounge + patio (cash/check only; confirm dog patio).', pin(_q('627 S Broadway, Boulder, CO 80305'))),
     ('Chautauqua Dining Hall', '🥾 Post-hike OK — at the Mesa Trail finish; Flatiron-view porch, dog-friendly. Best if you pick Mesa Trail.', pin(_q('900 Baseline Rd, Boulder, CO 80302'))),
     ('Postino Boulder', '🚿 Shower first — Pearl St wine bar, dog patio; easy downtown dinner.', pin(_q('1468 Pearl St Ste 110, Boulder, CO 80302'))),
     ('Corrida', '👔 Dress up + shower first — rooftop Flatirons views, wood-fired steak; no dogs. A celebratory together-day dinner.', pin(_q('1023 Walnut St Ste 400, Boulder, CO 80302'))),
   ],
   after=[
     ("South Boulder Creek (in-park)", "Lower creek near the entrance has flat, shaded access for Mochi to splash and cool off — free if still in your park window.", pin(_q("Eldorado Canyon State Park, Eldorado Springs, CO"))),
     ("South Pearl Street", "Walkable neighborhood strip — coffee, Sweet Cow ice cream, a dog-loving crowd. Pre-dinner stroll.", pin(_q("South Pearl Street, Boulder, CO"))),
     ("Mountain Sun Pub", "Pearl St institution, house beers, dog-friendly front patio — connects to a Pearl festival evening.", pin(_q("Mountain Sun Pub and Brewery, Boulder, CO"))),
   ],
   q=[
     "Pre-book the Eldorado timed entry (free, cpwshop.com, weekends need it) or treat this as a weekday and default to Mesa Trail? Or take the free Eldo Shuttle and skip the reservation?",
     "Sprinter note: Eldorado lots close 6pm for utility work — be out by 5:30 if driving in.",
     "If the evening is a Chautauqua concert, the Mesa Trail option is cleaner (start + finish at Chautauqua, walk to the auditorium).",
   ]),
 dict(id="BLD-G", type="TOWN / REST DAY", drive="~26 min · 9 mi round trip",
   oneliner="Town day: brunch, Pearl St, climbing gym, breweries",
   ctx="A rest-the-legs day. Indoor climbing for Ian, town for Anny, regroup for breweries.",
   ian="Rope climbing at Movement or BRC", anny="Brunch + neighborhoods + Trident Booksellers",
   mochi="Airbnb (A/C after Jul 30) or a fenced dog park (the Reservoir swim beach restricts dogs in summer).", together="Pearl St + breweries PM.",
   acts=[], res="None.", backup="Boulder Creek Path walk", evening="Pearl St dinner / festival",
   route_stops=["Movement Boulder, Boulder, CO","Boulder Dushanbe Teahouse, Boulder, CO","Pearl Street Mall, Boulder, CO"],
   beta=[
     "Ian's climbing: Movement Boulder (2845 Valmont Rd) $29 day pass, rentals ~$11, 30–40 ft walls incl. the Grey Wall — or BRC/Boulder Rock Club (2829 Mapleton) $29, smaller/local feel with a partner-finder board. Quietest weekday mornings.",
     "Mochi heads-up: Boulder Reservoir bans dogs at the swim beach mid-May–Labor Day (the whole trip). Use Valmont Dog Park (fenced, 3 acres, 10 min from the Airbnb) or East Boulder Dog Park (fenced, has a wadeable pond) instead.",
     "Pearl Street Mall: dogs aren't allowed on the brick mall itself (enforced), but the bordering restaurant patios are dog-welcoming — plan patio seating vs. strolling separately.",
     "Boulder Farmers Market (Central Park, 13th & Canyon): Sat 8am–2pm / Wed 3:30–7:30pm, no pets — Ian/Anny can swing solo while Mochi's home.",
     "Boulder is a beer town — Avery (huge dog beer garden), Upslope at Flatiron Park (dog patio), and Mountain Sun (Pearl, dog front patio) are the standouts.",
   ],
   lunch="It's the town day — make brunch the anchor. Dushanbe Teahouse takes walk-ins for brunch (creek patio, dogs welcome) — arrive by 9:30 to beat the line; or Chautauqua Dining Hall for the Flatiron-view porch. Split it: Ian climbs while Anny + Mochi grab a patio table.",
   eat=[
     ('Dushanbe Teahouse', '🥾 Come as you are — walk-in creekside brunch, north dog patio; the rest-day anchor (arrive ~9:30).', pin(_q('1770 13th St, Boulder, CO 80302'))),
     ('Avery Brewing Co.', '🚵 Come as you are — huge dog patio + 30 taps, full menu; the PM brewery stop (closed Mon).', pin(_q('4910 Nautilus Ct N, Boulder, CO 80301'))),
     ('Upslope Brewing — Flatiron Park', '🚵 Come as you are — production taproom, food trucks + live music, dog patio. Open to ~9pm.', pin(_q('1898 S Flatiron Ct, Boulder, CO 80301'))),
     ('Frasca Food and Wine', "👔 Dress up + shower first — Boulder's destination dinner (James Beard + Michelin); indoor, no dogs. Book ~a month out.", pin(_q('1738 Pearl St, Boulder, CO 80302'))),
   ],
   after=[
     ("Pearl Street Mall", "Street performers + shops; dogs on the bordering patios, not the bricks. Wednesdays add free Bands on the Bricks.", pin(_q("Pearl Street Mall, Boulder, CO"))),
     ("Trident Booksellers & Cafe", "Anny's stop — employee-owned indie bookstore + specialty coffee on Pearl, dogs welcome inside.", pin(_q("Trident Booksellers and Cafe, Boulder, CO"))),
     ("Valmont Dog Park", "Best Mochi option while the Reservoir's off-limits — fenced 3-acre off-leash, 10 min from the Airbnb.", pin(_q("Valmont Dog Park, Boulder, CO"))),
     ("Boulder Creek Path", "Flat, shaded creekside path through downtown — easy afternoon leg-stretch.", pin(_q("Boulder Creek Path, Boulder, CO"))),
   ],
   q=[
     "Which gym for Ian — Movement Boulder (bigger, the Grey Wall) or BRC (classic local feel)? Both $29.",
     "Confirm replacing 'Boulder Reservoir dog beach' with Valmont or East Boulder Dog Park as Mochi's solo-time spot (Reservoir bans dogs mid-May–Labor Day).",
     "If this lands on a Sat or Wed, the Farmers Market is on (no dogs) — stagger it with Mochi at the Airbnb.",
   ]),
 dict(id="BLD-H", type="DAY TRIP", drive="~1h25 · 47 mi round trip",
   oneliner="Day trip: Golden — Coors tour, Clear Creek, North Table hike",
   ctx="The most dog-friendly day trip. Brewery, creek tubing, a foothills hike — all leashed-OK.",
   ian="North Table Mtn (5.9 mi) + tubing + brewery", anny="Same",
   mochi="Comes — most dog-friendly day trip.", together="Golden town day; pairs with a Red Rocks show (Jul 30).",
   acts=[], res="None.", backup="Betasso Preserve or Flagstaff Mountain",
   route_stops=["North Table Mountain Trailhead, Golden, CO","The Golden Mill, Golden, CO","Clear Creek Whitewater Park, Golden, CO"],
   evening="Red Rocks: Killer Queen (Jul 30 only) — redrocksonline.com",
   beta=[
     "North Table Mountain loop (W 53rd St TH): ~5.9 mi moderate mesa loop, leashed, NO shade/water — bring 2–3× water + a bowl for Mochi, paws can burn on basalt. Informal roadside parking fills by 8am. The Rim Rock connector is under a raptor closure Feb 1–Jul 31 — if it's still closed, you do the mesa-top loop only.",
     "Clear Creek runs through downtown Golden — leashed dog path, no alcohol. By late August flows are low: wading, not high tubing season. Check the daily flag at visitgolden.com (green/yellow/red).",
     "Coors tour: NO dogs inside (incl. service animals), Thu–Mon, $20, reservations release 25 days out. Skip it as a group — do a dog-friendly Golden brewery instead.",
     "Heat: Golden hits 90–95°F midday in late July with zero shade on the mesa. Start by 7–7:30am, off the mesa by 10. Don't leave Mochi in the van.",
     "Red Rocks (Jul 30, Killer Queen tribute): no dogs at concerts. Doors 7pm/show 8pm, ~50 min from Golden; the Sprinter is oversized → Lower South Lot 2. Leave Golden by 4:30 to drop Mochi in Boulder first.",
   ],
   lunch="Skip a pre-hike sit-down — North Table has no bailout and you'll be hot coming off it. Pack food + water in the van, then post-hike head into downtown Golden: The Golden Mill (food hall, dogs welcome in the outdoor yard, 5 stalls + 50 self-pour taps) suits a crew with different appetites. Fallback: Windy Saddle Cafe patio (call ahead re: dogs).",
   eat=[
     ('The Golden Mill', '🚵 Come as you are — food hall (5 kitchens + 50 self-pour taps), leashed dogs in the ground-floor yard. Best post-hike group pick.', pin(_q('1012 Ford St, Golden, CO 80401'))),
     ('New Terrain Brewing', '🚵 Come as you are — beer garden with an ADJACENT off-leash dog park + food trucks at the base of North Table Mtn.', pin(_q('16401 Table Mountain Pkwy, Golden, CO 80403'))),
     ('Windy Saddle Cafe', '🥾 Post-hike OK — downtown cyclist favorite, in-house bakery + scratch breakfast/lunch, dog patio (opens 7am).', pin(_q('1110 Washington Ave Ste 100, Golden, CO 80401'))),
     ('The Eddy Taproom & Hotel', '🚿 Shower first — polished creekside New American, dogs at the bar + patio; nicer than the brewery patios.', pin(_q('1640 8th St, Golden, CO 80401'))),
   ],
   after=[
     ("Clear Creek path & Whitewater Park", "Shaded creek path from downtown to the whitewater park — Mochi wades the shallows (leashed). The crew's cool-down in the shade.", pin(_q("Clear Creek Whitewater Park, Golden, CO"))),
     ("Downtown Golden / Welcome Arch", "Washington Ave — dog-friendly sidewalks, ice cream, the classic arch photo. Flat leg-stretch.", pin(_q("Washington Avenue, Golden, CO"))),
     ("Red Rocks Amphitheatre (Jul 30)", "If it's the 30th: drop Mochi in Boulder, then Sprinter → Lower South Lot 2. Doors 7/show 8. Killer Queen on AXS — book early.", pin(_q("Red Rocks Amphitheatre, Morrison, CO"))),
   ],
   q=[
     "Is this day worth it only on Jul 30 (Red Rocks)? Golden's a fine standalone half-day, but the concert is what makes the 30th compelling.",
     "Rim Rock Trail closure ends Jul 31 — on Jul 30 the full loop's connector may be closed (mesa-top only). Check jeffco.us/open-space; swap to cooler Betasso if needed.",
     "Where does Mochi go during Red Rocks? Plan the ~30–35 min Golden→Boulder drop before the 25 min Boulder→Morrison drive.",
   ]),
 dict(id="BLD-I", type="SEPARATE DAY", drive="~1h35 · 38 mi round trip",
   oneliner="Separate: Ian Walker Ranch ride/run / Anny + Mochi Flatirons Vista",
   ctx="Ian's marquee technical ride (or run). Anny drops him at the trailhead and hikes a nearby dog loop.",
   ian="Walker Ranch Loop (7.8 mi) — MTB or trail run", anny="Flatirons Vista / Doudy Draw (3.4 mi)",
   mochi="With Anny (Flatirons Vista is dog-friendly).", together="Regroup after Ian's loop; easy PM.",
   acts=["walker_mtb","flatirons_vista"], res="None — Anny drops Ian at the trailhead.",
   route_stops=["Walker Ranch Trailhead, Boulder, CO","Doudy Draw Trailhead, Boulder, CO","Chautauqua Dining Hall, Boulder, CO"],
   backup="Gross Reservoir hike (30 min, no reservation)", evening="Festival night",
   beta=[
     "Walker Ranch Loop (MTB): black/advanced, 7.8 mi / ~1,510 ft. Ride CLOCKWISE for the best descents and to take the famous rocky-stair section (a steep stone staircase to South Boulder Creek) as a descent — many walk it. The big payoff is the ~2-mi drop to the creek. Lot (46 spots) fills by 9am; Meyers Gulch TH 1.1 mi away is overflow.",
     "Trail-run angle: runs beautifully counter-clockwise — sustained descent off the top, creek, then a long switchbacked climb; ~50% black-rated footing. A legit hard solo effort.",
     "Anny + Mochi: Flatirons Vista / Doudy Draw (~3.4 mi, ~500 ft) — open, exposed mesa, sweeping Flatiron views, little shade. Start by 7–9am for the heat; bring water + a bowl. (Flatirons Vista TH was under renovation in spring 2026 — confirm reopened; Doudy Draw TH is unaffected.)",
     "Meyers Homestead alt (same Flagstaff corridor): from Meyers Gulch TH (1.1 mi from Walker Ranch) — 2.5 mi out-and-back, shadier + cooler, elk likely. Lets Anny stay near Ian's lot.",
   ],
   lunch="Separate starts (Ian on Flagstaff, Anny near Eldorado Springs) → a town regroup once Ian finishes (1.5–2.5 hr). Chautauqua Dining Hall is the natural reunion at the base of Flagstaff Rd on the way in (porch, Flatiron views). Fallback: Southern Sun for post-effort beer + food.",
   eat=[
     ('Chautauqua Dining Hall', '🥾 Post-hike OK — the regroup at the base of Flagstaff Rd; Flatiron-view porch, dog-friendly. Book weekends.', pin(_q('900 Baseline Rd, Boulder, CO 80302'))),
     ('Southern Sun Pub & Brewery', '🚵 Come as you are — S Boulder brewpub, dog patio (confirm) + house ales; post-effort recovery (cash/check only).', pin(_q('627 S Broadway, Boulder, CO 80305'))),
     ('Postino Boulder', '🚿 Shower first — Pearl St wine bar + boards, dog patio.', pin(_q('1468 Pearl St Ste 110, Boulder, CO 80302'))),
     ('River and Woods', '🚿 Shower first — New American, dog backyard; a nicer dinner after a hard morning.', pin(_q('2328 Pearl St, Boulder, CO 80302'))),
   ],
   after=[
     ("Flagstaff Mountain (sunset)", "Literally the road to Ian's trailhead — stop at the summit/Panorama Point for the best sunset over Boulder. No hiking.", pin(_q("Flagstaff Mountain Summit, Boulder, CO"))),
     ("Pearl Street Mall", "Evening street performers + ice cream; dogs on the bordering patios, not the bricks. Wednesdays = Bands on the Bricks.", pin(_q("Pearl Street Mall, Boulder, CO"))),
     ("East Boulder Dog Park", "Fenced off-leash with a wadeable pond to cool Mochi after her exposed mesa hike.", pin(_q("East Boulder Community Dog Park, Boulder, CO"))),
   ],
   q=[
     "MTB or trail run for Ian? Clockwise MTB = Boulder's best descent; counter-clockwise run = harder fitness effort + creek soak.",
     "Anny: stay in the Flagstaff corridor (Meyers Homestead — shady, near Ian's lot) or the open Flatirons Vista / Doudy Draw (better views, hotter)?",
     "Confirm Flatirons Vista TH reopened after its spring-2026 renovation (Doudy Draw TH works regardless).",
   ]),
 dict(id="BLD-J", type="EASY / RECOVERY DAY", drive="~43 min · 16 mi round trip",
   oneliner="Easy day: Reservoir dog beach + Chautauqua meadow + downtown",
   ctx="Mochi's day. Off-leash swim at the Reservoir, easy meadow walk, slow downtown afternoon.",
   ian="Easy / recovery", anny="Easy / recovery",
   mochi="Mochi's day — a pond wade + easy meadow walk (see the Reservoir caveat below).", together="Dog park + Chautauqua meadow + downtown.",
   acts=["chautauqua"], res="None.", backup="Flatirons Vista easy loop", evening="Festival night / quiet",
   route_stops=["Chautauqua Park, Boulder, CO","East Boulder Community Dog Park, Boulder, CO","Pearl Street Mall, Boulder, CO"],
   beta=[
     "Reservoir caveat: Boulder Reservoir's main swim beach RESTRICTS dogs mid-May–Labor Day (the whole trip), so the 'off-leash dog beach swim' isn't reliable this window. Best reliable off-leash water: East Boulder Dog Park (fenced, with a pond Mochi can wade), or Coot Lake — but Coot is leash-required seasonally, so verify current rules first.",
     "Chautauqua Meadow walk: 1–2 mi, ~200 ft, easy, on-leash (OSMP), best Flatiron-base scenery in town — perfect recovery-day legs.",
     "Pearl Street Mall: dogs aren't allowed on the brick mall but the bordering patios welcome them — plan patio sitting vs. strolling.",
     "Heat: it's a recovery day — keep Mochi's exertion to early/late and shaded; carry water everywhere.",
   ],
   lunch="A slow town day — make it a sit-down brunch/lunch. Chautauqua Dining Hall (Flatiron-view porch, dog patio) pairs perfectly with the meadow walk, or Dushanbe Teahouse on the creek. No need to picnic unless you want the meadow.",
   eat=[
     ('Dushanbe Teahouse', '🥾 Come as you are — walk-in creekside brunch, north dog patio; pairs with the Chautauqua meadow walk.', pin(_q('1770 13th St, Boulder, CO 80302'))),
     ('Chautauqua Dining Hall', '🥾 Post-hike OK — Flatiron-view porch, dog-friendly; the meadow-walk lunch.', pin(_q('900 Baseline Rd, Boulder, CO 80302'))),
     ('Postino Boulder', '🚿 Shower first — Pearl St wine bar, dog patio; low-effort early dinner.', pin(_q('1468 Pearl St Ste 110, Boulder, CO 80302'))),
     ('The Rayback Collective', '🚵 Come as you are — food-truck park with a pup zone; a mellow dog-centric afternoon hang.', pin(_q('2775 Valmont Rd, Boulder, CO 80304'))),
   ],
   after=[
     ("East Boulder Dog Park", "Fenced off-leash park with a pond Mochi can wade — the reliable swim swap for the Reservoir.", pin(_q("East Boulder Community Dog Park, Boulder, CO"))),
     ("Pearl Street Mall", "Slow downtown afternoon — street performers, shops; dogs on the bordering patios.", pin(_q("Pearl Street Mall, Boulder, CO"))),
     ("Boulder Creek Path", "Flat, shaded creekside stroll — gentlest recovery walk in town, dog-friendly.", pin(_q("Boulder Creek Path, Boulder, CO"))),
   ],
   q=[
     "BLD-J was built around a Boulder Reservoir off-leash swim, but the swim beach bans dogs mid-May–Labor Day. OK to swap to East Boulder Dog Park's pond? (Or I can check Coot Lake's current dog hours.)",
   ]),

 # ── MAMMOTH (Aug 15–17) — Ian + Mochi only (Anny away at the bach party). ──────────
 # Mochi rule: anything ≤4 hr → Mochi waits in the A/C van (after Jul 30 the van has A/C +
 # Starlink); a full bike-park day / big ride / long drive → dog daycare.
 dict(id="MAM-A", type="BIKE PARK", drive="~10 min (in town)", hub="MAM", bike=True,
   oneliner="Lift-served laps at Mammoth Mountain Bike Park — Mochi to daycare",
   ctx="The marquee day: 80+ mi of lift-served gravity off the Panorama Gondola (Kamikaze, Off the Top). It's a full day and dogs aren't allowed at the park, so Mochi goes to daycare. Ikon Pass = 2 free bike-park days (confirm your tier).",
   ian="Drop Mochi at daycare 7:30–8am → full day at Mammoth Bike Park (Panorama Gondola, ~3,000 ft lift descent). Kamikaze + Off the Top are the classics; full-sus enduro/DH bike. Pick Mochi up 4–4:30pm. See Activities (Eastern Sierra MTB).",
   mochi="DAYCARE (full day >4 hr, no dogs at the park): PUP Hiking Co — drop 7:30–8, pickup 4–4:30, (760) 582-2176 — BOOK AHEAD. Backups: Sierra Dog Ventures (714) 609-8510; Donna the Dog Lady (760) 387-2331.",
   acts=[], res="Bike-park ticket (Ikon 2 free days, else ~$65–80); reserve PUP Hiking daycare 6–8 wks ahead.",
   backup="Park closed/wet → ride Lower Rock Creek (MAM-B) or a town-trail loop (Uptown/Downtown) instead.",
   evening="Mammoth Brewing Co. beer garden or Distant Brewing (dogs inside) once Mochi's back."),
 dict(id="MAM-B", type="BIG RIDE", drive="~1h10 · ~50 mi round trip (Tom's Place)", hub="MAM", bike=True,
   oneliner="Lower Rock Creek tech descent (or another ES backcountry ride) — Mochi to daycare",
   ctx="The Eastern Sierra's classic ~8 mi tech singletrack descent through an aspen canyon, ~35 min S at Tom's Place (US-395). Self-shuttle on the parallel road. A full day with the drive, so Mochi goes to daycare.",
   ian="Drop Mochi at daycare → Lower Rock Creek: ~7.7 mi, ~1,900 ft descent, rocky/techy lower gorge. Self-shuttle the road or lap the upper aspens. See Activities (Eastern Sierra MTB).",
   mochi="DAYCARE (full day + drive >4 hr): PUP Hiking Co (760) 582-2176 / Sierra Dog Ventures (714) 609-8510. Backup: Donna the Dog Lady, Round Valley ~50 min, (760) 387-2331.",
   acts=[], res="Reserve daycare ahead. No trail fee.",
   backup="Stay close instead: Mammoth Rock / Sherwin ridge + town loops — short enough that Mochi waits in the A/C van (<4 hr) between laps.",
   evening="Dinner in town — Roberto's (Mexican) or Mammoth Tavern."),
 dict(id="MAM-C", type="DOG DAY", drive="~30 min · varies", hub="MAM", bike=False,
   oneliner="Easy acclimation + dog day: Convict Lake, Hot Creek, Lakes Basin — Mochi comes",
   ctx="A lighter, dog-friendly day to acclimatize (town 7,880 ft, rides top out ~11,000). Every stop is short and dog-OK, so Mochi rides along in the A/C van and joins — no daycare needed.",
   ian="Mammoth Rock / Sherwin Ridge AM warm-up (~4 mi). PM: Convict Lake loop (2–3 mi, flat, Mochi swims) + Hot Creek Geological Site (thermal vents, leashed overlook). Optional easy spin on the Lakes Basin path.",
   mochi="COMES ALL DAY — no daycare. Convict Lake, the Hot Creek overlook + the Lakes Basin path are all dog-friendly (leashed). Each stop is <4 hr, so the A/C van covers any gaps.",
   acts=[], res="None.",
   backup="Hot/smoky → Lakes Basin shade + a short Twin Lakes stroll, or a town/rest day (Stellar Brew coffee, brewery patios).",
   evening="Toomey's at the gondola base or a brewery patio (dogs welcome)."),
 dict(id="MAM-D", type="DAY TRIP", drive="~1h · ~40 mi round trip (June Lake)", hub="MAM", bike=True,
   oneliner="June Lake day: a pedal-up ride + the June Lake Loop — Mochi mixed",
   ctx="~20–30 min N on the June Lake Loop. Ride Reversed Peak (tech aspen loop) or grind June Mountain's Chair 6 fire road, then cruise the lakes + a patio. June Mtn runs no summer lifts — everything's pedal-up.",
   ian="AM ride: Reversed Peak Loop (~2.8 mi, tech) or June Mtn Chair 6 (~8 mi, +2,200 ft). PM: June Lake Loop lakeshore + a patio. See Activities (Eastern Sierra MTB).",
   mochi="MIXED: for the AM ride (>4 hr with the drive) → daycare in Mammoth before heading up, OR keep the ride short (<4 hr) and Mochi waits in the A/C van at a shaded lot. PM lakes are dog-friendly. NOTE: June Lake Brewing no longer allows dogs (2026) — use the Tiger Bar / T-Bar patio.",
   acts=[], res="Daycare if riding long; none otherwise.",
   backup="Storms → skip the ride; do the scenic June Lake Loop drive + a lakeshore walk with Mochi.",
   evening="Tiger Bar & Cafe or T-Bar Social Club (dog patio) in June Lake; or back in Mammoth."),
]
for o in OPTIONS:
    o.setdefault("hub", "BLD")

# ════════════════════════════════════════════════════════════════════════════════
#  FIXED-DAY TABS  (linked from the Itinerary date cell)
# ════════════════════════════════════════════════════════════════════════════════
# title, banner type, wake, sleep, miles, hrs, base, plan, ian, anny, mochi, together,
# notes, res, scenic, dining, daycare, route(list of stops for a maps link)
def route(stops): return stops
FIXED = {
 "Jul 16 (Thu)": dict(banner="PREP", plan="Final packing",
   wake="Home (Bay Area)", sleep="Redwood City / Davis", miles="", hrs="",
   base="—", together="Final packing — load the Sprinter, last gear + grocery run, charge everything.",
   notes="Last full day before departure. Confirm reservations on the Itinerary 'Advance Reservations' list."),
 "Jul 17 (Fri)": dict(banner="FIXED", plan="Surgery appt (AM) → drive to North Tahoe — van camp on the 89-N corridor",
   wake="Redwood City", sleep="Truckee — CA-89 N corridor (van)", miles="~230", hrs="4", base="—",
   together=("Final surgery appointment in Sunnyvale first thing; everyone rolls out from the clinic ~9:30 AM. "
             "One triple-duty stop in Davis (fuel + driver swap + lunch), I-80 over Donner, Truckee by early "
             "afternoon — Friday Sierra traffic can stretch it, so 4–6 hr door-to-door is the honest band. "
             "Groceries in Truckee, then 10–20 min up CA-89 N to camp along the Little Truckee (pick the exact "
             "spot on iOverlander). See DRIVE PLAN."),
   mochi=("In the van for the drive; evening leg-stretch + swim at the reservoirs (Prosser / Boca / Stampede) "
          "near camp."),
   notes=("Appointment is fixed — if it runs long, slide everything back. Friday exodus: every hour later out of "
          "the Bay adds I-80 traffic; aim to be rolling by 10:30. Elephant Fire status (Jul 15): 42% contained, ALL evacuations lifted, burning NE — AWAY from Truckee — est. full containment Jul 22; CA-89 open, camp corridor clear of the closure. Glance at fire.airnow.gov + InciWeb that morning anyway. Tahoe NF is Stage 1: no campfires outside developed rings — gas stove + the free CA campfire permit. The Sprinter takes CLEAN #2 "
          "ULSD ONLY — Davis is the confirmed stop; in CA avoid truck-stop chains (R99 renewable risk)."),
   route=["Sunnyvale, CA","1601 Research Park Dr, Davis, CA 95616","Truckee, CA"],
   drive_plan=dict(
     summary=("~230 mi · ~4 hr driving (Friday traffic can push door-to-door to 5–6). Depart the clinic ~9:30 AM → "
              "camp on the 89-N corridor by ~3:30–4:30 PM. Sunnyvale → Davis (the ONE fuel stop) → I-80 over "
              "Donner → Truckee groceries → CA-89 N to camp."),
     route_url=maps_route(["Sunnyvale, CA","1601 Research Park Dr, Davis, CA 95616","Truckee, CA"]),
     route_label="Sunnyvale → Davis (fuel) → Truckee → CA-89 N camp",
     rows=[
       dict(kind="depart", k="🚐 ~9:30 AM · Depart",
            v="Sunnyvale, straight from the clinic — Driver 1 at the wheel."),
       dict(kind="leg", k="Leg 1 · 9:30–11:15 AM",
            v="105 mi · 1h46m · Driver 1 — Sunnyvale → Davis up the East Bay, I-880 N → I-80 E.",
            url=maps_route(["Sunnyvale, CA","1601 Research Park Dr, Davis, CA 95616"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP + 🍽 LUNCH · Davis · ~11:15–11:50 AM",
            v=("1601 Research Park Dr (the Chevron at Richards Blvd) — the same confirmed clean-#2 stop as always; "
               "the ONLY fuel you need today. Fill, swap, eat."),
            url=pin(_q("1601 Research Park Dr, Davis, CA 95616"))),
       dict(kind="leg", k="Leg 2 · 11:50 AM–1:45 PM (+Friday buffer)",
            v=("113 mi · Driver 2 (fresh) — Davis → Truckee on I-80 E over Donner Pass. This is the stretch Friday "
               "traffic inflates; podcasts up, patience on."),
            url=maps_route(["1601 Research Park Dr, Davis, CA 95616","Truckee, CA"])),
       dict(kind="stop", k="🛒 GROCERIES · Truckee · ~1:45–2:30 PM",
            v="Stock the fridge for 3 camp nights (Save Mart / New Moon in Truckee). Tomorrow's towns are tiny.",
            url=pin(_q("Save Mart, Truckee, CA"))),
       dict(kind="leg", k="Leg 3 · ~2:30–3:00 PM",
            v="10–20 min up CA-89 N toward Sierraville — camps string along the Little Truckee. Corridor is OPEN (the fire closure sits NE of Loyalton, well clear). Reservable CGs are all FULL on summer Fridays — dispersed is the plan, exactly per iOverlander.",
            url=maps_route(["Truckee, CA","Lower Little Truckee Campground, CA-89, California"])),
       dict(kind="arrive", k="🏁 ~3:00–4:30 PM · Camp",
            v="Set up at ~6,000 ft, evening river/reservoir swim for Mochi, first van night of the trip."),
     ],
     fuel_options=[
       ("Davis — PRIMARY (Chevron)", "1601 Research Park Dr · ~mile 105 (~2 hr in). Confirmed clean #2 (CA Chevron retail = petroleum ULSD); fuel + swap + lunch in one.", "1601 Research Park Dr, Davis, CA 95616"),
       ("Dixon — alt (same point)", "2599 N 1st St · ~mile 98, right off I-80, 7 mi before Davis.", "2599 N 1st St, Dixon, CA 95620"),
       ("Auburn — top-off option", "13405 Lincoln Way · ~mile 153, the last confirmed clean diesel before the Donner climb.", "13405 Lincoln Way, Auburn, CA 95603"),
     ],
     sleep_options=[
       ("Prosser Hill OHV dispersed (BEST BET)", "Off CA-89 ~4 mi N of Truckee, ~5,900 ft — legal, free, ~20 mi from the fire. Pick the exact pullout on iOverlander; stove only (Stage 1).", pin(_q("Prosser Hill OHV Staging Area, Truckee, CA"))),
       ("Sagehen Creek / Kyburz Flat dispersed", "8–14 mi up 89: both legal free dispersed areas, outside the fire closure — quieter and a touch higher.", pin(_q("Sagehen Creek Road, CA-89, Truckee, CA"))),
       ("Boca Reservoir dispersed roads", "Legal dispersed on the roads around Boca — evening reservoir swim for Mochi. (The developed CGs — Boca / Boca Rest / Logger / Prosser / Little Truckee — are ALL full on Fridays.)", pin(_q("Boca Reservoir, Truckee, CA"))),
       ("Mt Rose CG — 10 first-come sites (bail-out)", "8,900 ft on NV-431 — reservables sold out but 10 FCFS sites exist. Only if you abandon 89-N; note dispersed camping is BANNED on the Mt Rose corridor and basin-wide at Tahoe.", pin(_q("Mt Rose Campground, Mount Rose Highway, NV"))),
     ],
   )),
 "Jul 18 (Sat)": dict(banner="TRAVEL", plan="US-50 'Loneliest Road' → Great Basin — sleep at 9,886 ft",
   wake="CA-89 N camp (Truckee)", sleep="Great Basin — Sacramento Pass BLM (or Wheeler Peak CG on a cancellation)", miles="~410", hrs="6.5", base="—",
   together=("The desert crossing, done smart: leave at 8:00 so the hot basin miles land before the worst heat and "
             "you claim camp by ~5. I-80 E to Fernley (fuel), US-50 through Fallon → Austin (fuel + swap + lunch) "
             "→ Ely (fuel) → Baker, then camp at Sacramento Pass BLM (first-come, 20 min shy of the park) — or up the scenic drive to 9,886 ft if the "
             "cancellation watch paid off. Dark-sky stars either way. Grimes Point, Sand Mountain and Hickison petroglyphs are the "
             "leg-stretchers en route. See DRIVE PLAN."),
   mochi=("Great Basin rules: leashed in campgrounds + on roads, NOT on park trails (the bristlecone grove is a "
          "human-only side trip). He gets his walks at the scenic pullouts and the campground loops."),
   notes=("Honest math: ~410 mi is ~6.5 hr of driving (a bit more than the 5–6 first guessed) — with stops it's a "
          "full 8:00 AM–5:00 PM day. CAMP REALITY (live rec.gov, Jul 15): Wheeler Peak CG is SOLD OUT for Sat 7/18 (36/36) — Upper Lehman + Grey Cliffs too. Tonight = Sacramento Pass BLM (free, first-come, 10 sites + shade ramadas, ~7,150 ft, on US-50 20 min before the park). Keep a cancellation watch on Wheeler Peak (rec.gov campground 10088563) and grab a Saturday drop if one appears. Park is Stage II fire restrictions — gas stove only. Fuel discipline on US-50: never pass Fernley, Austin, or "
          "Ely below half a tank. Wheeler Peak Scenic Drive is fine for the van (<24 ft limit past Upper Lehman)."),
   route=["Truckee, CA","Fernley, NV","Austin, NV","Ely, NV","Great Basin National Park, NV"],
   drive_plan=dict(
     summary=("~410 mi · ~6.5 hr driving. Depart camp 8:00 AM → Wheeler Peak CG ~4:45–5:15 PM. 89-S to Truckee → "
              "I-80 E → Fernley (fuel — closes the Davis gap) → US-50: Fallon → Austin (fuel + swap + lunch) → "
              "Eureka (optional top-off) → Ely (fuel) → Sacramento Pass camp (Wheeler Peak scenic drive if a site opened). Scenic trio "
              "(Grimes Point / Sand Mountain / Hickison) folded into the route."),
     route_url=maps_route(["Truckee, CA","Fernley, NV","Grimes Point Archaeological Area, Fallon, NV","Sand Mountain Recreation Area, NV","Austin, NV","Hickison Petroglyph Recreation Area, NV","Ely, NV","Great Basin National Park, NV"]),
     route_label="Truckee → Fernley → Fallon → Austin → Ely → Great Basin",
     rows=[
       dict(kind="depart", k="🌅 8:00 AM · Break camp",
            v="Down 89 to Truckee, I-80 E — Driver A. Early start = cool basin miles + a relaxed camp claim."),
       dict(kind="stop", k="⛽ FUEL · Fernley · ~9:30–9:50 AM",
            v=("Love's, 825 Commerce Center Dr (I-80 Exit 46). REQUIRED stop — it's ~208 mi since the Davis fill, "
               "right at the range edge. Fill full for the Loneliest Road."),
            url=pin(_q("Love's Travel Stop, 825 Commerce Center Dr, Fernley, NV 89408"))),
       dict(kind="leg", k="Leg 2 · 9:50 AM–12:15 PM (incl. scenic stops)",
            v=("~137 mi · Driver A — US-50 ALT through Fallon, then the open desert. Grimes Point petroglyphs "
               "(~mile 120) and Sand Mountain's singing dune (~mile 135) are 15-min leg-stretches on the route "
               "link. Watch for low-flying jets — this is Top Gun country (NAS Fallon)."),
            url=maps_route(["Fernley, NV","Grimes Point Archaeological Area, Fallon, NV","Sand Mountain Recreation Area, NV","Austin, NV"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP + 🍽 LUNCH · Austin · ~12:15–12:55 PM",
            v="Austin Chevron, Main St / US-50 (~mile 230 of the day). Fuel, swap drivers, lunch — the classic Loneliest Road pit stop.",
            url=pin(_q("Chevron, Austin, NV 89310"))),
       dict(kind="leg", k="Leg 3 · 12:55–3:05 PM",
            v=("147 mi · Driver B — Austin → Ely via Eureka. Hickison Petroglyphs (24 mi past Austin) for a quick "
               "stretch; optional insurance top-off at the Eureka Chevron (mile ~300)."),
            url=maps_route(["Austin, NV","Hickison Petroglyph Recreation Area, NV","Eureka, NV","Ely, NV"])),
       dict(kind="stop", k="⛽ FUEL · Ely · ~3:05–3:25 PM",
            v="Top off in Ely (multiple name-brand stations) — this tank covers tonight, tomorrow's start, and the run to Delta, UT.",
            url=pin(_q("Ely, NV 89301"))),
       dict(kind="leg", k="Leg 4 · 3:25–4:45 PM",
            v="~46 mi on US-50/93 to Sacramento Pass. If the Wheeler Peak watch paid off: continue to Baker + 12 mi up the scenic drive (van OK, <24 ft) — 7,000 → 9,886 ft in twenty minutes.",
            url=maps_route(["Ely, NV","Great Basin National Park, NV"])),
       dict(kind="arrive", k="🏁 ~4:20 PM · Sacramento Pass BLM (or Wheeler Peak on a cancellation)",
            v=("Claim a first-come site (10 sites — aim to arrive by 5 on a Saturday), dinner under the darkest sky of the trip "
               "— Great Basin is a gold-tier dark-sky park. Bring layers: lows near 40°F even in July.")),
     ],
     fuel_options=[
       ("Fernley — Love's (REQUIRED)", "825 Commerce Center Dr, I-80 Exit 46 · ~mile 90. Closes the 208-mi gap since Davis; NV chains pump standard #2.", "Love's Travel Stop, 825 Commerce Center Dr, Fernley, NV 89408"),
       ("Austin — Chevron", "Main St / US-50 · ~mile 230. Confirmed diesel; fuel + swap + lunch.", "Chevron, Austin, NV 89310"),
       ("Eureka — Chevron (optional)", "US-50 Main St · ~mile 300. Insurance top-off.", "Chevron, Eureka, NV 89316"),
       ("Ely — best fuel on the route", "Multiple name-brand stations · ~mile 355. Fill for tonight + tomorrow's start.", "Ely, NV 89301"),
     ],
     scenic=[
       ("Grimes Point petroglyphs", "Boulder field of 8,000-year-old rock art right off US-50 E of Fallon — 15-min loop, leashed dogs fine.", pin(_q("Grimes Point Archaeological Area, Fallon, NV"))),
       ("Sand Mountain", "600-ft singing dune 2 mi off the highway — quick photo stop (hot sand midday — check paws).", pin(_q("Sand Mountain Recreation Area, NV"))),
       ("Hickison Petroglyphs", "BLM site at Hickison Summit, 24 mi E of Austin — shady leg-stretch loop.", pin(_q("Hickison Petroglyph Recreation Area, NV"))),
     ],
     sleep_options=[
       ("Sacramento Pass BLM (PRIMARY — free, FCFS)", "US-50, ~20 min W of the park turnoff, ~7,150 ft: 10 sites, shade ramadas, vault toilets, no water (filled jugs at Fernley/Ely). Arrive by ~5 PM on a Saturday; overflow = legal BLM dispersed nearby (iOverlander).", pin(_q("Sacramento Pass Recreation Area, US-50, NV"))),
       ("Wheeler Peak CG — cancellation watch", "9,886 ft, the cool prize — SOLD OUT Sat 7/18 as of Jul 15 (36/36, rec.gov 10088563). Watch for a drop; the van is legal on the scenic drive (<24 ft limit).", pin(_q("Wheeler Peak Campground, Great Basin National Park, NV"))),
       ("Lower Lehman Creek — 4-day window", "Books only 4 days out; 7/18 filled the moment it opened. Not worth planning around — check once, then let it go.", pin(_q("Lower Lehman Creek Campground, Great Basin National Park, NV"))),
     ],
   )),
 "Jul 19 (Sun)": dict(banner="TRAVEL", plan="Across Utah — pick your push (3 options, all end up high + cool)",
   wake="Great Basin — Wheeler Peak CG", sleep="High country — your pick (La Sal / Grand Mesa / Vail Pass)",
   miles="354–537", hrs="6–8", base="—",
   together=("Decision day: how far east? All three options run the same first stretch — US-50/6 to Delta, I-70 at "
             "Salina — then peel off at different points, and every one ends the night at 9,500–11,000 ft. "
             "A) La Sal Mountains above Moab (~6 hr) · B) Grand Mesa (~7 hr — the recommended balance) · "
             "C) Vail Pass / Shrine Pass (~8 hr, nearly done with the crossing). Pick on the road; the DRIVE PLAN "
             "below has a route link for each."),
   mochi=("Hot valley midday (Green River runs ~100°F) — he stays in the A/C van at stops; the payoff is an "
          "evening walk in cool forest wherever you land."),
   notes=("Don't sleep low: whatever option you pick, finish the climb. Fire snapshot (Jul 15): I-70 corridor + Grand Mesa CLEAR; La Sals OPEN (the 106k-acre Babylon Fire is 45–60 mi south in the Abajos — expect some SE-Utah haze); every forest on the route is Stage 2 — no campfires anywhere, gas stove only. Re-check InciWeb before picking. Fuel: Delta → Salina → "
          "(Green River for A · Grand Junction for B/C · +Glenwood for C) — all previously verified clean-#2 "
          "stops."),
   route=["Great Basin National Park, NV","Delta, UT","Salina, UT","Green River, UT"],
   drive_plan=dict(
     summary=("354–537 mi · 6–8 hr driving depending on the option. Depart 8:30 AM after a campground-loop walk. "
              "Common stem: Great Basin → Delta (fuel + swap, ~10:30) → Salina (I-70, optional top-off) → then "
              "choose. Every option's link opens the full route in Maps."),
     route_url=maps_route(["Great Basin National Park, NV","Delta, UT","Salina, UT","Green River, UT","Grand Junction, CO"]),
     route_label="Great Basin → Delta → I-70 east (then pick A / B / C)",
     route_options_title="🧭 PICK YOUR PUSH — one option, three distances, all sleep cool",
     route_options_callout=("⚠️  Decide by Salina (~11:45 AM). Every option ends at 9,500–11,000 ft — the miles "
                            "differ, the night temperature doesn't. Tap an option to open its exact route."),
     route_options=[
       dict(name="Option A · La Sal Mountains — 354 mi · ~6 hr (camp ~3:00 PM)",
            note=("I-70 → US-191 toward Moab, then the La Sal Loop Rd up to Geyser Pass Rd dispersed sites at "
                  "~9,500 ft — red-rock views from aspen shade. Shortest day, biggest afternoon. Camp: at-large dispersed off Geyser Pass Rd, ~9,500–10,600 ft (Warner Lake CG was full for Sunday + rates only 20-ft vehicles)."),
            url=maps_route(["Great Basin National Park, NV","Delta, UT","Moab, UT","38.4906,-109.2686"])),
       dict(name="Option B · Grand Mesa — 430 mi · ~7 hr (camp ~4:30 PM) ★ RECOMMENDED",
            note=("I-70 through Green River to Grand Junction (fuel: Love's #517), then CO-65 up the world's "
                  "largest flat-top mountain — lakes + spruce at ~10,000 ft. Best progress-vs-arrival balance. "
                  "Camp: Island Lake CG had 9 Sunday sites open as of 7/15 (rec.gov 233387), Cobbett Lake 8 (233936) — or legal dispersed off the Mesa's forest roads."),
            url=maps_route(["Great Basin National Park, NV","Delta, UT","Grand Junction, CO","39.0316,-107.9861"])),
       dict(name="Option C · Vail Pass / Shrine Pass — 537 mi · ~8 hr (camp ~6:00 PM)",
            note=("The big push: I-70 through Glenwood Canyon (fuel Glenwood), camp off Shrine Pass Rd at "
                  "~11,000 ft. Crossing basically done — Boulder is 1h48 away. Camp: dispersed off Shrine Pass Rd (FR-709) at 10,700–11,100 ft — the coldest option."),
            url=maps_route(["Great Basin National Park, NV","Delta, UT","Glenwood Springs, CO","39.5299,-106.2179"])),
     ],
     rows=[
       dict(kind="depart", k="🌅 8:30 AM · Depart",
            v="Wheeler Peak CG, Driver A — campground-loop walk for Mochi first; tank is full from Ely."),
       dict(kind="leg", k="Leg 1 · 8:30–10:30 AM",
            v="~100 mi · Driver A — down the scenic drive, US-50/6 E across the state line to Delta, UT.",
            url=maps_route(["Great Basin National Park, NV","Delta, UT"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP · Delta · ~10:30–10:55 AM",
            v="Maverik, 44 N US-6 (RV lanes, 24-hr diesel) — first reliable fuel in Utah; fill for the I-70 miles.",
            url=pin(_q("Maverik, 44 N US Highway 6, Delta, UT 84624"))),
       dict(kind="leg", k="Leg 2 · 10:55–11:45 AM",
            v="~70 mi · Driver B — US-50 to Salina and onto I-70 E. DECIDE HERE: A, B, or C (options above).",
            url=maps_route(["Delta, UT","Salina, UT"])),
       dict(kind="stop", k="⛽ Top-off options eastbound",
            v=("Salina Flying J (I-70 Exit 253) → Green River Pilot (1085 E Main, option A) → Grand Junction "
               "Love's #517 (748 22 Road, options B/C) → Glenwood Springs Sinclair (option C). All verified "
               "clean-#2; longest gap on any option < 150 mi."),
            url=pin(_q("Love's Travel Stop, 748 22 Road, Grand Junction, CO 81505"))),
       dict(kind="arrive", k="🏁 3:00 / 4:30 / 6:00 PM · Camp high",
            v="Per your option — set camp, dinner in the trees, and a genuinely cold night wherever you chose."),
     ],
     fuel_options=[
       ("Delta — Maverik (PRIMARY)", "44 N US-6 · ~mile 100. RV lanes, 24-hr diesel; the fuel + swap stop.", "Maverik, 44 N US Highway 6, Delta, UT 84624"),
       ("Salina — Flying J", "I-70 Exit 253 · ~mile 176. Top off at the decision point.", "Flying J Travel Center, Salina, UT 84654"),
       ("Green River — Pilot (option A)", "1085 E Main St · ~mile 278.", "Pilot Travel Center, 1085 E Main St, Green River, UT 84525"),
       ("Grand Junction — Love's #517 (B/C)", "748 22 Road, I-70 Exit 26 · ~mile 360.", "Love's Travel Stop, 748 22 Road, Grand Junction, CO 81505"),
       ("Glenwood Springs — Sinclair (C)", "Hwy 6/82 · ~mile 460 — before the canyon climb.", "Sinclair, Glenwood Springs, CO 81601"),
     ],
     sleep_options=[
       ("A · Geyser Pass Rd dispersed — La Sals", "At-large dispersed at ~9,500–10,600 ft in aspen + spruce above the red rock; maintained gravel, van-fine dry. Stage 2 = stove only. Pick the pullout on iOverlander.", pin(_q("Geyser Pass Road, La Sal Mountains, Moab, UT"))),
       ("B · Island Lake CG — Grand Mesa (RESERVE)", "10,300 ft; 9 sites open for Sun 7/19 as of Jul 15 (rec.gov 233387). Cobbett Lake (233936, 8 open) + Jumbo (233189, 2) nearby, plus dispersed along the Mesa roads.", pin(_q("Island Lake Campground, Grand Mesa, CO"))),
       ("C · Shrine Pass Rd dispersed — Vail Pass", "Classic FR-709 pullouts at 10,700–11,100 ft ('passable by all but the lowest passenger cars'). Coldest, starriest option (~35–38°F).", pin(_q("Shrine Pass Road, Vail Pass, CO"))),
     ],
   )),
 "Jul 20 (Mon)": dict(banner="TRAVEL", plan="Converge on the Colorado high country (Vail Pass / Camp Hale)",
   wake="Last night's camp (A / B / C)", sleep="Shrine Pass or Camp Hale area (van, ~9,200–11,000 ft)",
   miles="0–272", hrs="0–5", base="—",
   together=("Everyone funnels to the same neighborhood tonight: the Vail Pass / Camp Hale high country. From "
             "La Sal it's ~4.75 hr, from Grand Mesa ~3 hr, and if you already pushed to Shrine Pass last night "
             "this is a LAYOVER — sleep in, hike Shrine Ridge (wildflowers, ~4 mi RT, dogs OK), move nothing. "
             "Set camp by ~3 PM regardless: afternoon thunderstorms are the summer rhythm up here."),
   mochi=("Aspen-and-spruce camps at 9,000–11,000 ft — his kind of country. Leash in developed sites; storms "
          "mid-afternoon (he hates thunder? camp early)."),
   notes=("Fire snapshot (Jul 15): Vail Pass + Camp Hale CLEAR. ⚠️ Willow Fire (6,539 ac, 28%) burns 6 mi WEST of Leadville with a closure on the Mt Massive/Hagerman side — US-24 over Tennessee Pass is open, but do NOT camp west of Leadville and expect smoke pockets near town. Re-check InciWeb before committing. Camp Hale/Homestake CHANGED Jun 15, 2026: FR-703/704 camping is now designated-sites-only, $20/night at the kiosk, all first-come this year (44 sites). Zero-drama move: reserve Camp Hale Memorial CG (rec.gov 232274, ~9,200 ft — 16 Monday sites open as of Jul 15). Shrine Pass dispersed stays free. Nights run 35–42°F at this elevation — the coldest sleeps of "
          "the whole trip. Tomorrow is a choice day (stay or hop to Boulder's backyard), so pick a camp you'd "
          "happily keep."),
   route=["Grand Mesa Visitor Center, Cedaredge, CO","Vail Pass Rest Area, CO"],
   drive_plan=dict(
     summary=("0–272 mi depending on where Sunday ended. From La Sal: I-70 E through Grand Junction + Glenwood "
              "(~272 mi / 4h45). From Grand Mesa: down CO-65, I-70 E to Vail Pass or over Battle Mtn to Camp Hale "
              "(~165 mi / ~3h). From Shrine Pass: zero — layover + hike. Fuel as needed at Glenwood (Sinclair) or "
              "Silverthorne (Conoco, I-70 & Hwy 9)."),
     route_url=maps_route(["Grand Junction, CO","Glenwood Springs, CO","Vail Pass Rest Area, CO"]),
     route_label="I-70 east → Vail Pass / Camp Hale",
     rows=[
       dict(kind="depart", k="🌅 Depart when the coffee's done",
            v="Short day by design — 9:00–10:00 AM is plenty from Grand Mesa; ~8:30 from La Sal."),
       dict(kind="leg", k="From La Sal · ~272 mi · 4h45",
            v="La Sal Loop down to US-191 → I-70 E through Grand Junction, Glenwood Canyon, Vail → Vail Pass. Fuel Glenwood.",
            url=maps_route(["38.4906,-109.2686","Grand Junction, CO","Glenwood Springs, CO","Vail Pass Rest Area, CO"])),
       dict(kind="leg", k="From Grand Mesa · ~165 mi · ~3h",
            v=("CO-65 down to I-70 E. For Shrine Pass: exit at Vail Pass rest area, FR-709. For Camp Hale: exit "
               "Minturn, US-24 over Battle Mountain (~3h)."),
            url=maps_route(["39.0316,-107.9861","Glenwood Springs, CO","Vail Pass Rest Area, CO"])),
       dict(kind="leg", k="Already at Shrine Pass? · LAYOVER",
            v=("Hike Shrine Ridge Trail from the pass (~4 mi RT, 11,089 → 11,888 ft, dogs OK, July wildflower "
               "peak) — or Camp Hale valley loop if you dropped down. Zero driving days are what the van's for."),
            url=pin(_q("Shrine Ridge Trailhead, Shrine Pass Road, Vail Pass, CO"))),
       dict(kind="stop", k="⛽ If fueling today",
            v="Glenwood Springs Sinclair (Hwy 6/82) westsiders · Silverthorne Conoco (I-70 & Hwy 9) if you overshoot east. Both verified clean #2.",
            url=pin(_q("Conoco, Silverthorne, CO 80498"))),
       dict(kind="arrive", k="🏁 By ~3:00 PM · Camp set",
            v="Shrine Pass Rd (FR-709) dispersed or the Camp Hale / Homestake Rd area (details below) — beat the afternoon storm cell, dinner early."),
     ],
     sleep_options=[
       ("Camp Hale Memorial CG (RESERVE — zero drama)", "~9,200 ft on the 10th Mountain Division's historic valley floor; 16 sites open for Mon 7/20 as of Jul 15 (rec.gov 232274). Stay on established routes — WWII ordnance country.", pin(_q("Camp Hale Memorial Campground, Leadville, CO"))),
       ("Shrine Pass Rd (FR-709) dispersed — free", "10,700–11,100 ft pullouts between Vail Pass and Redcliff; coldest + starriest. Stove only (Stage 2, forest-wide).", pin(_q("Shrine Pass Road, Vail Pass, CO"))),
       ("Homestake Rd (FR-703) — $20 designated sites", "New this summer: 44 numbered first-come sites along 15 mi of the Homestake valley; Gold Park CG (12 FCFS sites) at the end.", pin(_q("Homestake Road, White River National Forest, CO"))),
       ("Kenosha Pass CG — backup east", "~10,000 ft on US-285; 13 Monday sites open as of Jul 15 (rec.gov 233185) if you'd rather stage southeast.", pin(_q("Kenosha Pass Campground, Jefferson, CO"))),
     ],
   )),
 "Jul 21 (Tue)": dict(banner="CHOICE DAY", plan="Layover in the high country — OR hop to Boulder's backyard",
   wake="Shrine Pass / Camp Hale camp", sleep="Same camp OR West Magnolia / Gordon Gulch (Nederland, van)",
   miles="0 or ~80–110", hrs="0 or ~1.5–2.2", base="—",
   together=("Two right answers. STAY: another alpine day — Shrine Ridge sunrise, Camp Hale's WWII 10th-Mountain "
             "ruins loop, zero packing. MOVE: a 1.5–2 hr hop lands you in the pines at West Magnolia or Gordon "
             "Gulch above Nederland (~8,600–9,000 ft) — Boulder's backyard, 35–40 min from the Airbnb, which "
             "turns tomorrow into a lazy roll-in with a whole Boulder afternoon. Tuesday = easy site-hunting at "
             "West Mag. Pick by weather + energy."),
   mochi=("Either way he wins: high-country trails at camp, or the West Mag network (multi-use MTB/hike trails "
          "right out of the dispersed sites — leash around other users)."),
   notes=("West Magnolia + Gordon Gulch are FREE, designated-numbered-site, first-come areas (no permit); a Tuesday arrival is easy pickings. ARP is Stage 2 — no fires, stove only. (Caribou Rd near Nederland is closed for road damage; doesn't touch West Mag.) Reservable fallbacks: Kelly Dahl CG — 12 Tuesday sites open as of Jul 15 (rec.gov 232280) — or Golden Gate Canyon SP (cpwshop.com). If you MOVE, do it before noon — I-70 eastbound + US-6 through Clear Creek is "
          "calmer midday, and afternoon storms make Loveland-area driving less fun."),
   route=["Vail Pass Rest Area, CO","Nederland, CO"],
   drive_plan=dict(
     summary=("If moving: Vail Pass → West Magnolia is ~79 mi / ~1h30 (I-70 E → US-6 through Clear Creek Canyon → "
              "CO-119 up Boulder Canyon to Nederland); from Camp Hale add ~30 min (~111 mi / 2h10). No fuel stop "
              "needed (Boulder is 22 mi from West Mag — fill there tomorrow)."),
     route_url=maps_route(["Vail Pass Rest Area, CO","Nederland, CO","39.9439,-105.5033"]),
     route_label="Vail Pass → Clear Creek → Nederland → West Magnolia",
     rows=[
       dict(kind="depart", k="STAY · zero miles",
            v="Layover: Shrine Ridge or Camp Hale valley walk, reading chairs, storm-watching. Tomorrow's drive to Boulder is 1h48 from here — still easy."),
       dict(kind="leg", k="MOVE · ~10:00 AM–12:00 PM",
            v=("79–111 mi — I-70 E past Georgetown, exit US-6 through Clear Creek Canyon (scenic, un-interstate), "
               "CO-119 through Black Hawk up to Nederland; West Magnolia is 3 mi south on CO-119, Gordon Gulch "
               "~4 mi up the Peak to Peak."),
            url=maps_route(["Vail Pass Rest Area, CO","Nederland, CO","39.9439,-105.5033"])),
       dict(kind="stop", k="🍦 Nederland pause",
            v="Ice cream / coffee in Nederland (Crosscut Pizza + B&F Market for anything forgotten) before claiming a site.",
            url=pin(_q("Nederland, CO"))),
       dict(kind="arrive", k="🏁 Early afternoon · Camp at 8,600–9,000 ft",
            v="Pick the exact pullout on iOverlander; afternoon on the West Mag trails. Boulder Airbnb is 38 min away — tomorrow is already easy."),
     ],
     sleep_options=[
       ("West Magnolia dispersed (designated sites, free)", "The Boulder-backyard classic: numbered sites in the pines off CO-119, 3 mi S of Nederland, ~9,000 ft — MTB/hike trails right from camp. First-come; Tuesday = relaxed.", pin(_q("West Magnolia Dispersed Camping, Nederland, CO"))),
       ("Gordon Gulch dispersed (designated sites, free)", "15 numbered sites off the Peak to Peak Hwy (~8,800 ft), 1.5 mi E of CO-72 — quieter and more wooded.", pin(_q("Gordon Gulch Dispersed Camping Area, Nederland, CO"))),
       ("Kelly Dahl CG (reservable fallback)", "~8,600 ft on CO-119 south of Nederland — 12 sites open for Tue 7/21 as of Jul 15 (rec.gov 232280).", pin(_q("Kelly Dahl Campground, Nederland, CO"))),
       ("Golden Gate Canyon SP (reservable fallback)", "Reverend's Ridge ~9,100 ft — showers (!), leashed dogs OK; book at cpwshop.com. Tuesdays rarely sell out.", pin(_q("Reverend's Ridge Campground, Golden Gate Canyon State Park, CO"))),
     ],
   )),
 "Jul 22 (Wed)": dict(banner="TRAVEL → ARRIVAL", plan="Roll into Boulder — Chautauqua walk + 3 PM check-in",
   wake="West Magnolia (or the Vail Pass camp)", sleep="Boulder — Airbnb", miles="~22 (or ~97)", hrs="0.6–1.8",
   base="582 Locust Pl, Boulder",
   checkin=("“Hike + Paraglide Boulder” · entire home, hosted by Kendal. Check-in after 3:00 PM · checkout by "
            "11:00 AM. Message host to coordinate entry: +1 234-264-0306. Confirmation HMS33NSE3T. Max 2 guests "
            "+ Mochi · quiet hours 10 PM–6 AM · exterior security cameras."),
   together=("The easiest travel day of the trip: down Boulder Canyon by ~10:30 AM (38 min from West Mag; 1h48 if "
             "you stayed at Vail Pass), groceries, then the classic arrival — Chautauqua meadow walk under the "
             "Flatirons + lunch. Check-in at 3 PM, unpack, exhale: you're in Boulder for 10 nights."),
   mochi=("Chautauqua meadow trails are leashed-dog-friendly. After 6 nights in the van he gets a real yard."),
   notes=("Fill the tank in Boulder this afternoon (28th St corridor) so the van sits ready. Boulder trailhead "
          "parking fills by 8 AM on weekends — today's a Wednesday, you're fine."),
   route=["39.9439,-105.5033","Boulder, CO","582 Locust Pl, Boulder, CO 80304"],
   dining="Boulder", daycare="Boulder", menu_next="BLD", acts_ref=True,
   drive_plan=dict(
     summary=("From West Magnolia: 22 mi · 38 min down Boulder Canyon (CO-119). From the Vail Pass area: 97 mi · "
              "~1h48 (I-70 E → US-6 → CO-93 into Boulder). Either way: groceries first (Whole Foods Pearl / "
              "Alfalfa's), Chautauqua late morning, check-in 3:00 PM."),
     route_url=maps_route(["39.9439,-105.5033","582 Locust Pl, Boulder, CO 80304"]),
     route_label="West Magnolia → Boulder Canyon → 582 Locust Pl",
     rows=[
       dict(kind="depart", k="🌅 ~10:00 AM · Break camp",
            v="No rush — check-in isn't till 3. Coffee in Nederland if you like."),
       dict(kind="leg", k="Leg 1 · ~10:00–10:40 AM",
            v="22 mi · CO-119 down Boulder Canyon — creek on your right, canyon walls closing in, then the city opens up. (Vail Pass variant: I-70 E → US-6 → CO-93 N, ~1h48.)",
            url=maps_route(["39.9439,-105.5033","Boulder, CO"])),
       dict(kind="stop", k="🛒 GROCERIES + 🍽 · Boulder · ~10:45 AM–2:30 PM",
            v="Stock up for the Airbnb (Whole Foods Pearl St / Alfalfa's), then Chautauqua: park at the Ranger Cottage lot, meadow loop under the Flatirons, lunch on the Dining Hall porch.",
            url=pin(_q("Chautauqua Park, Boulder, CO"))),
       dict(kind="arrive", k="🏠 3:00 PM · Check in",
            v="582 Locust Pl — message Kendal ahead to coordinate. Unload, shower, neighborhood walk with Mochi. Boulder chapter begins."),
     ],
   )),
 "Aug 1 (Sat)": dict(banner="TRAVEL", plan="Drive home, day 1 — Snowy Range Scenic Byway → Saratoga hot springs",
   wake="Boulder — Airbnb (checkout 11 AM)", sleep="Saratoga, WY (van)", miles="192", hrs="3.8", base="—",
   together=("Checkout is 11 AM — breakfast out, then roll straight north. US-287 to Laramie (lunch + fuel), "
             "then WY-130 over 10,847-ft Snowy Range Pass: Lake Marie leg-stretch at the top (~65°F up there), "
             "down the west side to Saratoga by ~5:15 PM. Evening soak at the FREE 24/7 Hobo Hot Pool. See DRIVE PLAN."),
   mochi=("Lake Marie / byway pullouts are USFS — leashed and welcome. In Saratoga he swims the NORTH PLATTE "
          "(Veterans Island Park or the river access by the pool) — NOT Saratoga Lake (blue-green algae history; "
          "check WyoHCBs.org before any lake swim)."),
   notes=("Top off the tank in Boulder before checkout (any name-brand on the 28th St corridor) — first planned fill "
          "is Laramie. Snowy Range 2026 'Active Vegetation Operations' can cause brief temporary closures — glance at "
          "the Medicine Bow alerts page day-of. Saratoga August normals 83°F / 50°F."),
   route=["582 Locust Pl, Boulder, CO 80304","Laramie, WY","Centennial, WY","Ryan Park, WY","Saratoga, WY"],
   drive_plan=dict(
     summary=("192 mi · ~3.8 hr driving. Depart at the 11 AM checkout → Saratoga ~5:15 PM with a proper hour on "
              "top of the Snowy Range. Boulder → Laramie (US-287 through Fort Collins) → Centennial → Snowy Range "
              "Scenic Byway (WY-130) over the pass → Saratoga. One fuel stop (Laramie) covers today + tomorrow's "
              "first hop."),
     route_url=maps_route(["582 Locust Pl, Boulder, CO 80304","Laramie, WY","Centennial, WY","Ryan Park, WY","Saratoga, WY"]),
     route_label="Boulder → Laramie → Snowy Range Byway → Saratoga",
     rows=[
       dict(kind="depart", k="🌅 11:00 AM · Depart",
            v="Boulder Airbnb at checkout, Driver A — tank topped off in town, breakfast already eaten out."),
       dict(kind="leg", k="Leg 1 · 11:00 AM–1:15 PM",
            v="112 mi · Driver A — Boulder → Laramie on US-287 N through Fort Collins, over the state line at Tie Siding.",
            url=maps_route(["582 Locust Pl, Boulder, CO 80304","Laramie, WY"])),
       dict(kind="stop", k="⛽ FUEL + 🍽 LUNCH · Laramie · ~1:15–2:00 PM",
            v="Pilot Travel Center #308, 1564 N McCue St (I-80 Exit 310 side of town) — 24-hr diesel; Love's #723 at the same exit if lanes are full. Fill here — it covers the rest of today (80 mi) plus tomorrow's first hop to Rawlins.",
            url=pin(_q("Pilot Travel Center, 1564 N McCue St, Laramie, WY 82072"))),
       dict(kind="leg", k="Leg 2 · 2:00–2:40 PM",
            v="33 mi · Driver B — Laramie → Centennial on WY-130 W across the Laramie Plains.",
            url=maps_route(["Laramie, WY","Centennial, WY"])),
       dict(kind="leg", k="🏔 2:40–4:15 PM · Snowy Range Pass",
            v=("Climb WY-130 to 10,847 ft. Park at the Lake Marie pullout: paved lakeshore path under Medicine Bow "
               "Peak, alpine wildflowers, ~65°F. Libby Flats observation point is 1 mi on. Budget a lazy hour up top.")),
       dict(kind="leg", k="Leg 3 · 4:15–5:15 PM",
            v="47 mi · descend the west side through Ryan Park, then WY-130 into Saratoga.",
            url=maps_route(["Centennial, WY","Ryan Park, WY","Saratoga, WY"])),
       dict(kind="arrive", k="🏁 ~5:15 PM · Arrive Saratoga",
            v=("Claim a campsite (candidates below), dinner in town, then the Hobo Hot Pool after dark — free, open "
               "24/7, town-owned, 311 E Walnut Ave. Pools run 106–119°F.")),
     ],
     fuel_options=[
       ("Boulder — pre-fill (before checkout)", "Any name-brand on the 28th St corridor. Start the trip full — first planned stop is Laramie, mile 112.", "Conoco, 28th Street, Boulder, CO"),
       ("Laramie — Pilot #308 (PRIMARY)", "1564 N McCue St · ~mile 112. 24-hr diesel; Love's #723 (1770 McCue St) shares the exit.", "Pilot Travel Center, 1564 N McCue St, Laramie, WY 82072"),
       ("Rawlins — Flying J #763 (tomorrow AM)", "I-80 Exit 209 (Johnson Rd); TA Rawlins at Exit 214 as backup. First stop of Day 2, mile 42 — no need to fuel again tonight.", "Flying J Travel Center, Johnson Rd, Rawlins, WY 82301"),
     ],
     scenic=[
       ("Lake Marie — Snowy Range Pass", "THE stop on WY-130: paved lakeside path beneath Medicine Bow Peak's 12,013-ft wall. Leashed dogs welcome (USFS).", pin(_q("Lake Marie, Snowy Range Scenic Byway, WY"))),
       ("Libby Flats Observation Point", "Stone lookout at the byway's crest — 360° over the Snowies and (on a clear day) into Colorado.", pin(_q("Libby Flats Observation Point, WY-130, Wyoming"))),
       ("Veterans Island Park — Saratoga", "Riverside 0.7-mi loop on the North Platte in town — Mochi's evening river dip (leashed).", pin(_q("Veterans Island Park, Saratoga, WY"))),
     ],
     sleep_options=[
       ("Saratoga Lake Campground (town)", "1 mi N of town — FCFS only, 50 electric $25 / 24 dry $15 a night, big open sites. NOTE: dog swims in the RIVER, not the lake (algae advisories some Augusts).", pin(_q("Saratoga Lake Campground, Saratoga, WY"))),
       ("South Brush Creek CG (USFS)", "~20 mi SE off WY-130 in the trees — reservable on recreation.gov (ID 10156047; 8 sites open for Aug 1 as of Jul 14 — book it now if you want certainty).", pin(_q("South Brush Creek Campground, Saratoga, WY"))),
     ],
   )),
 "Aug 2 (Sun)": dict(banner="TRAVEL", plan="Drive home, day 2 — I-80 west → camp high in the Uinta Mountains",
   wake="Saratoga, WY (van)", sleep="Uintas — Bear River corridor (van)", miles="284", hrs="4.4", base="—",
   together=("Optional dawn soak at the Hobo Pool, then the workmanlike I-80 miles: Rawlins → Rock Springs → "
             "Evanston, and 30 min up UT-150 (Mirror Lake Highway) to a ~8,500–9,000 ft spruce-forest camp on the "
             "Bear River. In camp by ~3:30 PM — river wade for Mochi, camp dinner, genuinely cold night. See DRIVE PLAN."),
   mochi=("Bear River corridor camps are USFS — leashed in camp, river access everywhere. Bring his towel: "
          "the water is snowmelt."),
   notes=("Mirror Lake Hwy has a $10/3-day amenity fee — your America the Beautiful annual pass covers it (hang it "
          "from the mirror). NO water taps at the FCFS camps — fill jugs + tanks in Evanston. Stage-1-style fire "
          "rules: campfires only in developed fire rings. Night lows in the 30s–40s at 9,000 ft — layers."),
   route=["Saratoga, WY","Rawlins, WY","Rock Springs, WY","Evanston, WY","Bear River Campground, Mirror Lake Scenic Byway, UT"],
   drive_plan=dict(
     summary=("284 mi · ~4.4 hr driving. Depart 9:00 AM (the by-9 default) → camp ~3:30 PM. Saratoga → I-80 at "
              "Walcott → Rawlins → Rock Springs → Evanston → UT-150 south into the Uintas. Fuel at Rawlins, Rock "
              "Springs and Evanston (last services before camp). Optional Flaming Gorge overlook detour decided at "
              "Green River."),
     route_url=maps_route(["Saratoga, WY","Rawlins, WY","Rock Springs, WY","Evanston, WY","Bear River Campground, Mirror Lake Scenic Byway, UT"]),
     route_label="Saratoga → Rawlins → Rock Springs → Evanston → Mirror Lake Hwy",
     rows=[
       dict(kind="depart", k="🌅 9:00 AM · Depart",
            v="Saratoga, Driver A — dawn Hobo Pool soak first if you're up; coffee for the road."),
       dict(kind="leg", k="Leg 1 · 9:00–9:40 AM",
            v="42 mi · Driver A — Saratoga → WY-130 N to I-80 at Walcott → Rawlins.",
            url=maps_route(["Saratoga, WY","Rawlins, WY"])),
       dict(kind="stop", k="⛽ FUEL + 🛒 GROCERIES · Rawlins · ~9:40–10:15 AM",
            v="Flying J #763, I-80 Exit 209 (Johnson Rd) — 24-hr lanes; TA at Exit 214 as backup. Fill + grab tonight's camp dinner fixings.",
            url=pin(_q("Flying J Travel Center, Johnson Rd, Rawlins, WY 82301"))),
       dict(kind="leg", k="Leg 2 · 10:15–11:55 AM",
            v="108 mi · Driver B — Rawlins → Rock Springs across the Great Divide Basin (Continental Divide twice).",
            url=maps_route(["Rawlins, WY","Rock Springs, WY"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP + 🍽 LUNCH · Rock Springs · ~11:55 AM–12:40 PM",
            v="Flying J #764, 650 Stagecoach Blvd (Exit 104). Fuel, swap drivers, lunch (~45 min). Heads-up: there is NO Love's in Rock Springs — this IS the truck stop.",
            url=pin(_q("Flying J Travel Center, 650 Stagecoach Blvd, Rock Springs, WY 82901"))),
       dict(kind="leg", k="Leg 3 · 12:40–2:20 PM",
            v=("104 mi · Driver A — Rock Springs → Evanston. Decision at Green River (~12:55): the Flaming Gorge "
               "Red Canyon overlook detour (UT-530 → UT-44 → WY-414 back to I-80) adds ~100 mi / ~2 hr — only if "
               "you're feeling it; camp arrival slips to ~5:30 PM."),
            url=maps_route(["Rock Springs, WY","Evanston, WY"])),
       dict(kind="stop", k="⛽ FUEL + 💧 WATER · Evanston · ~2:20–2:50 PM",
            v="Pilot #141, 289 Bear River Dr (Exit 6) — 24-hr. LAST SERVICES before camp — fill diesel AND every water jug (the FCFS camps up UT-150 have no taps).",
            url=pin(_q("Pilot Travel Center, 289 Bear River Dr, Evanston, WY 82930"))),
       dict(kind="leg", k="Leg 4 · 2:50–3:25 PM",
            v="30 mi · UT-150 (Mirror Lake Hwy) south along the Bear River — camps start ~mile 19 and string south to mile 25.",
            url=maps_route(["Evanston, WY","Bear River Campground, Mirror Lake Scenic Byway, UT"])),
       dict(kind="arrive", k="🏁 ~3:25 PM · Arrive — Bear River corridor",
            v="Pick your camp (candidates below), hang the park pass on the mirror, river time for Mochi, early fire-ring dinner."),
     ],
     fuel_options=[
       ("Rawlins — Flying J #763", "I-80 Exit 209 (Johnson Rd); TA Rawlins at Exit 214 as backup.", "Flying J Travel Center, Johnson Rd, Rawlins, WY 82301"),
       ("Rock Springs — Flying J #764", "650 Stagecoach Blvd, Exit 104 · ~mile 150 of the day. Love's #888 is 15 mi on at Green River (Exit 85) if you'd rather fuel at the Flaming Gorge decision point.", "Flying J Travel Center, 650 Stagecoach Blvd, Rock Springs, WY 82901"),
       ("Evanston — Pilot #141 (LAST before camp)", "289 Bear River Dr, Exit 6 (second Pilot at Exit 3). Nothing up UT-150 until Kamas, 78 mi over the top.", "Pilot Travel Center, 289 Bear River Dr, Evanston, WY 82930"),
     ],
     scenic=[
       ("Flaming Gorge — Red Canyon Overlook", "The optional big detour: 1,700-ft red-rock canyon over the reservoir from the 7,400-ft rim (visitor center + paved rim path, leashed dogs OK). +~100 mi / +2 hr vs direct.", pin(_q("Red Canyon Visitor Center, Dutch John, UT"))),
       ("Fort Bridger State Historic Site", "Quick I-80 leg-stretch (exit 34): 1840s trading post + parade grounds. Grounds are dog-friendly on leash.", pin(_q("Fort Bridger State Historic Site, WY"))),
       ("Bear River State Park — Evanston", "Right behind the Evanston rest area: captive bison + elk herds and flat riverside paths — a perfect 20-min Mochi stretch.", pin(_q("Bear River State Park, Evanston, WY"))),
     ],
     sleep_options=[
       ("Sulphur CG — 9,000 ft (RESERVE)", "The pick: high, dark spruce forest on the upper Bear River. recreation.gov ID 233361 — 9 of 21 sites open for Sun Aug 2 as of Jul 14. Book it today.", pin(_q("Sulphur Campground, Mirror Lake Highway, UT"))),
       ("Stillwater CG — 8,500 ft (reservable)", "Biggest of the corridor camps (21 sites, ID 232105, 17 open for Aug 2) — the easy fallback if Sulphur fills.", pin(_q("Stillwater Campground, Mirror Lake Highway, UT"))),
       ("East Fork Bear River CG — 8,400 ft", "Small (7 sites, ID 247366) right on the river. Reservable; 6 open for Aug 2 as of Jul 14.", pin(_q("East Fork Bear River Campground, UT"))),
       ("Bear River / Hayden Fork / Beaver View (FCFS)", "The no-reservation string along UT-150 — roll in and claim; Sundays empty out early afternoon.", pin(_q("Hayden Fork Campground, Mirror Lake Highway, UT"))),
     ],
   )),
 "Aug 3 (Mon)": dict(banner="TRAVEL", plan="Drive home, day 3 — over the High Uintas → Park City lunch → Bonneville → Angel Lake",
   wake="Uintas (van)", sleep="Angel Lake, NV (van)", miles="286", hrs="5.0", base="—",
   together=("Morning alpine hike (Ruth Lake, 10,200 ft), crest the byway at Bald Mountain Pass (10,715 ft), "
             "Provo River Falls pullout, then down to Kamas and a Park City patio lunch. I-80 west past Great Salt "
             "Lake, 15 minutes ON the Bonneville Salt Flats, and up NV-231 to a cirque-lake camp at 8,380 ft above "
             "Wells. See DRIVE PLAN."),
   mochi=("Ruth Lake + the falls pullouts: leashed, USFS, all good. Salt flats: quick photo only — ground can be "
          "95°F+ at midday, test with your hand. Angel Lake: leashed lakeshore loop, and NO potable water at the "
          "campground — his jugs got filled in Evanston."),
   notes=("RESERVE Angel Lake CG (recreation.gov ID 232015, $18): 9 of 25 sites open for Mon Aug 3 as of Jul 14. "
          "Dog-friendly motel fallbacks in Wells: Motel 6 (I-80 exit 352, pets free) or Sharon Motel. Wendover "
          "midday ~95°F — an A/C drive-through, not a stop (except the salt)."),
   route=["Bear River Campground, Mirror Lake Scenic Byway, UT","Kamas, UT","Park City, UT","Bonneville Salt Flats Rest Area, I-80, UT","West Wendover, NV","Wells, NV"],
   drive_plan=dict(
     summary=("286 mi · ~5.0 hr driving. Break camp 8:00 AM → Angel Lake ~5:05 PM. Ruth Lake hike first (TH ~13 mi "
              "south of camp at mile 35), then Kamas → Park City lunch → I-80 past SLC → salt-flats leg-stretch → "
              "Wendover fuel → Wells → 12 paved miles up NV-231 to Angel Lake. Fuel at Lake Point + Wendover keeps "
              "every gap under ~150 mi."),
     route_url=maps_route(["Bear River Campground, Mirror Lake Scenic Byway, UT","Kamas, UT","Park City, UT","West Wendover, NV","Wells, NV"]),
     route_label="Uintas → Kamas → Park City → Wendover → Wells → Angel Lake",
     rows=[
       dict(kind="depart", k="🌅 8:00 AM · Break camp",
            v="Coffee + pack — Ruth Lake trailhead is 13 mi south up the byway (mile marker ~35)."),
       dict(kind="leg", k="🥾 8:20–10:00 AM · Ruth Lake hike",
            v=("~2 mi RT, 10,200 ft, gentle — THE classic short Uintas hike. Granite bowls, wildflower meadows, "
               "leashed Mochi swims. Back on the road with Bald Mountain Pass (10,715 ft) + Provo River Falls "
               "pullouts in the next 12 miles."),
            url=pin(_q("Ruth Lake Trailhead, Mirror Lake Highway, UT"))),
       dict(kind="leg", k="Leg 1 · 10:00–11:05 AM",
            v="48 mi · Driver A — over the top and down UT-150 to Kamas, then UT-248 into Park City.",
            url=maps_route(["Ruth Lake Trailhead, Utah","Kamas, UT","Park City, UT"])),
       dict(kind="stop", k="🍽 LUNCH · Park City · ~11:05 AM–12:15 PM",
            v=("Main Street stroll + a dog patio: Atticus Coffee & Teahouse (738 Main St) or Este Pizza. "
               "('Bark City' keeps an official dog-patio list.)"),
            url=pin(_q("Atticus Coffee Books & Teahouse, 738 Main St, Park City, UT"))),
       dict(kind="leg", k="Leg 2 · 12:15–1:00 PM",
            v="~40 mi · Driver B — I-80 W past Salt Lake City to Lake Point (exit 99).",
            url=maps_route(["Park City, UT","Lake Point, UT"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP · Lake Point · ~1:00–1:20 PM",
            v="Flying J Travel Center, I-80 exit 99 (1605 N Sunset Rd area) — the big truck stop W of SLC. Fill here: it's 110 mi of desert to Wendover.",
            url=pin(_q("Flying J Travel Center, Lake Point, UT"))),
       dict(kind="leg", k="Leg 3 · 1:20–3:00 PM",
            v=("110 mi · Driver A — Great Salt Lake's south shore, then the long white causeway miles. Stop at the "
               "BONNEVILLE SALT FLATS rest area (~10 mi before Wendover): walk out on the salt, 15 min, hot — "
               "then done."),
            url=maps_route(["Lake Point, UT","Bonneville Salt Flats Rest Area, I-80, UT","West Wendover, NV"])),
       dict(kind="stop", k="⛽ FUEL · West Wendover · ~3:00–3:20 PM",
            v="Pilot #147, 1200 W Wendover Blvd (Exit 410), West Wendover — splash-and-go, A/C running for Mochi (~91°F out there).",
            url=pin(_q("Pilot Travel Center, 1200 W Wendover Blvd, West Wendover, NV 89883"))),
       dict(kind="leg", k="Leg 4 · 3:20–4:20 PM",
            v="59 mi · Driver B — Wendover → Wells (top up water jugs in town if needed).",
            url=maps_route(["West Wendover, NV","Wells, NV"])),
       dict(kind="leg", k="Leg 5 · 4:20–4:50 PM",
            v="13 mi · NV-231 — paved switchbacks 3,000 ft up the East Humboldt Range to the lake.",
            url=maps_route(["Wells, NV","Angel Lake, Wells, NV"])),
       dict(kind="arrive", k="🏁 ~4:50 PM · Arrive Angel Lake (8,380 ft)",
            v="Glacial cirque lake hanging over the desert. Camp dinner, lakeshore loop with Mochi, sunset + real stars. ~78°F days, low-40s nights."),
     ],
     fuel_options=[
       ("Lake Point — Flying J (PRIMARY)", "I-80 exit 99, W of SLC — fills the Evanston→Wendover gap (244 mi is over the van's range without it).", "Flying J Travel Center, Lake Point, UT"),
       ("West Wendover — Pilot #147", "1200 W Wendover Blvd, Exit 410 · ~mile 214 of the day.", "Pilot Travel Center, 1200 W Wendover Blvd, West Wendover, NV 89883"),
       ("Wells — Flying J / Love's (top-off)", "Both at the US-93 interchange (Exit 352): Flying J 156 US-93 S, Love's 157 S US-93 — right at the NV-231 turnoff. Optional — covers tomorrow to Elko with margin.", "Flying J Travel Center, 156 US 93, Wells, NV 89835"),
     ],
     scenic=[
       ("Provo River Falls", "Tiered roadside cascades at UT-150 mile ~24 — 5-min pullout, leashed dogs fine.", pin(_q("Provo River Falls Overlook, Mirror Lake Highway, UT"))),
       ("Bald Mountain Pass — 10,715 ft", "The byway's crest; Mirror Lake shimmers below. Photo pullout.", pin(_q("Bald Mountain Pass, Mirror Lake Highway, UT"))),
       ("Bonneville Salt Flats rest area", "Free I-80 rest area where you can walk on the salt — the land-speed-record flats. Test the ground before Mochi walks it.", pin(_q("Bonneville Salt Flats Rest Area, Interstate 80, Utah"))),
     ],
     sleep_options=[
       ("Angel Lake CG (RESERVE — the plan)", "recreation.gov ID 232015 · $18 · 8,380 ft · leashed dogs OK · NO potable water. 9 sites open for Mon Aug 3 as of Jul 14 — book now.", pin(_q("Angel Lake Campground, Wells, NV"))),
       ("Motel 6 Wells (dog fallback)", "I-80 exit 352 — 2 pets stay free. The zero-drama fallback if weather turns up top.", pin(_q("Motel 6, Wells, NV"))),
       ("Sharon Motel, Wells (dog fallback)", "Best-reviewed motel in town, $15/pet.", pin(_q("Sharon Motel, Wells, NV"))),
     ],
   )),
 "Aug 4 (Tue)": dict(banner="TRAVEL", plan="Drive home, day 4 — Ruby Mountains: Lamoille Canyon + lake hike → Winnemucca",
   wake="Angel Lake (van)", sleep="Winnemucca, NV (van)", miles="246", hrs="4.4", base="—",
   together=("The easy day. Coffee over the cirque, roll down to Elko, then 30 mi up NV-227 into LAMOILLE CANYON — "
             "Nevada's Yosemite, a 12-mi glacial byway into the Ruby Mountains. Hike Lamoille Lake (3 mi RT, "
             "9,740 ft) from Road's End, picnic, then two easy I-80 hours to Winnemucca by ~4 PM. Laundry, showers, "
             "resupply — staged for tomorrow's push home. See DRIVE PLAN."),
   mochi=("Lamoille Canyon trails: leashed dogs welcome; the lake is his. Stage 1 fire restrictions in the Rubies "
          "(fires only in developed rings — no ground fires at any picnic stop)."),
   notes=("Canyon road is fully rebuilt + open to Road's End (post-2018-fire repaving done). Elko ~90°F midday, "
          "canyon far cooler. Winnemucca is hot at street level — Water Canyon Rec Area above town is the cooler, "
          "prettier sleep."),
   route=["Angel Lake, Wells, NV","Elko, NV","Lamoille Canyon Scenic Byway, NV","Winnemucca, NV"],
   drive_plan=dict(
     summary=("246 mi · ~4.4 hr driving. Depart 8:30 AM → Winnemucca ~4:00 PM with a 2.5-hr canyon window. Angel "
              "Lake → Elko (fuel) → Lamoille Canyon Road's End → back to I-80 → Battle Mountain → Winnemucca "
              "(fuel). Longest fuel gap ~186 mi (Elko → Winnemucca incl. the canyon spur) — inside range, but top "
              "off properly at Elko."),
     route_url=maps_route(["Angel Lake, Wells, NV","Elko, NV","Lamoille Canyon Scenic Byway, NV","Winnemucca, NV"]),
     route_label="Angel Lake → Elko → Lamoille Canyon → Winnemucca",
     rows=[
       dict(kind="depart", k="🌅 8:30 AM · Depart",
            v="Down the NV-231 switchbacks, Driver A — desert floor in 25 minutes."),
       dict(kind="leg", k="Leg 1 · 8:30–9:35 AM",
            v="60 mi · Driver A — Angel Lake → Elko on I-80 W along the East Humboldts.",
            url=maps_route(["Angel Lake, Wells, NV","Elko, NV"])),
       dict(kind="stop", k="⛽ FUEL + ☕ · Elko · ~9:35–10:00 AM",
            v="Sinclair truck stop, 1790 Idaho St (Exit 301/303) — Elko has no Pilot/Love's/Flying J; this is the in-town diesel (NV name-brand = standard #2). Alt: Pilot in Carlin, 19 mi W on today's route. FILL FULL — next planned fuel is Winnemucca, 186 mi away including the canyon spur.",
            url=pin(_q("Sinclair, 1790 Idaho St, Elko, NV 89801"))),
       dict(kind="leg", k="Leg 2 · 10:00–10:45 AM",
            v="31 mi · Driver B — NV-227 through Spring Creek, then the Lamoille Canyon Scenic Byway (NF-660): 12 paved miles, 2,000 ft of glacier-carved walls, to Road's End (8,800 ft).",
            url=maps_route(["Elko, NV","Lamoille Canyon Scenic Byway, NV"])),
       dict(kind="leg", k="🥾 10:45 AM–1:15 PM · Lamoille Lake + picnic",
            v=("3 mi RT to the alpine lake at 9,740 ft (first leg of the Ruby Crest Trail) — leashed Mochi swims. "
               "Shorter option: the Nature Trail loop or Thomas Canyon walk if legs say so. Picnic at Road's End."),
            url=pin(_q("Lamoille Lake Trailhead, Road's End, Lamoille Canyon, NV"))),
       dict(kind="leg", k="Leg 3 · 1:15–3:50 PM",
            v="155 mi · Driver A — back down the canyon, I-80 W past Battle Mountain to Winnemucca. The big empty; podcast country.",
            url=maps_route(["Lamoille Canyon Scenic Byway, NV","Winnemucca, NV"])),
       dict(kind="stop", k="⛽ FUEL · Winnemucca · ~3:50–4:10 PM",
            v="Flying J #770, 1880 W Winnemucca Blvd (Exit 176) — Love's + Pilot cluster the same interchange. Fill TONIGHT — tomorrow departs 7:30 AM for the long push home.",
            url=pin(_q("Flying J Travel Center, 1880 W Winnemucca Blvd, Winnemucca, NV 89445"))),
       dict(kind="arrive", k="🏁 ~4:10 PM · Arrive Winnemucca",
            v="Early arrival on purpose: showers/laundry at an RV park or head up to Water Canyon for a cool, quiet night. Big day tomorrow."),
     ],
     fuel_options=[
       ("Elko — Sinclair, 1790 Idaho St (PRIMARY) (PRIMARY)", "In-town truck stop, Exit 301/303. Backup: Pilot Travel Center, 791 10th St, Carlin (Exit 280, 19 mi W — you drive past it today).", "Sinclair, 1790 Idaho St, Elko, NV 89801"),
       ("Winnemucca — Flying J #770 (evening fill)", "1880 W Winnemucca Blvd, Exit 176; alternates Love's (3575 W Winnemucca Blvd) + Pilot (Exit 173). Fill on arrival so the 7:30 AM start is wheels-up.", "Flying J Travel Center, 1880 W Winnemucca Blvd, Winnemucca, NV 89445"),
     ],
     scenic=[
       ("Lamoille Canyon Scenic Byway", "The 12-mi drive itself is the sight — 'Nevada's Yosemite.' Multiple pullouts with interpretive signs.", pin(_q("Lamoille Canyon Scenic Byway, NV"))),
       ("California Trail Interpretive Center", "Free BLM museum right on I-80 W of Elko (exit 292) — a genuinely good 30-min stop if the hike ran short.", pin(_q("California Trail Interpretive Center, Elko, NV"))),
     ],
     sleep_options=[
       ("Water Canyon Recreation Area (BLM)", "6 mi SE above town up Water Canyon Rd — free/cheap sites in the cottonwoods, noticeably cooler than town. Verify current status on iOverlander.", pin(_q("Water Canyon Recreation Area, Winnemucca, NV"))),
       ("New Frontier RV Park (hookups + showers)", "In-town full-service option — showers + laundry before the final push.", pin(_q("New Frontier RV Park, Winnemucca, NV"))),
     ],
   )),
 "Aug 5 (Wed)": dict(banner="TRAVEL", plan="Drive home, day 5 — the push: Winnemucca → Tahoe dog swim → HOME by evening",
   wake="Winnemucca (van)", sleep="🏠 HOME — Redwood City (fallback: Truckee)", miles="432", hrs="6.9", base="—",
   together=("The one long day, front-loaded early to buy slack. Desert I-80 to Sparks (fuel + swap), over Donner "
             "Summit to Truckee — GO/NO-GO check at 11 AM — then Mochi's victory swim at Kings Beach's Coon Street "
             "Dog Beach, lunch, and the last 225 mi: Davis fuel stop (the Jul 17 station, reversed) and home to "
             "Redwood City ~5:30–6:30 PM. See DRIVE PLAN."),
   mochi=("Coon Street Dog Beach (east end of Kings Beach) is his payoff — designated dog beach, leash rules "
          "posted. NOT the main Kings Beach sand (no dogs) and NOT Donner's West End Beach (no dogs)."),
   notes=("FALLBACK IS BUILT IN: if the 11 AM Truckee check says 'tired', stay the night (options below) and be "
          "home Aug 6 by ~noon — that's still inside the window. ⚠️ Smoke check: the Elephant Fire (~20 mi N of "
          "Truckee, 12,300 ac on Jul 14, evac orders) — glance at InciWeb/Tahoe NF alerts on Aug 1; if the basin is "
          "smoky, skip the beach and push straight through. Sacramento Valley ~90–100°F midday = A/C stretch."),
   route=["Winnemucca, NV","Sparks, NV","Truckee, CA","Kings Beach, CA","1601 Research Park Dr, Davis, CA 95616","Redwood City, CA"],
   drive_plan=dict(
     summary=("432 mi · ~6.9 hr driving — the trip's longest day, split by a lake swim. Depart 7:30 AM (not the "
              "usual 9 — this is the one early start) → home ~5:30–6:30 PM. Winnemucca → Sparks → Truckee "
              "(GO/NO-GO) → Kings Beach → Davis → Redwood City. Fuel Sparks + Davis; two driver swaps at the "
              "stops."),
     route_url=maps_route(["Winnemucca, NV","Sparks, NV","Truckee, CA","Kings Beach, CA","1601 Research Park Dr, Davis, CA 95616","Redwood City, CA"]),
     route_label="Winnemucca → Sparks → Truckee → Kings Beach → Davis → HOME",
     rows=[
       dict(kind="depart", k="🌅 7:30 AM · Depart (early — on purpose)",
            v="Winnemucca, Driver A, tank already full from last night. Every minute here is beach time later."),
       dict(kind="leg", k="Leg 1 · 7:30–9:50 AM",
            v="161 mi · Driver A — Winnemucca → Sparks through Lovelock + Fernley: the fast, empty desert miles, cool morning air.",
            url=maps_route(["Winnemucca, NV","Sparks, NV"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP + ☕ · Sparks · ~9:50–10:15 AM",
            v="TA Reno, 200 N McCarran Blvd, Sparks (Exit 19) — in NEVADA the truck-stop chains still pump standard #2. Last cheap non-California diesel — fill full.",
            url=pin(_q("TA Travel Center, 200 N McCarran Blvd, Sparks, NV 89431"))),
       dict(kind="leg", k="Leg 2 · 10:15–10:55 AM",
            v="35 mi · Driver B — Sparks → Truckee up the Truckee River canyon and over the state line.",
            url=maps_route(["Sparks, NV","Truckee, CA"])),
       dict(kind="stop", k="🧭 ~11:00 AM · TRUCKEE — GO / NO-GO",
            v=("THE decision point. Fresh + on time → carry on below (home tonight). Dragging, or the basin is "
               "smoky → flip to the fallback: beach or not, sleep Truckee/Donner (options below), Squeeze In or "
               "Jax at the Tracks breakfast tomorrow, home Aug 6 by ~noon. Both outcomes are inside the plan.")),
       dict(kind="leg", k="Leg 3 · 11:00–11:20 AM",
            v="12 mi · CA-267 over Brockway Summit to Kings Beach.",
            url=maps_route(["Truckee, CA","Kings Beach, CA"])),
       dict(kind="leg", k="🐕 11:20 AM–12:45 PM · Coon Street Dog Beach + lunch",
            v=("Foot of Coon St, east end of Kings Beach — the designated dog beach (picnic tables, restrooms, "
               "paid parking at the boat launch). Mochi's Tahoe swim, humans' sandwich lunch at the tables — or "
               "quick patio takeout in town."),
            url=pin(_q("Coon Street Dog Beach, Kings Beach, CA"))),
       dict(kind="leg", k="Leg 4 · 12:45–3:05 PM",
            v="127 mi · Driver B — back over CA-267 to I-80 W, over Donner Summit, down through Sacramento (hot valley, A/C on) to Davis.",
            url=maps_route(["Kings Beach, CA","1601 Research Park Dr, Davis, CA 95616"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP · Davis · ~3:05–3:25 PM",
            v=("1601 Research Park Dr — the SAME user-confirmed clean-#2 station as the Jul 17 drive out, run in "
               "reverse. It's the CHEVRON at the Richards Blvd interchange — CA Chevron retail diesel is petroleum CARB ULSD (≤B5), R99 risk LOW. Still glance for a 'Renewable/R99/HPR' pump decal before filling; Chevron 4475 Chiles Rd + Shell 1010 Olive Dr are same-town fallbacks."),
            url=pin(_q("1601 Research Park Dr, Davis, CA 95616"))),
       dict(kind="leg", k="Leg 5 · 3:25–5:15 PM",
            v="97 mi · Driver A (fresh for Bay traffic) — Davis → Redwood City. Add buffer: Bay evening traffic decides the exact landing time.",
            url=maps_route(["1601 Research Park Dr, Davis, CA 95616","Redwood City, CA"])),
       dict(kind="arrive", k="🏁 ~5:30–6:30 PM · HOME — Redwood City",
            v="1,440 mi from Boulder in five days, every night at elevation, zero interstate motels. Unpack tomorrow. 🎉"),
     ],
     fuel_options=[
       ("Sparks — TA Reno / Sparks (PRIMARY) (PRIMARY)", "200 N McCarran Blvd, Exit 19 · ~mile 190. Earlier option: Love's Fernley (Exit 46). ⚠️ In CALIFORNIA avoid Pilot/Flying J/Love's/TA/76/ARCO — many pump R99 renewable there; the van needs petroleum #2.", "TA Travel Center, 200 N McCarran Blvd, Sparks, NV 89431"),
       ("Davis — 1601 Research Park Dr (PRIMARY, CA)", "The user-confirmed clean-#2 stop from Jul 17, ~mile 335 — leaves 97 mi home. It's a Chevron — CA petroleum ULSD; glance for an R99 decal anyway.", "1601 Research Park Dr, Davis, CA 95616"),
       ("Auburn — 13405 Lincoln Way (alt)", "The other user-confirmed CA station (~mile 280) — use it if you want the fill before Sacramento instead of after.", "13405 Lincoln Way, Auburn, CA 95603"),
     ],
     scenic=[
       ("Donner Summit / Donner Lake overlook", "Rainbow Bridge + the classic lake-from-above pullout on old US-40, 5 min off I-80 if the day is running easy.", pin(_q("Donner Summit Bridge, Truckee, CA"))),
     ],
     sleep_options=[
       ("FALLBACK — Coachland RV Park, Truckee", "In-town hookups; the easy roll-in if the NO-GO call gets made.", pin(_q("Coachland RV Park, Truckee, CA"))),
       ("FALLBACK — Donner Memorial SP campground", "Forested state-park camp by the lake — check same-day availability on ReserveCalifornia.", pin(_q("Donner Memorial State Park Campground, Truckee, CA"))),
     ],
   )),
 "Aug 14 (Fri)": dict(banner="TRAVEL", plan="Drive Ely → Mammoth Lakes",
   wake="Ely, NV", sleep="Mammoth Lakes", miles="293", hrs="4.75", base="—",
   together="AM: optional self-guided walk through the Nevada Northern Railway Museum (opens 8am). Then the remote US-6 run to Mammoth — must arrive by afternoon. Emily bach party in the Mammoth area. See DRIVE PLAN.",
   notes=("⚠️ FUEL IS THE WHOLE GAME: the Ely→Tonopah leg is ~168 mi with ZERO services — against the van's ~200 mi "
          "range, leaving Ely with a FULL tank is NON-NEGOTIABLE. Tonopah is the only real fuel before the US-395 "
          "corridor; top off again in Bishop before the climb to Mammoth. Expect long cell dead zones + high-desert "
          "heat — carry water. NV name-brand 'Diesel' ≈ #2; CA = name-brand pumps only (B5 standard)."),
   route=["Ely, NV","Tonopah, NV","Bishop, CA","Mammoth Lakes, CA"], dining="Mammoth",
   drive_plan=dict(
     summary=("293 mi · ~4h50m driving. After a self-guided AM walk through the NNR Museum (opens 8am; the steam "
              "ride is 4:30pm Fri — too late), depart Ely ~9:30 AM → arrive Mammoth ~3:30 PM. US-6 W across central "
              "Nevada through Tonopah → past Coaldale + Benton → the US-6/US-395 jct N of Bishop → US-395 N → CA-203 "
              "into Mammoth. ⚠️ The Ely→Tonopah leg is ~168 mi with NO services — leave Ely FULL. Refill at Tonopah, "
              "top off in Bishop."),
     route_url=maps_route(["Ely, NV","Tonopah, NV","Bishop, CA","Mammoth Lakes, CA"]),
     route_label="Ely → Tonopah → (Benton) → Bishop → Mammoth Lakes",
     rows=[
       dict(kind="depart", k="🚂 ~8:00–9:00 AM · NNR Museum (optional)",
            v="Self-guided walk through the 1906 steam yard + roundhouse before leaving Ely (1100 Ave A)."),
       dict(kind="depart", k="🌅 ~9:30 AM · Depart Ely · FULL TANK",
            v="Driver A, topped off at the Love's. Do NOT leave Ely below full — the next leg has zero fuel for ~168 mi."),
       dict(kind="leg", k="Leg 1 · 9:30 AM–12:10 PM",
            v="168 mi · US-6 W across the high desert to Tonopah. ⚠️ NO services the entire leg — no fuel, no reliable cell, nothing. This is the leg the whole day is planned around. Driver A.",
            url=maps_route(["Ely, NV","Tonopah, NV"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP + 🍽 LUNCH · Tonopah · ~12:10–12:45 PM",
            v="Love's #857, 1170 US-95 (US-95/US-6) — the only real fuel between Ely and the US-395 corridor. Fill, swap to Driver B, lunch.",
            url=pin(_q("Love's Travel Stop, 1170 US Highway 95, Tonopah, NV 89049"))),
       dict(kind="leg", k="Leg 2 · 12:45–2:40 PM",
            v="116 mi · US-6 W past Coaldale Jct (no fuel) + Benton, CA to the US-6/US-395 jct ~5 mi N of Bishop, then US-395 S into Bishop. Driver B.",
            url=maps_route(["Tonopah, NV","Bishop, CA"])),
       dict(kind="stop", k="⛽ TOP-OFF · Bishop · ~2:40–2:55 PM",
            v="Chevron, 2392 N Sierra Hwy (or Shell, 1290 N Main St). Top off before the sustained Sherwin-Grade climb to Mammoth. Swap to Driver A.",
            url=pin(_q("Chevron, 2392 N Sierra Highway, Bishop, CA 93514"))),
       dict(kind="leg", k="Leg 3 · 3:00–3:45 PM",
            v="40 mi · US-395 N up Sherwin Grade past Crowley Lake → CA-203 W (Minaret Rd) into Mammoth Lakes (~7,900 ft). Driver A.",
            url=maps_route(["Bishop, CA","Mammoth Lakes, CA"])),
       dict(kind="arrive", k="🏁 ~3:45 PM · Arrive", v="Mammoth Lakes — Emily bach party in the area. (Aug 15–17 are flexible — see the Mammoth DAY OPTIONS menu.)"),
     ],
     fuel_options=[
       ("Ely — Love's #691 (FILL FULL)", "1701 Great Basin Blvd · mile 0, 24 hr. Non-negotiable: leave Ely at 100% — the next leg is ~168 mi with zero services. Confirmed clean #2.", "Love's Travel Stop, 1701 Great Basin Blvd, Ely, NV 89301"),
       ("Tonopah — Love's #857 (mid-route)", "1170 US-95 · ~mile 168, 24 hr. The ONLY real fuel between Ely and US-395 — your fuel + swap + lunch stop.", "Love's Travel Stop, 1170 US Highway 95, Tonopah, NV 89049"),
       ("Bishop — Chevron / Shell (top-off)", "2392 N Sierra Hwy / 1290 N Main St · ~mile 284. Top off before the climb to Mammoth (CA = name-brand pumps, B5 standard).", "Chevron, 2392 N Sierra Highway, Bishop, CA 93514"),
     ],
     scenic=[
       ("Nevada Northern Railway Museum", "1100 Ave A, Ely — National Historic Landmark steam-era rail yard; self-guided AM walk before departure.", pin(_q("Nevada Northern Railway, 1100 Avenue A, Ely, NV"))),
       ("Tonopah Historic Mining Park", "Silver-boom headframes + tunnels above town — an easy 30-min stop at the fuel/lunch break.", pin(_q("Tonopah Historic Mining Park, Tonopah, NV"))),
       ("The Clown Motel, Tonopah", "'America's Scariest Motel' (6,000+ clowns) next to the 1900s mining cemetery — a quick, weird photo stop on US-6.", pin(_q("Clown Motel, Tonopah, NV"))),
       ("Boundary Peak / White Mountains", "Nevada's highest point (13,147 ft), dramatic on the skyline as US-6 nears the CA line.", pin(_q("Boundary Peak, Nevada"))),
       ("Benton Hot Springs", "Tiny 1850s stage town with private soaking tubs, just off US-6 near the CA junction.", pin(_q("Benton Hot Springs, CA"))),
     ],
   )),
 # Aug 15–17 (Mammoth) are now FLEXIBLE — see the MAM-A..D options + the Mammoth DAY OPTIONS menu.
 "Aug 18 (Tue)": dict(banner="TRAVEL", plan="Drive Tioga Pass → Fresno area (drop Mochi at boarding)",
   wake="Mammoth Lakes", sleep="Near Fresno", miles="200", hrs="5", base="—",
   together="Scenic drive over Tioga Pass through Yosemite, then down to drop Mochi at Fresno-area boarding before the Rae Lakes Loop. See DRIVE PLAN.",
   notes=("⚠️ This is ~200 mi / ~5 hr of DRIVING (not the short hop it looks like — Tioga + Yosemite are slow); "
          "budget 6–7 hr door-to-kennel with the gate + stops. FILL THE VAN FULL IN MAMMOTH before leaving — a "
          "full tank covers the whole leg, so you skip the limited/seasonal in-park pumps. Yosemite 2026: NO "
          "timed-entry reservation required — just $35/vehicle at the Tioga (east) gate. Mochi boards through the "
          "Rae Lakes Loop at Elaine's Pet Resorts, 3912 N Hayston Ave, Fresno (559) 227-5959 — book ahead for Aug + "
          "bring vaccination records."),
   route=["Mammoth Lakes, CA","Lee Vining, CA","Yosemite Valley, CA","Oakhurst, CA","Fresno, CA"],
   drive_plan=dict(
     summary=("~200 mi · ~5 hr driving (Tioga + Yosemite are slow — budget 6–7 hr door-to-kennel). FILL FULL in "
              "Mammoth first — a full tank covers the whole leg, so you skip the limited in-park pumps. CA-203 → "
              "US-395 N to Lee Vining → CA-120 W over Tioga Pass (9,943 ft) through Yosemite → Yosemite Valley → "
              "CA-41 S (Wawona) → Oakhurst → Fresno. Yosemite 2026: NO reservation needed — pay $35/vehicle at the "
              "Tioga gate. Depart ~7:30–8:00 AM → reach Elaine's Pet Resorts ~1:30–3:00 PM, inside kennel hours."),
     route_url=maps_route(["Mammoth Lakes, CA","Lee Vining, CA","Yosemite Valley, CA","Oakhurst, CA","Fresno, CA"]),
     route_label="Mammoth → Lee Vining → Tioga/Yosemite → Oakhurst → Fresno",
     rows=[
       dict(kind="depart", k="🚐 ~7:30–8:00 AM · Depart · FULL TANK",
            v="Mammoth, Driver A. Fill the van completely in town first — there's no reliable fuel once you're on Tioga Rd."),
       dict(kind="leg", k="Leg 1 · ~30 min",
            v="27 mi · CA-203 → US-395 N to Lee Vining (jct CA-120). Driver A.",
            url=maps_route(["Mammoth Lakes, CA","Lee Vining, CA"])),
       dict(kind="stop", k="⛽ LAST EAST-SIDE FUEL (if not full) · Lee Vining",
            v="Tioga Gas Mart / 'The Mobil', 22 Vista Point Rd (jct US-395 & CA-120). Top off here if you skipped Mammoth — after this there's essentially no fuel until Crane Flat.",
            url=pin(_q("Tioga Gas Mart, 22 Vista Point Road, Lee Vining, CA 93541"))),
       dict(kind="leg", k="Leg 2 · ~2.5 hr (incl. gate + scenic)",
            v="74 mi · CA-120 W over Tioga Pass (9,943 ft) through Yosemite high country → Crane Flat → into Yosemite Valley. Pay $35 at the Tioga gate. Slow + winding + summer congestion; swap drivers at Olmsted Point / Tenaya Lake. Only in-park fuel = Crane Flat Chevron (backstop only). Driver B from here.",
            url=maps_route(["Lee Vining, CA","Yosemite Valley, CA"])),
       dict(kind="leg", k="Leg 3 · ~1.5 hr",
            v="~50 mi · CA-41 S (Wawona Rd) out the south end — over Chinquapin, through Wawona, down to Oakhurst. Optional fuel/lunch at the Oakhurst Chevron. Driver B.",
            url=maps_route(["Yosemite Valley, CA","Oakhurst, CA"])),
       dict(kind="leg", k="Leg 4 · ~50 min",
            v="~50 mi · CA-41 S through Coarsegold, descending into the Central Valley to Fresno.",
            url=maps_route(["Oakhurst, CA","Fresno, CA"])),
       dict(kind="stop", k="🐕 DROP MOCHI · ~1:30–3:00 PM · Elaine's Pet Resorts",
            v="3912 N Hayston Ave, Fresno · (559) 227-5959. Boards through the Rae Lakes Loop. Arrive mid-afternoon to clear check-in before close; vaccination records required.",
            url=pin(_q("Elaine's Pet Resorts, 3912 N Hayston Ave, Fresno, CA 93726"))),
       dict(kind="arrive", k="🏁 Settle near Fresno", v="Stage for the early-morning drive to the Rae Lakes trailhead tomorrow."),
     ],
     fuel_options=[
       ("Mammoth Lakes — FILL FULL (PRIMARY)", "Top off in town before departure (multiple name-brand stations) — a full tank covers the whole ~200 mi leg, so you never gamble on the in-park pumps.", "Mammoth Lakes, CA 93546"),
       ("Lee Vining — Tioga Gas Mart / Mobil", "22 Vista Point Rd (jct US-395 & CA-120). Last reliable diesel EAST of Tioga — top off if you didn't fill in Mammoth.", "Tioga Gas Mart, 22 Vista Point Road, Lee Vining, CA 93541"),
       ("Crane Flat — Chevron (in-park backstop)", "8028 Big Oak Flat Rd, Yosemite NP — 24/7 pay-at-pump, the ONLY reliable fuel between Lee Vining + the valley. Emergency only.", "Crane Flat Chevron, 8028 Big Oak Flat Road, Yosemite National Park, CA 95389"),
       ("Oakhurst — Chevron (exit-side)", "40219 CA-41 — good final top-off before Fresno. (Avoid the Mariposa Chevron on CA-140 — its diesel is B20.)", "Chevron, 40219 CA-41, Oakhurst, CA 93644"),
     ],
     scenic=[
       ("Tioga Lake", "Alpine lake right beside CA-120 just W of the entrance (~9,650 ft) — quick roadside stop.", pin(_q("Tioga Lake, Yosemite, CA"))),
       ("Tuolumne Meadows", "Vast subalpine meadow; roadside pullouts + seasonal store. Mochi can stretch in the paved lot only.", pin(_q("Tuolumne Meadows, Yosemite, CA"))),
       ("Olmsted Point", "Dramatic granite overlook toward Half Dome / Clouds Rest — a paved viewpoint + good driver-swap spot.", pin(_q("Olmsted Point, Yosemite, CA"))),
       ("Tenaya Lake", "Large alpine lake right off the road with a granite backdrop — roadside/parking-area only with the dog.", pin(_q("Tenaya Lake, Yosemite, CA"))),
     ],
   )),
 "Aug 19 (Wed)": dict(banner="FIXED — BACKPACK START", plan="Drive to trailhead + start Rae Lakes Loop",
   wake="Fresno area", sleep="Trail", miles="100", hrs="2.0", base="Kings Canyon trailhead",
   together="Drive to the trailhead and START the Rae Lakes Loop. Must start today. OK to start before lunch.",
   notes="Permits already secured. Both Ian & Anny; Mochi boarded in Fresno (no dogs in NP backcountry)."),
 "Aug 20 (Thu)": dict(banner="FIXED — BACKPACK", plan="Rae Lakes Loop — hiking",
   wake="Trail", sleep="Trail", miles="0", hrs="0", base="Backcountry",
   together="Rae Lakes Loop — day 2.", notes="No cell service. Bear canisters required."),
 "Aug 21 (Fri)": dict(banner="FIXED — BACKPACK", plan="Rae Lakes Loop — hiking",
   wake="Trail", sleep="Trail", miles="0", hrs="0", base="Backcountry",
   together="Rae Lakes Loop — day 3 (Glen Pass / Rae Lakes).", notes="No cell service."),
 "Aug 22 (Sat)": dict(banner="FIXED — BACKPACK", plan="Rae Lakes Loop — hiking",
   wake="Trail", sleep="Trail", miles="0", hrs="0", base="Backcountry",
   together="Rae Lakes Loop — day 4.", notes="No cell service."),
 "Aug 23 (Sun)": dict(banner="FIXED — BACKPACK END", plan="Finish Rae Lakes Loop",
   wake="Trail", sleep="Van near Fresno", miles="0", hrs="0", base="Backcountry → Fresno",
   together="Finish the loop, hike out to the trailhead, drive to the van near Fresno. Pick up Mochi if boarding hours allow, else tomorrow AM.",
   notes="Hike end."),
 "Aug 24 (Mon)": dict(banner="TRAVEL — HOME", plan="Drive home",
   wake="Van near Fresno", sleep="Home", miles="175", hrs="3.0", base="—",
   together="Pick up Mochi at Elaine's, then the final drive home. Trip complete. See DRIVE PLAN.",
   notes=("~175 mi / ~3 hr — well under the ~200 mi range, so no fuel stop needed on a full tank (optional top-off "
          "+ swap at the Gilroy Shell where CA-152 meets US-101). A ~9:30 AM pickup gets you home ~1:00–1:30 PM, "
          "ahead of the Bay Area PM commute. CA = name-brand pumps only (B5 standard)."),
   route=["Fresno, CA","Los Banos, CA","Gilroy, CA","Redwood City, CA"],
   drive_plan=dict(
     summary=("~175 mi · ~3 hr driving — the final leg. After picking Mochi up at Elaine's Pet Resorts (Fresno), "
              "depart ~9:30 AM → home ~1:00–1:30 PM, ahead of the Bay Area PM commute. CA-99 N → CA-152 W over "
              "Pacheco Pass (past San Luis Reservoir) → US-101 N up the Peninsula. Well under the ~200 mi range — "
              "no fuel stop needed on a full tank; optional top-off + driver swap at the Gilroy Shell where CA-152 "
              "meets US-101."),
     route_url=maps_route(["Fresno, CA","Los Banos, CA","Gilroy, CA","Redwood City, CA"]),
     route_label="Fresno → Los Banos → Pacheco Pass → Gilroy → Redwood City",
     rows=[
       dict(kind="stop", k="🐕 PICK UP MOCHI · ~9:00 AM · Elaine's Pet Resorts",
            v="3912 N Hayston Ave, Fresno · (559) 227-5959 (Mon 7am–6pm). Collect Mochi, then roll.",
            url=pin(_q("Elaine's Pet Resorts, 3912 N Hayston Ave, Fresno, CA 93726"))),
       dict(kind="depart", k="🚐 ~9:30 AM · Depart", v="Fresno, Driver A."),
       dict(kind="leg", k="Leg 1 · 9:30–10:50 AM",
            v="~75 mi · CA-99 N to the Chowchilla/Madera area → CA-152 W toward Los Banos (flat, fast valley miles). Driver A.",
            url=maps_route(["Fresno, CA","Los Banos, CA"])),
       dict(kind="leg", k="Leg 2 · 10:50–11:45 AM",
            v="~45 mi · CA-152 W over Pacheco Pass past San Luis Reservoir, down to the US-101 junction at Gilroy. Driver A.",
            url=maps_route(["Los Banos, CA","Gilroy, CA"])),
       dict(kind="stop", k="🔄 SWAP (+ optional ⛽ top-off) · Gilroy · ~11:45 AM",
            v="Shell, 850 Pacheco Pass Hwy (right at the CA-152/US-101 jct), 24 hr. Swap to fresh Driver B for the Peninsula freeway miles; top off if you want. Stretch Mochi here / at Casa de Fruta.",
            url=pin(_q("Shell, 850 Pacheco Pass Highway, Gilroy, CA 95020"))),
       dict(kind="leg", k="Leg 3 · 12:00–1:15 PM",
            v="~55 mi · US-101 N through Morgan Hill + San Jose up the Peninsula to Redwood City. Driver B.",
            url=maps_route(["Gilroy, CA","Redwood City, CA"])),
       dict(kind="arrive", k="🏁 ~1:00–1:30 PM · Home", v="Trip complete. 🎉"),
     ],
     fuel_options=[
       ("Gilroy — Shell (optional top-off + swap)", "850 Pacheco Pass Hwy at the CA-152/US-101 jct · ~mile 120, 24 hr. Coincides with the driver swap — fewer stops on the last day. Name-brand clean #2.", "Shell, 850 Pacheco Pass Highway, Gilroy, CA 95020"),
       ("Los Banos — Chevron ExtraMile (midpoint)", "1164 E Pacheco Blvd · ~mile 75, 24 hr. The earlier midpoint option on CA-152.", "Chevron, 1164 E Pacheco Blvd, Los Banos, CA 93635"),
     ],
     scenic=[
       ("Romero Overlook / San Luis Reservoir", "On CA-152 at Pacheco Pass — sweeping reservoir view, a quick photo-and-stretch (dog OK on leash in the lot).", pin(_q("Romero Overlook Visitor Center, CA"))),
       ("Casa de Fruta", "10021 Pacheco Pass Hwy, ~2 mi E of the 152/156 jct — classic roadside stop with food, restrooms + dog-friendly grounds.", pin(_q("Casa de Fruta, 10021 Pacheco Pass Highway, Hollister, CA 95023"))),
     ],
   )),
}

# order of fixed tabs (chronological, for placement)
FIXED_ORDER = list(FIXED.keys())

# which calendar dates are flexible (Itinerary date cell -> DAY OPTIONS)
FLEX_DATES = (["Jul %d" % d for d in range(23,32)] +
              ["Aug 15","Aug 16","Aug 17"])

# ════════════════════════════════════════════════════════════════════════════════
#  TAB BUILDER
# ════════════════════════════════════════════════════════════════════════════════
class Tab:
    def __init__(self):
        self.V=[]; self.F=[]; self.M=[]; self.H=[]; self.L=[]
    def row(self, cells, h=None):
        norm=[c if isinstance(c,tuple) else (c,None) for c in cells]
        r=len(self.V)
        self.V.append([t for t,_ in norm]+[""]*(NCOLS-len(norm)))
        for ci,(t,link) in enumerate(norm):
            if link and t: self.L.append((r,ci,t,link))
        if h: self.H.append((r,h))
        return r
    def fmt(self,r,c0,c1,bg=None,fg=None,bold=False,size=None,align=None,wrap=True,valign="MIDDLE",italic=False):
        cf={}
        if bg is not None: cf["backgroundColor"]=bg
        tf={"bold":bold}
        if fg is not None: tf["foregroundColor"]=fg
        if size is not None: tf["fontSize"]=size
        if italic: tf["italic"]=True
        cf["textFormat"]=tf
        if align: cf["horizontalAlignment"]=align
        cf["verticalAlignment"]=valign; cf["wrapStrategy"]="WRAP" if wrap else "OVERFLOW_CELL"
        self.F.append((r,c0,c1,cf))
    def mg(self,r,c0=0,c1=NCOLS): self.M.append((r,c0,c1))

    # high-level rows
    def title(self,t): r=self.row([t]); self.mg(r); self.fmt(r,0,NCOLS,bg=TITLE_BG,fg=WHITE,bold=True,size=15,align="LEFT"); self.H.append((r,40))
    def subtitle(self,t): r=self.row([t]); self.mg(r); self.fmt(r,0,NCOLS,bg=SUB_BG,fg=WHITE,bold=True,size=11,align="LEFT"); self.H.append((r,30))
    def banner(self,t,bg): r=self.row([t]); self.mg(r); self.fmt(r,0,NCOLS,bg=bg,fg=WHITE,bold=True,size=10,align="LEFT"); self.H.append((r,24))
    def ctx(self,t): r=self.row([t]); self.mg(r); self.fmt(r,0,NCOLS,bg=WHITE,fg=GREY,italic=True,align="LEFT",wrap=True); self.H.append((r,34))
    def spacer(self,px=10): r=self.row([""]); self.H.append((r,px))
    def section(self,t): r=self.row([t]); self.mg(r); self.fmt(r,0,NCOLS,bg=SEC_BG,fg=DARK,bold=True,size=10,align="LEFT"); self.H.append((r,26))
    def kv(self,k,v,bg=KEY_BG,vbg=WHITE,link=None,vfg=DARK):
        r=self.row([k,(v,link)]); self.mg(r,1,NCOLS)
        self.fmt(r,0,1,bg=bg,fg=DARK,bold=True,align="LEFT",valign="TOP",wrap=True)
        self.fmt(r,1,NCOLS,bg=vbg,fg=(LINKC if link else vfg),align="LEFT",valign="TOP",wrap=True)
        self.H.append((r,max(24,22+22*(max(len(v)//95,0)))))
    def lane(self,icon,who,text,bg):
        r=self.row([f"{icon} {who}",text]); self.mg(r,1,NCOLS)
        self.fmt(r,0,1,bg=bg,fg=DARK,bold=True,align="LEFT",valign="TOP")
        self.fmt(r,1,NCOLS,bg=WHITE,fg=DARK,align="LEFT",valign="TOP",wrap=True)
        self.H.append((r,max(26,22+20*(max(len(text)//95,0)))))

    def activity_block(self,key):
        a=ACT[key]
        r=self.row([a["name"]]); self.mg(r); self.fmt(r,0,NCOLS,bg=ALT,fg=DARK,bold=True,align="LEFT"); self.H.append((r,24))
        self.kv("Trailhead", a["th"], bg=WHITE, link=a.get("pin"))
        meta=[]
        if a.get("stats"): meta.append(a["stats"])
        if a.get("drive"): meta.append("drive "+a["drive"])
        if a.get("dog"): meta.append("dogs: "+a["dog"])
        self.kv("Details", "  ·  ".join(meta), bg=WHITE)
        if a.get("link"): self.kv("Trail map", a.get("linklabel","Open trail map"), bg=WHITE, link=a["link"])
        if a.get("nearhike"): self.kv("Anny hikes nearby", a["nearhike"], bg=WHITE)
        if a.get("note"): self.kv("Note", a["note"], bg=WHITE, vfg=GREY)

    def bullet(self, text):
        r=self.row(["•  "+text]); self.mg(r)
        self.fmt(r,0,NCOLS,bg=WHITE,fg=DARK,align="LEFT",valign="TOP",wrap=True)
        self.H.append((r,max(24,20+18*(len(text)//95))))
    def callout(self, text, bg=WARN):
        r=self.row([text]); self.mg(r)
        self.fmt(r,0,NCOLS,bg=bg,fg=DARK,align="LEFT",valign="TOP",wrap=True)
        self.H.append((r,max(34,22+18*(len(text)//95))))
    def pick(self, name, note, link=None, bg=KEY_BG):
        r=self.row([(name,link), note]); self.mg(r,1,NCOLS)
        self.fmt(r,0,1,bg=bg,fg=(LINKC if link else DARK),bold=True,align="LEFT",valign="TOP",wrap=True)
        self.fmt(r,1,NCOLS,bg=WHITE,fg=DARK,align="LEFT",valign="TOP",wrap=True)
        lines=max(len(note)//88, len(name)//18, 0)
        self.H.append((r,max(30,24+18*lines)))
    def maplink(self, label, url):
        r=self.row([(label,url)]); self.mg(r)
        self.fmt(r,0,NCOLS,bg=MAPBTN_BG,fg=LINKC,bold=True,size=11,align="CENTER")
        self.H.append((r,32))
    def genmarker(self):
        """Visible per-page contract: tells a human this tab is generated + edit-tracked."""
        self.spacer(6)
        r=self.row([genmeta.marker_text()]); self.mg(r)
        self.fmt(r,0,NCOLS,bg=ALT,fg=GREY,italic=True,size=8,align="LEFT",wrap=True)
        self.H.append((r,30))

def _write_content(sid, title, tab):
    """Write values + formatting + links into the sheet with id `sid` (named `title`)."""
    sh.values_update(f"'{title}'!A1", params={"valueInputOption":"USER_ENTERED"}, body={"values":tab.V})
    reqs=[]
    # column A width
    reqs.append({"updateDimensionProperties":{"range":{"sheetId":sid,"dimension":"COLUMNS","startIndex":0,"endIndex":1},"properties":{"pixelSize":150},"fields":"pixelSize"}})
    reqs.append({"updateDimensionProperties":{"range":{"sheetId":sid,"dimension":"COLUMNS","startIndex":1,"endIndex":NCOLS},"properties":{"pixelSize":92},"fields":"pixelSize"}})
    for (r,c0,c1,cf) in tab.F:
        reqs.append({"repeatCell":{"range":{"sheetId":sid,"startRowIndex":r,"endRowIndex":r+1,"startColumnIndex":c0,"endColumnIndex":c1},
            "cell":{"userEnteredFormat":cf},"fields":"userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)"}})
    for (r,c0,c1) in tab.M:
        reqs.append({"mergeCells":{"range":{"sheetId":sid,"startRowIndex":r,"endRowIndex":r+1,"startColumnIndex":c0,"endColumnIndex":c1},"mergeType":"MERGE_ALL"}})
    for (r,px) in tab.H:
        reqs.append({"updateDimensionProperties":{"range":{"sheetId":sid,"dimension":"ROWS","startIndex":r,"endIndex":r+1},"properties":{"pixelSize":px},"fields":"pixelSize"}})
    for (r,ci,label,url) in tab.L:
        reqs.append({"updateCells":{"rows":[{"values":[{"userEnteredValue":{"stringValue":label},
            "textFormatRuns":[{"startIndex":0,"format":{"link":{"uri":url},"underline":True,"foregroundColor":LINKC}}]}]}],
            "fields":"userEnteredValue,textFormatRuns","start":{"sheetId":sid,"rowIndex":r,"columnIndex":ci}}})
    # hide gridlines
    reqs.append({"updateSheetProperties":{"properties":{"sheetId":sid,"gridProperties":{"hideGridlines":True}},"fields":"gridProperties.hideGridlines"}})
    for k in range(0,len(reqs),400):
        sh.batch_update({"requests":reqs[k:k+400]})

def flush(title, tab):
    """Rebuild a tab safely. Two protections live here (see genmeta.py + the plan):

      1. MANUAL-EDIT DETECTION — if a live tab with this title was hand-edited since we
         last generated it, do NOT overwrite it: record it in DIRTY and return the
         existing gid untouched (unless FORCE_ALL or title in FORCE).
      2. CRASH-SAFE BUILD-THEN-SWAP — build the new content in a temp tab, and only once
         it's fully written delete the old tab + rename temp -> final in ONE batch. A
         crash mid-write leaves the *previous* good tab in place; the sheet never goes
         blank.
    """
    meta=_ensure_meta()
    existing=GID.get(title)
    # 1. don't clobber human edits
    if existing is not None and not FORCE_ALL and title not in FORCE:
        if genmeta.is_dirty(sh, title, meta):
            DIRTY.append(title)
            print(f"  ⚠️  SKIP '{title}' — manual edits detected; left untouched.")
            return existing
    # 2. build-then-swap
    tmp=("__tmp__"+title)[:99]
    if tmp in GID:                       # clear a stale temp from a prior crash
        sh.batch_update({"requests":[{"deleteSheet":{"sheetId":GID[tmp]}}]}); GID.pop(tmp,None)
    resp=sh.batch_update({"requests":[{"addSheet":{"properties":{"title":tmp,
        "gridProperties":{"rowCount":max(len(tab.V)+4,30),"columnCount":NCOLS}}}}]})
    sid=resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    GID[tmp]=sid
    _write_content(sid, tmp, tab)        # full content into the temp tab
    swap=[]
    if existing is not None:
        swap.append({"deleteSheet":{"sheetId":existing}})
    swap.append({"updateSheetProperties":{"properties":{"sheetId":sid,"title":title},"fields":"title"}})
    sh.batch_update({"requests":swap})   # atomic: old gone + temp renamed to final
    GID.pop(tmp,None); GID[title]=sid
    # 3. fingerprint the new tab so the next run can detect hand edits to it
    genmeta.record(sh, title, meta)
    return sid

def day_route(hub, stops):
    """Multi-stop driving route: Airbnb -> stops... -> Airbnb (Google Maps directions)."""
    pts = [_q(s) for s in ([BASE[hub]] + stops + [BASE[hub]])]
    return ("https://www.google.com/maps/dir/?api=1"
            f"&origin={pts[0]}&destination={pts[-1]}"
            f"&waypoints={'%7C'.join(pts[1:-1])}&travelmode=driving")

# ── build option tab ─────────────────────────────────────────────────────────────
def build_option(o):
    t=Tab(); hub=o["hub"]
    t.title(f"{o['id']}  ·  {o['type']}")
    t.subtitle(o["oneliner"])
    t.ctx(o["ctx"])
    if o.get("route_stops"):
        t.maplink("🗺  Open today's route in Google Maps  ·  Airbnb → trailhead → food → home",
                  day_route(hub, o["route_stops"]))
    t.spacer(6)
    t.section("AT A GLANCE")
    t.kv("Home base", BASE[hub])
    _dnote = "  (full day route — tap the 🗺 link up top for live traffic)" if o.get("route_stops") else "  (round-trip estimate)"
    t.kv("Total drive", o["drive"]+_dnote)
    t.kv("Reservations", o["res"])
    t.kv("Mochi", o["mochi"], bg=MOCHI_BG)
    t.kv("Weather backup", o["backup"])
    if o.get("evening"): t.kv("Evening", o["evening"])
    t.spacer(6)
    t.section("THE PLAN")
    bikes = o.get("bike") or any(k in ("valmont","walker_mtb","steamboat_bp","evolution") for k in o["acts"])
    t.lane("🚵" if bikes else "🥾","Ian", o["ian"], IAN_BG)
    if o.get("anny"): t.lane("🥾","Anny", o["anny"], ANNY_BG)
    t.lane("🐕","Mochi", o["mochi"], MOCHI_BG)
    if o.get("together"): t.lane("👥","Together", o["together"], TOG_BG)
    if o["acts"]:
        t.spacer(6); t.section("TRAIL & ACTIVITY DETAIL  (live links)")
        for k in o["acts"]: t.activity_block(k)
    if o.get("beta"):
        t.spacer(6); t.section("TRAIL BETA — WHAT HIKERS SAY")
        for b in o["beta"]: t.bullet(b)
    if o.get("wmp_route"):
        t.spacer(6); t.section("🚐 SHUTTLE LOGISTICS — CB → ASPEN  (folded in from the old West Maroon Pass tab)")
        t.ctx("Point-to-point over the Elk Mountains: hike one way; two shuttles + a car-relocation handle it.   Legend:  🟩 hike  ·  🟦 public shuttle/bus  ·  🟧 your car.")
        for icon, step in o["wmp_route"]: t.kv(icon, step, bg=ALT)
        t.spacer(4); t.section("TRAIL STATS")
        for k, v in o["wmp_stats"]: t.kv(k, v)
        t.spacer(4); t.section("SERVICES & BOOKING  (costs for 4 people + Mochi)")
        for nm, desc, lk in o["wmp_services"]: t.pick(nm, desc, link=lk)
        t.spacer(4); t.section("RESERVATIONS — book all three, in order")
        for b in o["wmp_reservations"]: t.bullet(b)
        t.spacer(4); t.section("🐕 MOCHI NOTES")
        for b in o["wmp_mochi"]: t.bullet(b)
        if o.get("wmp_sources"):
            t.spacer(4); t.section("SOURCES")
            for label, url in o["wmp_sources"]: t.kv("Source", label, link=url)
    if o.get("lunch"):
        t.spacer(6); t.section("LUNCH — PICNIC OR TOWN?")
        t.callout(o["lunch"])
    if o.get("eat"):
        t.spacer(6); t.section("WHERE TO EAT  ·  🥾 post-hike OK  ·  🚿 shower first  ·  👔 dress up  ·  tap a name for the map")
        for (nm,note,lk) in o["eat"]: t.pick(nm,note,link=lk)
    if o.get("after"):
        t.spacer(6); t.section("AFTER THE HIKE")
        for (nm,note,lk) in o["after"]: t.pick(nm,note,link=lk)
    if o.get("q"):
        t.spacer(6); t.section("OPEN QUESTIONS — decide when you workshop this day")
        for qq in o["q"]: t.bullet(qq)
    t.spacer(6); t.section("POINTERS")
    t.kv("← Back to the menu", "DAY OPTIONS tab", link=turl(REF["menu"]))
    t.kv("Full activity tab", "Activities — Hikes, Runs & MTB", link=turl(REF["acts"]))
    if any(k in ("valmont","walker_mtb","steamboat_bp","evolution") for k in o["acts"]) or hub in ("BLD","STM","CB"):
        t.kv("Same-day trailhead pairs", "Trailhead Distances", link=turl(REF["thd"]))
    t.kv("Where to eat", "Dining Guide", link=turl(REF["dining"]))
    t.kv("Dog daycare", "Dog Daycare Options", link=turl(REF["daycare"]))
    t.genmarker()
    return flush(o["id"], t)

# ── build fixed-day tab ───────────────────────────────────────────────────────────
def build_fixed(title, d):
    t=Tab()
    dow = title.split("(")[1].rstrip(")")
    datepart = title.split(" (")[0]
    t.title(f"{datepart}  ·  {dow}")
    t.subtitle(d["plan"])
    bannerbg = {"TRAVEL":BANNER_TRAVEL}.get(d["banner"].split(" ")[0], BANNER_FIX)
    if d["banner"].startswith("TRAVEL"): bannerbg=BANNER_TRAVEL
    t.banner(d["banner"], bannerbg)
    t.spacer(6)
    t.section("AT A GLANCE")
    t.kv("Wake up", d["wake"])
    t.kv("Sleep", d["sleep"])
    drive = (f"{d['miles']} mi · ~{d['hrs']} hr" if d.get("miles") else "in town / day trip") if d.get("miles")!="" else "—"
    if d.get("miles"): drive=f"{d['miles']} mi · ~{d['hrs']} hr"
    elif d.get("miles")=="": drive="—"
    if d.get("route"):
        t.kv("Driving", drive, link=maps_route(d["route"]))
    else:
        t.kv("Driving", drive)
    t.kv("Home base", d.get("base","—"))
    if d.get("checkin"): t.kv("🏠 Airbnb check-in", d["checkin"])
    if d.get("notes"): t.kv("Notes / heads-up", d["notes"], vfg=GREY)
    t.spacer(6)
    t.section("THE PLAN")
    if d.get("ian"): t.lane("🚴","Ian", d["ian"], IAN_BG)
    if d.get("anny"): t.lane("🥾","Anny", d["anny"], ANNY_BG)
    if d.get("mochi"): t.lane("🐕","Mochi", d["mochi"], MOCHI_BG)
    if d.get("together"): t.lane("👥","Everyone", d["together"], TOG_BG)
    if d.get("backup"): t.lane("☔","Backup", d["backup"], BACK_BG)
    # opportunities / evening
    if d.get("opp"):
        t.spacer(6); t.section("OPPORTUNITIES / EVENINGS");
        r=t.row([d["opp"]]); t.mg(r); t.fmt(r,0,NCOLS,bg=WHITE,fg=DARK,align="LEFT",wrap=True); t.H.append((r,32))
    # drive plan — route / fuel / driver swaps (travel days)
    if d.get("drive_plan"):
        dp=d["drive_plan"]
        t.spacer(6); t.section("🚐 DRIVE PLAN — ROUTE · FUEL · DRIVER SWAPS")
        t.ctx(dp["summary"])
        _rl = dp.get("route_label")
        t.maplink("🗺  Open the full route in Google Maps" + (f"  ·  {_rl}" if _rl else ""), dp["route_url"])
        for s in dp["rows"]:
            if s["kind"]=="stop":
                t.kv(s["k"], s["v"], bg=WARN, vbg=WARN, link=s.get("url"))
            else:
                t.kv(s["k"], s["v"], link=s.get("url"))
        if dp.get("route_options"):
            t.spacer(4)
            t.section(dp.get("route_options_title", "🥾 CHOOSE YOUR ROUTE — pick ONE"))
            t.callout(dp.get("route_options_callout",
                      "⚠️  DECIDE before you leave. Tap an option's name to open that exact route in Google Maps."))
            for ro in dp["route_options"]:
                t.pick(ro["name"], ro["note"], link=ro["url"])
        if dp.get("fuel_options"):
            t.spacer(4); t.section("CLEAN-DIESEL STOPS  ·  #2 ULSD, no biodiesel  ·  tap for the map")
            for (nm,note,addr) in dp["fuel_options"]:
                t.pick(nm, note, link=pin(_q(addr)))
        if dp.get("scenic"):
            t.spacer(4); t.section("STRETCH YOUR LEGS / SCENIC  ·  optional, on the route")
            for (nm,note,link) in dp["scenic"]:
                t.pick(nm, note, link=link)
        if dp.get("sleep_options"):
            t.spacer(4); t.section("WHERE TO SLEEP — candidates to verify in Harvest Hosts / iOverlander")
            for (nm,note,link) in dp["sleep_options"]:
                t.pick(nm, note, link=link)
    # pointers
    t.spacer(6); t.section("POINTERS")
    if d.get("acts_ref"): t.kv("Activity options", "Activities — Hikes, Runs & MTB", link=turl(REF["acts"]))
    if d.get("menu_next"):
        hub=d["menu_next"]; t.kv(f"{HUBNAME[hub]} day menu", "DAY OPTIONS tab", link=turl(REF["menu"]))
    if d.get("scenic"): t.kv("Scenic stop", f"Scenic Stops & Drives ({d['scenic']})", link=turl(REF["scenic"]))
    if d.get("dining"): t.kv("Where to eat", f"Dining Guide ({d['dining']})", link=turl(REF["dining"]))
    if d.get("daycare"): t.kv("Dog daycare", f"Dog Daycare Options ({d['daycare']})", link=turl(REF["daycare"]))
    t.kv("← Back to the grid", "Itinerary tab", link=turl(REF["itin"]))
    t.genmarker()
    return flush(title, t)

# ════════════════════════════════════════════════════════════════════════════════
#  RUN
# ════════════════════════════════════════════════════════════════════════════════
_LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".rebuild.lock")
_LOCK_STALE_SEC = 1800

def _other_rebuild_running():
    """Best-effort: pids of OTHER live python processes running this script (excludes our
    own process + parent shell wrapper, and any non-python match to avoid self-matching the
    grep/zsh command line)."""
    try:
        pids = subprocess.run(["pgrep","-f","rebuild_trip_tabs.py"],
                              capture_output=True, text=True).stdout.split()
    except Exception:
        return []
    mine = {os.getpid(), os.getppid()}
    others = []
    for p in pids:
        if not p.strip() or int(p) in mine:
            continue
        try:
            comm = subprocess.run(["ps","-p",p,"-o","comm="],
                                  capture_output=True, text=True).stdout.strip().lower()
        except Exception:
            comm = ""
        if "python" in comm:           # a real interpreter, not the shell wrapper
            others.append(p)
    return others

def _acquire_lock():
    others = _other_rebuild_running()
    if others:
        print(f"❌ Another rebuild_trip_tabs.py is already running (pid {', '.join(others)}). Aborting.")
        sys.exit(1)
    try:
        fd = os.open(_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # lockfile present — alive & recent => abort; stale => take over
        try:
            pid_s, ts_s = open(_LOCK).read().split(",")
            age = time.time() - float(ts_s)
            alive = True
            try: os.kill(int(pid_s), 0)
            except OSError: alive = False
            if alive and age < _LOCK_STALE_SEC:
                print(f"❌ Lockfile held by live pid {pid_s} ({int(age)}s old). Aborting.")
                sys.exit(1)
            print(f"⚠️  Removing stale lockfile (pid {pid_s}, {int(age)}s old).")
        except (ValueError, OSError):
            print("⚠️  Removing unreadable lockfile.")
        os.remove(_LOCK)
        fd = os.open(_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, f"{os.getpid()},{time.time()}".encode())
    os.close(fd)

def _release_lock():
    try: os.remove(_LOCK)
    except OSError: pass

if __name__=="__main__":
    ap = argparse.ArgumentParser(description="Rebuild colorado-trip day/option tabs (crash-safe, manual-edit aware).")
    ap.add_argument("--force", action="append", default=[], metavar="TAB",
                    help="overwrite this tab even if it was hand-edited (repeatable)")
    ap.add_argument("--force-all", action="store_true",
                    help="overwrite every tab regardless of manual edits")
    args = ap.parse_args()
    FORCE.update(args.force)
    FORCE_ALL = args.force_all

    _acquire_lock()
    try:
        _ensure_meta()
        # NOTE: no upfront mass-delete. flush() does crash-safe build-then-swap per tab,
        # so a failure never leaves the sheet blank. Orphan tabs are cleaned at the end.

        # PHASE 2 — build option + fixed tabs (dirty tabs are skipped inside flush())
        opt_gid={}
        for o in OPTIONS:
            opt_gid[o["id"]]=build_option(o)
        print(f"PHASE 2a: built/kept {len(opt_gid)} option tabs: {', '.join(opt_gid)}")
        fixed_gid={}
        for title in FIXED_ORDER:
            fixed_gid[title]=build_fixed(title, FIXED[title])
        print(f"PHASE 2b: built/kept {len(fixed_gid)} fixed-day tabs.")

        # PHASE 2c — remove orphan tabs no longer in the model (old date tabs / the old
        # Day BLD-E draft / stale __tmp__ tabs from a prior crash) — but NEVER delete a
        # tab a human has edited.
        OLD_RE=re.compile(r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d+ \(\w+\)$')
        OPT_RE=re.compile(r'^(BLD|STM|CB|MAM)-[A-J]$')
        orphans=[]
        for s in sh.fetch_sheet_metadata()["sheets"]:
            ttl=s["properties"]["title"]
            is_orphan = (ttl.startswith("__tmp__") or ttl.startswith("Day BLD-E")
                         or (OLD_RE.match(ttl) and ttl not in fixed_gid)
                         or (OPT_RE.match(ttl) and ttl not in opt_gid))
            if not is_orphan: continue
            if not ttl.startswith("__tmp__") and genmeta.is_dirty(sh, ttl, _META):
                print(f"  ⚠️  KEEP orphan '{ttl}' — manual edits detected.")
                continue
            orphans.append(s["properties"])
        if orphans:
            sh.batch_update({"requests":[{"deleteSheet":{"sheetId":p["sheetId"]}} for p in orphans]})
            for p in orphans: GID.pop(p["title"],None)
        print(f"PHASE 2c: removed {len(orphans)} orphan tab(s).")

        # PHASE 3 — wire DAY OPTIONS id cells -> option tabs
        menu_ws=sh.worksheet("DAY OPTIONS"); msid=menu_ws.id
        mvals=menu_ws.get_all_values()
        link_reqs=[]
        for ri,rrow in enumerate(mvals):
            for ci,cell in enumerate(rrow):
                cid=cell.strip()
                if cid in opt_gid:
                    link_reqs.append({"updateCells":{"rows":[{"values":[{"userEnteredValue":{"stringValue":cid},
                        "textFormatRuns":[{"startIndex":0,"format":{"link":{"uri":turl(opt_gid[cid])},"underline":True,"foregroundColor":LINKC}}]}]}],
                        "fields":"userEnteredValue,textFormatRuns","start":{"sheetId":msid,"rowIndex":ri,"columnIndex":ci}}})
        # PHASE 3b — wire Itinerary date cell -> fixed tab or DAY OPTIONS
        itin_ws=sh.worksheet("Itinerary"); isid=itin_ws.id
        ivals=itin_ws.get_all_values()
        DATE_RE=re.compile(r'^(Jul|Aug) \d+$')
        for ri,rrow in enumerate(ivals):
            if not rrow: continue
            c0=rrow[0].strip()
            if not DATE_RE.match(c0): continue
            dow = rrow[1].strip() if len(rrow)>1 else ""
            full=f"{c0} ({dow})"
            target=None
            if full in fixed_gid: target=turl(fixed_gid[full])
            elif c0 in FLEX_DATES: target=turl(REF["menu"])
            if target:
                link_reqs.append({"updateCells":{"rows":[{"values":[{"userEnteredValue":{"stringValue":c0},
                    "textFormatRuns":[{"startIndex":0,"format":{"link":{"uri":target},"underline":True,"foregroundColor":LINKC}}]}]}],
                    "fields":"userEnteredValue,textFormatRuns","start":{"sheetId":isid,"rowIndex":ri,"columnIndex":0}}})
        for k in range(0,len(link_reqs),400):
            sh.batch_update({"requests":link_reqs[k:k+400]})
        print(f"PHASE 3: wired {len(link_reqs)} links (DAY OPTIONS ids + Itinerary dates).")
        print("DONE.")
    finally:
        save_genmeta()
        genmeta.report(DIRTY)
        _release_lock()
