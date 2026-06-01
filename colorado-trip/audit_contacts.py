"""
audit_contacts.py — READ-ONLY data-quality audit for the colorado-trip sheet.

Catches the two failure modes that bite a trip planner:
  1. CONFLICTS — the same business carries DIFFERENT phone numbers in different tabs
     (e.g. Dolly's Mountain Shuttle showed 3 numbers; Red Rover Resort showed 2). These
     feed real booking actions, so a wrong copy is dangerous.
  2. MISSING — a dining / daycare / shuttle entry with no phone AND no website.

It never writes. Run it before/after a cleanup to confirm conflicts are gone, and
periodically to catch drift. Reconcile flagged conflicts in the owning tab's data.
"""

import re
import collections
import config
import gspread

gc = gspread.service_account(filename=config.CREDENTIALS_FILE)
sh = gc.open_by_key(config.SPREADSHEET_ID)

PHONE_RE = re.compile(r'(?<!\d)(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})(?!\d)')
URL_RE = re.compile(r'(https?://|www\.|[\w.\-]+\.(?:com|org|net|gov|io|co))', re.I)

# businesses worth pinning a single correct number for (booking-critical)
ENTITIES = [
    "Dolly", "Red Rover", "PUP Hiking", "Sierra Dog", "Oh Be Dogful", "Donna the Dog",
    "Ride Workshop", "Powdercats", "Elaine", "Pet Medical", "Visalia", "Camp Bow Wow",
    "Truckee-Tahoe Pet", "Wanderlust", "Front Range", "Cottonwood", "Rogue", "Bark Central",
]
CONTACT_TABS = ["Dining Guide", "Dog Daycare Options", "MTB Shuttles & Guides", "Reservations"]


def norm_phone(p):
    d = re.sub(r"\D", "", p)
    return d[-10:] if len(d) >= 10 else d


def fmt_phone(d):
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else d


_SKIP = re.compile(r"^(TRUE|FALSE|\W*\d+\W*|[🔴🟡🟢⚪🔵•\s\d—\-]+)$")

def primary_name(row):
    """The row's own business name: first cell with real text (skip checkboxes, priority
    chips, emoji/number markers). Used so a phone is tied to ONE business, not whatever
    else a notes cell happens to mention."""
    for c in row:
        s = c.strip()
        if len(s) >= 4 and not _SKIP.match(s):
            return s
    return ""

def first_phone(row):
    """The first phone reading left-to-right = the contact column, before any notes
    column that might quote another business's number."""
    for c in row:
        m = PHONE_RE.search(c)
        if m:
            return norm_phone(m.group(1))
    return None


# ── gather every row's phones / urls / text, per tab ─────────────────────────────
tabs = {}
for ws in sh.worksheets():
    if ws.title == "_genmeta":
        continue
    tabs[ws.title] = ws.get_all_values()

# ── 1. CONFLICTS: one business, multiple distinct phones ─────────────────────────
print("=" * 70)
print("DATA-QUALITY AUDIT  ·  colorado-trip sheet")
print("=" * 70)
print("\n## 1. PHONE CONFLICTS (same business, different PRIMARY-contact numbers)\n")
conflicts = 0
for ent in ENTITIES:
    found = collections.defaultdict(list)   # normalized phone -> ["tab row N", ...]
    for tab, rows in tabs.items():
        for i, r in enumerate(rows):
            # only count the row if THIS business is the row's own name, and only its
            # first (contact) phone — so notes that quote another business don't pollute.
            if ent.lower() in primary_name(r).lower():
                ph = first_phone(r)
                if ph:
                    found[ph].append(f"{tab} r{i+1}")
    if len(found) > 1:
        conflicts += 1
        print(f"  ⚠️  {ent}: {len(found)} different numbers —")
        for ph, where in found.items():
            print(f"        {fmt_phone(ph)}   ←  {', '.join(where)}")
if not conflicts:
    print("  ✓ none")

# ── 2. MISSING contact info on contact-style tabs ────────────────────────────────
HEADER_NAMES = {"restaurant / place", "facility", "service / operator", "priority", "rank",
                "name", "item", "come as you are?"}
print("\n## 2. MISSING phone+website on dining/daycare/shuttle entries\n")
for tab in CONTACT_TABS:
    rows = tabs.get(tab, [])
    missing = []
    for i, r in enumerate(rows):
        cells = [c.strip() for c in r]
        name = primary_name(r)
        nlow = name.lower()
        # skip non-entries: blanks, section banners (contain "|"), column headers, prompts
        if (len(name) < 4 or "|" in name or name.endswith("?") or nlow in HEADER_NAMES
                or sum(1 for c in cells if c) < 3):
            continue
        line = " ".join(cells)
        if not PHONE_RE.search(line) and not URL_RE.search(line):
            missing.append((i + 1, name[:42]))
    label = f"  {tab}: "
    if missing:
        print(label + f"{len(missing)} entry row(s) with no phone or site —")
        for rn, nm in missing[:12]:
            print(f"        r{rn}: {nm}")
        if len(missing) > 12:
            print(f"        … +{len(missing)-12} more")
    else:
        print(label + "✓ all entries have a phone or site")

print("\n" + "=" * 70)
print(f"SUMMARY: {conflicts} phone-conflict(s) to reconcile.")
print("=" * 70)
