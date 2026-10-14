"""Ingredient table and meal library for the fuel-plan prototype.

Nutrient values are per 100 g, approximated from USDA FoodData Central
(SR Legacy / Foundation entries for the generic food). They are good enough
to sanity-check a plan, not to label a product -- v2 should pull exact rows
from the FDC API by fdcId. Sodium assumes "no salt added" canned goods.

Each meal has fixed ingredients plus up to three *levers* -- one ingredient
per macro (protein / carb / fat) whose grams the builder scales so the meal
lands on its slot target. Kitchen constraints (gas oven + range, microwave,
kettle, food processor, immersion blender; no countertop blender, no air
fryer) are respected in the method text.
"""

from typing import NamedTuple


class Food(NamedTuple):
    kcal: float
    p: float
    c: float
    f: float
    fiber: float
    sat: float
    na: float      # sodium mg
    k: float       # potassium mg
    unit: str
    g_per_unit: float
    chol: float = 0.0  # dietary cholesterol mg


# name: (kcal, protein, carb, fat, fiber, sat_fat, sodium_mg, potassium_mg, unit_label, grams_per_unit)
_FOODS = {
    "rolled oats (dry)":            (379, 13.2, 67.7, 6.5, 10.1, 1.1, 6, 362, "cup", 80),
    "nonfat Greek yogurt":          (59, 10.2, 3.6, 0.4, 0.0, 0.1, 36, 141, "cup", 245),
    "blueberries (frozen ok)":      (57, 0.7, 14.5, 0.3, 2.4, 0.0, 1, 77, "cup", 148),
    "raspberries (frozen ok)":      (52, 1.2, 11.9, 0.7, 6.5, 0.0, 1, 151, "cup", 123),
    "banana":                       (89, 1.1, 22.8, 0.3, 2.6, 0.1, 1, 358, "medium", 118),
    "apple":                        (52, 0.3, 13.8, 0.2, 2.4, 0.0, 1, 107, "medium", 182),
    "orange":                       (47, 0.9, 11.8, 0.1, 2.4, 0.0, 0, 181, "medium", 131),
    "Medjool dates":                (277, 1.8, 75.0, 0.2, 6.7, 0.0, 1, 696, "date", 24),
    "honey":                        (304, 0.3, 82.4, 0.0, 0.2, 0.0, 4, 52, "tbsp", 21),
    "whole egg":                    (143, 12.6, 0.7, 9.5, 0.0, 3.1, 142, 138, "large egg", 50),
    "liquid egg whites":            (52, 10.9, 0.7, 0.2, 0.0, 0.0, 166, 163, "cup", 243),
    "100% whole-wheat bread":       (252, 12.4, 42.7, 3.5, 6.0, 0.7, 450, 250, "slice", 43),
    "corn tortillas":               (218, 5.7, 44.6, 2.9, 6.3, 0.4, 45, 186, "tortilla", 26),
    "chicken breast (cooked)":      (165, 31.0, 0.0, 3.6, 0.0, 1.0, 74, 256, "oz", 28.35),
    "salmon fillet (cooked)":       (206, 22.1, 0.0, 12.4, 0.0, 2.5, 61, 384, "oz", 28.35),
    "cod (cooked)":                 (105, 22.8, 0.0, 0.9, 0.0, 0.2, 78, 244, "oz", 28.35),
    "shrimp (cooked)":              (99, 24.0, 0.2, 0.3, 0.0, 0.1, 111, 259, "oz", 28.35),
    "93% lean ground turkey (cooked)": (213, 27.1, 0.0, 11.6, 0.0, 3.0, 82, 296, "oz", 28.35),
    "tuna in water, no salt added": (116, 25.5, 0.0, 0.8, 0.0, 0.2, 50, 237, "can (drained)", 113),
    "extra-firm tofu":              (144, 17.3, 2.8, 8.7, 2.3, 1.3, 14, 237, "block (14 oz)", 397),
    "protein powder (whey isolate)": (370, 83.0, 3.0, 1.5, 0.0, 0.8, 300, 500, "scoop", 30),
    "skim milk":                    (34, 3.4, 5.0, 0.1, 0.0, 0.1, 42, 156, "cup", 245),
    "lentils (cooked)":             (116, 9.0, 20.1, 0.4, 7.9, 0.1, 2, 369, "cup", 198),
    "black beans (no salt, drained)": (132, 8.9, 23.7, 0.5, 8.7, 0.1, 1, 355, "cup", 172),
    "chickpeas (no salt, drained)": (164, 8.9, 27.4, 2.6, 7.6, 0.3, 7, 291, "cup", 164),
    "edamame (shelled, cooked)":    (121, 11.9, 8.9, 5.2, 5.2, 0.6, 6, 436, "cup", 155),
    "brown rice (cooked)":          (123, 2.7, 25.6, 1.0, 1.6, 0.3, 4, 86, "cup", 195),
    "jasmine rice (cooked)":        (130, 2.7, 28.2, 0.3, 0.4, 0.1, 1, 35, "cup", 158),
    "quinoa (cooked)":              (120, 4.4, 21.3, 1.9, 2.8, 0.2, 7, 172, "cup", 185),
    "whole-wheat pasta (cooked)":   (149, 6.0, 30.1, 1.7, 3.9, 0.3, 4, 96, "cup", 140),
    "russet potato (baked, skin on)": (93, 2.5, 21.2, 0.1, 2.2, 0.0, 10, 535, "medium", 173),
    "sweet potato (baked)":         (90, 2.0, 20.7, 0.2, 3.3, 0.1, 36, 475, "medium", 150),
    "broccoli (cooked)":            (35, 2.4, 7.2, 0.4, 3.3, 0.1, 41, 293, "cup", 156),
    "spinach / greens (raw)":       (23, 2.9, 3.6, 0.4, 2.2, 0.1, 79, 558, "cup", 30),
    "bell pepper":                  (31, 1.0, 6.0, 0.3, 2.1, 0.0, 4, 211, "medium", 119),
    "carrots":                      (41, 0.9, 9.6, 0.2, 2.8, 0.0, 69, 320, "cup", 128),
    "frozen stir-fry vegetables":   (65, 2.9, 13.1, 0.2, 4.4, 0.0, 35, 169, "cup", 182),
    "crushed tomatoes, no salt added": (32, 1.6, 7.3, 0.3, 1.9, 0.0, 20, 293, "cup", 242),
    "onion":                        (40, 1.1, 9.3, 0.1, 1.7, 0.0, 4, 146, "medium", 110),
    "extra-virgin olive oil":       (884, 0.0, 0.0, 100.0, 0.0, 13.8, 2, 1, "tbsp", 13.5),
    "avocado":                      (160, 2.0, 8.5, 14.7, 6.7, 2.1, 7, 485, "medium", 150),
    "almonds":                      (579, 21.2, 21.6, 49.9, 12.5, 3.8, 1, 733, "oz", 28),
    "walnuts":                      (654, 15.2, 13.7, 65.2, 6.7, 6.1, 2, 441, "oz", 28),
    "natural peanut butter (no salt)": (588, 24.0, 22.0, 50.0, 6.0, 10.0, 17, 649, "tbsp", 16),
    "chia seeds":                   (486, 16.5, 42.1, 30.7, 34.4, 3.3, 16, 407, "tbsp", 12),
    "hummus (homemade)":            (166, 7.9, 14.3, 9.6, 6.0, 1.4, 240, 228, "tbsp", 15),
    "low-sodium soy sauce":         (53, 8.0, 5.0, 0.0, 0.8, 0.0, 3600, 200, "tsp", 6),
    "sports drink mix":             (380, 0.0, 95.0, 0.0, 0.0, 0.0, 500, 150, "scoop", 22),
    "green beans":                  (31, 1.8, 7.0, 0.2, 2.7, 0.0, 6, 211, "cup", 125),
    "zucchini":                     (17, 1.2, 3.1, 0.3, 1.0, 0.1, 8, 261, "medium", 196),
    "mushrooms":                    (22, 3.1, 3.3, 0.3, 1.0, 0.0, 5, 318, "cup", 70),
    "frozen corn":                  (96, 3.4, 21.0, 1.5, 2.4, 0.2, 1, 218, "cup", 145),
    "kale (raw)":                   (35, 2.9, 4.4, 1.5, 4.1, 0.2, 53, 348, "cup", 21),
    "whole-wheat pita":             (266, 9.8, 55.0, 2.6, 7.4, 0.4, 340, 170, "pita", 64),
}

# Dietary cholesterol, mg per 100 g (USDA FDC, approximate). Plant foods are 0.
_CHOL = {
    "nonfat Greek yogurt": 5, "whole egg": 372, "chicken breast (cooked)": 85, "salmon fillet (cooked)": 63,
    "cod (cooked)": 55, "shrimp (cooked)": 189, "93% lean ground turkey (cooked)": 103,
    "tuna in water, no salt added": 30, "protein powder (whey isolate)": 15, "skim milk": 2,
}

FOODS = {name: Food(*vals, chol=_CHOL.get(name, 0.0)) for name, vals in _FOODS.items()}

# Lower-cholesterol stand-ins used when a day goes over the cholesterol limit.
CHOL_SUBS = {"whole egg": "liquid egg whites", "shrimp (cooked)": "cod (cooked)",
             "93% lean ground turkey (cooked)": "chicken breast (cooked)"}

UNIT_PLURAL = {"medium": "medium", "oz": "oz", "cup": "cup", "tbsp": "tbsp", "tsp": "tsp"}

# Purchase hints for the grocery list. "either" = both Safeway and Whole Foods carry it.
GROCERY_HINTS = {
    "protein powder (whey isolate)": "Whole Foods carries unflavored/vanilla isolate; pick one with ~25 g protein per scoop and no added sugar.",
    "tuna in water, no salt added": "Look for the 'no salt added' label; regular canned tuna is ~5x the sodium.",
    "black beans (no salt, drained)": "'No salt added' cans (both stores); or rinse regular cans to cut ~40% of the sodium.",
    "chickpeas (no salt, drained)": "'No salt added' cans (both stores).",
    "crushed tomatoes, no salt added": "'No salt added' cans (both stores).",
    "100% whole-wheat bread": "Pick a loaf with >=3 g fiber and <=170 mg sodium per slice.",
    "corn tortillas": "Plain corn tortillas (3 ingredients); the refrigerated case at Whole Foods has good ones.",
    "low-sodium soy sauce": "Low-sodium soy or tamari; still salty, so measure it.",
    "sports drink mix": "Any carb drink mix with ~20-25 g carbs per scoop plus sodium; or DIY: 1:1 table sugar + maltodextrin + a pinch of salt.",
    "liquid egg whites": "Cartons of 100% egg whites.",
    "edamame (shelled, cooked)": "Frozen shelled edamame (both stores).",
    "frozen stir-fry vegetables": "Any unsauced frozen vegetable blend.",
}

GROCERY_SECTION = {
    "Produce": ["banana", "apple", "orange", "avocado", "broccoli (cooked)", "spinach / greens (raw)", "bell pepper",
                "carrots", "onion", "russet potato (baked, skin on)", "sweet potato (baked)", "Medjool dates",
                "green beans", "zucchini", "mushrooms", "kale (raw)"],
    "Protein": ["chicken breast (cooked)", "salmon fillet (cooked)", "cod (cooked)", "shrimp (cooked)",
                "93% lean ground turkey (cooked)", "extra-firm tofu", "whole egg", "liquid egg whites",
                "tuna in water, no salt added"],
    "Dairy": ["nonfat Greek yogurt", "skim milk"],
    "Frozen": ["blueberries (frozen ok)", "raspberries (frozen ok)", "frozen stir-fry vegetables", "edamame (shelled, cooked)",
               "frozen corn"],
    "Pantry & grains": ["rolled oats (dry)", "brown rice (cooked)", "jasmine rice (cooked)", "quinoa (cooked)",
                        "whole-wheat pasta (cooked)", "100% whole-wheat bread", "corn tortillas", "whole-wheat pita",
                        "lentils (cooked)", "black beans (no salt, drained)", "chickpeas (no salt, drained)",
                        "crushed tomatoes, no salt added", "hummus (homemade)", "low-sodium soy sauce", "honey"],
    "Nuts, seeds & oils": ["almonds", "walnuts", "natural peanut butter (no salt)", "chia seeds", "extra-virgin olive oil"],
    "Supplements & ride fuel": ["protein powder (whey isolate)", "sports drink mix"],
}

# Cooked-weight foods bought raw: multiply cooked grams by this to get grams to buy.
RAW_PER_COOKED = {
    "chicken breast (cooked)": 1.33, "salmon fillet (cooked)": 1.25, "cod (cooked)": 1.25,
    "shrimp (cooked)": 1.25, "93% lean ground turkey (cooked)": 1.3,
    "brown rice (cooked)": 0.37, "jasmine rice (cooked)": 0.36, "quinoa (cooked)": 0.37,
    "whole-wheat pasta (cooked)": 0.42, "lentils (cooked)": 0.40,
}

# Meal library.
#   slot: B breakfast, L lunch, S snack, D dinner
#   pre_ok: low-fat/low-fiber enough to eat 1.5-3 h before a ride
#   portable: survives a cooler bag (packed lunches, trips)
#   levers: {"p": ingredient, "c": ingredient, "f": ingredient} -- any may be absent
#   lever_max: max grams for a lever (keeps portions sane)
MEALS = [
    # ---------------- breakfasts ----------------
    {"id": "oats", "slot": "B", "name": "Kettle oats with berries, yogurt and walnuts", "pre_ok": True, "portable": True,
     "fixed": {"blueberries (frozen ok)": 100, "chia seeds": 10},
     "levers": {"p": "nonfat Greek yogurt", "c": "rolled oats (dry)", "f": "walnuts"},
     "method": "Pour boiling kettle water over the oats (2:1), cover 5 min (or microwave 2 min). Top with berries, chia, yogurt, chopped walnuts. Pre-ride: skip the chia and walnuts."},
    {"id": "scramble", "slot": "B", "name": "Veggie egg scramble on toast with avocado", "pre_ok": False, "portable": False,
     "fixed": {"whole egg": 50, "spinach / greens (raw)": 60, "bell pepper": 60},
     "levers": {"p": "liquid egg whites", "c": "100% whole-wheat bread", "f": "avocado"},
     "method": "Gas range, nonstick pan: wilt the spinach and pepper, add 1 egg plus the whites, soft-scramble. Toast the bread; mash the avocado on top."},
    {"id": "smoothie", "slot": "B", "name": "Banana-berry protein smoothie + toast", "pre_ok": True, "portable": True,
     "fixed": {"skim milk": 300, "raspberries (frozen ok)": 100, "spinach / greens (raw)": 30},
     "levers": {"p": "protein powder (whey isolate)", "c": "banana", "f": "natural peanut butter (no salt)"},
     "c_extra": {"100% whole-wheat bread": 0.35},
     "method": "Tall jar + immersion blender: milk, frozen berries, banana, spinach, protein, peanut butter. Blend 45 s. Toast on the side."},
    {"id": "yogurt_bowl", "slot": "B", "name": "Greek yogurt bowl with oats, banana and almonds", "pre_ok": True, "portable": True,
     "fixed": {"raspberries (frozen ok)": 80},
     "levers": {"p": "nonfat Greek yogurt", "c": "banana", "f": "almonds"},
     "c_extra": {"rolled oats (dry)": 0.4},
     "method": "No cooking. Yogurt, raw oats, sliced banana, thawed raspberries, chopped almonds. Make it the night before for a packed breakfast or an early start."},

    # ---------------- lunches ----------------
    {"id": "grain_bowl", "slot": "L", "name": "Chicken quinoa bowl with chickpeas and greens", "pre_ok": False, "portable": True,
     "fixed": {"chickpeas (no salt, drained)": 80, "spinach / greens (raw)": 60, "bell pepper": 80},
     "levers": {"p": "chicken breast (cooked)", "c": "quinoa (cooked)", "f": "extra-virgin olive oil"},
     "method": "Batch on prep day: roast the chicken on a sheet pan (425°F, 20-22 min); simmer quinoa 15 min. Assemble with greens, pepper, and chickpeas; dress with olive oil + lemon."},
    {"id": "lentil_soup", "slot": "L", "name": "Lentil-tomato soup with chicken + toast", "pre_ok": False, "portable": True,
     "fixed": {"lentils (cooked)": 180, "crushed tomatoes, no salt added": 150, "carrots": 60, "onion": 50, "spinach / greens (raw)": 30},
     "levers": {"p": "chicken breast (cooked)", "c": "100% whole-wheat bread", "f": "extra-virgin olive oil"},
     "method": "Big pot on the gas range: sweat the onion and carrot in oil, add lentils, tomatoes, and water, simmer 25 min, then half-blend with the immersion blender. Stir in shredded chicken and spinach. Freezes well."},
    {"id": "tuna_sando", "slot": "L", "name": "Tuna-avocado sandwich + apple", "pre_ok": True, "portable": True,
     "fixed": {"spinach / greens (raw)": 30, "apple": 180},
     "levers": {"p": "tuna in water, no salt added", "c": "100% whole-wheat bread", "f": "avocado"},
     "method": "Mash the tuna with the avocado (no mayo), pile onto the bread with greens. Packs well."},
    {"id": "tacos", "slot": "L", "name": "Chicken and black bean tacos", "pre_ok": False, "portable": False,
     "fixed": {"black beans (no salt, drained)": 100, "bell pepper": 80, "onion": 40},
     "levers": {"p": "chicken breast (cooked)", "c": "corn tortillas", "f": "avocado"},
     "method": "Char the tortillas directly on the gas burner. Sauté the peppers and onion; warm the beans with cumin. Top with sliced avocado and lime."},

    # ---------------- snacks ----------------
    {"id": "yogurt_snack", "slot": "S", "name": "Greek yogurt + fruit + almonds", "pre_ok": True, "portable": True,
     "fixed": {"blueberries (frozen ok)": 80},
     "levers": {"p": "nonfat Greek yogurt", "c": "banana", "f": "almonds"},
     "method": "Bowl, no cooking."},
    {"id": "shake_fruit", "slot": "S", "name": "Protein shake + banana / dates", "pre_ok": True, "portable": True,
     "fixed": {},
     "levers": {"p": "protein powder (whey isolate)", "c": "Medjool dates", "f": "natural peanut butter (no salt)"},
     "c_extra": {"banana": 0.5},
     "method": "Shaker bottle with skim milk or water. Best right after a ride or lift."},
    {"id": "hummus_plate", "slot": "S", "name": "Hummus, carrots, and an egg on toast", "pre_ok": False, "portable": True,
     "fixed": {"carrots": 100, "whole egg": 50},
     "levers": {"p": "nonfat Greek yogurt", "c": "100% whole-wheat bread", "f": "hummus (homemade)"},
     "method": "Food processor hummus (chickpeas, tahini, lemon, garlic, no added salt) on prep day. Hard-boil a few eggs in the kettle-boiled water. Yogurt makes a tzatziki-style dip."},

    # ---------------- dinners ----------------
    {"id": "salmon_sheet", "slot": "D", "name": "Sheet-pan salmon, sweet potato and broccoli", "pre_ok": False, "portable": False,
     "fixed": {"broccoli (cooked)": 200},
     "levers": {"p": "salmon fillet (cooked)", "c": "sweet potato (baked)", "f": "extra-virgin olive oil"},
     "method": "Gas oven at 425°F. Roast the sweet potato wedges in a little oil for 15 min, add the broccoli and salmon, and roast 12-14 min more."},
    {"id": "stirfry", "slot": "D", "name": "Chicken + edamame stir-fry over jasmine rice", "pre_ok": False, "portable": True,
     "fixed": {"frozen stir-fry vegetables": 200, "edamame (shelled, cooked)": 60, "low-sodium soy sauce": 8},
     "levers": {"p": "chicken breast (cooked)", "c": "jasmine rice (cooked)", "f": "extra-virgin olive oil"},
     "method": "Hot wok or skillet on the gas range. Microwave the rice (or batch-cook it). Use only 1.5 tsp of soy sauce, plus ginger, garlic, and rice vinegar for flavor."},
    {"id": "bolognese", "slot": "D", "name": "Turkey-lentil bolognese with whole-wheat pasta", "pre_ok": False, "portable": True,
     "fixed": {"crushed tomatoes, no salt added": 200, "lentils (cooked)": 80, "onion": 60, "carrots": 50, "spinach / greens (raw)": 30},
     "levers": {"p": "93% lean ground turkey (cooked)", "c": "whole-wheat pasta (cooked)", "f": "extra-virgin olive oil"},
     "method": "Brown the turkey, add the onion and carrot (pulsed in the food processor), tomatoes, and lentils; simmer 20 min. Batch-friendly: make 4 portions."},
    {"id": "shrimp_quinoa", "slot": "D", "name": "Garlic shrimp, roasted potatoes and greens", "pre_ok": False, "portable": False,
     "fixed": {"broccoli (cooked)": 150, "bell pepper": 80},
     "levers": {"p": "shrimp (cooked)", "c": "russet potato (baked, skin on)", "f": "extra-virgin olive oil"},
     "method": "Roast the potato chunks (425°F, 30 min). Sauté the shrimp with garlic and lemon in the last 5 min; steam the broccoli in the microwave."},
    {"id": "tofu_bowl", "slot": "D", "name": "Crispy tofu, brown rice and broccoli bowl", "pre_ok": False, "portable": True,
     "fixed": {"broccoli (cooked)": 180, "carrots": 60, "low-sodium soy sauce": 6},
     "levers": {"p": "extra-firm tofu", "c": "brown rice (cooked)", "f": "extra-virgin olive oil"},
     "method": "Press the tofu, cube it, and bake at 425°F for 25 min. Brown rice batch-cooked or microwaved. Tofu alone tops out around 35 g protein, so add a yogurt dessert if short."},
    {"id": "chili", "slot": "D", "name": "Chicken and black bean chili with baked potato", "pre_ok": False, "portable": True,
     "fixed": {"black beans (no salt, drained)": 150, "crushed tomatoes, no salt added": 200, "bell pepper": 80, "onion": 60},
     "levers": {"p": "chicken breast (cooked)", "c": "russet potato (baked, skin on)", "f": "avocado"},
     "method": "Dutch oven on the gas range: onion, pepper, chili powder, and cumin, then the tomatoes, beans, and diced chicken; simmer 30 min. Microwave the potato 8 min. Top with avocado. Batch: 4 portions."},
]

MEALS += [
    # ---------------- more breakfasts ----------------
    {"id": "pb_toast", "slot": "B", "name": "Peanut-butter banana toast + skim latte", "pre_ok": True, "portable": False,
     "fixed": {"skim milk": 250},
     "levers": {"p": "protein powder (whey isolate)", "c": "100% whole-wheat bread", "f": "natural peanut butter (no salt)"},
     "c_extra": {"banana": 1.0},
     "method": "Toast the bread, spread peanut butter, slice banana on top. Kettle-hot water + espresso or instant coffee and steamed (microwaved) milk; stir a scoop of protein into the milk before heating."},
    {"id": "chia_pudding", "slot": "B", "name": "Overnight chia-oat pudding with berries", "pre_ok": False, "portable": True,
     "fixed": {"chia seeds": 15, "raspberries (frozen ok)": 100, "skim milk": 200},
     "levers": {"p": "nonfat Greek yogurt", "c": "rolled oats (dry)", "f": "almonds"},
     "method": "The night before: stir oats, chia, milk, and yogurt in a jar; refrigerate. Top with berries and almonds in the morning."},
    # ---------------- more lunches ----------------
    {"id": "chickpea_pita", "slot": "L", "name": "Smashed chickpea + chicken pita with greens", "pre_ok": False, "portable": True,
     "fixed": {"chickpeas (no salt, drained)": 90, "spinach / greens (raw)": 40, "bell pepper": 60},
     "levers": {"p": "chicken breast (cooked)", "c": "whole-wheat pita", "f": "avocado"},
     "method": "Smash the chickpeas with lemon and the avocado (food processor pulse or fork), fold in chopped chicken and peppers, stuff the pita with greens."},
    {"id": "bean_bowl", "slot": "L", "name": "Black bean, corn and brown rice bowl", "pre_ok": False, "portable": True,
     "fixed": {"black beans (no salt, drained)": 120, "frozen corn": 70, "bell pepper": 60, "spinach / greens (raw)": 30},
     "levers": {"p": "chicken breast (cooked)", "c": "brown rice (cooked)", "f": "avocado"},
     "method": "Microwave the corn and beans with cumin and lime; serve over brown rice with chicken, peppers, and sliced avocado. Salsa fresca instead of jarred salsa keeps the sodium down."},
    {"id": "salmon_quinoa", "slot": "L", "name": "Leftover salmon over quinoa and kale", "pre_ok": False, "portable": True,
     "fixed": {"kale (raw)": 40, "zucchini": 100},
     "levers": {"p": "salmon fillet (cooked)", "c": "quinoa (cooked)", "f": "extra-virgin olive oil"},
     "method": "Cook a double salmon portion at dinner the night before. Massage the kale with lemon and oil, add quinoa, sautéed zucchini, and flaked salmon."},
    # ---------------- more snacks ----------------
    {"id": "edamame_snack", "slot": "S", "name": "Edamame + orange + almonds", "pre_ok": False, "portable": True,
     "fixed": {"edamame (shelled, cooked)": 100},
     "levers": {"p": "nonfat Greek yogurt", "c": "orange", "f": "almonds"},
     "method": "Microwave frozen edamame 2 min (no added salt; lemon + chili flakes instead). Orange on the side."},
    {"id": "recovery_milk", "slot": "S", "name": "Recovery shake: milk, protein, banana, oats", "pre_ok": True, "portable": True,
     "fixed": {"skim milk": 300},
     "levers": {"p": "protein powder (whey isolate)", "c": "banana", "f": "natural peanut butter (no salt)"},
     "c_extra": {"rolled oats (dry)": 0.3},
     "method": "Tall jar + immersion blender, 30 s. Drink within 30 min of the ride or lift."},
    # ---------------- more dinners ----------------
    {"id": "cod_beans", "slot": "D", "name": "Baked cod, roasted potatoes and green beans", "pre_ok": False, "portable": False,
     "fixed": {"green beans": 200},
     "levers": {"p": "cod (cooked)", "c": "russet potato (baked, skin on)", "f": "extra-virgin olive oil"},
     "method": "425°F: potato wedges in a little oil for 20 min, then add the cod (lemon, garlic, paprika) and green beans for 12 min more."},
    {"id": "fajita_sheet", "slot": "D", "name": "Sheet-pan chicken fajitas", "pre_ok": False, "portable": True,
     "fixed": {"bell pepper": 160, "onion": 80, "black beans (no salt, drained)": 80},
     "levers": {"p": "chicken breast (cooked)", "c": "corn tortillas", "f": "avocado"},
     "method": "Toss the chicken strips, peppers, and onion in oil, cumin, chili powder, and lime; roast at 425°F for 20 min. Warm the tortillas over the gas burner."},
    {"id": "lentil_curry", "slot": "D", "name": "Red lentil + spinach curry over brown rice", "pre_ok": False, "portable": True,
     "fixed": {"lentils (cooked)": 150, "crushed tomatoes, no salt added": 150, "spinach / greens (raw)": 60, "onion": 60},
     "levers": {"p": "nonfat Greek yogurt", "c": "brown rice (cooked)", "f": "extra-virgin olive oil"},
     "method": "Sweat the onion with garlic, ginger, and curry powder in oil; add lentils and tomatoes, simmer 15 min, wilt in spinach. Serve with a big spoon of Greek yogurt. Batch-friendly."},
    {"id": "primavera", "slot": "D", "name": "Chicken + vegetable whole-wheat pasta primavera", "pre_ok": False, "portable": True,
     "fixed": {"zucchini": 120, "mushrooms": 80, "spinach / greens (raw)": 40, "crushed tomatoes, no salt added": 100},
     "levers": {"p": "chicken breast (cooked)", "c": "whole-wheat pasta (cooked)", "f": "extra-virgin olive oil"},
     "method": "Sauté the zucchini and mushrooms in oil and garlic, add the tomatoes and spinach, toss with the pasta and sliced chicken. Lemon zest + black pepper instead of salt and parmesan."},
]

LEVER_MAX = {
    "extra-virgin olive oil": 25, "walnuts": 40, "almonds": 40, "natural peanut butter (no salt)": 32,
    "avocado": 75, "hummus (homemade)": 90, "protein powder (whey isolate)": 60, "nonfat Greek yogurt": 400,
    "liquid egg whites": 300, "Medjool dates": 96, "banana": 240, "rolled oats (dry)": 120,
    "100% whole-wheat bread": 86, "corn tortillas": 156, "extra-firm tofu": 300, "whole-wheat pita": 128,
    "sweet potato (baked)": 400, "russet potato (baked, skin on)": 400,
}

# When a lever hits its max and the meal is still short, top up with a side.
# macro -> slot -> (ingredient, max grams)
FALLBACK = {
    "c": {"B": ("banana", 240), "L": ("orange", 262), "S": ("Medjool dates", 72), "D": ("jasmine rice (cooked)", 300)},
    "f": {"*": ("extra-virgin olive oil", 15), "oil": ("almonds", 30)},
    "p": {"*": ("nonfat Greek yogurt", 250)},
}
FIBER_SOFT_MAX = 55
