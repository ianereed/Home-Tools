"""Deterministic ingredient -> Todoist grocery-section classifier.

The meal-planner sends each ingredient to a Todoist section. Historically the
section came from a Gemini call that (a) was never run on photo-intake/corpus
recipes (NULL section -> dumped to the produce fallback at send time) and (b)
sometimes used fake default section names. This module replaces that with a
deterministic, dependency-free keyword classifier so every ingredient lands in a
sensible section, predictably and for free.

Rules are checked in order; the FIRST matching substring wins. Order encodes
precedence: overrides come before the generic term they'd otherwise collide with.
Worked examples that drove the ordering:
  - "chicken broth"            broth->Shelf must beat chicken->Meats
  - "unsalted butter"          butter->Dairy must beat salt->Shelf
  - "salt (for eggs)"          salt->Shelf must beat egg->Dairy
  - "butter (or coconut oil)"  butter->Dairy must beat coconut oil->Shelf
  - "heavy cream (or coconut milk)" cream->Dairy must beat coconut milk->Shelf
  - "kewpie mayo"              kewpie->Asian must beat mayo->Dairy
  - "old el paso green chilies" canned green chilies->Shelf must beat chili->Produce
  - "ground ginger"            dried spice->Shelf must beat ginger->Produce
"frozen" is matched as a leading qualifier only (so "...if frozen, add 1-2 min"
in a chicken note doesn't route chicken to Frozen). Section names match the live
TODOIST_SECTIONS keys exactly.
"""
from __future__ import annotations

# Canonical Todoist sections (must match TODOIST_SECTIONS keys on the mini).
FRUITS_VEGGIES = "Fruits + Veggies"
DAIRY = "Dairy + cold items"
FROZEN = "Frozen"
MEATS = "Meats"
SHELF = "Shelf-stable"
HOME = "Home/Pharmacy"
ASIAN = "Asian market"

CANONICAL_SECTIONS = frozenset(
    {FRUITS_VEGGIES, DAIRY, FROZEN, MEATS, SHELF, HOME, ASIAN, "Meals"}
)

# Ordered grocery sections (excludes "Meals", which is the recipe-header section,
# not an ingredient destination). Used as the real-name fallback for the Gemini
# categorize prompt when TODOIST_SECTIONS isn't in the environment.
GROCERY_SECTIONS = [FRUITS_VEGGIES, DAIRY, MEATS, SHELF, FROZEN, ASIAN, HOME]

# Unknowns fall here: most uncategorized grocery items are pantry staples, and
# Shelf-stable is the least-bad default (vs the old produce fallback).
_FALLBACK = SHELF

# Ordered (substring, section). First substring found in the lowercased name wins.
_RULES: list[tuple[str, str]] = [
    # --- Frozen specials ("frozen " leading qualifier handled in code) ---
    ("puff pastry", FROZEN),
    ("ice cube", FROZEN),
    # --- Pantry/produce overrides that must beat a later generic term ---
    ("canned", SHELF),            # canned tomatoes -> pantry, not produce
    ("broth", SHELF),             # chicken broth -> pantry, not meat
    ("stock", SHELF),             # chicken stock -> pantry, not meat
    ("ground ginger", SHELF),     # dried spice, not fresh ginger
    ("green chil", SHELF),        # canned green chilies, not fresh chili
    ("pineapple tidbits", SHELF),  # canned, not fresh fruit
    ("kewpie", ASIAN),            # kewpie mayo -> Asian market, not generic mayo
    # --- Dairy + cold (butter before salt; salt before egg; coconut before milk) ---
    ("butter", DAIRY),            # unsalted butter -> dairy (also beats coconut oil)
    ("salt", SHELF),              # "salt (for eggs)" -> pantry, not dairy
    ("sour cream", DAIRY),
    ("cream", DAIRY),             # heavy/whipped cream (beats coconut milk below)
    ("egg", DAIRY),
    ("cheese", DAIRY),
    ("cheddar", DAIRY),
    ("parmigiano", DAIRY),
    ("parmesan", DAIRY),
    ("mascarpone", DAIRY),
    ("mozzarella", DAIRY),
    ("mayo", DAIRY),              # plain mayo (kewpie handled above)
    ("yogurt", DAIRY),
    ("coconut flesh", FRUITS_VEGGIES),
    ("coconut milk", SHELF),
    ("coconut oil", SHELF),
    ("coconut", SHELF),           # shredded/sweetened coconut
    ("milk", DAIRY),              # whole milk (coconut milk handled above)
    # --- Asian market (specialty) ---
    ("soy sauce", ASIAN),
    ("oyster sauce", ASIAN),
    ("shaoxing", ASIAN),
    ("rice wine", ASIAN),
    ("dry sherry", ASIAN),
    ("sesame oil", ASIAN),
    ("sesame seed", ASIAN),
    ("seaweed", ASIAN),
    ("nori", ASIAN),
    ("mirin", ASIAN),
    ("fish sauce", ASIAN),
    ("curry paste", ASIAN),
    ("garam masala", ASIAN),
    ("miso", ASIAN),
    ("udon", ASIAN),
    ("nishiki", ASIAN),
    ("short grain rice", ASIAN),
    ("sichuan peppercorn", ASIAN),
    ("gochujang", ASIAN),
    ("hoisin", ASIAN),
    ("imitation crab", ASIAN),    # sushi component, bought at the Asian market
    # --- Meats / seafood ---
    ("chicken", MEATS),
    ("beef", MEATS),
    ("chuck roast", MEATS),
    ("pork", MEATS),
    ("bacon", MEATS),
    ("sausage", MEATS),
    ("drumstick", MEATS),
    ("salmon", MEATS),
    ("shrimp", MEATS),
    ("tempura", MEATS),
    ("crab", MEATS),
    ("turkey", MEATS),
    ("lamb", MEATS),
    # --- Fruits + Veggies (fresh produce) ---
    ("onion", FRUITS_VEGGIES),
    ("scallion", FRUITS_VEGGIES),
    ("garlic", FRUITS_VEGGIES),
    ("ginger", FRUITS_VEGGIES),   # fresh/minced ginger (ground handled above)
    ("broccoli", FRUITS_VEGGIES),
    ("carrot", FRUITS_VEGGIES),
    ("celery", FRUITS_VEGGIES),
    ("cabbage", FRUITS_VEGGIES),
    ("bok choy", FRUITS_VEGGIES),
    ("mushroom", FRUITS_VEGGIES),
    ("shiitake", FRUITS_VEGGIES),
    ("leek", FRUITS_VEGGIES),
    ("bell pepper", FRUITS_VEGGIES),
    ("shallot", FRUITS_VEGGIES),
    ("sweet potato", FRUITS_VEGGIES),
    ("potato", FRUITS_VEGGIES),
    ("avocado", FRUITS_VEGGIES),
    ("lemon", FRUITS_VEGGIES),
    ("lime", FRUITS_VEGGIES),
    ("cilantro", FRUITS_VEGGIES),
    ("parsley", FRUITS_VEGGIES),
    ("basil", FRUITS_VEGGIES),
    ("fresh thyme", FRUITS_VEGGIES),
    ("cherry or grape", FRUITS_VEGGIES),
    ("grape tomato", FRUITS_VEGGIES),
    ("tomato", FRUITS_VEGGIES),   # canned handled above
    ("chili", FRUITS_VEGGIES),    # fresh chili (dried/flakes/green-canned handled elsewhere)
    ("chile", FRUITS_VEGGIES),
    ("pineapple", FRUITS_VEGGIES),  # tidbits handled above
    # --- Shelf-stable (pantry, baking, spices, dry goods) ---
    ("flour", SHELF),
    ("sugar", SHELF),
    ("baking soda", SHELF),
    ("baking powder", SHELF),
    ("cornstarch", SHELF),
    ("corn starch", SHELF),
    ("cumin", SHELF),
    ("coriander", SHELF),
    ("turmeric", SHELF),
    ("paprika", SHELF),
    ("oregano", SHELF),
    ("cayenne", SHELF),
    ("red pepper", SHELF),        # flakes / crushed dried red peppers
    ("star anise", SHELF),
    ("peppercorn", SHELF),
    ("pepper", SHELF),            # ground black/white pepper (bell pepper handled above)
    ("chocolate", SHELF),
    ("cocoa", SHELF),
    ("cacao", SHELF),
    ("vanilla", SHELF),
    ("honey", SHELF),
    ("espresso", SHELF),
    ("coffee", SHELF),
    ("mustard", SHELF),
    ("beans", SHELF),
    ("enchilada sauce", SHELF),
    ("orzo", SHELF),
    ("pasta", SHELF),
    ("noodle", SHELF),            # udon handled above
    ("rice", SHELF),              # nishiki/short-grain handled above
    ("lentil", SHELF),
    ("graham", SHELF),
    ("ladyfinger", SHELF),
    ("pavesini", SHELF),
    ("custard powder", SHELF),
    ("walnut", SHELF),
    ("oil", SHELF),               # olive/vegetable/canola/peanut (sesame/coconut handled above)
    ("vinegar", SHELF),
    ("wine", SHELF),              # white/cooking wine (rice wine handled above)
    ("water", SHELF),
    ("tortilla", SHELF),
    ("pie crust", SHELF),
    ("cracker", SHELF),
    ("nonstick", SHELF),          # baking spray -> baking aisle
    ("baking spray", SHELF),
    # --- Home / Pharmacy (non-food household) ---
    ("parchment", HOME),
    ("foil", HOME),
    ("paper towel", HOME),
]


def classify(name: str, notes: str = "") -> str:
    """Return the canonical Todoist section for an ingredient name.

    Matches the first rule whose substring appears in the lowercased name
    (notes are appended as a tiebreaker hint). "frozen X" as a leading qualifier
    routes to Frozen, but "frozen" buried in a cooking note does not. Unknown
    items fall back to Shelf-stable. Never returns a non-canonical section.
    """
    hay = f"{name or ''} {notes or ''}".lower().strip()
    if hay.startswith("frozen "):
        return FROZEN
    for needle, section in _RULES:
        if needle in hay:
            return section
    return _FALLBACK
