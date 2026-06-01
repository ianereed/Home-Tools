"""Build the 'Dining Guide' tab.

Data-driven + idempotent: deletes the existing 'Dining Guide' tab (if present)
and rebuilds it from the SECTIONS list below (trip-ordered base cities + day-trip /
transit stops, each its own color bar). Researched + verified May 2026; biased
toward popular walk-in / hole-in-the-wall locals' spots, with a few flagged
reservation splurges (⭐).

12 columns. Computed at build time:
  • From Airbnb  — walk/bike/drive + miles via Google Distance Matrix (cached to
    dining_distances.json). Mochi-aware: biking is only flagged "good" for spots we
    CAN'T bring the dog (indoor-only); dog-friendly bikeable spots say drive-w/-Mochi.
  • Come As You Are? — dress/dirt level (post-MTB dusty / post-hike sweaty / clean /
    dress-up), from type+price rules with a few overrides.
  • Address — native clickable Google-Maps link.
'Dog Friendly?' tracks Mochi (golden retriever) along the whole trip.
"""
import json
import os
import time
import urllib.parse
import urllib.request

import gspread
from config import SPREADSHEET_ID, CREDENTIALS_FILE, MAPS_API_KEY
from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets'])
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

def rgb(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

# ── COLORS ────────────────────────────────────────────────────────────────────
TITLE_BG  = rgb(15,  23,  42)
TAHOE_BG  = rgb(13,  71,  91)     # deep lake teal (Tahoe / Truckee)
MOAB_BG   = rgb(124,  57,   0)    # amber-orange (Moab)
BOULD_BG  = rgb(0,  105,  92)     # dark teal (Boulder + day trips)
STEAM_BG  = rgb(21,  101, 192)    # deep blue (Steamboat)
LEAD_BG   = rgb(56,   87,  35)    # high-country green (Leadville / BV / Twin Lakes)
CB_BG     = rgb(69,   27, 142)    # deep purple (Crested Butte + Aspen)
TRANSIT_BG= rgb(71,   85, 105)    # slate gray (SLC / Ely / Great Basin)
MAM_BG    = rgb(127,  29,  29)    # deep crimson (Mammoth / Bishop)
COL_HDR   = rgb(230, 230, 230)
DARK_TXT  = rgb(30,  30,  30)
WHITE     = rgb(255, 255, 255)
LINK_C    = {"red": 21/255, "green": 101/255, "blue": 192/255}

HEADERS = ["Restaurant / Place", "City", "From Airbnb", "Type", "Price",
           "Reservation", "Come As You Are?", "Dog Friendly?", "Phone",
           "Website", "Address", "Notes / Must-Know"]

# ── ADDRESSES (name without ⭐ -> street address) ──────────────────────────────
ADDRESSES = {
    # Tahoe / Truckee
    "Bridgetender Tavern & Grill": "65 W Lake Blvd, Tahoe City, CA 96145",
    "Alibi Ale Works": "931 Tahoe Blvd, Incline Village, NV 89451",
    # Moab
    "The Spoke on Center": "5 N Main St, Moab, UT 84532",
    "Trailhead Public House": "11 E 100 N, Moab, UT 84532",
    "Moab Brewery": "686 S Main St, Moab, UT 84532",
    "Love Muffin Cafe": "139 N Main St, Moab, UT 84532",
    # Boulder
    "Santo": "1265 Alpine Ave, Boulder, CO 80304",
    "Nopalito's": "1805 29th St, Ste 1138, Boulder, CO 80301",
    "The Parkway Cafe": "4700 Pearl St #4, Boulder, CO 80301",
    "Moe's Broadway Bagel": "3267 28th St, Boulder, CO 80304",
    "OZO Coffee": "1015 Pearl St #100, Boulder, CO 80302",
    "Boxcar Coffee Roasters": "1825 Pearl St B, Boulder, CO 80302",
    "Chautauqua Dining Hall": "900 Baseline Rd, Boulder, CO 80302",
    "T/aco": "1175 Walnut St, Boulder, CO 80302",
    "McDevitt Taco Supply": "4800 Baseline Rd Ste C-110, Boulder, CO 80303",
    "Audrey Jane's Pizza Garage": "2675 13th St, Boulder, CO 80304",
    "Dragonfly Noodle": "2014 10th St, Boulder, CO 80302",
    "Zoe Ma Ma": "919 Pearl St, Boulder, CO 80302",
    "Avery Brewing Co.": "4910 Nautilus Ct N, Boulder, CO 80301",
    "The Rayback Collective": "2775 Valmont Rd, Boulder, CO 80304",
    "Dushanbe Teahouse": "1770 13th St, Boulder, CO 80302",
    "Postino Boulder": "1468 Pearl St Ste 110, Boulder, CO 80302",
    "River and Woods": "2328 Pearl St, Boulder, CO 80302",
    "Mountain Sun Pub & Brewery": "1535 Pearl St, Boulder, CO 80302",
    "Southern Sun Pub & Brewery": "627 S Broadway, Boulder, CO 80305",
    "Upslope Brewing — Flatiron Park": "1898 S Flatiron Ct, Boulder, CO 80301",
    "Trident Booksellers & Cafe": "940 Pearl St, Boulder, CO 80302",
    "Frasca Food and Wine": "1738 Pearl St, Boulder, CO 80302",
    "Corrida": "1023 Walnut St Ste 400, Boulder, CO 80302",
    "Blackbelly": "1606 Conestoga St Ste 1, Boulder, CO 80301",
    # Golden
    "Cannonball Creek Brewing": "393 N Washington Ave, Golden, CO 80403",
    "New Terrain Brewing": "16401 Table Mountain Pkwy, Golden, CO 80403",
    "The Eddy Taproom & Hotel": "1640 8th St, Golden, CO 80401",
    "Table Mountain Grill": "1310 Washington Ave, Golden, CO 80401",
    "The Golden Mill": "1012 Ford St, Golden, CO 80401",
    "Windy Saddle Cafe": "1110 Washington Ave Ste 100, Golden, CO 80401",
    # Nederland
    "Crosscut Pizzeria & Taphouse": "4 E 1st St, Nederland, CO 80466",
    "Salto Coffee Works": "112 E 2nd St, Nederland, CO 80466",
    "Train Cars Coffee & Kava": "101 S Peak to Peak Hwy, Nederland, CO 80466",
    # Estes Park
    "Rock Cut Brewing Co.": "390 W Riverside Dr, Estes Park, CO 80517",
    "The Barrel": "251 Moraine Ave, Estes Park, CO 80517",
    "Rock Inn Mountain Tavern": "1675 CO-66, Estes Park, CO 80517",
    "Bird & Jim": "915 Moraine Ave, Estes Park, CO 80517",
    # Steamboat
    "Creekside Café & Grill": "131 11th St, Steamboat Springs, CO 80487",
    "Winona's": "617 Lincoln Ave, Steamboat Springs, CO 80487",
    "Yampa Valley Kitchen": "207 9th St, Steamboat Springs, CO 80487",
    "Lil' House Country Biscuits": "2093 Curve Plaza, Steamboat Springs, CO 80487",
    "Mountain Tap Brewery": "910 Yampa St, Steamboat Springs, CO 80487",
    "Storm Peak Brewing": "1885 Elk River Plaza, Steamboat Springs, CO 80487",
    "Salt & Lime": "628 Lincoln Ave, Steamboat Springs, CO 80487",
    "TacoCabo": "729 Yampa St, Steamboat Springs, CO 80487",
    "Back Door Grill": "825 Oak St, Steamboat Springs, CO 80487",
    "Moe's Original BBQ": "1898 Kamar Plaza, Steamboat Springs, CO 80487",
    "Seedhouse Coffee Roasters": "1009 Lincoln Ave, Steamboat Springs, CO 80487",
    "The Commons (food hall)": "56 7th St, Steamboat Springs, CO 80487",
    "Laundry Kitchen & Cocktails": "127 11th St, Steamboat Springs, CO 80487",
    "Big Iron Coffee Co.": "635 Lincoln Ave, Steamboat Springs, CO 80487",
    "Freshies Restaurant": "595 S Lincoln Ave, Steamboat Springs, CO 80487",
    "The Clark Store": "54175 RCR 129, Clark, CO 80428",
    "Aurum Food & Wine": "811 Yampa St, Steamboat Springs, CO 80487",
    "Café Diva": "1855 Ski Time Square Dr, Steamboat Springs, CO 80487",
    # Leadville / BV / Twin Lakes
    "The Twin Lakes Inn & Saloon": "6435 E State Hwy 82, Twin Lakes, CO 81251",
    "Tennessee Pass Cafe": "222 Harrison Ave, Leadville, CO 80461",
    "High Mountain Pies": "115 W 4th St, Leadville, CO 80461",
    "City on a Hill Coffee": "508 Harrison Ave, Leadville, CO 80461",
    "Eddyline Brewery & Pub": "102 Linderman Ave, Buena Vista, CO 81211",
    "Deerhammer Distillery": "321 E Main St, Buena Vista, CO 81211",
    "Tennessee Pass Cookhouse": "E Tennessee Rd, Leadville, CO 80461",
    # Crested Butte / Mt CB
    "Secret Stash": "303 Elk Ave, Crested Butte, CO 81224",
    "Mikey's Pizza": "611 3rd St, Crested Butte, CO 81224",
    "Teocalli Tamale": "311 Elk Ave, Crested Butte, CO 81224",
    "Bonez Tequila Bar & Grill": "130 Elk Ave, Crested Butte, CO 81224",
    "Paradise Cafe": "435 6th St, Crested Butte, CO 81224",
    "McGill's": "228 Elk Ave, Crested Butte, CO 81224",
    "Camp 4 Coffee": "402 1/2 Elk Ave, Crested Butte, CO 81224",
    "Rumors Coffee & Tea House": "414 Elk Ave, Crested Butte, CO 81224",
    "The Eldo Brewery & Brewpub": "215 Elk Ave, Crested Butte, CO 81224",
    "Bruhaus": "223 Elk Ave, Crested Butte, CO 81224",
    "Butte Burgers": "22 Crested Mtn Ln, Mount Crested Butte, CO 81225",
    "Tin Cup Pasty Co.": "620 Gothic Rd Ste C150, Mount Crested Butte, CO 81225",
    "Montanya Distillers": "204 Elk Ave, Crested Butte, CO 81224",
    "The Public House": "202 Elk Ave, Crested Butte, CO 81224",
    "The Breadery": "209 Elk Ave, Crested Butte, CO 81224",
    "Butte Bagels": "218 Maroon Ave Ste A, Crested Butte, CO 81224",
    "Butte 66": "10 Crested Butte Way, Mount Crested Butte, CO 81225",
    "Coffee Lab": "620 Gothic Rd Ste C-100, Mount Crested Butte, CO 81225",
    "Soupçon": "127A Elk Ave, Crested Butte, CO 81224",
    "The Sunflower": "214 Elk Ave, Crested Butte, CO 81224",
    # Aspen
    "Meat & Cheese": "319 E Hopkins Ave, Aspen, CO 81611",
    "White House Tavern": "302 E Hopkins Ave, Aspen, CO 81611",
    # Transit
    "Kerouac's at Stargazer Inn": "115 S Baker Ave, Baker, NV 89311",
    "Red Iguana": "736 W North Temple, Salt Lake City, UT 84116",
    "Crown Burgers": "377 E 200 South, Salt Lake City, UT 84111",
    "Lucky 13 Bar & Grill": "135 W 1300 South, Salt Lake City, UT 84115",
    "Cellblock Steakhouse": "211 5th St, Ely, NV 89301",
    "Racks Bar & Grill": "753 Aultman St, Ely, NV 89301",
    "Economy Drug Soda Fountain": "696 E Aultman St, Ely, NV 89301",
    # Mammoth / Bishop
    "The Stove Restaurant": "644 Old Mammoth Rd, Mammoth Lakes, CA 93546",
    "Good Life Cafe": "126 Old Mammoth Rd Ste 112, Mammoth Lakes, CA 93546",
    "The Warming Hut": "343 Old Mammoth Rd, Mammoth Lakes, CA 93546",
    "Stellar Brew & Natural Cafe": "3280 Main St, Mammoth Lakes, CA 93546",
    "Black Velvet Coffee": "3343 Main St Ste F, Mammoth Lakes, CA 93546",
    "Looney Bean Coffee": "26 Old Mammoth Rd Ste H, Mammoth Lakes, CA 93546",
    "Mammoth Brewing Co. (EATery)": "18 Lake Mary Rd, Mammoth Lakes, CA 93546",
    "Distant Brewing": "568 Old Mammoth Rd, Mammoth Lakes, CA 93546",
    "Shelter Distilling": "100 Canyon Blvd Unit 217, Mammoth Lakes, CA 93546",
    "Roberto's Cafe": "271 Old Mammoth Rd, Mammoth Lakes, CA 93546",
    "Gomez's Restaurant & Tequileria": "100 Canyon Blvd Ste 225, Mammoth Lakes, CA 93546",
    "Toomey's": "6085 Minaret Rd, Mammoth Lakes, CA 93546",
    "Mammoth Tavern": "587 Old Mammoth Rd Ste 10, Mammoth Lakes, CA 93546",
    "Emberz BBQ": "120 Commerce Dr, Mammoth Lakes, CA 93546",
    "Skadi": "3228 Main St, Mammoth Lakes, CA 93546",
    "Lakefront Restaurant": "163 Twin Lakes Rd, Mammoth Lakes, CA 93546",
    "Erick Schat's Bakkerÿ": "763 N Main St, Bishop, CA 93514",
    "Taqueria Las Palmas": "136 E Line St Ste B, Bishop, CA 93514",
    "Holy Smoke Texas BBQ": "772 N Main St, Bishop, CA 93514",
}

# ── DATA ──────────────────────────────────────────────────────────────────────
# row = [name, city, type, price, reservation, phone, website, notes, dog]

tahoe_rows = [
    ["Bridgetender Tavern & Grill", "Tahoe City", "American tavern / Burgers", "$$",
     "Walk-in", "(530) 583-3342", "tahoebridgetender.com",
     "Casual riverfront tavern on the Truckee River with a big summer patio — burgers, fish & fries, tacos, deep beer list. Dogs welcome on the designated pet patio (NOT the riverfront seating) — ask the host. The easy Tahoe lunch stop.",
     "✅ Patio (dog area)"],
    ["Alibi Ale Works", "Tahoe City", "Craft brewery / Brewpub", "$$", "Walk-in",
     "(775) 831-8300", "alibialeworks.com",
     "Incline Public House (931 Tahoe Blvd) is closest to North Tahoe lodging — leashed dogs in the 'Beer Forest' / lower patio (the Truckee Public House is dog-friendly outdoors too). The old Enterprise St brewery is closed. Outdoor only.",
     "✅ Patio"],
]

moab_rows = [
    ["The Spoke on Center", "Moab", "American / Burgers", "$$", "Walk-in",
     "(435) 259-5510", "thespokemoab.com",
     "Centrally located, bike-themed, popular post-trail dinner. Burgers, big fry baskets, salads. Sidewalk patio with a misting system for hot afternoons.",
     "✅ Patio"],
    ["Trailhead Public House", "Moab", "Gastropub / Burgers", "$$", "Walk-in",
     "(435) 355-0521", "moabtrailhead.com",
     "Historic adobe building. Dedicated dog-friendly rooftop patio with its OWN dog menu — best Mochi spot in Moab. Note: stairs up to the patio.",
     "✅ Rooftop (dog menu)"],
    ["Moab Brewery", "Moab", "Brewpub / American", "$$", "Walk-in",
     "(435) 259-6333", "themoabbrewery.com",
     "Open since 1996, ~14 house beers + craft spirits. Reliable hearty pub fare — fish tacos, bacon mac, blackened burger. Big, busy, good value.",
     "❓ Call ahead"],
    ["Love Muffin Cafe", "Moab", "Breakfast / Coffee", "$$", "Walk-in",
     "(435) 259-6833", "lovemuffincafe.com",
     "Homemade muffins, espresso, breakfast burritos, packable road sandwiches. HOURS: open Fri–Tue ~6:30am–1pm, CLOSED Wed–Thu. Backups: Sweet Cravings Bakery, Quesadilla Mobilla (food truck, 95 N Main).",
     "❓ Grab-and-go"],
]

# Boulder section: Boulder core, then Golden / Nederland / Estes Park day trips.
boulder_rows = [
    ["Santo", "Boulder", "New Mexican / Breakfast + dinner", "$$",
     "Walk-in for breakfast; reserve dinner", "(303) 442-6100", "santoboulder.com",
     "From Top Chef winner Hosea Rosenberg (Blackbelly). Real Hatch green chile, blue corn, chef-driven breakfast burritos a step above the diner version.",
     "✅ Patio"],
    ["Nopalito's", "Boulder", "Breakfast burrito / Counter", "$", "Walk-in", "", "",
     "Repeatedly named Boulder's ultimate breakfast burrito — fluffy eggs, seasoned potatoes, melted cheese. Classic hole-in-the-wall fuel before a hike or ride. Go early.",
     "❓ Call ahead"],
    ["The Parkway Cafe", "Boulder", "Diner / Breakfast + lunch", "$",
     "Walk-in, busy weekends", "", "",
     "Longtime institution, generous portions. Breakfast burrito smothered in green chile is the move; breakfast enchiladas also beloved. Dogs welcome at outdoor tables.",
     "✅ Patio"],
    ["Moe's Broadway Bagel", "Boulder", "Bagels / Breakfast counter", "$", "Walk-in",
     "", "",
     "20+ yr favorite for hot fresh bagels + breakfast sandwiches. The 'Mt. Sanitas' is the grab-before-a-hike pick — near the Sanitas/Chautauqua trailheads.",
     "❓ Order to go"],
    ["OZO Coffee", "Boulder", "Coffee / Local roaster", "$", "Walk-in", "", "ozocoffee.com",
     "Homegrown Boulder roaster since 2007, voted Best Roaster (Boulder Weekly 2025). Six locations incl. downtown Pearl St. Reliable post-ride stop.",
     "✅ Patio (varies)"],
    ["Boxcar Coffee Roasters", "Boulder", "Coffee / Local roaster", "$", "Walk-in",
     "", "boxcarcoffeeroasters.com",
     "Beloved Pearl St fixture, sustainably sourced, serious coffee-nerd quality. Great morning espresso before exploring downtown.",
     "✅ Patio"],
    ["Chautauqua Dining Hall", "Boulder", "American / Post-hike sit-down", "$$",
     "Reservations recommended", "(303) 440-3776", "chautauqua.com",
     "Operating since 1898, right beneath the Flatirons at the Chautauqua trailhead. Wraparound porch with stunning views; bison + turkey burgers. The quintessential post-hike meal — book ahead in summer.",
     "✅ Porch"],
    ["T/aco", "Boulder", "Tacos / Casual", "$$", "Walk-in", "", "",
     "Consistently named Boulder's best tacos. House-made tortillas are the difference; guajillo pork belly, shrimp, barbacoa + rotating seasonals. On Walnut St downtown.",
     "✅ Patio"],
    ["McDevitt Taco Supply", "Boulder", "Street tacos / Counter", "$", "Walk-in", "",
     "mcdevitttacosupply.com",
     "Pearl St taco cart since 2011, now a brick-and-mortar street-taco shop on Baseline Rd. House salsas; quick, cheap, great fillings. (The Pearl St presence is just the original cart, not a sit-down.)",
     "❓ Call ahead"],
    ["Audrey Jane's Pizza Garage", "Boulder", "Pizza / Casual", "$$", "Walk-in", "",
     "audreyjanespizzagarage.com",
     "Tops local 'best pizza' threads; chewy NY-style with Colorado twists. The Spicy Pig (pepperoni, jalapeño, hot honey) is the signature. Featured on Diners, Drive-Ins & Dives.",
     "❓ Call ahead"],
    ["Dragonfly Noodle", "Boulder", "Ramen / Pan-Asian", "$$", "Walk-in, busy weekends",
     "", "dragonflynoodle.com",
     "Sleek modern ramen — spicy black garlic tonkotsu with housemade noodles is the standout, plus pan-Asian small plates. Best ramen in Boulder proper.",
     "❌ Indoor only"],
    ["Zoe Ma Ma", "Boulder", "Chinese street food / Counter", "$$", "Walk-in", "",
     "zoemama.com",
     "Hand-pulled noodles + dumplings, added to the MICHELIN Guide in 2023. Fast, fresh, great-value drop-in lunch. Downtown near the creek.",
     "✅ Patio"],
    ["Dushanbe Teahouse", "Boulder", "Tea house / Brunch", "$$",
     "Walk-in (north patio); reserve indoor", "(303) 442-4993", "boulderteahouse.com",
     "Ornate hand-carved Tajik teahouse gifted by sister-city Dushanbe — 100+ teas, full bar, international menu, weekend brunch from 8am. Creekside on Boulder Creek. Dogs only on the grapevine-shaded NORTH self-serve patio (order at the bar) — not creekside/indoors.",
     "✅ North patio"],
    ["Postino Boulder", "Boulder", "Wine bar / Small plates", "$$", "Walk-in",
     "(303) 285-3755", "postino.com",
     "Lively wine café at 15th & Pearl — build-your-own bruschetta boards + approachable wine, wrap-around Pearl St Mall patio. Great happy hour + board/bottle deals. Weekend brunch from 9am. Dogs on the patio.",
     "✅ Patio"],
    ["River and Woods", "Boulder", "New American / CO comfort", "$$$",
     "Reservations recommended", "(303) 993-6301", "riverandwoodsboulder.com",
     "In a restored century-old miner's cabin with a leafy backyard + front porch. Crowd-sourced 'heirloom' dishes + seasonal Colorado comfort food; weekend brunch 10–2. Leashed dogs in the backyard.",
     "✅ Patio"],
    ["Mountain Sun Pub & Brewery", "Boulder", "Brewpub", "$$",
     "Walk-in (CASH/check only)", "(303) 546-0886", "mountainsunpub.com",
     "Pearl St institution since 1993 — house ales, no-frills community vibe. CASH OR CHECK ONLY (ATM on site). Beloved rotating taps. Dogs not on the food patio (health code) — a Mochi-stays-home stop.",
     "❌ Indoor only"],
    ["Southern Sun Pub & Brewery", "Boulder", "Brewpub", "$$",
     "Walk-in (CASH/check only)", "(303) 543-0886", "mountainsunpub.com",
     "South Boulder sister to Mountain Sun — bigger footprint, lounge + patio, 21 house taps. Same cash-only policy. Convenient to South Boulder trailheads. Dog-patio status is ambiguous — call to confirm.",
     "❓ Call ahead"],
    ["Upslope Brewing — Flatiron Park", "Boulder", "Brewery / Food trucks", "$$",
     "Walk-in", "(303) 396-1898", "upslopebrewing.com",
     "Production brewery + taproom in Flatiron Park — live music, rotating food trucks, tours. Open to ~9pm. Leashed dogs welcome on the patios (no longer inside, health rules).",
     "✅ Patio"],
    ["Trident Booksellers & Cafe", "Boulder", "Coffee / Bookstore", "$", "Walk-in",
     "(303) 443-3133", "tridentcafe.com",
     "Boulder institution since 1980 — specialty coffee + fine teas paired with new/used books, a classic Pearl St study/hang. Open 7am–9pm. Dogs at the back patio.",
     "✅ Patio"],
    ["Avery Brewing Co.", "Boulder", "Brewery / Patio + restaurant", "$$", "Walk-in",
     "(303) 440-4324", "averybrewing.com",
     "Bucket-list Boulder brewery — 30+ on draft (bold IPAs, barrel-aged sours) and a full restaurant menu, not just snacks. Large Gunbarrel taproom + dog patio. CLOSED Mondays; opens 11:30.",
     "✅ Patio"],
    ["The Rayback Collective", "Boulder", "Food-truck park / Beer garden", "$$",
     "Walk-in", "(303) 214-2127", "therayback.com",
     "Food-truck park + full bar, huge lawn, yard games, rotating trucks. Dedicated 'pup zone' and pup cups — the most dog-centric hang in Boulder. Lunch ~11–3, dinner ~5–9.",
     "✅ Large lawn"],
    ["⭐ Frasca Food and Wine", "Boulder", "Italian (Friulano) / Fine dining", "$$$$",
     "Required — book on Tock early", "(303) 442-6966", "frascafoodandwine.com",
     "SPLURGE. 2025 James Beard Outstanding Restaurant winner + Michelin-starred (Bobby Stuckey / Lachlan Mackinnon-Patterson). Boulder's destination dinner. Reservations drop ~a month out and vanish fast — plan now.",
     "❌ Indoor only"],
    ["⭐ Corrida", "Boulder", "Spanish/Basque steakhouse / Rooftop", "$$$",
     "Reservations recommended", "(303) 444-1333", "corridaboulder.com",
     "SPLURGE. 4th-floor rooftop with Flatirons views, wood-fired steak + seafood. Book the terrace at golden hour. Sat brunch from 10:30a.",
     "❌ Rooftop, no dogs"],
    ["⭐ Blackbelly", "Boulder", "New American / Whole-animal", "$$$",
     "Reservations recommended (Tock)", "(303) 247-1000", "blackbelly.com",
     "SPLURGE. Hosea Rosenberg's flagship — whole-animal butchery, hyper-local sourcing, artisan butcher shop next door (great picnic provisions). Chef's counter is the experience tier. East Boulder.",
     "❓ Patio seasonal"],
    # --- Golden (day trip Jul 30) ---
    ["Cannonball Creek Brewing", "Golden", "Brewery / Patio", "$$", "Walk-in", "",
     "cannonballcreekbrewing.com",
     "Widely rated Golden's best beer — standout West Coast IPAs (Mindbender, Solid Gold). No kitchen, but rotating food trucks. Two dog patios near Clear Creek.",
     "✅ Patio"],
    ["New Terrain Brewing", "Golden", "Brewery / Beer garden", "$$", "Walk-in", "",
     "newterrainbrewing.com",
     "Big beer garden with an ADJACENT off-leash dog park + food trucks at the base of North Table Mtn — Mochi runs while you drink. GABF-medal beers. Ideal post-ride.",
     "✅ Beer garden"],
    ["The Golden Mill", "Golden", "Food hall / Self-pour", "$$", "Walk-in", "",
     "thegoldenmill.com",
     "Multi-level food hall — 5 kitchens (Rolling Smoke BBQ, sushi, tacos, fried chicken) + two self-pour walls (50+ taps). Open to 9pm (10 Fri/Sat). Leashed dogs in the ground-floor yard only (not rooftop/indoors). Best post-hike group pick near Clear Creek.",
     "✅ Ground yard"],
    ["Windy Saddle Cafe", "Golden", "Cafe / Bakery", "$$", "Walk-in", "(303) 279-1905",
     "windysaddle.com",
     "Downtown-Golden cyclist favorite — in-house bakery, scratch breakfast + lunch (dinner weekends only), opens 7am. Natural pre/post-ride stop near Lookout Mtn + Clear Creek. Dog-friendly outdoor seating.",
     "✅ Patio"],
    ["The Eddy Taproom & Hotel", "Golden", "New American / Casual-upscale", "$$",
     "Reservations rec. weekends", "", "theeddygolden.com",
     "Seasonal menu with local cattle + game, creekside downtown — a more polished sit-down than the brewery patios. Dogs welcome at the bar and on the patio.",
     "✅ Patio"],
    ["Table Mountain Grill", "Golden", "Mexican / Casual", "$$", "Walk-in", "",
     "tablemountaingrill.com",
     "Downtown Golden Mexican mainstay with an enclosed heated patio; treats + water bowls for dogs. Easy dog-friendly lunch stop on a Golden day trip.",
     "✅ Patio (heated)"],
    # --- Nederland (en route to Indian Peaks / Brainard) ---
    ["Crosscut Pizzeria & Taphouse", "Nederland", "Pizza / Taphouse", "$$", "Walk-in",
     "(303) 258-3519", "crosscutpizza.com",
     "Wood-fired Neapolitan pizza at Hwy 119 & 1st — local ingredients, rotating taps, casual mountain-town vibe; popular before/after Indian Peaks. Limited weekday hours (often opens 3pm; from 11:30 weekends, closed Tue) — confirm. Dogs on the patio.",
     "✅ Patio"],
    ["Salto Coffee Works", "Nederland", "Coffee", "$", "Walk-in", "(303) 258-3537",
     "saltocoffee.com",
     "Beloved local roaster-café + community hub — house-roasted coffee, light fare, evening beer/wine some days. Morning-to-early-afternoon hours vary (often closed midweek) — call. Pet-friendly outdoor seating.",
     "✅ Patio"],
    ["Train Cars Coffee & Kava", "Nederland", "Coffee (rail cars)", "$", "Walk-in",
     "(303) 258-2455", "traincarscoffeeandkava.com",
     "Iconic coffee in vintage railroad cars at the south edge of town — locally roasted coffee, mini-doughnuts, breakfast sandwiches, green-chile burritos (now also kava). Open daily, early. Good first stop up the Peak to Peak.",
     "❓ Call ahead"],
    # --- Estes Park (RMNP day trip Jul 25) ---
    ["Rock Cut Brewing Co.", "Estes Park", "Brewery / Food trucks", "$$", "Walk-in",
     "(970) 586-7300", "rockcutbrewing.com",
     "Popular craft brewery with a big dog-friendly riverside patio; no kitchen — rotating food truck + outside food welcome. Family/dog-oriented. ~noon–8/9pm (11am Sun). Casual post-park beer + bite.",
     "✅ Patio"],
    ["The Barrel", "Estes Park", "Beer garden / Self-pour", "$$", "Walk-in",
     "(970) 616-2090", "thebarrel.beer",
     "Open-air downtown beer garden — self-pour wall (~64 taps outdoors May–Oct), rotating food trucks, bring-your-own food OK. Big communal seating, dog-friendly year-round. Mochi can settle while you decompress.",
     "✅ Patio"],
    ["Rock Inn Mountain Tavern", "Estes Park", "Mountain tavern / American", "$$$",
     "Reservations recommended", "(970) 586-4116", "rockinnestes.com",
     "Historic 1937 log roadhouse — buffalo burgers, local trout, Divide views + live music. On CO-66 / Marys Lake Rd (south side), NOT Fall River Rd. Covered dog-friendly patio.",
     "✅ Covered patio"],
    ["Bird & Jim", "Estes Park", "New American", "$$$",
     "Reservations recommended (Resy)", "(970) 586-9832", "birdandjim.com",
     "Top-rated polished New American — farm-to-table seasonal menu, full bar, mountain-view patio; among the best sit-downs in town. Open daily lunch–dinner. Reserve ahead in season. Dogs on the patio.",
     "✅ Patio"],
]

steamboat_rows = [
    ["Creekside Café & Grill", "Steamboat", "Breakfast / Brunch", "$$",
     "Walk-in (weekend wait)", "(970) 879-4925", "rexsfamily.com",
     "Local consensus pick for best breakfast, daily 7am–2pm. Eggs Benedict, Belgian malted waffles, Bloody Marys. Get there before 9am weekends. Creekside patio.",
     "✅ Creekside patio"],
    ["Winona's", "Steamboat", "Breakfast / Bakery / Diner", "$$",
     "Walk-in, busy weekends", "(970) 879-2483", "winonas-steamboat.com",
     "Downtown institution on Lincoln Ave — giant homemade cinnamon rolls, omelets, breakfast burritos. Classic main-street people-watching. Line moves fast.",
     "❓ Call ahead"],
    ["Freshies Restaurant", "Steamboat", "Breakfast / Brunch", "$$",
     "Walk-in (weekend wait)", "(970) 879-8099", "freshies.restaurant",
     "Longtime local breakfast/brunch staple on Lincoln Ave — healthy bowls, egg dishes, sandwiches. Breakfast ~7–11 (noon Sun), lunch to 2:30. Great pre/post-ride fuel; go early. Limited outdoor seating — confirm dog.",
     "❓ Call ahead"],
    ["Lil' House Country Biscuits", "Steamboat", "Breakfast / Counter", "$", "Walk-in",
     "(970) 870-8507", "rexsfamily.com",
     "West-side hole-in-the-wall — Carolina-style biscuit sandwiches, breakfast burritos, coffee. Great grab-and-go before a west-end hike/ride. Opens early; confirm seasonal hours.",
     "✅ Patio"],
    ["Big Iron Coffee Co.", "Steamboat", "Coffee", "$", "Walk-in", "", "",
     "Local Lincoln Ave coffee spot — coffee + breakfast burritos/sandwiches/baked goods, patio seating in good weather. The downtown dog-patio coffee stop. (Replaces the old 'Ghost Ranch' name — that was the closed Ghost Ranch Saloon, now The Commons.)",
     "✅ Patio (weather)"],
    ["Seedhouse Coffee Roasters", "Steamboat", "Coffee / Roaster", "$", "Walk-in", "",
     "seedhousecoffee.com",
     "Small-batch 100% organic local roaster, community favorite. Best quality-focused cup in town. Alternatives: Dusky Grouse (dog-welcoming, pup cups), Steamboat Coffee Roasters (Lincoln Ave).",
     "❓ Call ahead"],
    ["Mountain Tap Brewery", "Steamboat", "Brewery / Wood-fired pizza", "$$", "Walk-in",
     "(970) 879-6646", "mountaintapbrewery.com",
     "On Yampa St by the river. Wood-fired pizzas + solid craft beer. Big dog patio with water bowls — ideal post-ride or after the Saturday farmers market (same stretch of Yampa St).",
     "✅ Patio (water bowls)"],
    ["Storm Peak Brewing", "Steamboat", "Brewery / Taproom", "$$", "Walk-in",
     "(970) 879-1999", "stormpeakbrewing.com",
     "The locals' brewery — relaxed, DOGS WELCOME INSIDE at the downtown taproom + rooftop patio (1885 Elk River Plz). Beer-focused (snacks + food trucks; BYO food OK). 2nd 'Bus Stop' taproom near the mountain base for après.",
     "✅ Inside + patio"],
    ["Salt & Lime", "Steamboat", "Mexican / Tacos / Rooftop", "$$",
     "Walk-in (rooftop fills)", "(970) 879-4448", "rexsfamily.com",
     "Lively downtown Mexican on Lincoln Ave with a fun rooftop + top margaritas. Birria, barbacoa quesadilla, Baja shrimp tacos. Go for the margs, rooftop vibe, and tacos.",
     "✅ Side patio"],
    ["TacoCabo", "Steamboat", "Tacos / Counter", "$", "Walk-in", "(970) 875-1820",
     "tacocabo.com",
     "No-frills counter for homemade street tacos, tamales, burritos — the cheap post-ride fuel option vs. the pricier sit-down Mexican spots. Quick in-and-out.",
     "❓ Call ahead"],
    ["Back Door Grill", "Steamboat", "Burgers", "$$", "Walk-in", "(970) 871-9971",
     "backdoorgrillsteamboat.com",
     "Widely called the best burger in town (4.5★, 670+ reviews). Natural CO beef, hand-formed patties, hand-cut fries daily. Try the Dirty Harry or Black Mamba. On Oak St; go off-peak.",
     "✅ Patio"],
    ["Moe's Original BBQ", "Steamboat", "BBQ", "$$", "Walk-in", "(970) 879-2369",
     "moesoriginalbbq.com",
     "Alabama-style smoked BBQ — pulled pork, ribs, smoked wings, catfish, classic sides. Casual + dog-friendly with patio water bowls. Order the smoked meats + wings.",
     "✅ Patio (water bowls)"],
    ["The Commons (food hall)", "Steamboat", "Food Hall (7 vendors + bar)", "$$",
     "Walk-in", "", "thecommonssteamboat.com",
     "Central downtown food hall with 7 local vendors + full bar — easy for a group that wants different things (tacos, slices, chicken, Greek). Affordable, dog-friendly patio. (In the old Ghost Ranch Saloon building.)",
     "✅ Patio"],
    ["The Clark Store", "Steamboat", "General store / Deli", "$", "Walk-in",
     "(970) 879-3849", "clarkstore.com",
     "Classic country store + chef-run deli in Clark, ~20 min N on CR-129 toward Hahns Peak — famous giant breakfast burrito, sandwiches, soup, ice cream + beer/wine. ~7am–7pm. Outdoor deck = easy dog stop en route to the lake hikes.",
     "✅ Deck"],
    ["Laundry Kitchen & Cocktails", "Steamboat", "Small plates / Cocktails", "$$$",
     "Reservations recommended", "(970) 870-0681", "thelaundryrestaurant.com",
     "In a restored 1910 hand-laundry building — exposed brick + a coveted Soda Creek patio. Small/shared plates + craft cocktails built for grazing. Dinner only, opens 4:30pm. The post-soak evening out.",
     "❓ Call ahead"],
    ["⭐ Aurum Food & Wine", "Steamboat", "New American / Riverfront", "$$$$",
     "Reservations recommended (Tock)", "(970) 879-9500", "aurumsteamboat.com",
     "SPLURGE. The top riverfront dinner, on Yampa St directly over the river. Seasonal New American (hoisin BBQ duck wings, braised short rib), rooftop patio + riverside deck. Opens 4:30pm; HH 4:30–6.",
     "✅ Riverside deck (confirm)"],
    ["⭐ Café Diva", "Steamboat", "New American / Fine dining", "$$$$",
     "Required — book well ahead", "(970) 871-0508", "cafediva.com",
     "SPLURGE. Steamboat's premier fine-dining room at Ski Time Square; seasonal menu, 275+ wine cellar, 4.9★ (1,380+ diners). Daily 5:30–9pm. The intimate special-occasion dinner.",
     "❌ Indoor only"],
]

lead_rows = [
    ["The Twin Lakes Inn & Saloon", "Twin Lakes", "Saloon / American", "$$", "Walk-in",
     "(719) 486-7965", "thetwinlakesinn.com",
     "Historic 1879 inn in tiny Twin Lakes — your NEAREST sit-down. Wild-West setting; saloon, sunroom, or patio (weather permitting). Praised fries, steak, blackened chicken alfredo. Confirm dinner hours by phone.",
     "✅ Patio (confirm)"],
    ["Tennessee Pass Cafe", "Leadville", "American / Casual", "$$", "Walk-in",
     "(719) 486-8101", "tennesseepass.com",
     "Local favorite on Harrison Ave, daily ~11am–9pm. Green chili, Forrest Child mushroom soup, trout Niçoise salad. Reliable meal at altitude. NOT the Cookhouse below.",
     "❓ Call ahead"],
    ["High Mountain Pies", "Leadville", "Pizza / Ribs", "$$", "Walk-in (takeout-heavy)",
     "(719) 486-5555", "hmpies.com",
     "20+ yr institution, 4.8★ pizza — braised pork, ranchero/green chili, manchego pies. Very limited indoor seating, so plan on takeout. 115 W 4th St.",
     "❓ Takeout"],
    ["City on a Hill Coffee", "Leadville", "Coffee shop", "$", "Walk-in",
     "(719) 293-4258", "cityonahillcoffee.com",
     "Cozy local coffee bar + small gear shop with outdoor seating. Good morning fuel before a high-country day. Lavender/vanilla lattes called out by reviewers.",
     "✅ Patio"],
    ["Eddyline Brewery & Pub", "Buena Vista", "Brewpub / Pizza + burgers", "$$",
     "Walk-in", "(719) 966-6018", "eddylinebrewery.com",
     "BV's flagship craft brewery, ~25 min south. Two locations: in-town Pub (102 Linderman) + larger South Main restaurant. Dog note: at South Main dogs leash OUTSIDE the fence, so the in-town pub patio may suit Mochi better.",
     "✅ Patio (see note)"],
    ["Deerhammer Distillery", "Buena Vista", "Distillery + kitchen", "$$", "Walk-in",
     "(719) 395-9464", "deerhammer.com",
     "Excellent small-batch whiskey + cocktails plus mountain-town comfort food (BBQ burger, fish & chips). 321 E Main. HOURS: closed Mon–Wed, open Thu–Sun ~1–8pm. Staff welcome dogs.",
     "✅ Patio"],
    ["⭐ Tennessee Pass Cookhouse", "Leadville", "Multi-course (backcountry yurt)", "$$$$",
     "Reservations REQUIRED", "(719) 486-8114", "tennesseepass.com",
     "SPECIAL, not a drop-in. Fixed multi-course gourmet dinner (elk tenderloin, CO lamb, sockeye) reached by a ~1-mile hike to a backcountry yurt. Summer Thu–Sun, late June–early Oct. Book ahead — only if you want a memorable meetup dinner.",
     "❌ Not dog-suitable"],
]

# Crested Butte section: CB town + Mt CB base + Aspen (West Maroon finish).
cb_rows = [
    ["Secret Stash", "Crested Butte", "Pizza / Eclectic", "$$",
     "Walk-in (busy summer eves)", "(970) 349-6245", "secretstash.com",
     "The beloved CB institution + #1-rated in town (4.6★, 1,300+ reviews). Funky bohemian decor. Order the 'Notorious F.I.G.' (fig, prosciutto, truffle oil). Now 303 Elk Ave w/ tripled capacity = shorter waits. 11am–9pm daily.",
     "✅ Patio (limited; call)"],
    ["Mikey's Pizza", "Crested Butte", "Pizza slices / Breakfast burritos", "$",
     "Walk-in", "(970) 349-1110", "mikeyspizza.net",
     "Local hole-in-the-wall slice joint, the post-ride refuel — thin-crust slices ~$3.50, cheap + fast. Breakfast burritos Mon–Fri 7am–12pm (a 'required pre-ride ritual' per locals). Delivers valley-wide.",
     "✅ Patio (weather)"],
    ["Teocalli Tamale", "Crested Butte", "Tacos / Mexican fast-casual", "$", "Walk-in",
     "(970) 349-2005", "teocallitamale.com",
     "Go-to fast/fresh/cheap Mexican on Elk Ave — voted 'Best Bang for the Buck.' Build-your-own burritos, tacos, tamales; the 'Piggy Pork' tacos get praise. Minimal seating = quick post-ride fuel. 311 Elk Ave.",
     "❓ Sidewalk only"],
    ["Paradise Cafe", "Crested Butte", "Breakfast / Lunch", "$$", "Walk-in",
     "(970) 349-5622", "paradisecafecb.com",
     "Voted best breakfast in town multiple years. Shaded tree-lined patio with mountain views — great pre-ride fuel. Big breakfasts, Bloody Marys, brunch. 435 6th St; daily 7am–1pm. Go early weekends.",
     "✅ Patio"],
    ["McGill's", "Crested Butte", "Breakfast / Lunch (diner)", "$$", "Walk-in", "",
     "mcgillscrestedbutte.com",
     "Classic generous all-day breakfast; ranked #1 for breakfast burritos in CB on Tripadvisor. Daily specials, hearty portions — solid carb-load before the bike park.",
     "❓ Call ahead"],
    ["Butte Bagels", "Crested Butte", "Bagels / Breakfast", "$", "Walk-in",
     "(970) 349-5630", "butte-bagels.com",
     "CB's scratch-made bagels + breakfast sandwiches + coffee — the pre-ride fuel. Opens ~7:30am, CLOSES ~2pm (reportedly closed Tue) — plan ride mornings around it. Tucked behind the post office off Elk Ave.",
     "❓ Grab-and-go"],
    ["Camp 4 Coffee", "Crested Butte", "Coffee roaster", "$", "Walk-in", "",
     "camp4coffee.com",
     "The iconic CB coffee, roasting since 1993 — repeatedly voted 'Best Cup of Joe in CB.' Tiny wood-sided cabin you'll smell before you see. Grab beans + a pastry. CB + CB South locations.",
     "✅ Walk-up window"],
    ["Coffee Lab", "Mt. Crested Butte", "Coffee", "$", "Walk-in", "", "cbcoffeelab.com",
     "Base-area coffee in Mountaineer Square by the bus stop — pre-ride/pre-lift espresso + quick bite. Opens ~6:30am; closing time varies (1pm vs later) — confirm. Walkable from a base Airbnb. (A 2nd location sits down in CB town.)",
     "❓ Counter"],
    ["Rumors Coffee & Tea House", "Crested Butte", "Coffee / Cafe", "$", "Walk-in",
     "(970) 349-7545", "",
     "Cozy spot attached to Townie Books on Elk Ave — relaxed morning coffee, light bites, breakfast burrito. 414 Elk Ave, daily ~7:30am–6/7pm.",
     "✅ Patio"],
    ["Bonez Tequila Bar & Grill", "Crested Butte", "Mexican / Tequila bar", "$$",
     "Walk-in", "(970) 349-5118", "bonez.co",
     "Contemporary Mexican with a riverside patio — heavy-handed margs, trio of salsas (spicy pineapple is the standout), overstuffed tacos. Lively dinner/happy hour. 130 Elk Ave; dinner from 4pm.",
     "✅ Riverside patio"],
    ["The Eldo Brewery & Brewpub", "Crested Butte", "Brewery / Brewpub", "$$", "Walk-in",
     "", "eldobrewery.com",
     "The only locally owned brewery in historic downtown CB — 2nd-floor on Elk Ave with a legendary deck and intimate late-night live music. Prime après-ride hang. ('The sun shines on the just and the unjust alike, but mostly on the just.')",
     "❓ Deck (verify)"],
    ["Bruhaus", "Crested Butte", "German beer hall / Comfort food", "$$", "Walk-in", "",
     "elkavehospitality.com/bruhaus",
     "Modern German-style beer hall — huge rotating craft list, bratwurst + elevated comfort food, sunny patio marketed for 'patio pups.' Squarely your post-ride scene. Mon–Thu 4–9, Fri 3–9, weekends earlier.",
     "✅ Patio (loves pups)"],
    ["The Public House", "Crested Butte", "Gastropub (Irwin Brewing)", "$$",
     "Reservations recommended", "(970) 349-0173", "publichousecb.com",
     "Restored Elk Ave pub — official Irwin Brewing taproom, seasonal comfort food, street patio + a downstairs live-music venue (touring + local acts). Lively post-ride. ~3–9pm; confirm day-of. Dog-patio on dog-friendly Elk Ave — confirm.",
     "❓ Call ahead"],
    ["Butte Burgers", "Crested Butte", "Burgers (smash-style)", "$", "Walk-in", "",
     "butteburgers.com",
     "Current best burger downtown — smash-style with crispy edges + house 'zinging' sauces, from the Butte Bagels crew. Post-ride and late-night eats. (Verify location — a CB base burger spot was reported closing.)",
     "❓ Verify seating"],
    ["Butte 66", "Mt. Crested Butte", "Slopeside BBQ / Bar & grill", "$$", "Walk-in",
     "(970) 349-2272", "skicb.com",
     "Slopeside at the Mt. CB base (Treasury Center) — roadhouse BBQ, burgers, milkshakes, big deck. Casual, exactly the post-lift/post-ride fit. Seasonal resort hours (~opens 11am) — verify off-season days. Walkable from a base-area Airbnb.",
     "❓ Call ahead"],
    ["Tin Cup Pasty Co.", "Mt. Crested Butte", "British pasties / Hand pies", "$",
     "Walk-in", "", "",
     "Fun local quick-bite — English-style savory hand pies, easy to grab before/after a ride. (Now at the Mt. CB base, 620 Gothic Rd — the original Elk Ave shop transitioned.) Dog-friendly patio.",
     "✅ Patio"],
    ["Montanya Distillers", "Crested Butte", "Rum distillery / Cocktail bar", "$$",
     "Walk-in (3–9pm daily)", "", "montanyarum.com",
     "Award-winning CO rum distillery tasting room — house white + aged rums, craft cocktails, small bites. Dog-friendly front + back patios. Perfect pre-dinner cocktail stop, not a full dinner. 204 Elk Ave.",
     "✅ Patios"],
    ["⭐ The Breadery", "Crested Butte", "Sourdough New American (dinner)", "$$$",
     "Required — book on Tock", "(970) 319-5118", "breaderycb.com",
     "NOTE: despite the name it's now a reservations-essential DINNER eatery/bakehouse (~5–9pm, closed Tue), not a morning bakery — strong sourdough/veg-forward menu. For CB morning pastries use Butte Bagels instead. Books up; deposit required.",
     "❓ Call ahead"],
    ["⭐ Soupçon", "Crested Butte", "French-American / Prix Fixe", "$$$$",
     "Required — book on Tock NOW", "", "soupconcb.com",
     "SPLURGE — the marquee CB dinner. 50+ yrs of French fine dining in a historic cabin off Elk Ave (127 Elk Ave). Multi-course tasting ~$200/pp. Two seatings (5:30 & 7:45). SELLS OUT in peak August — book the moment dates are set.",
     "❌ Indoor only"],
    ["⭐ The Sunflower", "Crested Butte", "Farm-to-table / New American", "$$$",
     "Recommended (reserve by text)", "(970) 417-7767", "",
     "SPLURGE (relaxed alt to Soupçon). Beloved farm-to-table at 214 Elk Ave — repeatedly called the best dinner on Elk Ave. Creative seasonal menu, entrées ~$55–62. Wed–Sat from 6pm in season; confirm days.",
     "❌ Indoor only"],
    # --- Aspen (end of the West Maroon Pass point-to-point hike) ---
    ["Meat & Cheese", "Aspen", "Charcuterie / Farm-to-table", "$$$",
     "Reservations recommended", "(970) 710-7120", "meatandcheeseaspen.com",
     "From the Avalanche Cheese Co. family — restaurant + retail farm shop built on specialty cheeses, W. Colorado meats, house charcuterie boards + seasonal plates. ~11 dog-friendly sidewalk tables. Great refuel if you finish West Maroon in Aspen.",
     "✅ Patio"],
    ["White House Tavern", "Aspen", "New American / Sandwiches", "$$$",
     "Walk-in (expect a wait)", "(970) 925-1007", "aspenwhitehouse.com",
     "Cozy 19th-c miner's cottage in Aspen's core — famous crispy fried-chicken sandwich + burgers, tight wine/cocktail list. No reservations, small space, lively. Leashed dogs at the ~6 outdoor tables.",
     "✅ Patio"],
]

transit_rows = [
    ["⭐ Kerouac's at Stargazer Inn", "Baker", "New American / Dinner", "$$$",
     "Reservations strongly rec.", "(775) 234-7323", "stargazernevada.com",
     "THE destination meal in Baker (Great Basin gateway) — genuine farm-to-table in the middle of nowhere. SEASONAL + LIMITED: ~May 21–Oct 17, Wed–Sun only, ~4–8:30pm. Book ahead — the only real dinner in town. Daytime backup: Great Basin Café at the park visitor center (~8–4).",
     "✅ Courtyard (confirm)"],
    ["Red Iguana", "SLC", "Mexican (famous mole)", "$$", "Walk-in (expect a wait)",
     "(801) 322-1489", "rediguana.com",
     "SLC's most-beloved restaurant (#1 on Yelp's 2025 SLC list). Famous moles — order the mole sampler. So popular they opened Red Iguana 2 two blocks away. North Temple, easy from I-15.",
     "❌ Indoor only"],
    ["Crown Burgers", "SLC", "Burgers / Greek-American", "$", "Walk-in",
     "(801) 532-1155", "crown-burgers.com",
     "The quintessential Utah pastrami burger — char-grilled patty topped with pastrami. Fast, cheap, reliable; downtown at 377 E 200 S. Great quick pass-through meal.",
     "❌ Indoor only"],
    ["Lucky 13 Bar & Grill", "SLC", "Burgers / Bar (21+)", "$$", "Walk-in",
     "(801) 487-4418", "lucky13slc.com",
     "Repeatedly voted Utah's best burger — massive handcrafted patties, scratch sauces (pastrami-Swiss, the nut-butter burger). NOTE: 21+ bar, not for anyone underage. Has a patio; open late.",
     "❓ Patio (21+)"],
    ["Cellblock Steakhouse", "Ely", "Steakhouse (Ely's fine dining)", "$$$",
     "Reservations recommended", "(775) 289-3033", "jailhousecasino.com",
     "Ely's standout — steaks + wine in a converted old jail; you can request to dine in an actual barred cell. Dinner only ~5–9pm. Fun, only-in-Ely experience at Jailhouse Casino.",
     "❌ Indoor only"],
    ["Racks Bar & Grill", "Ely", "Bar & grill / American", "$$", "Walk-in",
     "(775) 289-4600", "",
     "Ranked #2 in Ely, the everyday local favorite. Patio when weather's nice. Try the Nevada cheesesteak or pastrami sandwich. Solid, dependable road-trip dinner.",
     "✅ Patio (seasonal)"],
    ["Economy Drug Soda Fountain", "Ely", "Soda fountain / Lunch counter", "$",
     "Walk-in", "(775) 289-4929", "",
     "Authentic three-generation soda fountain inside the drugstore — malts, classic sandwiches, famous lime rickeys. Daytime only — a fun stop on the way OUT of town, not dinner.",
     "❌ Indoor only"],
]

mammoth_rows = [
    ["The Stove Restaurant", "Mammoth Lakes", "Breakfast / Diner", "$$",
     "Walk-in (weekend wait)", "(760) 934-2821", "thestoverestaurantmammoth.com",
     "40+ yr institution at 644 Old Mammoth Rd. Classic country breakfast — big pancakes, scrambles, biscuits + gravy. Breakfast/lunch only 7am–2pm. Go on a weekday or right at open. The quintessential local breakfast.",
     "❓ Indoor-focused"],
    ["Good Life Cafe", "Mammoth Lakes", "Breakfast / Healthy American", "$$",
     "Walk-in, busy weekends", "(760) 934-1734", "goodlifemammoth.com",
     "126 Old Mammoth Rd. Huge breakfast burritos, scrambles, big salads + vegan/veg/GF. Beloved sunny patio — a top post-ride brunch pick. Arrive before 9am weekends.",
     "✅ Patio (dog-popular)"],
    ["The Warming Hut", "Mammoth Lakes", "Breakfast / Brunch", "$$", "Walk-in",
     "(760) 965-0549", "thewarminghutmammoth.com",
     "Named for the McCoys' original Main Lodge warming hut. Standouts: chilaquiles, breakfast hash, strong Bloody Marys. Everything fresh. Breakfast 9–3, lunch to 5, dinner 5–8. A consistent local rec.",
     "❓ Call ahead"],
    ["Stellar Brew & Natural Cafe", "Mammoth Lakes", "Coffee / Cafe", "$", "Walk-in",
     "(760) 924-3559", "stellarbrew.life",
     "Little blue cabin off Main St — organic/direct-trade coffee + genuinely good food (breakfast burritos, pesto-avo egg sandwiches, big baked goods). Great patio. Ideal grab-and-go or relaxed ride fuel.",
     "✅ Patio"],
    ["Black Velvet Coffee", "Mammoth Lakes", "Specialty coffee / Wine bar", "$$",
     "Walk-in", "(760) 920-0024", "blackvelvetcoffee.com",
     "3343 Main St. Roasts their own beans — widely called the best coffee in Mammoth, the spot for single-origin pour-overs + serious espresso. Wine in the evenings. The connoisseur's coffee stop. Opens ~6am.",
     "❓ Indoor-focused"],
    ["Looney Bean Coffee", "Mammoth Lakes", "Coffee / Breakfast burritos", "$",
     "Walk-in", "(760) 934-1345", "looneybeanmammoth.com",
     "26 Old Mammoth Rd, daily 6am–5pm. Famous for massive breakfast burritos (easily two meals) + savory croissants — a great cheap pre-ride / trailhead-bound fuel stop. Explicitly dog-friendly.",
     "✅ Patio"],
    ["Mammoth Brewing Co. (EATery)", "Mammoth Lakes", "Brewery / Patio", "$$",
     "Walk-in (no res)", "(760) 934-7141", "mammothbrewingco.com",
     "18 Lake Mary Rd — the famous local brewery, a must-do. Big tasting room + outdoor beer garden. The EATery does elevated pub food (the 'Damn Good Burger' w/ Double Nut Brown sauce). Brewery 10am–close; food from 11:30. Packs out après.",
     "✅ Beer garden"],
    ["Distant Brewing", "Mammoth Lakes", "Brewery / Patio", "$$", "Walk-in",
     "(760) 965-0303", "distantbrewing.com",
     "Smaller, hip local brewery — excellent hazy IPAs, house beer cheese w/ pretzels, BLTAs. Indoor AND outdoor both dog-friendly (dogs allowed INSIDE) — one of the most dog-welcoming spots in town.",
     "✅ Patio + indoor"],
    ["Shelter Distilling", "Mammoth Lakes", "Distillery / Bar + casual food", "$$",
     "Walk-in", "(760) 934-2200", "shelterdistilling.com",
     "In The Village. House-distilled spirits + craft cocktails plus solid food (smashburger, shishito peppers, sweet-potato tacos). Front + back patios. Great après-bike cocktail-and-snacks stop in the walkable core.",
     "✅ Patio"],
    ["Roberto's Cafe", "Mammoth Lakes", "Mexican", "$$", "Walk-in (lines move)",
     "(760) 934-3667", "robertoscafe.com",
     "A Mammoth favorite since 1985. Authentic, generous Mexican — carnitas, chile verde, tamales, carne asada, shrimp tacos. Free chips + salsa, HH 2–5pm. The locals' Mexican pick over flashier Gomez's.",
     "✅ Patio"],
    ["Gomez's Restaurant & Tequileria", "Mammoth Lakes", "Mexican / Tequila bar", "$$",
     "Walk-in", "(760) 924-2693", "gomezs.com",
     "100 Canyon Blvd, prime Village spot (7 days, ~noon–8:30). Big draw is the 600+ tequila wall + the 'world-famous Mammoth Margarita'; food is solid Tex-Mex. Go for the patio scene + margs + people-watching.",
     "✅ Village patio"],
    ["Toomey's", "Mammoth Lakes", "American / Seafood (chef-driven)", "$$",
     "Walk-in (no res)", "(760) 924-4408", "toomeysmammoth.com",
     "6085 Minaret Rd, right at the Village gondola base — unbeatable post-bike-park food. Fish tacos, lobster taquitos, coconut shrimp, lobster pot pie + a soft-serve machine. 11am–9pm daily. (Mammoth's best current seafood — Sushi Rei has closed.)",
     "✅ Patio"],
    ["Mammoth Tavern", "Mammoth Lakes", "Gastropub / Burgers", "$$$",
     "Reservations rec. weekends", "(760) 934-3902", "mammothtavern.com",
     "Up Old Mammoth Rd with valley views. Excellent burgers, fried-chicken sandwich, smoked-salmon potato skins; strong cocktails + generous happy hour. A reliable sit-down dinner a notch up from brewery food, without being a splurge.",
     "❓ Indoor-focused"],
    ["Emberz BBQ", "Mammoth Lakes", "BBQ (Texas-style)", "$$", "Walk-in (go early)", "",
     "instagram.com/emberzbbq_mammoth",
     "Tiny BBQ 'food shack' with a cult following — brisket + elk sausage, cornbread, baked beans, mac & cheese. Seasonal/limited hours and they SELL OUT, so check IG and arrive early. The local BBQ sleeper hit.",
     "✅ Patio"],
    ["⭐ Skadi", "Mammoth Lakes", "Fine dining (Nordic / Alpine)", "$$$$",
     "Reservations MANDATORY — book ahead", "(760) 914-0962", "skadirestaurant.com",
     "SPLURGE. Inside the Empeiria High Sierra Hôtel (3228 Main St). Chef Ian Algerøen's Nordic/Alpine tasting cuisine — game, seasonal dishes — in an intimate ~12-table room. Repeatedly named best in Mammoth (4.8★). The one big dinner. Reserve before you arrive.",
     "❌ Indoor only"],
    ["⭐ Lakefront Restaurant", "Mammoth Lakes", "Contemporary American (fine dining)", "$$$$",
     "Reservations recommended", "(760) 934-2442", "tamaracklodge.com",
     "SPLURGE (scenic alt to Skadi). Inside Tamarack Lodge on Twin Lakes — the most romantic setting in Mammoth, lakeside in the pines. Bison carpaccio, elk chops, wild-mushroom cannelloni. Beautiful for sunset.",
     "❌ Indoor (dog grounds; call)"],
    ["⭐ Erick Schat's Bakkerÿ", "Bishop", "Bakery", "$", "Walk-in (very busy)",
     "(760) 873-7156", "erickschatsbakery.com",
     "763 N Main St — the famous Eastern Sierra bakery, #1 in Bishop (4.5★, 1,600+), ~6am–6pm. Home of the Original Sheepherder Bread. The move: a made-to-order deli sandwich + pastries + bread for the cooler. Mandatory 395 road-trip stop.",
     "✅ Outside benches"],
    ["Taqueria Las Palmas", "Bishop", "Mexican / Tacos", "$", "Walk-in",
     "(760) 873-4337", "",
     "136 E Line St. Repeatedly called the best Mexican in Bishop — authentic, casual. Famous beans + rice, shrimp al diablo (chiles grown by the owner), strong margaritas. A bit tucked away but worth finding. ~11am–9pm.",
     "❓ Limited seating"],
    ["Holy Smoke Texas BBQ", "Bishop", "BBQ (Texas-style)", "$$", "Walk-in",
     "(760) 872-4227", "holysmoketexasbbq.com",
     "772 N Main St, across from Schat's. Brisket, ribs, tri-tip + table sauces; brisket + ribs get top praise. CLOSED TUESDAYS; Mon 11–8, Wed–Sun from 11. Classic combine-with-Schat's Bishop food run.",
     "❓ Outdoor (verify)"],
]

# ── SECTIONS (title, color, rows) — chronological ─────────────────────────────
SECTIONS = [
    ("LAKE TAHOE & TRUCKEE  |  Jul 17 – 19  (trip start)",   TAHOE_BG,   tahoe_rows),
    ("MOAB  |  Jul 21 night",                                MOAB_BG,    moab_rows),
    ("BOULDER  |  Jul 22 – Aug 1  (+ Golden / Nederland / Estes day trips)", BOULD_BG, boulder_rows),
    ("STEAMBOAT SPRINGS  |  Aug 1 – 6",                      STEAM_BG,   steamboat_rows),
    ("LEADVILLE · BUENA VISTA · TWIN LAKES  |  Aug 6 – 9",   LEAD_BG,    lead_rows),
    ("CRESTED BUTTE  |  Aug 9 – 12  (+ Aspen, West Maroon finish)", CB_BG, cb_rows),
    ("ON THE ROAD: SLC · ELY · GREAT BASIN  |  Aug 12 – 13", TRANSIT_BG, transit_rows),
    ("MAMMOTH LAKES & BISHOP  |  Aug 14 – 18",               MAM_BG,     mammoth_rows),
]

# ── DISTANCE FROM AIRBNB (walk / bike / drive, Mochi-aware) ────────────────────
BASES = {
    "Boulder":          "582 Locust Place, Boulder, CO 80304",
    "Steamboat":        "1036 Lincoln Avenue, Steamboat Springs, CO 80487",
    "Crested Butte":    "6 Emmons Road, Crested Butte, CO 81225",
    "Mt. Crested Butte":"6 Emmons Road, Crested Butte, CO 81225",
}
# Static labels for cities with no/unknown Airbnb (day trips, transit, pending).
STATIC_DIST = {
    "Tahoe City":     "🚗 Day trip / lodging area",
    "Moab":           "🚗 1-night stop",
    "Golden":         "🚗 Day trip (~40 min)",
    "Nederland":      "🚗 Day-trip stop (~40 min)",
    "Estes Park":     "🚗 Day trip (~1h15)",
    "Leadville":      "🚗 Drive (~30 min)",
    "Buena Vista":    "🚗 Drive (~50 min)",
    "Twin Lakes":     "🚶 In Twin Lakes (base)",
    "Aspen":          "🥾 End of W. Maroon hike",
    "Baker":          "— transit stop",
    "SLC":            "— transit stop",
    "Ely":            "— transit stop",
    "Mammoth Lakes":  "— Airbnb TBD",
    "Bishop":         "🚗 ~40 min S of Mammoth",
}
CACHE_FILE = os.path.join(os.path.dirname(__file__), "dining_distances.json")

def _http(url):
    return json.load(urllib.request.urlopen(url, timeout=30))

def _matrix(origin, dests, mode):
    """Return list of (miles, minutes) for origin->each dest in `mode`."""
    url = ("https://maps.googleapis.com/maps/api/distancematrix/json?key=" + MAPS_API_KEY
           + "&mode=" + mode + "&units=imperial&origins=" + urllib.parse.quote(origin)
           + "&destinations=" + urllib.parse.quote("|".join(dests)))
    d = _http(url)
    out = []
    if d.get("status") != "OK":
        return [(None, None)] * len(dests)
    for el in d["rows"][0]["elements"]:
        if el.get("status") == "OK":
            out.append((el["distance"]["value"] / 1609.34, el["duration"]["value"] / 60))
        else:
            out.append((None, None))
    return out

# Load cache, then fill any missing (base-town) addresses via Distance Matrix.
dist_cache = {}
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE) as f:
        dist_cache = json.load(f)

# Collect, per base, the addresses we still need.
need = {}   # base_city -> {address: row_dog}
for _t, _c, rows in SECTIONS:
    for r in rows:
        city, name = r[1], r[0].lstrip("⭐ ").strip()
        if city in BASES and name in ADDRESSES:
            addr = ADDRESSES[name]
            if addr not in dist_cache:
                need.setdefault(city, {})[addr] = True

for base_city, addr_set in need.items():
    origin = BASES[base_city]
    addrs = list(addr_set)
    print(f"Distance Matrix: {base_city} base -> {len(addrs)} addresses (walk+drive)…")
    walk = _matrix(origin, addrs, "walking");  time.sleep(0.2)
    drive = _matrix(origin, addrs, "driving"); time.sleep(0.2)
    for a, (wmi, wmin), (dmi, dmin) in zip(addrs, walk, drive):
        dist_cache[a] = {"walk_mi": wmi, "walk_min": wmin,
                         "drive_mi": dmi, "drive_min": dmin}
with open(CACHE_FILE, "w") as f:
    json.dump(dist_cache, f, indent=1)

def dist_label(city, name, dog):
    """Walk/bike/drive label from the Airbnb. Mochi-aware: bikeable + dog-friendly
    -> bike-solo/drive-with-Mochi; bikeable + indoor-only -> bike is a good option."""
    if city not in BASES:
        return STATIC_DIST.get(city, "")
    addr = ADDRESSES.get(name)
    d = dist_cache.get(addr) if addr else None
    if not d or d.get("walk_mi") is None:
        return ""
    wmi, dmi = d["walk_mi"], d.get("drive_mi") or d["walk_mi"]
    dog_comes = dog.startswith("✅")          # Mochi welcome -> we'd bring her
    if wmi <= 1.1:
        return f"🚶 {wmi:.1f} mi · walk"
    if wmi <= 2.5:
        if dog_comes:
            return f"🚴/🚗 {wmi:.1f} mi · bike solo / drive w/ Mochi"
        return f"🚴 {wmi:.1f} mi · bike (Mochi stays)"
    return f"🚗 {dmi:.1f} mi · drive"

# ── COME AS YOU ARE? (dress / dirt level) ─────────────────────────────────────
# Most-permissive level each spot tolerates.
DRESS_UP   = "👔 Dress up a bit"
CLEAN      = "🚿 Clean & casual"
POSTHIKE   = "🥾 Post-hike OK (sweaty)"
POSTMTB    = "🚵 Post-MTB OK (dusty)"
_MTB_KW  = ("brewery", "brewing", "brewpub", "beer", "distill", "taproom", "food hall",
            "food-truck", "taco", "counter", "bagel", "pizza", "burger", "slopeside",
            "pasties", "deli", "general store", "tequila", "coffee", "roaster", "bbq",
            "rooftop")
_HIKE_KW = ("cafe", "café", "bakery", "breakfast", "diner", "brunch", "tea house",
            "healthy")
_UP_KW   = ("fine dining", "prix fixe", "nordic", "steakhouse")
DRESS_OVERRIDE = {
    "Chautauqua Dining Hall": POSTHIKE,
    "Toomey's": POSTMTB,
    "The Public House": POSTMTB,
    "Meat & Cheese": POSTHIKE,
    "White House Tavern": POSTHIKE,
    "Mammoth Tavern": CLEAN,
    "Tennessee Pass Cookhouse": CLEAN,
    "Erick Schat's Bakkerÿ": POSTMTB,
}

def dress_for(name, typ, price):
    n = name.lstrip("⭐ ").strip()
    if n in DRESS_OVERRIDE:
        return DRESS_OVERRIDE[n]
    t = typ.lower()
    if price == "$$$$" or any(k in t for k in _UP_KW):
        return DRESS_UP
    if any(k in t for k in _MTB_KW):
        return POSTMTB
    if any(k in t for k in _HIKE_KW):
        return POSTHIKE
    return CLEAN

# ── BUILD VALUE MATRIX ────────────────────────────────────────────────────────
NCOLS = len(HEADERS)
EMPTY = [""] * NCOLS

def to_row(r):
    name, city, typ, price, resv, phone, web, notes, dog = r
    bare = name.lstrip("⭐ ").strip()
    return [name, city, dist_label(city, bare, dog), typ, price, resv,
            dress_for(name, typ, price), dog, phone, web,
            ADDRESSES.get(bare, ""), notes]

ALL_ROWS = [["Dining Guide — Colorado / Eastern Sierra 2026"] + [""] * (NCOLS - 1)]
ALL_ROWS.append(["Come As You Are?  🚵 post-MTB (dusty) · 🥾 post-hike (sweaty) · "
                 "🚿 clean & casual · 👔 dress up   |   From Airbnb: 🚶 walk · "
                 "🚴 bike (only worth it solo — biking w/ Mochi is hard) · 🚗 drive"]
                + [""] * (NCOLS - 1))
title_rows = [(0, TITLE_BG)]
legend_rows = [1]
colhdr_rows = []

for title, bg, rows in SECTIONS:
    sec_i = len(ALL_ROWS)
    ALL_ROWS.append([title] + [""] * (NCOLS - 1))
    title_rows.append((sec_i, bg))
    colhdr_rows.append(len(ALL_ROWS))
    ALL_ROWS.append(HEADERS)
    for r in rows:
        ALL_ROWS.append(to_row(r))
    ALL_ROWS.append(EMPTY)

n_rows = len(ALL_ROWS)

# ── (RE)CREATE TAB ────────────────────────────────────────────────────────────
try:
    sh.del_worksheet(sh.worksheet("Dining Guide"))
    print("Deleted existing 'Dining Guide' tab.")
except gspread.WorksheetNotFound:
    pass

ws = sh.add_worksheet(title="Dining Guide", rows=n_rows + 2, cols=NCOLS)
sheet_id = ws._properties['sheetId']
ws.update(range_name="A1", values=ALL_ROWS)

# ── FORMATTING ────────────────────────────────────────────────────────────────
def fmt_row(row_i, bg, text_color, bold=True, size=None):
    tf = {"bold": bold, "foregroundColor": text_color}
    if size:
        tf["fontSize"] = size
    return {"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": row_i, "endRowIndex": row_i+1,
                  "startColumnIndex": 0, "endColumnIndex": NCOLS},
        "cell": {"userEnteredFormat": {"backgroundColor": bg, "textFormat": tf}},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"}}

def merge(row_i):
    return {"mergeCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": row_i, "endRowIndex": row_i+1,
                  "startColumnIndex": 0, "endColumnIndex": NCOLS},
        "mergeType": "MERGE_ALL"}}

requests = []
for row_i, bg in title_rows:
    requests.append(merge(row_i))
    requests.append(fmt_row(row_i, bg, WHITE, bold=True))
for row_i in legend_rows:
    requests.append(merge(row_i))
    requests.append(fmt_row(row_i, rgb(238, 238, 238), DARK_TXT, bold=False))
for row_i in colhdr_rows:
    requests.append(fmt_row(row_i, COL_HDR, DARK_TXT, bold=True))
requests.append(fmt_row(0, TITLE_BG, WHITE, bold=True, size=14))
requests.append({"repeatCell": {
    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
              "startColumnIndex": 0, "endColumnIndex": NCOLS},
    "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
    "fields": "userEnteredFormat(horizontalAlignment)"}})

# Column widths: Restaurant, City, From Airbnb, Type, Price, Reservation,
#                Come As You Are?, Dog, Phone, Website, Address, Notes
for i, px in [(0, 200), (1, 105), (2, 215), (3, 165), (4, 50), (5, 160),
              (6, 150), (7, 140), (8, 125), (9, 165), (10, 230), (11, 340)]:
    requests.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                  "startIndex": i, "endIndex": i+1},
        "properties": {"pixelSize": px}, "fields": "pixelSize"}})

# Wrap + top-align all data rows.
requests.append({"repeatCell": {
    "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": n_rows,
              "startColumnIndex": 0, "endColumnIndex": NCOLS},
    "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
    "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)"}})

# Freeze the title row. (Can't also freeze col 1 — the banner rows are merged
# across all columns, which the API rejects for a partial column freeze.)
requests.append({"updateSheetProperties": {
    "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
    "fields": "gridProperties.frozenRowCount"}})

sh.batch_update({"requests": requests})

# ── CLICKABLE LINKS: Website (col 9) + Address->Google Maps (col 10) ───────────
def link_cell(row_i, col_i, label, uri):
    return {"updateCells": {
        "rows": [{"values": [{
            "userEnteredValue": {"stringValue": label},
            "textFormatRuns": [{"startIndex": 0, "format": {
                "link": {"uri": uri}, "underline": True, "foregroundColor": LINK_C}}],
        }]}],
        "fields": "userEnteredValue,textFormatRuns",
        "start": {"sheetId": sheet_id, "rowIndex": row_i, "columnIndex": col_i}}}

link_reqs = []
for row_i, row in enumerate(ALL_ROWS):
    if row[1] == "" or row[0] == "Restaurant / Place":   # skip banners/headers
        continue
    web = row[9].strip()
    if web:
        uri = web if web.startswith("http") else "https://" + web
        link_reqs.append(link_cell(row_i, 9, web, uri))
    addr = row[10].strip()
    if addr:
        maps = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(
            row[0].lstrip("⭐ ").strip() + ", " + addr)
        link_reqs.append(link_cell(row_i, 10, addr, maps))
if link_reqs:
    sh.batch_update({"requests": link_reqs})

n_spots = sum(len(r) for _, _, r in SECTIONS)
print(f"Done. Dining Guide rebuilt: {len(SECTIONS)} sections, {n_spots} spots, "
      f"{n_rows} rows, {NCOLS} cols. Linkified {len(link_reqs)} cells. sheet_id={sheet_id}")
