"""Static content for the Diet page — distilled from the Phase 5 research
memos (`health-dashboard/research/diet_research.md` and
`diet_app_recommendation.md`). Pure data, no PHI: nothing here is gated, and
nothing here should ever contain goal values, medication names, or a
clinician's name — see CARDIO_PLAN.md Standing rule 2.

Keep this in sync with the memos by hand; it is a curated distillation, not a
parser, so a memo edit does not automatically propagate here.
"""

STARTING_APPROACH = (
    "**DASH first, per physician instruction.** DASH has the strongest, most "
    "directly-relevant randomized-trial evidence for blood pressure specifically, "
    "and it's the easiest of these patterns to shop and meal-prep for. Because "
    "training sweat losses can be substantial, the 2,300 mg (or stricter 1,500 mg) "
    "sodium ceiling below is designed for a sedentary population — reassess it with "
    "your physician in light of training volume, and don't count in-workout "
    "electrolyte fueling against the daily ceiling. Blue Zones principles "
    "(legume-forward eating, minimally processed food, eating to moderate "
    "fullness) are a reasonable secondary layer on top of a DASH foundation, not "
    "a replacement for it — its evidence base is observational and contested. "
    "Mediterranean is a close, well-evidenced second option with no hard sodium "
    "ceiling. Portfolio is LDL-focused and lower priority here since LDL is "
    "managed pharmacologically."
)

ACTIVITY_GUIDELINES = (
    "**AHA physical activity guidelines (adults):** at least 150 min/week "
    "moderate-intensity aerobic activity, or 75 min/week vigorous-intensity, or an "
    "equivalent combination — spread across the week when possible. Greater "
    "benefit above 300 min/week moderate-intensity. Muscle-strengthening activity "
    "at moderate-to-high intensity at least 2 days/week. Reduce total sitting "
    "time."
)

# Each entry: title, one-paragraph summary, key evidence-backed points, and
# the top sources cited in diet_research.md for that pattern.
SECTIONS = [
    {
        "title": "DASH (Dietary Approaches to Stop Hypertension)",
        "body_md": (
            "Built from ordinary grocery-store items — more vegetables, fruit, "
            "whole grains, low-fat dairy, fish, poultry, beans, nuts, and "
            "vegetable oils; less saturated fat, sugar-sweetened drinks, and "
            "sweets. No special or exotic foods required. The best-evidenced "
            "pattern here for blood pressure specifically, and the AHA's own "
            "2023 diet-scoring gave it a perfect 100/100."
        ),
        "key_points": [
            "Original DASH trial (Appel 1997, NEJM): systolic BP down 11.4 mmHg, "
            "diastolic down 5.5 mmHg vs. a typical American diet, in hypertensive "
            "participants — comparable in magnitude to single-drug antihypertensive "
            "therapy.",
            "DASH-Sodium trial (Sacks 2001, NEJM): combining the DASH pattern with "
            "the lowest (1,500 mg/day) sodium level gave the largest BP benefit of "
            "any arm tested — systolic down up to 8.9 mmHg vs. the higher-sodium "
            "control.",
            "LDL reductions were also reported in follow-up analyses, though as a "
            "secondary finding, not the trial's primary endpoint.",
            "Sodium ceiling: 2,300 mg/day standard, 1,500 mg/day for the larger "
            "effect. Potassium target: ~4,700 mg/day. Saturated fat ≤6% of "
            "calories (total fat ≤27%). No single fiber gram target is published "
            "— it's driven up via produce/whole-grain/legume serving counts.",
        ],
        "sources": [
            ("Appel et al., \"A Clinical Trial of the Effects of Dietary Patterns "
             "on Blood Pressure\", NEJM 1997",
             "https://www.nejm.org/doi/full/10.1056/NEJM199704173361601"),
            ("Sacks et al., DASH-Sodium trial, NEJM 2001",
             "https://www.nejm.org/doi/full/10.1056/NEJM200101043440101"),
            ("NHLBI: DASH Eating Plan",
             "https://www.nhlbi.nih.gov/health/dash-eating-plan"),
            ("AHA: How 10 popular diets scored for heart health (2023)",
             "https://www.heart.org/en/news/2023/04/27/heres-how-10-popular-diets-scored-for-heart-health"),
        ],
    },
    {
        "title": "Mediterranean",
        "body_md": (
            "More fruit, vegetables, whole grains, potatoes, beans/legumes, nuts "
            "and seeds, with olive oil as the primary fat; moderate dairy, eggs, "
            "fish, and poultry (fish/poultry over red meat); optional low-to-"
            "moderate wine with meals. Minimal processing is a core principle. "
            "Highly compatible with high-carbohydrate training diets and no hard "
            "sodium ceiling — the strongest \"does double duty\" option, with "
            "evidence for both blood pressure and hard cardiovascular endpoints."
        ),
        "key_points": [
            "Cochrane systematic review (30 RCTs, 12,461 participants): systolic "
            "BP down ~2.99 mmHg, diastolic down ~2.0 mmHg vs. minimal/no "
            "intervention, plus a small LDL reduction (~0.15 mmol/L).",
            "PREDIMED trial (Estruch et al., NEJM 2018 corrected republication, "
            "7,447 high-risk participants): Mediterranean diet + olive oil showed "
            "roughly a 30% relative risk reduction in the composite of MI, "
            "stroke, or cardiovascular death; stroke-specific hazard ratio 0.60.",
            "AHA's 2023 diet-scoring gave Mediterranean 89/100, just behind "
            "DASH — docked mainly for allowing moderate alcohol and no explicit "
            "sodium ceiling.",
            "No formal numeric sodium ceiling; potassium and fiber rise "
            "indirectly through produce/legume/whole-grain intake rather than a "
            "stated gram target.",
        ],
        "sources": [
            ("Rees et al., Cochrane review of Mediterranean-style diet for CVD prevention",
             "https://pmc.ncbi.nlm.nih.gov/articles/PMC7427685/"),
            ("Estruch et al., PREDIMED (corrected), NEJM 2018",
             "https://www.nejm.org/doi/full/10.1056/NEJMoa1800389"),
            ("AHA: Mediterranean diet",
             "https://www.heart.org/en/healthy-living/healthy-eating/eat-smart/nutrition-basics/mediterranean-diet"),
        ],
    },
    {
        "title": "Blue Zones",
        "body_md": (
            "\"Plant slant\" — roughly 95-100% plant-based, built around daily "
            "beans/legumes (≥½ cup/day), ~2 handfuls of nuts, meat as a rare/"
            "celebratory food, modest fish, minimized dairy/eggs, low added "
            "sugar, and eating to ~80% fullness in a social context. Directionally "
            "consistent with cardiovascular benefit, but the evidence base is "
            "observational, not RCT-quantified, and recent peer-reviewed critical "
            "reviews directly challenge the underlying longevity data — treat as "
            "a lower-certainty, complementary layer on top of DASH, not a "
            "standalone primary pattern."
        ),
        "key_points": [
            "Adventist Health Study-2 (96,000+ participant cohort, the U.S. "
            "\"Blue Zone\"): vegetarians/pescatarians show lower hypertension "
            "rates and lower LDL/triglycerides than non-vegetarians in the same "
            "community — a cohort association, not a controlled-trial effect "
            "size.",
            "No RCT-grade quantified BP or LDL effect size exists for the Blue "
            "Zones pattern as a whole.",
            "Caveat from the literature itself: a 2024 review found an "
            "\"absence of scientific evidence relating community lifestyle to "
            "longevity\" in Blue Zones regions, and a 2025 analysis raises "
            "specific data-reliability concerns about the underlying "
            "centenarian records.",
            "No formal numeric sodium/potassium/saturated-fat targets are "
            "published — guidance is servings-based (beans, nuts, grains), not "
            "nutrient-gram-based.",
        ],
        "sources": [
            ("Adventist Health Study background",
             "https://pmc.ncbi.nlm.nih.gov/articles/PMC11556529/"),
            ("Critical review: \"Lessons From the Blue Zones\" (PMC, 2024/2025)",
             "https://pmc.ncbi.nlm.nih.gov/articles/PMC12048395/"),
            ("Blue Zones: Food Guidelines",
             "https://www.bluezones.com/recipes/food-guidelines/"),
        ],
    },
    {
        "title": "Portfolio",
        "body_md": (
            "A cholesterol-lowering overlay on top of a low-saturated-fat "
            "baseline diet: viscous/soluble fiber (oats, barley, psyllium), "
            "plant sterols/stanols (usually from fortified foods), soy protein, "
            "and tree nuts. The most LDL-potent of the four patterns — but also "
            "the most grocery- and tracking-intensive, and the lowest-priority "
            "one here since LDL is already managed pharmacologically."
        ),
        "key_points": [
            "Original trial (Jenkins et al., JAMA 2003): the Portfolio diet cut "
            "LDL by ~30%, statistically similar to a statin arm's ~31% reduction, "
            "vs. only ~8% for a low-saturated-fat control diet alone.",
            "Pooled meta-analysis (Chiavaroli et al., 2018, 7 trials / 439 "
            "participants): LDL down a pooled ~17%, with high-certainty evidence "
            "for most lipid outcomes; no significant effect on HDL or body weight.",
            "Real-world adherence caveat: free-living (non-feeding-trial) "
            "adherence typically yields more modest ~5-15% LDL reductions than "
            "the ~17-30%+ seen under controlled conditions.",
            "Component targets: ~20 g/day viscous fiber, ~2 g/day plant "
            "sterols/stanols, ~50 g/day soy/plant protein, ~45 g/day nuts. No "
            "sodium or potassium target — it's a cholesterol-focused overlay, "
            "not a blood-pressure pattern.",
        ],
        "sources": [
            ("Jenkins et al., dietary portfolio vs. statin trial, JAMA 2003",
             "https://jamanetwork.com/journals/jama/fullarticle/196970"),
            ("Chiavaroli et al., Portfolio diet meta-analysis, 2018",
             "https://pubmed.ncbi.nlm.nih.gov/29807048/"),
            ("Portfolio Diet official site — components & targets",
             "https://portfoliodiet.org/"),
        ],
    },
]

# App recommendation, distilled from diet_app_recommendation.md section 5-6.
RECOMMENDATION = {
    "app": "FoodNoms",
    "tagline": "Recommended diet-tracking app — writes cleanly to Apple Health",
    "why": [
        "Writes only the nutrients each entry actually has to Apple Health — no "
        "zero-filling of absent fields, unlike several competitors.",
        "Correctly preserves food names and timestamps in HealthKit — a "
        "documented advantage over MyFitnessPal's lossy writes.",
        "Free with unlimited tracking and a free (never-paywalled) barcode "
        "scanner; optional $39.99/yr Plus tier adds AI photo logging.",
        "Every other app evaluated (MyFitnessPal, Cronometer, MacroFactor, "
        "Lose It!) had either an undocumented or a documented-lossy Apple "
        "Health write. Garmin Connect's native nutrition feature doesn't write "
        "to Apple Health at all and was disqualified outright.",
    ],
    "caveat": (
        "FoodNoms' food database is adequate but not as encyclopedic as "
        "MyFitnessPal's (14M items) or Cronometer's research-grade 84-nutrient "
        "depth — an accepted trade-off given integration cleanliness was "
        "weighted above app features."
    ),
    "integration_path": [
        "Log a meal in FoodNoms (barcode, manual entry, or AI photo) — it "
        "writes the entry's present nutrient fields to Apple Health "
        "immediately.",
        "Apple Health (HealthKit) now holds new nutrition samples under "
        "Apple's documented identifiers (DietaryEnergyConsumed, "
        "DietaryProtein, DietaryCarbohydrates, DietaryFatTotal, "
        "DietaryFatSaturated, DietaryFiber, DietarySugar, DietarySodium, "
        "DietaryPotassium).",
        "Health Auto Export (already installed, already POSTing to this "
        "dashboard's :8095 receiver every 6h) gets the nutrition metrics added "
        "to its existing REST API automation's metric selection.",
        "The existing :8095 receiver (collectors/apple_health_server.py) gets "
        "a new elif branch to sum same-day nutrition samples per metric and "
        "upsert one row per (date, source) into nutrition_daily — this is "
        "Phase 7, not implemented yet.",
    ],
    "sources": [
        ("FoodNoms: writing to Apple Health",
         "https://foodnoms.com/help/writing-to-health/"),
        ("Apple Developer: Nutrition Type Identifiers",
         "https://developer.apple.com/documentation/healthkit/nutrition-type-identifiers"),
        ("Health Auto Export: Supported Data and Metrics",
         "https://help.healthyapps.dev/en/health-auto-export/getting-started/supported-data/"),
    ],
}

# Public DASH nutrient targets (health-dashboard/research/diet_research.md,
# section 1's "Sodium / potassium / fiber / saturated-fat targets"). DASH
# itself publishes no single fiber gram target, so fiber_g_general_target uses
# the FDA Nutrition Facts Daily Value (a general public reference, not
# DASH-specific) since the memo explicitly declined to invent one.
DASH_TARGETS = {
    "sodium_mg_ceiling": 2300,
    "sodium_mg_ideal": 1500,
    "potassium_mg": 4700,
    "saturated_fat_pct_calories": 6,
    "total_fat_pct_calories": 27,
    "fiber_g_general_target": 28,
}
