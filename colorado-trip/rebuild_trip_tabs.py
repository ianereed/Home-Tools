"""Rebuild the trip's day tabs under the new model:

  • FLEXIBLE days (Boulder Jul 23-31, Steamboat Aug 2-5, CB Aug 10-11) get NO per-day
    tab. They are run from the DAY OPTIONS menu, which links to one OPTION tab per menu
    row: BLD-A..J, STM-A..D, CB-A..C (17 tabs, rich BLD-E style).
  • FIXED days (everything else) get a date-titled tab ("Jul 16 (Thu)" ...), linked
    from the Itinerary's date cell.

Phases:
  1. delete the 39 old "Mon DD (Dow)" per-day tabs + the old BLD-E draft (idempotent —
     also deletes any of the NEW titles if a prior run created them).
  2. create + populate the 17 option tabs and the 25 fixed-day tabs.
  3. wire links: DAY OPTIONS ID cell -> option tab; Itinerary date cell -> fixed tab
     (or -> DAY OPTIONS for flexible days).

All links are native rich-text links (see memory feedback_gsheets_hyperlink_native).
Re-runnable: it deletes the tabs it owns before recreating, so design tweaks = edit +
re-run. Does NOT touch Activities / Dining / Trailhead Distances / etc.
(The former standalone 'West Maroon Pass' tab is now folded into the CB-C option — see
the wmp_* fields on the CB-C OPTIONS dict, rendered in build_option.)
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
        "CB":"6 Emmons Rd, Unit 122, Mt Crested Butte, CO 81225"}
HUBNAME = {"BLD":"Boulder","STM":"Steamboat","CB":"Crested Butte"}

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

 dict(id="STM-A", type="TOGETHER DAY", drive="~25 min · 8 mi round trip", hub="STM",
   oneliner="Together: Fish Creek Falls — lower + upper falls",
   ctx="Steamboat's signature hike. Lower falls is a quick 0.5 mi; push to the upper falls for the full 5 mi.",
   ian="Upper Falls (5 mi RT, moderate)", anny="Same hike", mochi="Comes along, leashed (off-leash claims for this trail are unofficial — keep him leashed).",
   together="One hike, the whole crew.", acts=["fish_creek"], res="$5 parking (cash/check); start early (popular).",
   route_stops=["Fish Creek Falls Trailhead, Steamboat Springs, CO","Mountain Tap Brewery, Steamboat Springs, CO","Yampa River Core Trail, Steamboat Springs, CO"],
   backup="Emerald Mtn Blackmere (3.7 mi, from town)", evening="Pro Rodeo (Fri/Sat) / Aurum dinner",
   beta=[
     "$5/vehicle, cash or check; medium lot with heavy midday use — arrive before 9am on weekends. Hours 6am–10pm.",
     "Lower Falls: 0.25-mi paved overlook to the 283-ft falls — an easy win for the whole crew in <30 min. Add the short dirt path to the base bridge.",
     "Upper Falls push: ~2.5 mi one-way (part of the 4.7-mi / ~1,400 ft loop, Moderate, 4.8★). Rocky/technical past the lower falls but well-shaded; Ian can extend to Long Lake for a bigger day.",
     "Dogs LEASHED throughout — AllTrails + Routt NF rules say leash; the 'off-leash past 0.25 mi' claim is unofficial. Keep Mochi leashed.",
     "August flows are lower than spring, so the upper creek crossings are easier (stepping stones exposed). Be descending by ~noon for the afternoon storm window.",
   ],
   lunch="Short morning near town (back ~10am for lower-only, noon–1 if you push to Upper). Head downtown for lunch — Yampa Valley Kitchen (dog patio) or Mountain Tap Brewery (dog patio, wood-fired pizza). Or picnic at the shaded lower lot picnic area with creek access for Mochi.",
   eat=[
     ('Creekside Café & Grill', '🥾 Come as you are — best breakfast in town, creekside dog patio, 0.1 mi from the Airbnb (7am–2pm).', pin(_q('131 11th St, Steamboat Springs, CO 80487'))),
     ('Mountain Tap Brewery', '🚵 Come as you are — Yampa St wood-fired pizza + craft beer, big dog patio with water bowls.', pin(_q('910 Yampa St, Steamboat Springs, CO 80487'))),
     ('Salt & Lime', '🚵 Come as you are — lively downtown Mexican, rooftop + side patio, top margaritas (0.3 mi walk).', pin(_q('628 Lincoln Ave, Steamboat Springs, CO 80487'))),
     ('Aurum Food & Wine', '👔 Dress up + shower first — the riverfront splurge over the Yampa; reserve. HH 4:30–6.', pin(_q('811 Yampa St, Steamboat Springs, CO 80487'))),
   ],
   after=[
     ("Yampa River Core Trail", "~6 mi paved riverside path through downtown, leashed dogs, river access points — easy post-hike stroll.", pin(_q("Yampa River Core Trail, Steamboat Springs, CO"))),
     ("Downtown Lincoln Ave", "Shops, galleries, ice cream — short walk from the Airbnb; lively on rodeo nights.", pin(_q("Lincoln Avenue, Steamboat Springs, CO"))),
     ("Steamboat Pro Rodeo", "Fri/Sat nights (gates 5:30, rodeo 7:30) — very Steamboat. Confirm the dog policy at the grounds, else Mochi waits in the van.", pin(_q("Steamboat Pro Rodeo, Steamboat Springs, CO"))),
   ],
   q=[
     "Is the rodeo a Fri/Sat anchor for this day? And is the rodeo ground dog-OK or does Mochi stay back?",
     "Confirm Aurum's riverfront patio is dog-OK before booking (Laundry Kitchen is the confirmed dog-patio fallback).",
     "If pushing to Upper Falls, set a turnaround time — Ian could extend to Long Lake solo while Anny turns back, given the storm window.",
   ]),
 dict(id="STM-B", type="SEPARATE DAY", drive="~27 min · 8 mi round trip", hub="STM",
   oneliner="Separate: Ian bike park / Anny + Mochi Emerald Mtn, PM hot springs together",
   ctx="Ian rides the lift-served park; Anny + Mochi take Emerald Mtn from town. Old Town Hot Springs together in the afternoon.",
   ian="Steamboat Bike Park — lift DH/enduro (back by lunch)", anny="Emerald Mtn Blackmere Trail (3.7 mi)",
   mochi="With Anny on Emerald; at the Airbnb (A/C) during the PM hot springs.",
   together="Old Town Hot Springs downtown in the afternoon.", acts=["steamboat_bp","emerald_blackmere"],
   route_stops=["Steamboat Resort, Steamboat Springs, CO","Howelsen Hill, Steamboat Springs, CO","Laundry Kitchen and Cocktails, Steamboat Springs, CO","Old Town Hot Springs, Steamboat Springs, CO"],
   res="Bike-park ticket / Ikon perk.", backup="Park closed/wet → Ian trail-runs Emerald (6–8 mi).",
   evening="Old Town Hot Springs / Pro Rodeo",
   beta=[
     "Steamboat Bike Park: gondola + Christie Peak Express haul bikes in summer (~10am–5pm, ~mid-June–late-Sept). 26 trails on ~2,200 vert: greens (Why Not), flowy/techy blues (Voodoo Child, Chutes), steep blacks (Jah Man). Ride early — August monsoon storms build by 1–2pm and can suspend the gondola; wet dirt closes the jump lines.",
     "Ikon: full pass = 2 free bike-haul days, Base = 1; redeem in person at the base ticket office (no online comp). Walk-up ~$65–80. Helmet required; full-face + pads recommended/rentable.",
     "Rentals + gear at the base village book out in August — reserve a full-suspension park bike ahead.",
     "Anny + Mochi: Emerald Mtn Blackmere — 3.7 mi / ~938 ft, walkable from downtown via Howelsen (~15 min along the Core Trail). Leashed (city trails, no off-leash on Emerald); exposed upper ridge — start early.",
   ],
   lunch="Ian descends by noon–12:30 and drives ~10 min downtown. Regroup at Laundry Kitchen & Cocktails (Soda Creek dog patio) — same neighborhood as Anny's Emerald finish. Fallback: Storm Peak Brewing's downtown taproom (dogs inside; NOT the Bus Stop location, which bans dogs). Then drop Mochi at the A/C Airbnb before the PM hot springs.",
   eat=[
     ('Storm Peak Brewing', '🚵 Come as you are — DOGS WELCOME INSIDE at the downtown taproom + rooftop; the easy post-ride beer.', pin(_q('1885 Elk River Plaza, Steamboat Springs, CO 80487'))),
     ('Mountain Tap Brewery', '🚵 Come as you are — Yampa St dog patio + water bowls, wood-fired pizza; the midday regroup.', pin(_q('910 Yampa St, Steamboat Springs, CO 80487'))),
     ('Back Door Grill', '🚵 Come as you are — widely called the best burger in town (Oak St), patio; go off-peak.', pin(_q('825 Oak St, Steamboat Springs, CO 80487'))),
     ('Laundry Kitchen & Cocktails', '🚿 Shower first — small plates + cocktails on the Soda Creek patio; the post-hot-springs evening (opens 4:30).', pin(_q('127 11th St, Steamboat Springs, CO 80487'))),
   ],
   after=[
     ("Old Town Hot Springs", "Downtown mineral pools + 230-ft slides (~$35, no reservation, NO dogs) — drop Mochi at the A/C Airbnb first. ~1.5–2 hr.", pin(_q("Old Town Hot Springs, Steamboat Springs, CO"))),
     ("Yampa River Core Trail", "Flat paved riverside path, leashed dogs — easy evening walk after a long day.", pin(_q("Yampa River Core Trail, Steamboat Springs, CO"))),
     ("Steamboat Pro Rodeo", "Fri/Sat — classic Steamboat; outdoor bleachers. Confirm dog policy or leave Mochi at the Airbnb.", pin(_q("Steamboat Pro Rodeo, Steamboat Springs, CO"))),
   ],
   q=[
     "Does Ian have an Ikon pass (2 free days / Base 1), and is he bringing his own bike or renting (reserve ahead)?",
     "Confirm the Airbnb A/C so Mochi's comfortable during the PM hot springs.",
     "Is there a Fri/Sat rodeo in the Aug 1–6 window to anchor the evening?",
   ]),
 dict(id="STM-C", type="BIG DAY", drive="~1h40 · 64 mi round trip", hub="STM",
   oneliner="Big day: Hahns Peak summit + Fishhook Lake (or Red Dirt)",
   ctx="Drive ~40 min north for a summit + alpine lake double. Or swap to Red Dirt for a gentler dog day.",
   ian="Hahns Peak fire-lookout (3 mi RT) + Fishhook Lake (6 mi)", anny="Same",
   mochi="Comes along (dog-friendly).", together="Summit + lake, full day out.", acts=["hahns","fishhook"],
   route_stops=["The Clark Store, Clark, CO","Hahns Peak Trailhead, Clark, CO","Hahns Peak Lake, Clark, CO","Storm Peak Brewing Company, Steamboat Springs, CO"],
   res="None — ~40 min drive N to Hahns Peak Village.", backup="Yampa River Core Trail (7 mi paved)",
   evening="Aurum dinner / Movies on the Mountain",
   beta=[
     "Hahns Peak fire lookout (10,839 ft): ~3 mi RT / ~1,000 ft, Hard, 4.8★. Meadow + forest to the shoulder, then a steep loose-talus summit cone (hands-on rock) crowned by the 1912 lookout + 360° views. Mochi needs help on the talus; leash on the exposed sections.",
     "Fishhook Lake: ~6 mi RT / ~1,200 ft from the Clearwater TH (FR 490/496) — alpine cirque lake. Doing both = a 9+ mi / ~2,200 ft day; budget the whole day.",
     "Drive: US-40 W → CR-129 (Elk River Rd) N ~26–30 mi through Clark; paved to Steamboat Lake, then graded gravel. The Sprinter handles it fine; ~45–50 min to the TH.",
     "Leashed (Medicine Bow-Routt NF); marmots/pikas above treeline will tempt Mochi. Summit by 11am — the cone is fully exposed to afternoon storms.",
     "Red Dirt as the gentler alt: the full trail is actually long (13.9 mi); for a true easy dog day do the flat Hahns Peak Lake Loop (3.3 mi) or a partial out-and-back instead.",
   ],
   lunch="Remote alpine day — pack a real picnic (sandwiches, cheese, 2+ L water + a bowl) and eat at the saddle or down at Hahns Peak Lake (Mochi wades the shore). On the drive up/back, The Clark Store (Clark, 7am–7pm) is the classic Elk River general-store/deli stop — coffee + breakfast burritos out, ice cream + cold drinks back. Dinner back in Steamboat: Aurum (splurge) or Storm Peak (dogs inside).",
   eat=[
     ('The Clark Store', '🚵 Come as you are — country store + deli on CR-129 (~20 min N toward Hahns); famous giant breakfast burrito, deck for Mochi. ~7am–7pm.', pin(_q('54175 RCR 129, Clark, CO 80428'))),
     ('Storm Peak Brewing', '🚵 Come as you are — dogs INSIDE at the downtown taproom; the easy tired-crew dinner back in town.', pin(_q('1885 Elk River Plaza, Steamboat Springs, CO 80487'))),
     ('Mountain Tap Brewery', '🚵 Come as you are — Yampa St dog patio, wood-fired pizza; casual post-summit dinner.', pin(_q('910 Yampa St, Steamboat Springs, CO 80487'))),
     ('Aurum Food & Wine', '👔 Dress up + shower first — riverfront splurge to celebrate a big summit day; reserve.', pin(_q('811 Yampa St, Steamboat Springs, CO 80487'))),
   ],
   after=[
     ("Hahns Peak Lake", "Flat ~3.3-mi lakeside loop at the base — post-summit cooldown, Mochi wades, peak reflection. Free (NF).", pin(_q("Hahns Peak Lake, Clark, CO"))),
     ("Steamboat Lake State Park", "25 mi S on CR-129 — beach + trails, leashed dogs (not in the swim water). A lake-view stop on the drive home ($12–17/vehicle).", pin(_q("Steamboat Lake State Park, Clark, CO"))),
     ("Yampa River Core Trail", "Back home — flat paved riverside walk to loosen legs before dinner.", pin(_q("Yampa River Core Trail, Steamboat Springs, CO"))),
   ],
   q=[
     "Hahns + Fishhook double (~9 mi) or Hahns alone (3 mi) + the easy lake loop? The double is a long day.",
     "Mochi on the Hahns summit talus — is he comfortable on loose rock? If not, the saddle view (~10,400 ft) is already great and a fine turnaround.",
     "For a true easy dog day, the 'Red Dirt' backup is actually 13.9 mi — use Hahns Peak Lake Loop (3.3 mi flat) instead. Confirm intent. Call the Hahns Peak/Bears Ears RD (970-870-2299) re: FR 490 if recent rain.",
   ]),
 dict(id="STM-D", type="TOWN / REST DAY", drive="~43 min · 16 mi round trip", hub="STM",
   oneliner="Town/rest: Strawberry Park Hot Springs + Yampa River + downtown",
   ctx="A soak-and-stroll rest day. Strawberry Park is cash-only and books ahead; Old Town is the easy fallback.",
   ian="Soak + downtown / Yampa River walk", anny="Soak + downtown",
   mochi="At the Airbnb (A/C) — no dogs at the springs.", together="Strawberry Park soak + Yampa River.",
   acts=[], res="Strawberry Park: book ~30 days ahead, CASH $30/person (strawberryhotsprings.com).",
   route_stops=["Strawberry Park Hot Springs, Steamboat Springs, CO","Freshies Restaurant, Steamboat Springs, CO","Yampa River Core Trail, Steamboat Springs, CO"],
   backup="Old Town Hot Springs (downtown, no reservation)", evening="Strawberry Park soak / downtown dinner",
   beta=[
     "Strawberry Park Hot Springs (44200 CR-36, ~7 mi N): $30/adult CASH ONLY (no cards, no ATM on site — get cash in town). Reservations required, open exactly 30 days ahead; 2-hr timed sessions. Clothing-optional after dark; no minors after dark.",
     "The road: the last ~2 mi of CR-36 are unpaved, steep, narrow and winding; high-clearance recommended. RVs/trailers are banned — a Sprinter is a van, not an RV, but its length/width is the concern. Call (970) 879-0342 to confirm a Sprinter's OK + road conditions, and go slow.",
     "NO dogs anywhere — not on the property AND not left in the lot. Mochi stays at the A/C Airbnb for this.",
     "Old Town Hot Springs (downtown, 136 Lincoln) is the easy walk-up fallback: $35, no reservation, 9 pools + cold plunge + 230-ft slides, <10 min from the Airbnb.",
     "Yampa River Core Trail (6.5 mi paved) is a great leashed Mochi walk morning or evening — but river TUBING doesn't allow dogs (city rule).",
   ],
   lunch="Town day — lean into a sit-down brunch before the soak. Freshies (Lincoln Ave) is the local breakfast/brunch go-to (quick, filling). For a dog patio, Ghost Ranch Coffee (patio) in the morning, then Mountain Tap (dog patio) for lunch. Fallback: Sweet Pea Market patio.",
   eat=[
     ('Creekside Café & Grill', '🥾 Come as you are — best-breakfast pick, creekside dog patio, 0.1 mi walk; the morning anchor.', pin(_q('131 11th St, Steamboat Springs, CO 80487'))),
     ('Big Iron Coffee Co.', '🚵 Come as you are — Lincoln Ave coffee + breakfast burritos, dog patio (0.3 mi walk).', pin(_q('635 Lincoln Ave, Steamboat Springs, CO 80487'))),
     ('Laundry Kitchen & Cocktails', '🚿 Shower first — Soda Creek patio small plates + cocktails; the post-soak dinner (opens 4:30). Reserve.', pin(_q('127 11th St, Steamboat Springs, CO 80487'))),
     ('Aurum Food & Wine', '👔 Dress up + shower first — riverfront splurge with a sunset post-soak; reserve.', pin(_q('811 Yampa St, Steamboat Springs, CO 80487'))),
   ],
   after=[
     ("Yampa River Core Trail", "Paved 6.5-mi riverside path, leashed dogs — morning Mochi walk before the soak, or an easy evening stroll.", pin(_q("Yampa River Core Trail, Steamboat Springs, CO"))),
     ("Yampa River Botanic Park", "Free 6-acre garden off the Core Trail, leashed dogs — a quiet 20-min leg-stretch.", pin(_q("Yampa River Botanic Park, Steamboat Springs, CO"))),
     ("Downtown Lincoln Ave", "Western storefronts, galleries, Sweet Pea Market — dog-friendly sidewalks, best in the evening post-soak.", pin(_q("Lincoln Avenue, Steamboat Springs, CO"))),
   ],
   q=[
     "Can the Sprinter handle the steep dirt CR-36 to Strawberry Park, and is the cash + 30-day reservation workable? If not, Old Town Hot Springs downtown is the zero-hassle fallback.",
     "Withdraw cash in town first — Strawberry Park is $30/adult cash only, no ATM (price has risen from $20; confirm at booking).",
     "Confirm the Airbnb A/C is running before you leave — Mochi can't go to the springs or wait in the van.",
   ]),

 dict(id="CB-A", type="SEPARATE DAY", drive="~23 min · 8 mi round trip", hub="CB",
   oneliner="Separate: Ian Evolution bike park / Anny + Mochi Oh-Be-Joyful, Alpenglow eve",
   ctx="Ian rides Evolution (walk from the Airbnb); Anny takes Mochi up Oh-Be-Joyful or the mellower Judd Falls. Back by 4:30 for the free concert.",
   ian="Evolution Bike Park — lift DH (back ~4:30 for the concert)",
   anny="Oh-Be-Joyful (9.6 mi, hard) OR Judd Falls / Copper Creek (moderate)",
   mochi="With Anny (both hikes are dog-friendly).", together="Reconvene for the Alpenglow Concert.",
   acts=["evolution","oh_be_joyful","judd_falls"], res="Bike-park ticket — get the 2-day pass (Aug 10 + 11).",
   route_stops=["Evolution Bike Park, Mount Crested Butte, CO","Oh-Be-Joyful Trailhead, Crested Butte, CO","Crested Butte Town Park, Crested Butte, CO"],
   backup="Lower Loop / Slate River (easy–moderate, river + meadow)",
   evening="Alpenglow Concert — free, 5:30pm, CB Town Park (no pets inside)",
   beta=[
     "Evolution Bike Park (CBMR base, walk from the Airbnb): 52 lift-served trails, beginner (Hotdogger) → expert proline (Psycho Rocks ⚫). Red Lady Express ~9am–5pm. Base ~9,400 ft — ride by 9am before afternoon storms; CBMR rain-checks weather holds of 90+ min.",
     "2-Day Bike Haul ~$126 ($63/day) vs $70 single — buy both days online ahead to skip the window. Rentals at the base book out in August; reserve a full-suspension bike + pads early.",
     "Anny + Mochi — Oh-Be-Joyful: 4.8★ wildflowers + waterfalls. The full hike to Blue Lake is ~13 mi / big day; to the OBJ falls junction it's a shorter ~4–5 hr. Leashed; spotty cell — download the map.",
     "OBJ access road (BLM 3220) is steep + rough, flagged unsuitable for larger low-clearance rigs — the Sprinter may struggle the last stretch. Park at Slate River Rd staging and walk/bike in, or confirm conditions first.",
     "Mellow alt — Judd Falls / Copper Creek (Gothic Rd): 2.2 mi / ~440 ft, paved/gravel approach (no clearance issue), waterfall + East River, done before noon. Leashed.",
   ],
   lunch="Separate day: Ian eats at the base (Butte 66 deck, or Coffee Lab for a pre-ride bite); Anny packs trail food + water for OBJ (long, spotty cell) and snacks at the falls. If she does Judd Falls instead she's back by noon for Teocalli Tamale or Butte Bagels (closes ~2pm). Reconvene by 4:30 for the 5:30 Alpenglow concert (drop Mochi first — no pets).",
   eat=[
     ('Coffee Lab', '🚵 Come as you are — base-area espresso in Mountaineer Square, walkable from the Airbnb; pre-lift fuel (~6:30am).', pin(_q('620 Gothic Rd Ste C-100, Mount Crested Butte, CO 81225'))),
     ('Butte 66', '🚵 Come as you are — slopeside BBQ + burgers at the Mt CB base, big deck; the post-lift fit (walkable).', pin(_q('10 Crested Butte Way, Mount Crested Butte, CO 81225'))),
     ('Bonez Tequila Bar & Grill', '🚵 Come as you are — Elk Ave contemporary Mexican, riverside patio, heavy margs; lively pre-concert dinner.', pin(_q('130 Elk Ave, Crested Butte, CO 81224'))),
     ('Montanya Distillers', '🚵 Come as you are — Elk Ave rum tasting room + dog patios; the pre-Alpenglow cocktail stop (not a full dinner).', pin(_q('204 Elk Ave, Crested Butte, CO 81224'))),
   ],
   after=[
     ("Elk Avenue stroll", "Browse the 3 blocks of Elk Ave + a Montanya rum cocktail; drop Mochi at the Airbnb before Town Park (no pets at the concert).", pin(_q("Elk Avenue, Crested Butte, CO"))),
     ("Alpenglow Concert — Town Park", "Free outdoor concert, Mondays 5:30–7:30 at Town Park (606 6th St). No pets, no glass; bring a low chair + picnic; free CB bus from the base.", pin(_q("Crested Butte Town Park, Crested Butte, CO"))),
     ("Slate River Road (sunset walk)", "After the concert, pick up Mochi for a flat dirt-road meadow walk toward Paradise Divide at golden hour.", pin(_q("Slate River Road, Crested Butte, CO"))),
   ],
   q=[
     "OBJ access road (BLM 3220) is rough for the van — default to Judd Falls (paved approach) unless you confirm conditions or stage + walk in?",
     "OBJ to Blue Lake is a ~6–7 hr day; back by 4:30 for Alpenglow means turning around at the falls (~4–5 hr, leave by 8am) or doing Judd Falls. Which?",
     "Alpenglow is Mondays — Aug 10 is a Monday (lines up). Confirm the 2-day Evolution pass covers Aug 10 + 11 (some are sold consecutive-only); reserve a rental bike 2–3 wks out if not bringing Ian's.",
   ]),
 dict(id="CB-B", type="SEPARATE DAY", drive="~1h20 · 33 mi round trip (Kebler is dirt — Google may misroute the long way)", hub="CB",
   oneliner="Separate: Ian Evolution day 2 / Anny + Mochi Three Lakes, pack up + last Elk Ave dinner",
   ctx="Second bike-park session on the 2-day pass; Anny + Mochi do the easy Three Lakes loop. Pack up, last dinner on Elk Avenue.",
   ian="Evolution Bike Park — second session (2-day pass)", anny="Three Lakes Loop (3 mi, easy)",
   mochi="With Anny (easy alpine-lake loop).", together="Pack up; last dinner on Elk Ave.",
   acts=["evolution","three_lakes"], res="Use the 2-day pass from Aug 10.",
   route_stops=["Evolution Bike Park, Mount Crested Butte, CO","38.8672,-107.2068","Elk Avenue, Crested Butte, CO"],
   backup="Lower Loop or Woods Walk (easy, dog-friendly)", evening="Last Elk Avenue dinner — Soupçon if booked",
   beta=[
     "Three Lakes Loop (Anny + Mochi): Lost Lake Campground TH on Kebler Pass Rd, ~16 mi W. ~3.5 mi / ~518 ft, Moderate, 4.8★ — Lost Lake, Dollar Lake + Lost Lake Slough, with a short waterfall spur. Wildflowers peak in August. Leashed (Gunnison NF); bring water (no reliable filter source).",
     "Kebler Pass Rd is packed dirt/gravel, no pavement — fine for the Sprinter in August (no mud/snow), scenic aspens, watch for elk.",
     "Evolution day 2 on the 2-day pass — lift laps green→expert, or pedal XC (Painter Boy) if legs are cooked. Load bikes after the ride while everything's open; the park closes mid-afternoon — natural cue to pack the van.",
     "In-town backup if Kebler's a no-go: Lower Loop (7 mi, easy, river) or Woods Walk — both leashed, dog-friendly, walkable from Elk Ave.",
   ],
   lunch="Pack-up day, keep it low-friction: Anny + Mochi picnic at Lost Lake (grab Butte Bagels before driving Kebler — closes ~2pm); Ian eats at the base or back at the Airbnb. Regroup in CB town ~5–6pm for the farewell dinner.",
   eat=[
     ("Mikey's Pizza", '🚵 Come as you are — hole-in-the-wall slices, the post-ride refuel; breakfast burritos Mon–Fri (a pre-ride ritual).', pin(_q('611 3rd St, Crested Butte, CO 81224'))),
     ('Secret Stash', "🚵 Come as you are — the CB institution (#1-rated), funky decor, the 'Notorious F.I.G.' pizza; patio limited (call).", pin(_q('303 Elk Ave, Crested Butte, CO 81224'))),
     ('Soupçon', '👔 Dress up + shower first — the marquee CB farewell dinner; French prix fixe (~$200/pp), INDOOR, no dogs. Book NOW (sells out in Aug).', pin(_q('127A Elk Ave, Crested Butte, CO 81224'))),
     ('The Sunflower', '🚿 Shower first — relaxed farm-to-table alt to Soupçon, best on Elk Ave; indoor, no dogs. Reserve by text.', pin(_q('214 Elk Ave, Crested Butte, CO 81224'))),
   ],
   after=[
     ("Elk Avenue (evening stroll)", "Last walk down the historic strip — galleries, ice cream, golden-hour light on the Elk Mtns. Dog-friendly sidewalks.", pin(_q("Elk Avenue, Crested Butte, CO"))),
     ("Montanya Distillers", "Open to 9pm — a rum nightcap to toast the end of the CB leg (patio).", pin(_q("Montanya Distillers, Crested Butte, CO"))),
     ("Mt CB base (last sunset)", "Drive up to the resort base for a final alpenglow look at the peak before departure — quick + memorable.", pin(_q("Mount Crested Butte, CO"))),
   ],
   q=[
     "Is Soupçon booked for the farewell dinner? It's 28 seats, indoor-only, no dogs, books weeks ahead on Tock — confirm it's open in summer + reserved, else Public House / Secret Stash.",
     "Dog plan for the Soupçon window (~2 hr, indoor): one person dines with Mochi outside and you alternate, or dog-sit? CB evenings are cool (~50°F) which helps.",
     "Three Lakes TH (Lost Lake CG) fills early on weekends — Anny departs by 8–9am. Confirm Montanya's summer hours when you book.",
   ]),
 dict(id="CB-C", type="BIG DAY", drive="~2.5 hr (shuttle logistics)", hub="CB",
   oneliner="Big day: Crested Butte → Aspen via West Maroon Pass (4 + Mochi)",
   ctx="The headline one-way alpine hike over the pass to Aspen — needs a car relocation + two shuttles. Full logistics + booking are in the SHUTTLE LOGISTICS section below.",
   ian="Hike 10.5 mi over West Maroon Pass (12,490 ft)", anny="Same hike",
   mochi="Comes along, leashed (USFS wilderness); consider booties for the rocky Aspen-side descent.", together="One-way point-to-point; full-day commitment.",
   acts=[], res="Dolly's + Maroon Bells bus + car relocation — book all three (see SHUTTLE LOGISTICS below).",
   backup="Full-day commitment; replaces a bike-park day.", evening="Dinner in Aspen or back in CB",
   beta=[
     "~10.2 mi point-to-point, West Maroon TH (CB side) → Maroon Lake (Aspen side), cresting the pass at 12,490 ft. CB→Aspen is the easier direction (~2,350 ft gain). Plan 6–8 hr; the Aspen-side descent is long, rocky, with ~3 creek crossings (wet feet).",
     "Late July–early Aug is peak wildflowers; past the pass the Maroon Bells come into full view dropping to the lake.",
     "Three-layer logistics: (1) Dolly's Mountain Shuttle CB→TH (Mochi needs a paid seat; books up early); (2) Maroon Bells Shuttles relocates the Sprinter CB→Aspen Highlands (~$415); (3) RFTA bus Maroon Lake→Aspen Highlands ($10/person, reserve — last bus 5pm, don't miss it).",
     "Start EARLY — crest the pass by 11am; August storms above treeline are dangerous. A 6:30am Dolly's pickup is the target.",
     "Simpler alt if shuttles don't line up: out-and-back from Schofield TH to the pass — same wildflower valley + summit views, only needs Dolly's round-trip, no van relocation or RFTA bus.",
   ],
   lunch="You're in wilderness 6–8 hr with no services — pack a full alpine picnic (dense sandwiches, salami/cheese, 3+ L water per person) and crack a snack at the pass. The reward is the Aspen side: Meat & Cheese (319 E Hopkins) is Aspen's top dog-friendly patio. If you shuttle back instead, it's an Elk Ave dinner in CB.",
   eat=[
     ('Camp 4 Coffee', "🚵 Come as you are — iconic CB coffee + pastry before Dolly's pickup (walk-up window).", pin(_q('402 1/2 Elk Ave, Crested Butte, CO 81224'))),
     ('Butte Bagels', '🚵 Come as you are — CB scratch bagels for the pre-hike fuel / trail food (opens ~7:30, closes ~2pm).', pin(_q('218 Maroon Ave Ste A, Crested Butte, CO 81224'))),
     ('Meat & Cheese', '🥾 Post-hike OK — Aspen charcuterie + farm-to-table, ~11 dog sidewalk tables; the reward if you finish in Aspen.', pin(_q('319 E Hopkins Ave, Aspen, CO 81611'))),
     ('White House Tavern', "🥾 Post-hike OK — Aspen miner's-cottage New American, famous fried-chicken sandwich, dog patio (walk-in, expect a wait).", pin(_q('302 E Hopkins Ave, Aspen, CO 81611'))),
   ],
   after=[
     ("Downtown Aspen (Hyman Ave mall)", "If you end in Aspen — free RFTA bus into town, walk the pedestrian mall, ice cream, let Mochi sniff around. Flat + shaded.", pin(_q("Wagner Park, Aspen, CO"))),
     ("Rio Grande Trail", "Aspen — flat paved riverside path along the Roaring Fork, leashed dogs — a scenic cooldown if legs allow.", pin(_q("Rio Grande Trail, Aspen, CO"))),
     ("Elk Avenue, CB", "If you shuttle back — dusk stroll on Elk Ave, galleries + ice cream, Mochi alongside.", pin(_q("Elk Avenue, Crested Butte, CO"))),
   ],
   # ── folded in from the former standalone 'West Maroon Pass' tab ──
   wmp_route=[
     ("🚐", "CRESTED BUTTE — town, ~8,900 ft · morning start. While you hike, Maroon Bells Shuttles drives your car CB→Aspen by road so it's waiting at the finish."),
     ("↓", "Dolly's Mountain Shuttle · CB → West Maroon Trailhead · ~40 min over Schofield Pass · $55/seat (Mochi needs a seat too)."),
     ("🥾", "WEST MAROON TRAILHEAD — 10,432 ft · start hiking EARLY (afternoon thunderstorms). Up the valley along the Crystal River through wildflower fields."),
     ("⛰️", "WEST MAROON PASS — 12,490 ft · HIGH POINT. Last ¼ mi is steep; big views both sides."),
     ("↓", "Descend (steep + rocky at first) · 3 Maroon Creek crossings · past Crater Lake."),
     ("🏞️", "MAROON LAKE TRAILHEAD — ~9,580 ft · the Maroon Bells (most-photographed peaks in N. America)."),
     ("🚌", "Maroon Bells RFTA bus · Maroon Lake → Aspen Highlands · 15 min · $10 'One-Way Return' ticket · last bus down 5:00 PM."),
     ("🍽️", "ASPEN — pick up your relocated car · dinner downtown."),
     ("🚗", "Drive back to Crested Butte · ~2.5 hr loop (CO-82 → Carbondale → McClure & Kebler Pass)."),
     ("🏠", "CRESTED BUTTE — home for the night."),
   ],
   wmp_stats=[
     ("Distance", "~10.5 miles one-way (point to point)"),
     ("Elevation gain", "2,357 ft of climbing (CB→Aspen direction)"),
     ("High point", "West Maroon Pass — 12,490 ft"),
     ("Difficulty", "Strenuous · 6–10 hr on trail (Dolly's quotes ~6 hr avg — you're only as fast as the slowest hiker)"),
     ("Season", "Passable late June–mid July depending on snow; August is prime + wildflowers"),
     ("Trailhead access", "West Maroon TH is 13–14 mi / ~40 min from CB over Schofield Pass (past Emerald Lake). 4x4 SUV + tiny lot — this is why you take Dolly's."),
   ],
   wmp_services=[
     ("🟧 Maroon Bells Shuttles — car relocation", "Drives YOUR car CB→Aspen by road while you hike, so it's waiting at the finish (operating since 2012). ~$415. They email final logistics + the exact Aspen drop point. Book well in advance.", "https://maroonbellsshuttles.com"),
     ("🟦 Dolly's Mountain Shuttle — ride to TH", "Drives the group (+ dog) CB → West Maroon Trailhead, ~40 min. Mochi is welcome but must be leashed AND have its own reserved (paid) seat — count 5 seats. $55/seat. 970-209-1568. Book early (FareHarbor), esp. weekends.", "https://crestedbutteshuttle.com"),
     ("🟦 Maroon Bells RFTA bus — return", "Bus from Maroon Lake Trailhead down to Aspen Highlands Welcome Center (15 min). Buy the 'One-Way Return Only' ticket, $10/hiker. Last bus down 5:00 PM — don't miss it. Confirm the leashed-dog policy.", "https://visitmaroonbells.com"),
   ],
   wmp_reservations=[
     "1 — Maroon Bells Shuttles (car relocation): book well in advance. They send final logistics + tell you exactly where the bus drops you to meet your car.",
     "2 — Dolly's Mountain Shuttle (ride to TH): book early, esp. weekends. Reserve 5 seats (4 people + Mochi). Online (FareHarbor) or call 970-209-1568.",
     "3 — Maroon Bells RFTA bus ('One-Way Return Only'): $10/hiker at visitmaroonbells.com. Pick a departure time; last bus down is 5:00 PM.",
   ],
   wmp_mochi=[
     "Trail: leashed dogs ARE allowed — this is USFS wilderness (Maroon Bells–Snowmass), not a National Park.",
     "Dolly's van: dog is welcome but needs its own reserved (paid) seat — count Mochi as a 5th seat.",
     "Maroon Bells RFTA bus: confirm the leashed-dog policy when you book the $10 return ticket.",
     "Fitness: 10.5 mi / +2,357 ft / 12,490 ft pass is a BIG day for a 2-yr-old golden. Doable for a fit dog — bring extra water + check paws on the rocky descent.",
   ],
   wmp_sources=[
     ("Dolly's Mountain Shuttle — summer / hike info", "https://crestedbutteshuttle.com"),
     ("Maroon Bells Shuttles — car relocation reservations", "https://maroonbellsshuttles.com"),
     ("Maroon Bells RFTA shuttle reservations", "https://visitmaroonbells.com"),
     ("Travel Crested Butte — hike guide (stats + route)", "https://www.travelcrestedbutte.com"),
   ],
   q=[
     "CRITICAL: are Dolly's Shuttle (+ a paid seat for Mochi) AND the Maroon Bells vehicle relocation (~$415) AND the RFTA Maroon Bells bus ($10/pp, last bus 5pm) all reserved? All book up weeks ahead.",
     "Commit to the full one-way CB→Aspen, or do the simpler out-and-back to the pass from Schofield (same payoff, no van relocation / RFTA)?",
     "Comfortable handing the Sprinter keys to Maroon Bells Shuttles for relocation (gear/sleep setup, Kebler Pass height)?",
   ]),
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
 "Jul 17 (Fri)": dict(banner="FIXED", plan="Final surgery appt (AM) → drive to Lake Tahoe",
   wake="Redwood City", sleep="Incline Village", miles="237", hrs="4",
   base="—",
   together="Final surgery appointment in Sunnyvale first thing. Everyone meets at the clinic and rolls out together at 9:30 AM — straight onto the road to Tahoe. One combined fuel + driver-swap + lunch stop in Davis; arrive Incline Village early afternoon. Full leg-by-leg breakdown in the DRIVE PLAN below.",
   notes="Appointment is fixed (cannot move). 9:30 AM departure assumes the appt wraps by then — if it runs long, slide the whole timeline back by the same amount. The Sprinter takes CLEAN #2 ULSD ONLY — no biodiesel of any blend; every fuel stop below is a confirmed clean-#2 source.",
   route=["Sunnyvale, CA","1601 Research Park Dr, Davis, CA 95616","Incline Village, NV"],
   drive_plan=dict(
     summary=("237 mi · ~4 hr of driving (~4h40 door-to-door with the stop).  Depart Sunnyvale 9:30 AM "
              "(straight from the clinic) → arrive Incline Village ~2:10 PM.  Plan = ONE stop that does triple "
              "duty: refuel + driver swap + lunch, lined up at the ~2-hour mark in Davis.  Van runs clean #2 "
              "ULSD only — NO biodiesel."),
     route_url=maps_route(["Sunnyvale, CA","1601 Research Park Dr, Davis, CA 95616","Incline Village, NV"]),
     route_label="Sunnyvale → Davis (fuel) → Incline Village",
     rows=[
       dict(kind="depart", k="🚐 9:30 AM · Depart",
            v="Sunnyvale (from the clinic) — Driver 1 at the wheel."),
       dict(kind="leg", k="Leg 1 · 9:30–11:15 AM",
            v="105 mi · 1h46m · Driver 1 — Sunnyvale → Davis, up the East Bay on I-880 N → I-80 E (flat valley miles).",
            url=maps_route(["Sunnyvale, CA","1601 Research Park Dr, Davis, CA 95616"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP + 🍽 LUNCH · ~11:15 AM",
            v=("1601 Research Park Dr, Davis — the ONLY fuel stop you need (132 mi left after this < the ~200 mi "
               "range), and it sits right at the 2-hour swap mark. Confirmed clean #2, no bio. ~35 min: fill the "
               "tank, swap drivers, bathroom + grab lunch."),
            url=pin(_q("1601 Research Park Dr, Davis, CA 95616"))),
       dict(kind="leg", k="Leg 2 · 11:50 AM–2:10 PM",
            v=("132 mi · 2h18m · Driver 2 (fresh) — Davis → Incline Village on I-80 E over Donner Pass, then "
               "NV-431 (Mt Rose Hwy) down to the lake. The fresh driver takes the 7,000-ft Sierra climb."),
            url=maps_route(["1601 Research Park Dr, Davis, CA 95616","Incline Village, NV"])),
       dict(kind="arrive", k="🏁 ~2:10 PM · Arrive", v="Incline Village, NV."),
     ],
     fuel_options=[
       ("Davis — PRIMARY", "1601 Research Park Dr · ~mile 105 (~2 hr in). Your fuel + swap + lunch stop. Leaves 132 mi to Incline — inside the ~200 mi range.", "1601 Research Park Dr, Davis, CA 95616"),
       ("Dixon — alt (same point)", "2599 N 1st St · ~mile 98, 7 mi before Davis, right off I-80. Use it if you'd rather stop a hair earlier.", "2599 N 1st St, Dixon, CA 95620"),
       ("Auburn — top-off / optional 2nd swap", "13405 Lincoln Way · ~mile 153, the LAST confirmed clean-diesel before the Donner climb. Skip if you filled at Davis; otherwise top off here (and sneak a 2nd driver swap) before the Sierra.", "13405 Lincoln Way, Auburn, CA 95603"),
     ],
   )),
 "Jul 18 (Sat)": dict(banner="FIXED FOR IAN · FLEXIBLE FOR ANNY", plan="Lake Tahoe — AM split, PM Shakespeare",
   wake="Incline Village", sleep="Incline Village", miles="0", hrs="0", base="Lake Tahoe area",
   ian="AM MTB — Northstar Bike Park (~$85–90, Ikon discounts) OR Hole in the Ground Loop (16 mi technical) OR Glass Mtn + Painted Rock (Tahoe City). See Activities (Tahoe).",
   anny="CHOICE — Page Meadows (wildflower meadow, 5–8 mi) or Donner Lake Rim Trail (ridge views) with Mochi. See Activities (Tahoe).",
   mochi="With Anny on her hike (both are leashed-OK).",
   together="Lunch after AM activities — Bridgetender Tavern (dog patio, Tahoe City) or Alibi Ale Works (Truckee). PM: Lake Tahoe Shakespeare Festival, Sand Harbor, 7:30pm (gates 5:30).",
   notes="PM anchor: Shakespeare at Sand Harbor — book laketahoeshakespeare.com (Macbeth or Heart of Robin Hood).",
   acts_ref=True, dining="Tahoe", daycare="Truckee"),
 "Jul 19 (Sun)": dict(banner="FIXED FOR IAN · FLEXIBLE FOR ANNY", plan="Second Tahoe day → evening hop to Reno",
   wake="Incline Village", sleep="Reno (staging — verify spot)", miles="37", hrs="0.8",
   base="Lake Tahoe area",
   ian="AM ride or a second Tahoe trail (Glass Mtn + Painted Rock / Hole in the Ground). See Activities (Tahoe).",
   anny="CHOICE — a second Tahoe hike with Mochi (Page Meadows / Donner Lake Rim).",
   mochi="With Anny on her hike; in the van for the short evening drive.",
   together="Relaxed second day at the lake. After an early dinner, leave Incline in the EVENING for the ~50-min hop down to Reno — your staging point for the Loneliest Road. Fuel up + stock groceries in Reno tonight (last major city before the desert). See DRIVE PLAN.",
   notes="Reno is the last big-city fuel / Costco / Trader Joe's before US-50. Top off tonight or first thing — clean #2 only (NV pumps labeled 'Diesel' are ≤B5; pick a name brand).",
   route=["Incline Village, NV","Reno, NV"], acts_ref=True,
   drive_plan=dict(
     summary=("~37 mi · ~50 min, all in the evening. Depart Incline ~6:00 PM after an early dinner → "
              "arrive Reno ~6:50 PM. Tonight is about staging in Reno so Monday's 376-mi "
              "Loneliest-Road push starts from a real city. Fill the tank + stock up here — it's the last "
              "Costco / Trader Joe's / name-brand fuel before Great Basin."),
     route_url=maps_route(["Incline Village, NV","Reno, NV"]),
     route_label="Incline Village → Reno",
     rows=[
       dict(kind="depart", k="🌆 ~6:00 PM · Depart", v="Incline Village after an early dinner (evening drive — not a 9 AM start)."),
       dict(kind="leg", k="Evening hop · 6:00–6:50 PM",
            v="37 mi · ~50 min · NV-431 (Mt Rose Hwy) down to Reno. One driver — short enough to skip a swap.",
            url=maps_route(["Incline Village, NV","Reno, NV"])),
       dict(kind="stop", k="⛽ FUEL + 🛒 PROVISION · Reno tonight",
            v="Top off with clean #2 at a name-brand station (Chevron / Shell / Pilot) and stock groceries + water — the last real resupply before the desert.",
            url=None),
       dict(kind="arrive", k="🏁 ~6:50 PM · Arrive Reno", v="Sleep in / near Reno — candidates below to verify."),
     ],
     sleep_options=[
       ("Grand Sierra Resort RV Park", "East Reno off I-80 — paid full-hookup RV park at the casino; clean, easy on/off for the US-50 start. Reserve ahead.", pin(_q("Grand Sierra Resort RV Park, Reno, NV"))),
       ("Boomtown / Cabela's, Verdi", "West Reno on I-80 — Boomtown casino RV park + Cabela's lot, a classic overnight stop. ~15 min backtrack west.", pin(_q("Boomtown Casino RV Park, Verdi, NV"))),
       ("BLM dispersed E toward Fernley", "Free dispersed camping on BLM land east toward Fernley / US-50 ALT — check iOverlander for exact pullouts + recent reports.", pin(_q("BLM dispersed camping Fernley NV"))),
       ("Option: push on to Fallon", "If the evening's going well, ~1 hr more puts you in Fallon (casino RV park / Walmart) and trims ~60 mi off Monday. Harvest Hosts options around Fallon too.", pin(_q("Fallon, NV"))),
     ],
   )),
 "Jul 20 (Mon)": dict(banner="TRAVEL", plan="Reno → Great Basin via US-50 (the Loneliest Road)",
   wake="Reno", sleep="Great Basin NP (campground TBD)", miles="376", hrs="6", base="—",
   mochi="In the van for the drive (frequent leg-stretch stops). At Great Basin, dogs are leashed-only in campgrounds + on roads — NOT on park trails (incl. the Bristlecone trail), so the bristlecones are a human-only side trip.",
   together="The big push: Reno across Nevada on US-50 to Great Basin NP — 376 mi, ~6 hr driving, two fuel + driver-swap stops (Austin, Ely). Towns are 60–110 mi apart, so fuel discipline matters. Arrive afternoon/evening; camp in the park. See DRIVE PLAN.",
   notes="Longest driving day of the leg. US-50 services are sparse — never pass Austin, Eureka, or Ely below a half tank. NV 'Diesel' pumps are ≤B5 (name brands ≈ straight #2 — fine for the van). Great Basin / Baker has only minimal fuel: arrive with enough to get back to Ely (56 mi).",
   route=["Reno, NV","Grimes Point Archaeological Area, Fallon, NV","Sand Mountain Recreation Area, NV","Austin, NV","Hickison Petroglyph Recreation Area, NV","Ely, NV","Great Basin National Park, NV"],
   scenic="US-50 + Great Basin",
   drive_plan=dict(
     summary=("376 mi · ~6 hr driving. Depart Reno 9:00 AM (the by-9 default; earlier just buys more scenic "
              "slack) → arrive Great Basin ~4:00–4:30 PM (later if you linger at the scenic stops). "
              "Reno → Grimes Point → Sand Mountain → Austin → Hickison → Ely → Great Basin on US-50 — the "
              "three scenic stops are folded into the main route link above. Plan = two stops that each do "
              "double duty (fuel + driver swap); the sparse towns dictate where they fall."),
     route_url=maps_route(["Reno, NV","Grimes Point Archaeological Area, Fallon, NV","Sand Mountain Recreation Area, NV","Austin, NV","Hickison Petroglyph Recreation Area, NV","Ely, NV","Great Basin National Park, NV"]),
     route_label="Reno → Grimes Pt → Sand Mtn → Austin → Hickison → Ely → Great Basin",
     rows=[
       dict(kind="depart", k="🌅 9:00 AM · Depart",
            v="Reno with a FULL tank, Driver A. The 9 AM default — earlier is better, it buys slack for the scenic stops."),
       dict(kind="leg", k="Leg 1 · 9:00–11:45 AM (incl. Fallon + scenic)",
            v="173 mi · Driver A — Reno → Austin via Fallon. Optional quick top-off + leg-stretch in Fallon (mile 63, last sizable town). Then Grimes Point (~mile 73) and Sand Mountain (~mile 88) just past Fallon — both are on the main route link. Add ~30–45 min if you stop at both.",
            url=maps_route(["Reno, NV","Fallon, NV","Grimes Point Archaeological Area, Fallon, NV","Sand Mountain Recreation Area, NV","Austin, NV"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP + 🍽 LUNCH · Austin · ~11:45 AM–12:25 PM",
            v="Austin Chevron, right on Main St / US-50 (~mile 173). Fuel, swap drivers, lunch (~40 min). From a full Reno tank you reach here with margin; fill for the next 147 mi.",
            url=pin(_q("Chevron, Austin, NV 89310"))),
       dict(kind="leg", k="Leg 2 · 12:25–2:45 PM",
            v="147 mi · Driver B — Austin → Ely via Eureka. Hickison Petroglyphs (24 mi E of Austin, ~mile 197) is an easy leg-stretch and sits on the main route link. Optional top-off at the Eureka Chevron (mile 243) for insurance.",
            url=maps_route(["Austin, NV","Hickison Petroglyph Recreation Area, NV","Eureka, NV","Ely, NV"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP · Ely · ~2:45–3:05 PM",
            v="Ely (~mile 320) is the biggest town + best fuel on the route (multiple name-brand stations). Top off here (~20 min) — Great Basin / Baker has only minimal fuel and you want enough to get back to Ely (56 mi).",
            url=pin(_q("Ely, NV 89301"))),
       dict(kind="leg", k="Leg 3 · 3:05–4:05 PM",
            v="56 mi · fresh driver — Ely → Great Basin NP via NV-487 → NV-488 into Baker.",
            url=maps_route(["Ely, NV","Great Basin National Park, NV"])),
       dict(kind="arrive", k="🏁 ~4:05–4:30 PM · Arrive",
            v="Great Basin NP — set up camp (later if you lingered at the scenic stops). Bristlecones, Wheeler Peak Scenic Drive + Lehman Caves are the reward (see Scenic Stops)."),
     ],
     fuel_options=[
       ("Austin — Chevron", "On US-50 / Main St. Confirmed diesel (GasBuddy). Your Leg-1 fuel + swap + lunch stop, ~mile 173.", "Chevron, Austin, NV 89310"),
       ("Eureka — Chevron", "On US-50 / Main St. Confirmed diesel. Optional mid-leg top-off, ~mile 243.", "Chevron, Eureka, NV 89316"),
       ("Ely — best fuel on the route", "Biggest town; multiple name-brand stations — last reliable fuel before the park. Top off, ~mile 320.", "Ely, NV 89301"),
       ("Fallon — last big town, W end", "Top off here if you didn't fill in Reno; cheapest fuel before the desert, ~mile 63.", "Fallon, NV 89406"),
     ],
     scenic=[
       ("Grimes Point petroglyphs", "Right off US-50 ~10 mi E of Fallon — short petroglyph loop, quick leg-stretch.", pin(_q("Grimes Point Archaeological Area, Fallon, NV"))),
       ("Sand Mountain Rec Area", "Singing 600-ft sand dune ~25 mi E of Fallon, just off US-50 — pull-off views or a short walk.", pin(_q("Sand Mountain Recreation Area, NV"))),
       ("Hickison Petroglyph Rec Area", "BLM site 24 mi E of Austin off US-50 — easy interpretive loop, good mid-drive break.", pin(_q("Hickison Petroglyph Recreation Area, NV"))),
       ("Great Basin: bristlecones + Wheeler Peak", "Arrival reward — see the Scenic Stops tab (Bristlecone Pine Trail, Wheeler Peak Scenic Drive, Lehman Caves). Note: park trails are no-dogs.", turl(REF["scenic"])),
     ],
     sleep_options=[
       ("Great Basin: Wheeler Peak CG", "9,886 ft, near the bristlecone trailhead — the scenic one. First-come; the steep access road isn't advised for vans/RVs >24 ft. Cool nights even in July.", pin(_q("Wheeler Peak Campground, Great Basin National Park, NV"))),
       ("Great Basin: Lower / Upper Lehman Creek CG", "Lower Lehman is the year-round, most van-accessible campground (some sites reservable on recreation.gov); Upper Lehman is first-come. Near the visitor center.", pin(_q("Lehman Creek Campground, Great Basin National Park, NV"))),
       ("Baker Creek CG", "Gravel road, first-come, creekside — quieter. Check road conditions.", pin(_q("Baker Creek Campground, Great Basin National Park, NV"))),
       ("Sacramento Pass BLM (free)", "Free BLM rec area on US-50 just W of the park turnoff — easy fallback if park campgrounds fill. Check iOverlander.", pin(_q("Sacramento Pass Recreation Area, NV"))),
       ("Whispering Elms / Baker town", "Small RV park + dispersed options in Baker (the gateway town). Harvest Hosts may have a spot nearby.", pin(_q("Whispering Elms Motel RV Park, Baker, NV"))),
     ],
   )),
 "Jul 21 (Tue)": dict(banner="TRAVEL", plan="Drive to Moab",
   wake="Great Basin NP", sleep="Moab", miles="330", hrs="5.2", base="—",
   together="Depart Great Basin 9:00 AM → arrive Moab ~3:15 PM direct (or ~5:00 PM with the Dead Horse Point spur on the way in — it's also a classic sunset overlook from Moab). 328 mi east on US-50/US-6 → I-70 → US-191. Two fuel + driver-swap stops (Delta, Green River); the van runs clean #2 ULSD only. See DRIVE PLAN.",
   notes="Great Basin / Baker has only minimal fuel — top off whatever you can before leaving; first reliable diesel is Delta (~mile 101). UT has no biodiesel mandate, so name-brand pumps ≈ straight #2 (fine for the van).",
   route=["Great Basin National Park, NV","Delta, UT","Green River, UT","Dead Horse Point State Park, UT","Moab, UT"],
   scenic="Dead Horse Point",
   drive_plan=dict(
     summary=("328 mi · ~5.2 hr driving. Depart Great Basin 9:00 AM (the by-9 default) → arrive Moab "
              "~3:15 PM direct, or ~5:00 PM if you fold in Dead Horse Point on the way in. Great Basin → "
              "Delta → Green River → (Dead Horse Point) → Moab on US-50/US-6 → I-70 → US-191 — the scenic "
              "stop is folded into the main route link above. Two stops that double as fuel + driver swap; "
              "UT towns are 75–180 mi apart, so fill at Delta and Green River."),
     route_url=maps_route(["Great Basin National Park, NV","Delta, UT","Green River, UT","Dead Horse Point State Park, UT","Moab, UT"]),
     route_label="Great Basin → Delta → Green River → Dead Horse Point → Moab",
     rows=[
       dict(kind="depart", k="🌅 9:00 AM · Depart",
            v="Great Basin NP, Driver A — with as much fuel as Baker could give you. First reliable diesel is Delta (~mile 101)."),
       dict(kind="leg", k="Leg 1 · 9:00–10:36 AM",
            v="101 mi · Driver A — Great Basin → Delta, UT on US-50 E / US-6 E (high desert, dead-straight valley miles into Utah).",
            url=maps_route(["Great Basin National Park, NV","Delta, UT"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP + 🍽 LUNCH · Delta · ~10:36–11:10 AM",
            v="Maverik, 44 N US-6, Delta (RV lanes, 24-hr diesel). Fuel, swap drivers, early lunch (~35 min). Fill here — it's 177 mi to Green River.",
            url=pin(_q("Maverik, 44 N US Highway 6, Delta, UT 84624"))),
       dict(kind="leg", k="Leg 2 · 11:10 AM–2:00 PM",
            v="177 mi · Driver B — Delta → Green River via Salina + I-70 through the San Rafael Swell (the scenic stretch). 177 mi is near the van's range, so top off at the Salina Flying J (I-70 Exit 253, ~mile 176) for insurance.",
            url=maps_route(["Delta, UT","Salina, UT","Green River, UT"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP · Green River · ~2:00–2:20 PM",
            v="Pilot Travel Center, 1085 E Main St, Green River (or Maverik, 1475 W Main). Top off + swap before the last push (and the Dead Horse spur).",
            url=pin(_q("Pilot Travel Center, 1085 E Main St, Green River, UT 84525"))),
       dict(kind="leg", k="Leg 3 · 2:20–3:15 PM",
            v="52 mi · fresh driver — Green River → Moab on I-70 E to Crescent Junction, then US-191 S. The Dead Horse Point / UT-313 turnoff is ~22 mi N of Moab — peel off here for the overlook, or save it for sunset.",
            url=maps_route(["Green River, UT","Moab, UT"])),
       dict(kind="arrive", k="🏁 ~3:15 PM · Arrive (or ~5:00 PM w/ Dead Horse)",
            v="Moab — set up camp (candidates below). Dead Horse Point is a 44-mi round-trip spur off US-191 (gooseneck of the Colorado 2,000 ft below) — best at sunset; Mochi OK at all overlooks on leash."),
     ],
     fuel_options=[
       ("Delta — Maverik (PRIMARY)", "44 N US-6 · ~mile 101. RV lanes + 24-hr diesel; first reliable fuel in Utah. Your fuel + swap + lunch stop.", "Maverik, 44 N US Highway 6, Delta, UT 84624"),
       ("Salina — Flying J (top-off)", "I-70 Exit 253 · ~mile 176. Full truck stop where US-50 meets I-70 — the insurance top-off on the long Delta→Green River leg.", "Flying J Travel Center, Salina, UT 84654"),
       ("Green River — Pilot (2nd stop)", "1085 E Main St · ~mile 278. Name-brand truck-stop diesel; fill before the last 52 mi + any Dead Horse detour.", "Pilot Travel Center, 1085 E Main St, Green River, UT 84525"),
     ],
     scenic=[
       ("Dead Horse Point State Park", "The marquee — 2,000-ft gooseneck overlook of the Colorado, 22 mi out UT-313 (folded into the main route link). Stunning at sunset; leashed dogs OK at overlooks. Small day-use fee.", pin(_q("Dead Horse Point State Park, UT"))),
       ("San Rafael Swell — Ghost Rock viewpoint", "I-70 cuts through the Swell W of Green River — pull-offs at Ghost Rock + Eagle Canyon for wild eroded geology. Free, right on the route.", pin(_q("Ghost Rock Viewpoint, I-70, Utah"))),
       ("Black Dragon Canyon", "I-70 just W of Green River (~exit 145) — short walk to a panel of pictographs incl. the 'black dragon'. Quick leg-stretch.", pin(_q("Black Dragon Canyon, Green River, UT"))),
       ("Sego Canyon rock art", "Off I-70 at Thompson Springs (~exit 187) — Barrier-Canyon / Fremont / Ute panels, an easy detour near the end.", pin(_q("Sego Canyon Rock Art Panel, Thompson Springs, UT"))),
     ],
     sleep_options=[
       ("UT-128 / Colorado Riverway BLM", "The classic Moab van corridor — a string of BLM campgrounds (Goose Island, Big Bend, Hal Canyon) along the Colorado NE of town. Some reservable on recreation.gov, rest first-come.", pin(_q("Goose Island Campground, Moab, UT"))),
       ("Willow Springs Road (free BLM)", "Free dispersed off US-191 N of Moab — popular, gets busy; high-clearance helps past the first pullouts. Check iOverlander.", pin(_q("Willow Springs Road dispersed camping, Moab, UT"))),
       ("Sand Flats Recreation Area", "Fee area by the Slickrock trail E of town — established sites, quick to downtown. First-come + some reservable.", pin(_q("Sand Flats Recreation Area, Moab, UT"))),
       ("Ken's Lake Campground (BLM)", "S of Moab off US-191 — reservable BLM sites by a small reservoir, quieter than the river corridor.", pin(_q("Ken's Lake Campground, Moab, UT"))),
       ("In-town RV (full hookup)", "Moab Valley RV Resort / Slickrock Campground if you want hookups + showers in town. Reserve ahead in peak July.", pin(_q("Moab Valley RV Resort, Moab, UT"))),
     ],
   )),
 "Jul 22 (Wed)": dict(banner="TRAVEL → ARRIVAL", plan="Chautauqua hike + settle into Boulder",
   wake="Moab", sleep="Boulder — Airbnb", miles="368", hrs="5.8", base="582 Locust Pl, Boulder",
   mochi="With everyone at Chautauqua + downtown (leashed).",
   together="Depart Moab 8:00 AM (early start beats the Glenwood-Canyon traffic + afternoon heat) → arrive Boulder ~3:20 PM. 368 mi on US-191 → I-70 E over the Rockies. Two fuel + driver-swap stops (Grand Junction, Glenwood Springs). PM: easy Chautauqua meadow walk (1–2 mi), get oriented, explore Baseline/Chautauqua. See DRIVE PLAN.",
   notes="8:00 AM start (earlier than the 9 AM default — it's a long day with mountain passes). Dead Horse Point is the opposite direction (a SW backtrack), so it lives on yesterday's plan; if you skipped it, it's a ~1.5-hr morning spur before you head east. CO has no biodiesel mandate — name-brand pumps ≈ clean #2.",
   route=["Moab, UT","Grand Junction, CO","Glenwood Springs, CO","582 Locust Pl, Boulder, CO"],
   scenic="Dead Horse Point",
   drive_plan=dict(
     summary=("368 mi · ~5.8 hr driving. Depart Moab 8:00 AM (earlier than the 9 AM default — long day, "
              "mountain passes) → arrive Boulder ~3:20 PM, in time for a Chautauqua meadow walk. Moab → "
              "Grand Junction → Glenwood Springs → Boulder on US-191 → I-70 E. Two stops that double as "
              "fuel + driver swap; the I-70 climb (Vail Pass, Eisenhower Tunnel) burns more, so fill at "
              "Glenwood before the high country."),
     route_url=maps_route(["Moab, UT","Grand Junction, CO","Glenwood Springs, CO","582 Locust Pl, Boulder, CO"]),
     route_label="Moab → Grand Junction → Glenwood Springs → Boulder",
     rows=[
       dict(kind="depart", k="🌅 8:00 AM · Depart",
            v="Moab, Driver A, full tank. Early start (not the 9 AM default) — it's 368 mi with two passes, and you want Boulder PM with daylight for Chautauqua."),
       dict(kind="leg", k="Leg 1 · 8:00–9:45 AM",
            v="112 mi · Driver A — Moab → Grand Junction on US-191 N to Crescent Junction, then I-70 E into Colorado.",
            url=maps_route(["Moab, UT","Grand Junction, CO"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP + ☕ 2ND BREAKFAST · Grand Junction · ~9:45–10:20 AM",
            v="Pilot Flying J, 2195 Hwy 6 & 50 (I-70 Exit 26) — 8 diesel lanes, biggest fuel on the route. Fuel, swap drivers, coffee/snack (~35 min).",
            url=pin(_q("Pilot Flying J, 2195 Highway 6 and 50, Grand Junction, CO 81505"))),
       dict(kind="leg", k="Leg 2 · 10:20–11:42 AM",
            v="89 mi · Driver B — Grand Junction → Glenwood Springs on I-70 E up the Colorado River, into Glenwood Canyon (the scenic stretch — sheer walls, river, bike path).",
            url=maps_route(["Grand Junction, CO","Glenwood Springs, CO"])),
       dict(kind="stop", k="⛽ FUEL + 🔄 SWAP + 🍽 LUNCH · Glenwood Springs · ~11:42 AM–12:20 PM",
            v="Top off + swap + lunch (~38 min). Fill here — the next leg climbs Vail Pass (10,662 ft) + the Eisenhower Tunnel (11,158 ft), which eats fuel.",
            url=pin(_q("Sinclair, Glenwood Springs, CO 81601"))),
       dict(kind="leg", k="Leg 3 · 12:20–3:20 PM",
            v="171 mi · fresh driver — Glenwood → Boulder on I-70 E over the passes, then US-6 / CO-93 up to Boulder. Optional top-off at the Silverthorne Maverik (~mile 290) before the Eisenhower descent.",
            url=maps_route(["Glenwood Springs, CO","582 Locust Pl, Boulder, CO"])),
       dict(kind="arrive", k="🏁 ~3:20 PM · Arrive Boulder",
            v="582 Locust Pl — settle in, then the easy Chautauqua meadow walk (1–2 mi) to get oriented."),
     ],
     fuel_options=[
       ("Grand Junction — Pilot Flying J", "2195 Hwy 6 & 50 (I-70 Exit 26) · ~mile 112. 8 diesel lanes — your primary fuel + swap stop on the CO side.", "Pilot Flying J, 2195 Highway 6 and 50, Grand Junction, CO 81505"),
       ("Glenwood Springs — name-brand", "On Hwy 6/82 · ~mile 201. Fill + swap + lunch before the high passes (Sinclair / Kum & Go / Conoco in town).", "Sinclair, Glenwood Springs, CO 81601"),
       ("Silverthorne — Maverik (top-off)", "Blue River Pkwy off I-70 · ~mile 290. Optional insurance top-off before the Eisenhower Tunnel descent to the Front Range.", "Maverik, Silverthorne, CO 80498"),
     ],
     scenic=[
       ("Glenwood Canyon (I-70)", "One of the most scenic stretches of interstate in the US — sheer canyon walls along the Colorado. No detour needed; stop at the Hanging Lake / No Name rest areas to stretch.", pin(_q("Glenwood Canyon Rest Area, Glenwood Springs, CO"))),
       ("Dead Horse Point — if you skipped it", "If you didn't do it yesterday, it's a ~1.5-hr morning spur (44 mi round-trip off US-191) BEFORE you turn east — adds ~1.5 hr to the day, so leave Moab earlier.", pin(_q("Dead Horse Point State Park, UT"))),
       ("Vail Pass / Continental Divide", "I-70 tops out at Vail Pass (10,662 ft) then the Eisenhower Tunnel (11,158 ft) — pull off at the Vail Pass rest area for alpine views.", pin(_q("Vail Pass Rest Area, CO"))),
     ],
   )),
 "Aug 1 (Sat)": dict(banner="TRAVEL → ARRIVAL", plan="Drive Boulder → Steamboat (+ a short hike — your pick)",
   wake="Boulder — Airbnb", sleep="Steamboat — Airbnb", miles="165–175", hrs="3.5 + hike", base="1036 Lincoln Ave, Steamboat",
   route=[BASE["BLD"], BASE["STM"]],
   together=("Leave the Boulder Airbnb by 8:30. Eat breakfast OUT in Boulder (no dishes to clean before you go). "
             "Then it's your call how you get to Steamboat — four routes below, each pairing a different short, "
             "excellent hike with a different arrival time. Settle into Steamboat, explore downtown: Storm Peak "
             "Brewing (dogs inside!), Yampa River walk."),
   notes=("Skipping the Farmers Market this year. The whole drive is ~165–175 mi — well under the van's ~200 mi "
          "range, so NO fuel stop is needed on a full tank (Sprinter takes clean #2 ULSD only — no biodiesel). "
          "Arrival times below assume rolling out of Boulder ~9:15 after breakfast."),
   opp="Steamboat Pro Rodeo (BBQ 6pm, rodeo 7:30, Romick Arena — steamboatprorodeo.com) · Movies on the Mountain (Gondola Sq, sunset, no dogs)",
   menu_next="STM", dining="Steamboat", daycare="Steamboat",
   drive_plan=dict(
     summary=("Boulder → Steamboat is ~165–175 mi / ~3.3–3.7 hr of driving depending on the route — well under the "
              "van's ~200 mi range, so NO fuel stop needed on a full tank. Leave by 8:30, eat breakfast OUT in "
              "Boulder, then ⚠️ DECIDE how you're getting there: four routes below, each pairs a different short "
              "hike with a different Steamboat arrival. Tap an option's name to open that exact route in Google "
              "Maps. ETAs assume rolling out of Boulder ~9:15 after breakfast."),
     route_url=maps_route([BASE["BLD"], BASE["STM"]]),
     route_label="Boulder → Steamboat (direct reference — pick a hike route below)",
     rows=[
       dict(kind="depart", k="🚐 8:30 AM · Depart",
            v="Roll out of the Boulder Airbnb (582 Locust Pl)."),
       dict(kind="leg", k="🍳 ~8:30–9:15 · Breakfast in Boulder",
            v=("Eat out so there are no dishes to clean. Dog-friendly picks: Nopalito (Boulder's 'ultimate "
               "breakfast burrito,' grab-and-go fuel), Santo (chef-driven, patio), or Lucile's (Creole, dogs at "
               "outdoor tables). Back on the road by ~9:15."),
            url=pin(_q("Nopalito Restaurant, Boulder, CO"))),
     ],
     route_options=[
       dict(name="① BERTHOUD PASS  ·  arrive Steamboat ~2:30 PM",
            url=maps_route([BASE["BLD"], "Berthoud Pass, CO 80438", BASE["STM"]]),
            note=("ON the US-40 route — zero detour. 11,307-ft pass; walk the Continental Divide Trail "
                  "out-and-back as far as you like (~45–90 min, flat-to-rolling, turn around whenever). Biggest "
                  "views for the least effort, right at the mid-drive high point. Dogs: leashed, fine. Heads-up: "
                  "high + exposed — go before afternoon storms and pack a layer.")),
       dict(name="② HERMAN GULCH  ·  arrive ~2:00 PM (short) / ~4:30 PM (full lake)",
            url=maps_route([BASE["BLD"], "Herman Gulch Trailhead, Silver Plume, CO 80476", BASE["STM"]]),
            note=("Just off I-70 at Bakerville (reroutes via Silverthorne / CO-9). Aug 1 = PEAK wildflowers, one of "
                  "Colorado's best displays. Full hike 6.5 mi / 1,700 ft to Herman Lake (~3.5–4 hr); or turn around "
                  "at the meadows for a short version (~1.5 hr). Dogs: leashed, fine. The big-payoff pick if you "
                  "want a real hike.")),
       dict(name="③ RABBIT EARS PEAK  ·  arrive Steamboat ~3:30 PM",
            url=maps_route([BASE["BLD"], "Rabbit Ears Peak Trailhead, Colorado", BASE["STM"]]),
            note=("Near the END — 32 min from the Airbnb, so you knock out the driving first and hike on fresher "
                  "legs. ~5–6 mi / 700 ft (shortenable) through meadows to the iconic twin-rock formation. Dogs: "
                  "leashed, fine. Trailhead access road can be rough — clearance helps.")),
       dict(name="④ FISH CREEK FALLS  ·  in Steamboat by ~12:30 PM, hike after",
            url=maps_route([BASE["BLD"], BASE["STM"], "Fish Creek Falls Trailhead, Steamboat Springs, CO 80487"]),
            note=("No mid-drive stop — drive straight through (~3h16), drop bags, then the falls is 0.5 mi / 5 min "
                  "from town. 0.25-mi paved overlook of a 280-ft waterfall, or 2.5 mi RT to the upper falls. $5 "
                  "parking (cash/check). The low-stress / bad-weather fallback — earliest arrival by far. Dogs: "
                  "leashed.")),
     ],
   )),
 "Aug 6 (Thu)": dict(banner="TRAVEL", plan="Drive Steamboat → Twin Lakes (Geotrek meetup)",
   wake="Steamboat — Airbnb", sleep="Twin Lakes", miles="144", hrs="2.75", base="—",
   ian="AM trail run: Emerald Mountain system (6–8 mi from Howelsen Hill). Back by lunch, then drive.",
   anny="AM hike: Red Dirt Trail with Mochi (gentle, creeks, wildflowers).",
   mochi="With Anny on Red Dirt.", together="Drive to Twin Lakes after the morning. Geotrek meetup begins.",
   notes="", route=["Steamboat Springs, CO","Twin Lakes, CO"]),
 "Aug 7 (Fri)": dict(banner="FIXED", plan="Geotrek meetup — Twin Lakes",
   wake="Twin Lakes", sleep="Twin Lakes", miles="0", hrs="0", base="Twin Lakes",
   together="Geotrek meetup in Twin Lakes.", notes="Group event — agenda set by Geotrek."),
 "Aug 8 (Sat)": dict(banner="FIXED", plan="Geotrek meetup — Twin Lakes",
   wake="Twin Lakes", sleep="Twin Lakes", miles="0", hrs="0", base="Twin Lakes",
   together="Geotrek meetup in Twin Lakes.", notes="Group event — agenda set by Geotrek."),
 "Aug 9 (Sun)": dict(banner="TRAVEL → ARRIVAL", plan="Drive Twin Lakes → Crested Butte",
   wake="Twin Lakes", sleep="Crested Butte — Airbnb", miles="144", hrs="2.5", base="6 Emmons Rd, Mt CB",
   mochi="Can swim at Emerald Lake.",
   together="Arrive CB ~noon–1pm. Check in. PM: Emerald Lake hike (1.7 mi, easy, flat — Mochi swims), 10 min from town. Explore Elk Avenue — Butte Bagels (closes ~2pm), dinner at The Breadery or The Public House.",
   notes="PM orienting hike after settling in.",
   backup="Copper Creek / Judd Falls (moderate, dog-friendly) if Emerald Lake is crowded.",
   menu_next="CB", scenic="Kebler Pass"),
 "Aug 12 (Wed)": dict(banner="TRAVEL", plan="Drive CB → SLC via Grand Junction + Colorado National Monument",
   wake="Crested Butte — Airbnb", sleep="SLC", miles="380", hrs="6.5", base="—",
   together="Long drive (~6.5 hr via Grand Junction/I-70). Colorado National Monument (Rim Rock Drive, 23 mi, 19 overlooks, dog-friendly, free w/ pass) adds 1.5–2 hr — worth it. Evening with SLC friend.",
   opp="SLC (Wed eve): Twilight Concert Series — check saltlakearts.org · Pepper + Myles Smith at The Lot at The Complex. (Optional — you're also seeing a friend tonight.)",
   notes="", route=["Crested Butte, CO","Colorado National Monument, CO","Salt Lake City, UT"], scenic="Colorado Natl Monument"),
 "Aug 13 (Thu)": dict(banner="TRAVEL", plan="Drive SLC → Ely",
   wake="SLC", sleep="Ely, NV", miles="242", hrs="4.0", base="—",
   together="Drive SLC → Ely. Plan the Nevada Northern Railway Museum visit for tomorrow AM before pushing to Mammoth.",
   notes="NNR Museum (1100 Ave A, Ely) — 1906 steam depot + roundhouse, Mon–Sat ~8am–5pm. Excursion train Sat–Sun only (not available — arrive Thu).",
   route=["Salt Lake City, UT","Ely, NV"], scenic="NNR"),
 "Aug 14 (Fri)": dict(banner="FIXED", plan="Drive Ely → Mammoth Lakes",
   wake="Ely, NV", sleep="Mammoth Lakes", miles="295", hrs="4.5", base="—",
   together="AM: optional Nevada Northern Railway Museum. Drive to Mammoth — must arrive by afternoon. Emily bach party in the Mammoth area.",
   notes="Must arrive Mammoth (or Bishop) by afternoon.", route=["Ely, NV","Mammoth Lakes, CA"]),
 "Aug 15 (Sat)": dict(banner="FIXED — IAN SOLO", plan="Ian — Mammoth Bike Park day 1 / Mochi at daycare",
   wake="Mammoth Lakes", sleep="Van — Bishop area", miles="0", hrs="0", base="Mammoth Lakes",
   ian="Drop Mochi at PUP Hiking Co (7:30am) → full day at Mammoth Mountain Bike Park (3,100 ft descent; Ikon 2 free days, else ~$65–80). Pick Mochi up 4–4:30pm. See Activities (Mammoth).",
   anny="Emily bach party (unavailable).",
   mochi="PUP Hiking Company — drop 7:30–8am, pickup 4–4:30pm. (760) 582-2176. BOOK AHEAD. Backup: Sierra Dog Ventures (714) 609-8510.",
   together="Ian + Mochi evening — pick up from daycare, settle in.",
   notes="High altitude (9,000–11,000 ft) — acclimate. Dogs not allowed at the bike park (hence daycare).",
   acts_ref=True, daycare="Mammoth"),
 "Aug 16 (Sun)": dict(banner="FIXED — IAN SOLO", plan="Ian — Lower Rock Creek Canyon MTB / Mochi at daycare",
   wake="Mammoth Lakes", sleep="Van — Bishop area", miles="0", hrs="0", base="Mammoth Lakes",
   ian="Drop Mochi at daycare → Lower Rock Creek Canyon (35 min to Tom's Place, US-395). 8–9 mi, 1,900 ft descent through aspen canyon — best trail ride in the Eastern Sierra. See Activities (Mammoth).",
   anny="Emily bach party (unavailable).",
   mochi="PUP Hiking or Sierra Dog Ventures. Backup: Donna the Dog Lady, Round Valley (50 min, (760) 387-2331).",
   together="Ian + Mochi — evening walk, easy wind-down.",
   notes="Lower Rock Creek is often shuttled downhill for max descent. Plan a full day with the drive.",
   acts_ref=True, daycare="Mammoth"),
 "Aug 17 (Mon)": dict(banner="FIXED — IAN + MOCHI", plan="Hike + acclimation day (Convict Lake / Hot Creek)",
   wake="Mammoth Lakes", sleep="Mammoth Lakes", miles="0", hrs="0", base="Mammoth Lakes",
   ian="Lighter day — Mammoth Rock / Sherwin Ridge AM (10 min, 4 mi warm-up). PM: Hot Creek Geological Site (thermal pools, dog overlook) + Convict Lake loop (2 mi easy, Mochi swims).",
   anny="Bach party winding down — may be free PM for Convict Lake together.",
   mochi="Ian has Mochi today — no daycare. Hot Creek + Convict Lake are both dog-friendly.",
   together="If Anny's free PM: Hot Creek + Convict Lake together. Easy, beautiful.",
   notes="", scenic="Hot Creek", daycare="Mammoth"),
 "Aug 18 (Tue)": dict(banner="TRAVEL", plan="Drive Tioga Pass → Fresno area (drop Mochi at boarding)",
   wake="Mammoth Lakes", sleep="Near Fresno", miles="150", hrs="2.3", base="—",
   together="Scenic drive over Tioga Pass through Yosemite ($35 entrance). Drop Mochi at Fresno-area boarding (Elaine's Pet Resorts recommended — (559) 227-5959; book well ahead for August).",
   notes="Mochi boards here through the Rae Lakes Loop.", route=["Mammoth Lakes, CA","Yosemite (Tioga Pass), CA","Fresno, CA"]),
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
   wake="Van near Fresno", sleep="Home", miles="180", hrs="3.0", base="—",
   together="Pick up Mochi, drive home. Trip complete.", notes="",
   route=["Fresno, CA","Home (Bay Area)"]),
}

# order of fixed tabs (chronological, for placement)
FIXED_ORDER = list(FIXED.keys())

# which calendar dates are flexible (Itinerary date cell -> DAY OPTIONS)
FLEX_DATES = (["Jul %d" % d for d in range(23,32)] +
              ["Aug %d" % d for d in range(2,6)] +
              ["Aug 10","Aug 11"])

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
    bikes = any(k in ("valmont","walker_mtb","steamboat_bp","evolution") for k in o["acts"])
    t.lane("🚵" if bikes else "🥾","Ian", o["ian"], IAN_BG)
    t.lane("🥾","Anny", o["anny"], ANNY_BG)
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
            t.section("🥾 CHOOSE YOUR ROUTE — pick ONE  ·  each is a different hike + a different Steamboat arrival")
            t.callout("⚠️  DECIDE before you leave: these are FOUR different ways to drive Boulder → Steamboat. "
                      "Tap an option's name to open that exact route in Google Maps.")
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
        orphans=[]
        for s in sh.fetch_sheet_metadata()["sheets"]:
            ttl=s["properties"]["title"]
            is_orphan = (ttl.startswith("__tmp__") or ttl.startswith("Day BLD-E")
                         or (OLD_RE.match(ttl) and ttl not in fixed_gid))
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
