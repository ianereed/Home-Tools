import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets']
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
ws = sh.worksheets()[0]

updates = [
    # ── HEADER ───────────────────────────────────────────────────────────────
    {"range": "R22", "values": [["Opportunities"]]},

    # ── ROW 24 (Jul 17 — Depart day) ─────────────────────────────────────────
    {"range": "G24", "values": [["Final surgery appt — depart after 5pm"]]},
    {"range": "H24", "values": [["Fixed appointment, cannot move. OK to leave town after 5pm."]]},

    # ── ROW 25 (Jul 18 — Lake Tahoe) ─────────────────────────────────────────
    {"range": "G25", "values": [["Drive to Lake Tahoe | AM: Ian MTB, Anny hikes with Mochi | PM: Shakespeare"]]},
    {"range": "H25", "values": [[""]]},
    {"range": "R25", "values": [["Lake Tahoe Shakespeare Festival — 'Macbeth' or 'Heart of Robin Hood', Sand Harbor, 7:30pm (gates 5:30pm). Book: laketahoeshakespeare.com"]]},

    # ── ROW 26 (Jul 19 — drive day) ──────────────────────────────────────────
    {"range": "H26", "values": [[""]]},

    # ── ROW 27 (Jul 20 — drive to Moab) ──────────────────────────────────────
    {"range": "H27", "values": [[""]]},

    # ── ROW 28 (Jul 21 — Dead Horse / Boulder arrival) ───────────────────────
    {"range": "G28", "values": [["AM: Dead Horse Point → drive to Boulder | Arrive ~5-6pm"]]},
    {"range": "H28", "values": [["Leave Moab by 8am. Dead Horse Point: 32mi from Moab — Colorado River gooseneck canyon, 1,000ft below. Mochi allowed at all overlooks on leash."]]},

    # ── ROW 29 (Jul 22 — Boulder arrival day) ────────────────────────────────
    {"range": "G29", "values": [["Chautauqua hike + settle in"]]},
    {"range": "H29", "values": [[""]]},
    {"range": "M29", "values": [[""]]},
    {"range": "R29", "values": [["Bands on the Bricks (free, Pearl St, 5:30-9pm) | Colorado Shakespeare Festival: Twelfth Night (outdoor, 7:30pm, CU campus)"]]},

    # ── ROW 30 (Jul 23 — Green Mountain) ─────────────────────────────────────
    {"range": "H30", "values": [[""]]},
    {"range": "M30", "values": [["Afternoon: neighborhood tour + Trident Booksellers."]]},
    {"range": "R30", "values": [["Colorado Music Festival — Slatkin: Copland + Gershwin, Chautauqua | Shakespeare: Shakespeare in Love (outdoor 7:30pm)"]]},

    # ── ROW 31 (Jul 24 — Sanitas) ────────────────────────────────────────────
    {"range": "G31", "values": [["Solo AM: Ian runs Mt Sanitas, Anny hikes Sanitas Valley | Afternoon together"]]},
    {"range": "H31", "values": [[""]]},
    {"range": "M31", "values": [["Afternoon: errands + neighborhoods."]]},
    {"range": "R31", "values": [["Colorado Shakespeare Festival: Twelfth Night (outdoor 7:30pm) | Julius Caesar (indoor 7pm)"]]},

    # ── ROW 32 (Jul 25 — Nederland) ──────────────────────────────────────────
    {"range": "H32", "values": [[""]]},
    {"range": "M32", "values": [[""]]},
    {"range": "R32", "values": [["Colorado Shakespeare Festival: Shakespeare in Love (outdoor 7:30pm) | Julius Caesar (indoor 7pm)"]]},

    # ── ROW 33 (Jul 26 — Valmont/Music) ──────────────────────────────────────
    {"range": "G33", "values": [["Ian: Valmont Bike Park AM | Anny + Mochi hike | Colorado Music Festival evening"]]},
    {"range": "H33", "values": [[""]]},
    {"range": "M33", "values": [["Boulder Farmers Market (Sat, 13th & Canyon, 8am-2pm)"]]},
    {"range": "R33", "values": [["Colorado Music Festival at Chautauqua | Shakespeare: Twelfth Night (outdoor 7:30pm) | Julius Caesar (indoor 7pm) | Boulder Farmers Market (Sat, 13th & Canyon, 8am-2pm)"]]},

    # ── ROW 34 (Jul 27 — Eldorado Canyon) ────────────────────────────────────
    {"range": "H34", "values": [["Van with Geotrek"]]},
    {"range": "M34", "values": [[""]]},
    {"range": "R34", "values": [["Colorado Music Festival at Chautauqua (evening)"]]},

    # ── ROW 35 (Jul 28 — Brunch + climbing) ──────────────────────────────────
    {"range": "G35", "values": [["Brunch + walkability test + afternoon climbing"]]},
    {"range": "H35", "values": [["Van with Geotrek"]]},
    {"range": "M35", "values": [["Brunch downtown. Walkability test. Afternoon: rope climbing at Movement or BRC."]]},
    {"range": "R35", "values": [["Avery Brewing Comedy Night ($5 pints during comedy) | Shakespeare: Twelfth Night (outdoor 7:30pm)"]]},

    # ── ROW 36 (Jul 29 — Walker Ranch) ───────────────────────────────────────
    {"range": "G36", "values": [["Ian trail run: Walker Ranch | Anny + Mochi hike Flatirons Vista"]]},
    {"range": "H36", "values": [["Van with Geotrek"]]},
    {"range": "M36", "values": [[""]]},
    {"range": "R36", "values": [["Bands on the Bricks SEASON FINALE (free, Pearl St, 5:30-9pm — don't miss!) | Colorado Shakespeare: Twelfth Night + Julius Caesar"]]},

    # ── ROW 37 (Jul 30 — Golden + Red Rocks) ─────────────────────────────────
    {"range": "G37", "values": [["Day trip to Golden | Evening: Red Rocks Killer Queen"]]},
    {"range": "H37", "values": [["Van back with A/C!"]]},
    {"range": "M37", "values": [[""]]},
    {"range": "R37", "values": [["Red Rocks: Killer Queen tribute (Queen concert, Thu Jul 30 evening — from Golden, even closer!) | Book: redrocksonline.com"]]},

    # ── ROW 38 (Jul 31 — Indian Peaks) ───────────────────────────────────────
    {"range": "H38", "values": [[""]]},
    {"range": "M38", "values": [[""]]},
    {"range": "R38", "values": [["Colorado Shakespeare Festival: Twelfth Night (outdoor 7:30pm)"]]},

    # ── ROW 39 (Aug 1 — Drive to Steamboat) ──────────────────────────────────
    {"range": "G39", "values": [["Drive Boulder → Steamboat | Arrive ~noon"]]},
    {"range": "H39", "values": [["Leave Boulder by 7am to catch Steamboat Farmers Market (9am-2pm, Yampa St — dogs discouraged). Arrive Steamboat ~noon."]]},
    {"range": "R39", "values": [["Steamboat Farmers Market (9am-2pm, Yampa St) | Pro Rodeo (BBQ 6pm, rodeo 7:30pm, Romick Arena — steamboatprorodeo.com) | Movies on the Mountain (free, Gondola Square, sunset — no dogs)"]]},

    # ── ROW 40 (Aug 2 — Fish Creek Falls) ────────────────────────────────────
    {"range": "H40", "values": [[""]]},
    {"range": "R40", "values": [["Pro Rodeo (Fri+Sat all summer — BBQ 6pm, rodeo 7:30pm, Romick Arena)"]]},

    # ── ROW 41 (Aug 3 — Bike park + Anny hike) ───────────────────────────────
    {"range": "G41", "values": [["Ian bike park + Anny hikes Emerald Mtn | PM: Old Town Hot Springs"]]},
    {"range": "H41", "values": [[""]]},

    # ── ROW 42 (Aug 4 — Hahns Peak) ──────────────────────────────────────────
    {"range": "H42", "values": [[""]]},

    # ── ROW 43 (Aug 5 — Strawberry Park) ─────────────────────────────────────
    {"range": "G43", "values": [["Strawberry Park Hot Springs + town day"]]},
    {"range": "H43", "values": [["Old Town Hot Springs (no reservation, downtown) as backup. Mochi at Airbnb during hot springs."]]},
    {"range": "M43", "values": [[""]]},
    {"range": "R43", "values": [["Strawberry Park Hot Springs ($20/person CASH, no dogs — reserve 30 days ahead at strawberryhotsprings.com) | Music on the Green (free, 10am, Yampa River Botanic Park, Strings Music Festival)"]]},

    # ── ROW 44 (Aug 6 — Trail run + Sunset Happy Hour) ───────────────────────
    {"range": "G44", "values": [["Ian trail run + Sunset Happy Hour gondola | Anny hikes Red Dirt Trail"]]},
    {"range": "H44", "values": [[""]]},
    {"range": "I44", "values": [["Check if Ikon Pass covers Sunset Happy Hour at Thunderhead (free w/ pass)."]]},
    {"range": "M44", "values": [[""]]},
    {"range": "R44", "values": [["Sunset Happy Hour at Thunderhead — gondola to 9,100ft, live music + sunset. $30/person (free w/ Ikon Pass). No dogs."]]},

    # ── ROW 46 (Aug 8 — CB bike park day 1) ──────────────────────────────────
    {"range": "H46", "values": [[""]]},

    # ── ROW 47 (Aug 9 — Black Canyon day trip) ────────────────────────────────
    {"range": "H47", "values": [[""]]},
    {"range": "N47", "values": [["Drive to Black Canyon of the Gunnison NP (~1.5hrs). Dog-friendly overlooks + Cedar Point Trail."]]},
    {"range": "R47", "values": [["CB Farmers Market (9am-2pm, Elk Ave — local produce, meats, crafts)"]]},

    # ── ROW 48 (Aug 10 — Oh-Be-Joyful + Alpenglow) ───────────────────────────
    {"range": "G48", "values": [["Together hike: Oh-Be-Joyful or Judd Falls | Evening: Alpenglow Concert"]]},
    {"range": "H48", "values": [[""]]},
    {"range": "I48", "values": [[""]]},
    {"range": "M48", "values": [[""]]},
    {"range": "R48", "values": [["Alpenglow Concert — free, 5:30pm, CB Town Park (30+ year tradition). No pets inside boundary."]]},

    # ── ROW 49 (Aug 11 — CB bike park day 2) ─────────────────────────────────
    {"range": "G49", "values": [["Ian bike park day 2 + pack up | Anny hikes Three Lakes"]]},
    {"range": "H49", "values": [[""]]},

    # ── ROW 50 (Aug 12 — Drive CB → SLC) ─────────────────────────────────────
    {"range": "G50", "values": [["Drive CB → SLC via Grand Junction + Colorado National Monument"]]},
    {"range": "H50", "values": [["Colorado National Monument (Rim Rock Drive, 23mi, 19 overlooks, dog-friendly, free w/ America the Beautiful Pass) — adds 1.5-2hrs, worth it. Evening with SLC friend."]]},
    {"range": "R50", "values": [["Music on the Mountain (free, CBMR base area, Wed 5:30-8pm — would need to stay one extra CB night to attend)"]]},

    # ── ROW 51 (Aug 13 — Drive SLC → Ely) ───────────────────────────────────
    {"range": "G51", "values": [["Drive SLC → Ely | AM: Nevada Northern Railway Museum"]]},
    {"range": "H51", "values": [["Nevada Northern Railway Museum (1100 Ave A, Ely) — original 1906 steam depot + roundhouse. Open Mon-Sat ~8am-5pm. Visit Aug 14 AM before driving to Mammoth. Excursion train Sat-Sun only — not available this trip (arrive Thu)."]]},
    {"range": "R51", "values": [["SLC: Twilight Concert Series (Wed evenings — check saltlakearts.org) | Pepper + Myles Smith at The Lot at The Complex"]]},

    # ── ROW 52 (Aug 14 — Drive Ely → Mammoth) ────────────────────────────────
    {"range": "H52", "values": [["Emily Bach Party in Mammoth area. Arrive by afternoon."]]},

    # ── ROWS 53-54 (Aug 15-16 — Ian solo Mammoth) ────────────────────────────
    {"range": "G53", "values": [["Ian solo — Mammoth Bike Park day 1 | Mochi at PUP Hiking Co"]]},
    {"range": "G54", "values": [["Ian solo — Lower Rock Creek Canyon MTB | Mochi at daycare"]]},

    # ── ROW 55 (Aug 17 — Anny returns) ───────────────────────────────────────
    {"range": "H55", "values": [["Anny: Bach Party winding down. Convict Lake together in PM if she's free."]]},
]

ws.batch_update(updates, value_input_option='USER_ENTERED')
print(f"Done. {len(updates)} cell updates sent in one batch.")
