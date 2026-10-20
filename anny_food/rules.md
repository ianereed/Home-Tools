# Anny Food — breakdown rules

How to turn a recipe (or a staple food, or a free-text log entry) into
per-serving food-group amounts. A Claude Code session follows this file
when it computes breakdowns; `python -m anny_food.cli import` checks the
result's shape, not its nutrition. Target numbers live in `targets.toml`.

Source: DGA 2020–2025 Table 5-1 and the cup/ounce-equivalent footnotes
(pp. 145–146, Footnotes B and C to Table A3-2), plus Ian's workbook.

## Output keys (per serving)

Every breakdown gives a number (≥ 0; use 0 when none) for each of these
keys. Totals (`veg_total`, `protein_total`) are never given; the app adds
them up.

| Key | Unit | What goes here |
|---|---|---|
| `veg_dark_green` | cup eq | broccoli, bok choy, kale, spinach, romaine, chard, collards, arugula, mustard/turnip greens, watercress |
| `veg_red_orange` | cup eq | carrots, red/orange/yellow bell peppers, sweet potatoes, tomatoes (incl. canned, sauce, paste), winter squash (butternut, pumpkin, acorn), red chili peppers |
| `veg_legumes` | cup eq | dried/canned beans, chickpeas, lentils, split peas, hummus (bean part). **Not** green beans or green peas. **Not** edamame/tofu (→ soy) |
| `veg_starchy` | cup eq | corn, white potatoes, green peas, plantains, lima beans, yams, cassava, taro, lotus root, water chestnut |
| `veg_other` | cup eq | everything else: onions, garlic, mushrooms, celery, cabbage, cauliflower, cucumber, zucchini, green beans, asparagus, avocado, beets, eggplant, green bell pepper, lettuce other than romaine, bean sprouts |
| `fruit` | cup eq | whole fruit, 100% juice, dried fruit |
| `grain_whole` | oz eq | brown rice, oats, quinoa, whole-wheat, popcorn, wild rice, farro, barley, bulgur, buckwheat, whole-grain corn tortillas |
| `grain_refined` | oz eq | white rice, regular pasta, white bread, flour tortillas, couscous, crackers, panko/breadcrumbs, all-purpose flour |
| `dairy` | cup eq | milk, yogurt, kefir, natural and processed cheese, calcium-fortified soy milk/yogurt |
| `protein_meat_eggs` | oz eq | meat, poultry, eggs |
| `protein_seafood` | oz eq | low-mercury fish and shellfish only (includes albacore) |
| `seafood_albacore` | oz eq | albacore/white tuna only. **Also** counted in `protein_seafood` |
| `protein_nuts_seeds_soy` | oz eq | nuts, seeds, nut/seed butters, tofu, tempeh, edamame, soy nuts |
| `oils` | g | see Oils |
| `calcium` | mg | see Calcium / choline |
| `choline` | mg | see Calcium / choline |

## Equivalences

**Vegetables.** 1 cup raw or cooked = 1 cup eq. 2 cups raw leafy greens =
1 cup eq. 1 cup 100% vegetable juice = 1 cup eq. Aromatics in small
amounts (1 clove garlic, 1 tbsp herbs) still count, at their real volume
(1 medium onion ≈ 1 cup chopped; 1 clove garlic ≈ 1 tsp ≈ 0.02 cup).

**Fruit.** 1 cup fruit or 100% juice = 1 cup eq. ½ cup dried fruit = 1 cup
eq. 1 medium apple/orange/banana ≈ 1 cup eq; 1 small ≈ ½.

**Grains.** 1 slice bread or 1 small (6") tortilla = 1 oz eq. 1 oz
ready-to-eat cereal = 1 oz eq. ½ cup cooked pasta/rice/oats = 1 oz eq.
1 oz dry pasta or rice (¼ rice-cooker cup) = 1 oz eq. 1 large (10")
flour tortilla ≈ 4 oz eq. 1 cup all-purpose flour ≈ 4 oz eq (≈ 125 g;
1 oz eq ≈ 28 g flour). Classify whole vs refined by ingredient; for a
mixed product with no stated split, use 50/50.

**Dairy.** 1 cup milk, yogurt or calcium-fortified soy milk = 1 cup eq.
1.5 oz natural cheese (≈ ⅓ cup shredded) = 1 cup eq. 2 oz processed
cheese = 1 cup eq. ½ cup ricotta ≈ 1 cup eq; 2 cups cottage cheese ≈ 1 cup
eq. **Don't count** cream, half-and-half, sour cream, butter, cream cheese,
or other plant milks.

**Protein.** 1 oz cooked lean meat/poultry/fish = 1 oz eq; raw weight →
cooked × 0.75 (1 lb raw ≈ 12 oz cooked). 1 egg = 1 oz eq. ¼ cup cooked
beans or tofu = 1 oz eq. 1 tbsp nut/seed butter = 1 oz eq. ½ oz nuts or
seeds (≈ 2 tbsp) = 1 oz eq.

**Legumes are stored once, under `veg_legumes`, in cup eq — never also
under protein.** The app counts them toward vegetables until the weekly
`veg_legumes` target is met, then counts the rest as protein (1 cup eq =
4 oz eq).

**Seafood.** Low-mercury only: salmon, cod, shrimp, tilapia, trout,
scallops, crab, clams, sardines, anchovies, pollock, catfish, haddock,
canned light tuna. Albacore/white tuna goes in `protein_seafood` *and*
`seafood_albacore`. High-mercury fish (swordfish, king mackerel, shark,
tilefish, bigeye tuna, marlin, orange roughy): **not counted** anywhere,
flag `high_mercury_fish`.

**Oils.** Count vegetable/olive/nut oils, mayonnaise, soft margarine and
salad dressings, plus the oil in nuts, seeds, avocado, olives and fatty
fish. 1 tbsp oil ≈ 14 g; 1 tbsp mayo ≈ 11 g; 1 oz nuts ≈ 14 g;
½ avocado ≈ 10 g; 3 oz salmon ≈ 6 g; 10 olives ≈ 4 g. Pesto ≈ 1.5 tbsp
oil per serving. Butter, lard, cream and coconut oil are solid fats:
**don't count**.

**Calcium / choline.** Estimate from the main contributors only, with
USDA-typical values:

| Food | Calcium (mg) | Choline (mg) |
|---|---|---|
| 1 cup milk / yogurt / fortified soy milk | 300 | 40 |
| 1.5 oz cheese | 300 | 10 |
| 1 cup Greek yogurt | 250 | 35 |
| 1 egg | 25 | 150 |
| 3 oz chicken / beef / pork (cooked) | 10 | 70–100 |
| 3 oz salmon / cod (cooked) | 10 | 90 |
| 3 oz canned salmon with bones | 200 | 90 |
| 3 oz sardines with bones | 325 | 70 |
| ½ cup calcium-set tofu | 250 | 35 |
| 1 cup cooked spinach / collards | 250 | 35 |
| 1 cup cooked broccoli | 60 | 60 |
| 1 cup cooked beans | 80 | 70 |
| 1 cup cooked quinoa | 30 | 45 |
| 1 oz almonds | 75 | 15 |
| 3 oz beef liver (flag `liver`) | 5 | 350 |

## Safety flags

Use only these codes (labels live in `targets.toml`): `raw_egg`,
`undercooked_meat`, `raw_fish`, `smoked_seafood`, `high_mercury_fish`,
`unpasteurized_dairy`, `deli_meat`, `pate`, `liver`, `raw_sprouts`,
`unpasteurized_juice`, `alcohol`. Flag only what the recipe actually
does. For example, an aioli with raw yolk gets `raw_egg`, but a baked
egg doesn't. Mention in `notes` when a flag depends on how it's cooked
("flagged deli_meat: fine if heated to steaming").

## Procedure

1. For each ingredient row, read `qty_per_serving` + `unit` + `name` (+
   `qty_raw` / `notes` when the qty is null). The app already stores
   quantities **per serving**, so don't divide by `base_servings` again.
2. Map the ingredient to a key (or to nothing: salt, spices, water,
   stock, sugar, butter, vinegar, soy sauce, baking powder…). An
   ingredient can feed several keys: cheese → `dairy` + `calcium` +
   `choline`; salmon → `protein_seafood` + `oils` + `choline`.
3. Convert to eq units with the rules above and write one `breakdown[]`
   line per (ingredient, key) with the amount you assumed.
4. Sum to per-serving `values`.
5. `confidence`: `high` = all quantities explicit and mapping obvious;
   `med` = some judgment (vague units, "1 can", cooked vs raw unclear);
   `low` = missing quantities, servings guessed, or the recipe is a stub.
6. Read `instructions` for hidden oil ("fry in 2 tbsp oil") and for
   cooking method (safety flags).

## JSON format (`import` input)

```json
{
  "model": "claude-opus-5-5",
  "recipes": [
    {
      "recipe_id": 12,
      "ingredients_hash": "<copy from export-pending>",
      "values": {"veg_dark_green": 0.5, "...": 0},
      "breakdown": [
        {"ingredient": "spinach", "amount": "1 cup raw", "key": "veg_dark_green", "eq": 0.5, "note": "leafy raw ÷2"}
      ],
      "safety_flags": [],
      "confidence": "high",
      "notes": ""
    }
  ],
  "staples": [
    {"name": "Greek yogurt, plain", "serving_label": "1 cup", "values": {"...": 0},
     "breakdown": [], "safety_flags": [], "confidence": "high", "notes": ""}
  ],
  "log": [
    {"log_id": 7, "values": {"...": 0}, "breakdown": [], "safety_flags": [],
     "confidence": "med", "notes": ""}
  ]
}
```

Any section may be missing. `values` must include every key in the table
above. Staples are matched on `name`: re-importing one replaces it.

`log` entries are things Anny typed ("1 cup Greek yogurt"). Give values for
the label as written; the app multiplies by the entry's `servings`
(normally 1).
