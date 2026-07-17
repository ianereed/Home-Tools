# Diet-Tracking App Recommendation — Phase 5b (Cardio Tracking Project)

**Purpose:** pick a diet/food-logging app to feed the dashboard's `nutrition_daily` table. This document contains no personal targets, medication names, or clinician names — generic language only.

**Core thesis being evaluated, and the scoring rule used throughout this memo:** the cheapest, cleanest integration path is whatever app can **write** its logged nutrition data into Apple Health, because Apple Health data already flows through an existing pipe with zero new infrastructure:

```
App logs a meal → writes to Apple Health (HealthKit)
  → Health Auto Export (already installed, already POSTing every 6h + on-charge)
  → POST http://homeserver:8095/  (Tailscale, already running on the Mac mini)
  → collectors/apple_health_server.py (already parses this exact payload shape)
  → SQLite (new metric branch needed, but same file/table already exists: nutrition_daily)
```

Any app that does *not* write cleanly to Apple Health means writing and maintaining a **second, app-specific collector** (a new API client, OAuth flow, or CSV-import script) parallel to the one that already exists. That is real, ongoing engineering cost. **Per this project's explicit instructions, integration cleanliness outweighs app features when scoring these apps** — a mediocre food database with a clean, complete Apple Health write is preferred over a best-in-class food database that requires bespoke integration code.

---

## 1. Existing integration surface (read from the repo, not researched)

- `APPLE_HEALTH_AUTOMATION.md`: Health Auto Export (an iPhone app, already installed and configured) POSTs JSON to `http://homeserver:8095/` via Tailscale MagicDNS, currently configured for Heart Rate, Resting Heart Rate, Sleep Analysis, and Heart Rate Variability, on a "REST API" automation with "Format: JSON (Version 2)," synced every 6 hours plus a manual charging-time trigger.
- `collectors/apple_health_server.py`: a `ThreadingHTTPServer` on port 8095 that expects `{"data": {"metrics": [{"name": "...", "data": [{"date": "...", "qty": ...}, ...]}]}}`. It currently switches on `metric["name"]` for `heart_rate`, `resting_heart_rate`, `heart_rate_variability`, and `sleep_analysis` — nothing nutrition-related is wired up yet, but the HTTP path, JSON parsing, and DB-write pattern all already exist and work. Adding nutrition support is an `elif` branch in `_process_health_data`, not a new subsystem.
- `nutrition_daily` schema (already exists, do not rename columns): `date TEXT, calories_kcal REAL, protein_g REAL, carbs_g REAL, fat_g REAL, saturated_fat_g REAL, fiber_g REAL, sugar_g REAL, sodium_mg REAL, potassium_mg REAL, source TEXT, PRIMARY KEY (date, source)`.
- The receiver deliberately does **not** ingest weight/BP (Garmin is authoritative there) — that precedent does not apply to nutrition, since nothing else in this project currently ingests nutrition data at all.

---

## 2. Garmin Connect nutrition — reality check

**Native food logging:** Garmin announced native nutrition tracking in Garmin Connect on **January 5, 2026**, gated behind the **Connect+ subscription ($6.99/month)**. It supports barcode scanning, AI photo-based food recognition, and a global food database, and surfaces calories/macros (protein, carbs, fat) tied into Garmin's "Active Intelligence" (sleep/recovery correlation). DC Rainmaker's January 2026 review calls the shipped feature **"competent but unrefined compared to the competition,"** missing recipes with serving sizes, flexible meal scheduling, regional barcode access while traveling, and MyFitnessPal history import — and notes **no export or API access is available** for the data at all, and **no dedicated sodium/micronutrient dashboard**. [Garmin newsroom](https://www.garmin.com/en-US/newsroom/press-release/sports-fitness/stay-on-top-of-nutrition-goals-in-garmin-connect/) — [DC Rainmaker review, Jan 2026](https://www.dcrainmaker.com/2026/01/garmin-connect-nutrition-logging-connect.html)

**Critical dealbreaker:** enabling Garmin's native nutrition feature **disconnects any existing MyFitnessPal integration entirely**, per Garmin's own support docs and confirmed in the DC Rainmaker review's comments (one user reported a delayed, unreliable partial reconnection). [Garmin support: Garmin Connect + Nutrition](https://support.garmin.com/en-US/?faq=yve3hAUsxU1IEzbzo91Gt6&productID=125677&tab=topics)

**MyFitnessPal ↔ Garmin sync (legacy path, if native nutrition is left off):** still functions for most users as of 2026 — MyFitnessPal pushes calories-consumed and MFP-logged workouts to Garmin; Garmin pushes weight, Garmin-logged workouts, and step-count-derived calorie burn to MyFitnessPal. However, a February 10, 2026 Garmin forum thread reports the nutrition-sync half breaking for some accounts (activity sync kept working, nutrition sync stopped), described as account-specific rather than universal. [Garmin forums: MFP nutrition sync broken since Feb 10 2026](https://forums.garmin.com/apps-software/mobile-apps-web/f/garmin-connect-web/431532/myfitnesspal---nutrition-sync-no-longer-working-in-garmin-connect-since-10th-feb-2026) — [MyFitnessPal: Garmin Connect FAQ](https://support.myfitnesspal.com/hc/en-us/articles/360040110912-Garmin-Connect-FAQ-and-Troubleshooting)

**`garminconnect` (cyberjunky) Python library — the library this project's Garmin collectors already use:** it does now expose nutrition methods, added as a community contribution (release 0.2.39): `get_nutrition_daily_food_log(cdate)`, `get_nutrition_daily_meals(cdate)`, `get_nutrition_daily_settings(cdate)`, hitting internal endpoints under `/nutrition-service/food/logs`, `/nutrition-service/meals`, `/nutrition-service/settings`. Verified directly against the library's source:

```
self.garmin_nutrition = "/nutrition-service"
self.garmin_connect_nutrition_daily_food_logs = f"{self.garmin_nutrition}/food/logs"
self.garmin_connect_nutrition_daily_meals = f"{self.garmin_nutrition}/meals"
self.garmin_connect_nutrition_daily_settings = f"{self.garmin_nutrition}/settings"

def get_nutrition_daily_food_log(self, cdate: str) -> dict[str, Any]: ...
def get_nutrition_daily_meals(self, cdate: str) -> dict[str, Any]: ...
def get_nutrition_daily_settings(self, cdate: str) -> dict[str, Any]: ...
```
[cyberjunky/python-garminconnect, `garminconnect/__init__.py`, lines ~486–3110](https://github.com/cyberjunky/python-garminconnect/blob/master/garminconnect/__init__.py)

These are **unofficial, reverse-engineered internal endpoints** riding on the library's session-cookie authentication (the same mechanism used for every other undocumented Garmin Connect endpoint this library wraps) — not part of Garmin's official Health API, and not documented by Garmin anywhere. They would only return data if the (paywalled) native nutrition feature is actively being used, and doing so severs MyFitnessPal sync.

**Garmin's official Health API** (`developer.garmin.com`, partner program): requires business-partner approval — no self-serve individual-developer credential exists — and is architected as push-based webhooks per data type (activities, daily summaries, sleep, stress, body composition, pulse ox, respiration, HRV, epochs). **No nutrition endpoint is documented anywhere in the official Health API**, and the Garmin Connect Developer Program is reported to currently be on hold with new applications suspended. [Garmin Health API overview](https://developer.garmin.com/gc-developer-program/health-api/)

**Verdict on Garmin:** dead end for this integration. The native feature is paywalled, "unrefined," writes nowhere outside Garmin's own silo (not to Apple Health), and actively breaks the one thing (MyFitnessPal sync) that could otherwise carry nutrition data. The only programmatic path is an unofficial reverse-engineered endpoint, not a documented API — a worse integration-cleanliness story than any of the dedicated diet apps below.

---

## 3. App-by-app evaluation

### MyFitnessPal
- **Writes to Apple Health:** Yes, when "Send Data to Apple Health" is enabled. Confirmed fields: **Carbohydrates, Protein, Fat, Sodium, Sugar** (meal-summary aggregates). **Caveat, documented in MFP's own support content and corroborated by community reports:** MFP does not reliably sync food names or timestamps into HealthKit, and attempting to read complex nutrition metadata back from Apple Health into MFP can trigger deletion/corruption of manual MFP logs — guidance explicitly recommends leaving "Dietary Energy Consumed" and "Nutrition" unchecked on the *read* side to avoid this. Fiber and potassium are not confirmed as written fields anywhere in the sources reviewed — likely absent. [MyFitnessPal: Apple Health FAQ](https://support.myfitnesspal.com/hc/en-us/articles/360032271092-Apple-Health-FAQ-and-Troubleshooting)
- **API/export:** MyFitnessPal's public API has been closed to new individual developers for years and remains so — existing partners may retain access, but the stated process is "contact API@myfitnesspal.com" for a business partnership, not a self-serve key. No first-party CSV export was found in current documentation.
- **Cost:** Free tier now caps logging at ~5 entries/day and shows ads; **barcode scanning was moved behind the paywall in late 2022 and remains there**. Premium: $19.99/mo or $79.99/yr. Premium+: $24.99/mo or $99.99/yr (adds a dietitian-built meal planner).
- **Friction:** Best-in-class database breadth (14M+ items, "genuinely encyclopedic" for packaged foods), but the free tier is now closer to a demo than a usable product.

### Cronometer
- **Writes to Apple Health:** Yes, bidirectional, syncing within minutes of logging. Cronometer's own blog states it exports **"13 different nutrients"** plus body/activity metrics (weight, blood glucose, blood pressure, body fat, body temperature, height) into Apple Health, and separately calls out Dietary Cholesterol as one of the exported fields — but Cronometer's public documentation does **not itemize the full list of 13**, so exact coverage of fiber/sodium/potassium/saturated-fat individually is not confirmed to the same precision as FoodNoms. [Cronometer blog: Apple Health integration](https://cronometer.com/blog/apple-health/)
- **API/export:** No self-serve public API for individual developers found; select third-party coaching platforms (Everfit, Kalix, Practice Better) have partner-level API access. CSV export exists; per-entry timestamps in export require the paid Gold tier.
- **Cost:** Unusually generous free tier — full 84-nutrient tracking, the research-grade USDA-derived NCCDB food database (1.1M items), and a **free, never-paywalled barcode scanner**, with no daily logging cap. Gold is $49.99/yr, adding custom charts and export timestamps.
- **Friction:** the deepest micronutrient tracking of any app reviewed (84 nutrients, vitamins/minerals/amino acids/fatty-acid subtypes), but reviews describe manual search as slow (45+ seconds per meal reported), more taps per entry than competitors, and no meaningful AI photo logging. Free-tier ads are reported to get more aggressive after the first week of use.

### MacroFactor
- **Writes to Apple Health:** Yes — MacroFactor's own 2022 changelog confirms nutrition export to Apple Health and Google Fit: "View your calories, macros, and supported micros in the context of your day's timeline." However, the exact list of "supported micros" is not itemized anywhere in MacroFactor's public help docs at the level of detail FoodNoms/Cronometer provide — treat saturated fat/fiber/sodium/potassium coverage as **plausible but not confirmed** field-by-field. [MacroFactor: nutrition export to Apple Health/Google Fit](https://macrofactor.com/mm-march-2022/)
- **API/export:** no public API or first-party CSV export found; Apple Health/Health Connect appear to be the only external data paths.
- **Cost:** **No free tier and the developers state there never will be one** ("ad-free... best-in-class user experience" is the stated rationale). 7-day free trial. $11.99/mo month-to-month, $71.99/yr annual ($5.99/mo equivalent); a 2026-era bundle with MacroFactor's separate Workouts app is $89.99/yr.
- **Friction:** 1.36M+ verified, human-vetted food database (fewer duplicates/errors than crowdsourced databases), strong barcode coverage across ~9 countries, and MacroFactor's own "Food Logging Speed Index" claims ~50% fewer taps than MyFitnessPal (24 vs. 36 actions across four common workflows) via favorites, smart history, barcode/label-scan/voice/AI-photo entry methods. Known differentiator (not integration-relevant): expenditure-adjusted coaching that estimates real TDEE from logged weight/intake trends rather than a static formula.

### Lose It!
- **Writes to Apple Health:** Reported (lower-confidence sources — no first-party Lose It! documentation was found as authoritative as MFP's/FoodNoms'/Cronometer's) to sync calories, macros, and "key micronutrients" automatically once enabled. Exact field list is **not confirmed** to the same rigor as the apps above; treat this as the weakest-documented Apple Health write of the group.
- **API/export:** no public API or first-party CSV export documentation found.
- **Cost:** cheapest paid tier reviewed — Premium is $39.99/yr ($3.33/mo), or a $299.99 lifetime option ($249.99 for existing subscribers). Free tier carries ads and, as of 2026, the **barcode scanner ("Scan It") has moved to Premium-only for new signups** (grandfathered free for legacy accounts) — the same enshittification pattern MyFitnessPal went through in 2022.
- **Friction:** largest raw database size reviewed (47M items), but size alone doesn't indicate curation quality, and the app doesn't have Cronometer's or MacroFactor's documented database-vetting process.

### FoodNoms
- **Writes to Apple Health:** Yes — this is the app's defining feature. FoodNoms both reads and writes calories, macros, vitamins, minerals, water, and caffeine to HealthKit, and — per its own support docs — **"writes only the nutrients your food entries include"** (no zero-filling of absent fields, so a logged item without sodium data simply won't write a sodium sample that day). It is explicitly documented as writing **food names and timestamps correctly into HealthKit**, called out by third-party reviews as a specific, verifiable advantage over MyFitnessPal's lossy/timestamp-dropping writes. It also reads Active Energy back from Apple Health to auto-adjust calorie targets (closed loop with Apple Watch data). [FoodNoms: Writing to Health](https://foodnoms.com/help/writing-to-health/) — [FoodNoms vs. MyFitnessPal](https://foodnoms.com/vs/myfitnesspal)
- **API/export:** no cross-platform REST API or web dashboard exists — deliberately, since it's an Apple-ecosystem-native app. Automation is via Apple Shortcuts, recently (2026) rebuilt for deeper Siri/Spotlight/Apple Intelligence integration. For this project's purposes this is a non-issue: Apple Health is the intended transit layer regardless, so the absence of an independent API is not a gap.
- **Cost:** free to download with unlimited tracking, **free barcode scanning** (never paywalled), and an optional "Foodnoms+" subscription at $39.99/yr or $5.99/mo (Family sharing plan $69.99–$70/yr) that adds an AI meal-photo scanner and other extras.
- **Friction:** iOS/iPadOS/macOS/watchOS only (no Android/web — irrelevant here, this is an all-Apple pipeline already). Historically a manual-entry-plus-barcode app rather than a photo-first one, though AI photo analysis has since been added as a Plus feature. Praised specifically for privacy (does not monetize food-log data) and simplicity.

### Other alternatives considered, briefly
- **Lifesum, YAZIO:** both support Apple Health sync and multiple logging modes (photo, voice, barcode); neither surfaces public evidence of deeper or more reliable Apple Health *write* fidelity than FoodNoms/Cronometer, and neither has FoodNoms' documented nutrient-completeness/timestamp guarantee. Not pursued further — no clear integration-cleanliness edge.
- **Cal AI:** photo-recognition-first logging; **acquired by MyFitnessPal in December 2025**, then suffered a reported 3.2M-user data breach in March 2026. Given the acquisition (its long-term product direction is now MFP's to decide) and the breach, not a serious contender for this integration right now.
- **Fitia, PlateLens:** newer AI-coaching-oriented entrants; no evidence found of Apple Health write completeness exceeding the apps evaluated in depth above. Not pursued further.

---

## 4. Comparison table

| App | Writes to Apple Health (Y/N + fields) | API / export | Cost (free tier / premium) | Logging friction |
|---|---|---|---|---|
| **Garmin Connect (native nutrition)** | **N** — siloed in Garmin's own app, no HealthKit write at all | No export/API; unofficial-only via `garminconnect` lib hitting undocumented endpoints; official Health API has no nutrition endpoint | Connect+ $6.99/mo required; no free tier for this feature | Barcode + AI photo, but "unrefined"; breaks MFP sync when enabled |
| **MyFitnessPal** | Y — Carbs, Protein, Fat, Sodium, Sugar (lossy names/timestamps; fiber/potassium unconfirmed) | Public API closed to new devs since ~2018; no first-party CSV export found | Free (ads, ~5 entries/day, no barcode) / Premium $19.99/mo–$79.99/yr / Premium+ $24.99/mo–$99.99/yr | 14M+ item database, but core features increasingly paywalled |
| **Cronometer** | Y — "13 nutrients" incl. cholesterol, plus body metrics (exact full list not itemized in docs) | No self-serve public API (partner-only via coaching platforms); CSV export (timestamps need Gold) | Free (84 nutrients, free barcode, no cap, some ads) / Gold $49.99/yr | Deepest micronutrient database (84 nutrients, USDA-derived); slower manual search, no AI photo |
| **MacroFactor** | Y — "calories, macros, and supported micros" (full micro list not itemized) | None found; Apple Health/Health Connect only | No free tier ever / $71.99/yr–$11.99/mo (7-day trial) | 1.36M vetted-food database, ~50% fewer taps than MFP, barcode/label/voice/AI-photo |
| **Lose It!** | Y (reported) — calories, macros, "key micronutrients" (weakest-documented of the group) | None found | Free (ads, barcode now Premium-only for new users) / Premium $39.99/yr, lifetime $299.99 | 47M-item database (size, not confirmed curation quality) |
| **FoodNoms** | **Y — most complete and most reliable**: calories, macros, vitamins, minerals, water, caffeine; writes only present fields; correctly preserves food names + timestamps in HealthKit (documented advantage over MFP) | No REST API by design (Shortcuts-based automation instead) — irrelevant here, since Apple Health is the intended transit layer anyway | Free (unlimited tracking, free barcode) / Foodnoms+ $39.99/yr–$5.99/mo (adds AI photo scanner) | Manual entry + free barcode scanner; AI photo added in Plus tier; Apple-only (fine — this pipeline is Apple-only already) |

---

## 5. Recommendation: **FoodNoms**

Scored strictly on the stated rule — **integration cleanliness outweighs features** — FoodNoms wins clearly. It is the only app in this comparison whose Apple Health write behavior is (a) explicitly documented by the vendor at the field level, (b) confirmed to write *only* the nutrients actually present per entry (so the pipeline won't silently zero-fill or fabricate values), and (c) independently corroborated by third-party reviews as preserving food names and timestamps correctly in HealthKit — a specific, named failure mode of MyFitnessPal, the next-most-featureful option. Every other app's Apple Health write is either partially undocumented at the field level (Cronometer, MacroFactor, Lose It!) or documented as lossy/unreliable (MyFitnessPal). Garmin's native nutrition feature doesn't write to Apple Health at all and is disqualified outright.

FoodNoms' lack of a cross-platform API/web dashboard, which would normally count against an app, is a non-issue here: this project's transit layer is Apple Health → Health Auto Export → `:8095` regardless, so an independent API would be redundant infrastructure, not a benefit. Its cost (free, with an optional $39.99/yr tier for AI photo logging) and its free barcode scanner also make it the cheapest low-friction option of the group, which is a pleasant side effect of picking the integration-cleanest app rather than the goal.

**Practical trade-off to flag:** FoodNoms' food database, while adequate, is not as encyclopedic as MyFitnessPal's 14M items or Cronometer's research-grade 84-nutrient depth. Given the explicit instruction that integration cleanliness outweighs features, this is an accepted trade-off, not an oversight.

---

## 6. End-to-end integration path (for a future Phase 7 — not implemented here)

1. **User logs a meal in FoodNoms** (barcode scan, manual entry, or AI photo). FoodNoms writes the entry's present nutrient fields to HealthKit immediately (it does not batch or delay).
2. **Apple Health (HealthKit)** now holds new samples under nutrition-type identifiers such as `HKQuantityTypeIdentifierDietaryEnergyConsumed`, `HKQuantityTypeIdentifierDietaryProtein`, `HKQuantityTypeIdentifierDietaryCarbohydrates`, `HKQuantityTypeIdentifierDietaryFatTotal`, `HKQuantityTypeIdentifierDietaryFatSaturated`, `HKQuantityTypeIdentifierDietaryFiber`, `HKQuantityTypeIdentifierDietarySugar`, `HKQuantityTypeIdentifierDietarySodium`, `HKQuantityTypeIdentifierDietaryPotassium` — this is Apple's own documented identifier list (`developer.apple.com/documentation/healthkit/nutrition-type-identifiers`), the authoritative source of truth since Health Auto Export is a thin export layer over HealthKit. [Apple: Nutrition Type Identifiers](https://developer.apple.com/documentation/healthkit/nutrition-type-identifiers)
3. **Health Auto Export app config change (manual, on the phone):** open Health Auto Export → the existing REST API automation (already configured, POSTing to `http://homeserver:8095/` on the existing 6-hour + on-charge schedule) → Metrics selection screen → add these to the currently-selected set (Heart Rate, Resting Heart Rate, Sleep Analysis, HRV): **Dietary Energy, Carbohydrates, Protein, Total Fat, Saturated Fat, Fiber, Dietary Sugar (labeled "Dietary Sugar" in the app's own supported-data list, not just "Sugar" — verify this exact label in-app before flipping the toggle), Sodium, Potassium.** [HealthyApps: Supported Data and Metrics](https://help.healthyapps.dev/en/health-auto-export/getting-started/supported-data/) — [HealthyApps: Supported Data wiki](https://github.com/Lybron/health-auto-export/wiki/Supported-Data). No new purchase should be required — REST API automation is a Premium-tier feature and this project's existing setup already uses it.
4. **POST to `:8095`:** same payload envelope this project's receiver already parses: `{"data": {"metrics": [{"name": "...", "units": "...", "data": [{"date": "...", "qty": ...}]}]}}` — confirmed as Health Auto Export's "common format" for quantity-type metrics. [HealthyApps: REST API automation docs](https://help.healthyapps.dev/en/health-auto-export/automations/rest-api/)
5. **Payload field-name mapping — verified vs. inferred (be precise about which is which):**
   - **Verified, human-readable metric titles** (from Health Auto Export's own "Supported Data" documentation): Dietary Energy, Dietary Water, Dietary Sugar, Cholesterol, Carbohydrates, Protein, Total Fat, Saturated Fat, Monounsaturated Fat, Polyunsaturated Fat, Fiber, Sodium, Potassium (plus a long tail of vitamins/minerals not needed here).
   - **Inferred JSON `name` field values** (Health Auto Export's own docs do not publish the exact snake_case string per metric, and repeated attempts to find a published example were unsuccessful): based on this project's own receiver already empirically observing `"heart_rate"` for "Heart Rate," `"resting_heart_rate"` for "Resting Heart Rate," `"heart_rate_variability"` for "Heart Rate Variability," and `"sleep_analysis"` for "Sleep Analysis" (see `collectors/apple_health_server.py`), the app's naming convention is consistently snake_case-of-the-title. Applying that pattern, the nutrition names should arrive as `dietary_energy`, `carbohydrates`, `protein`, `total_fat`, `saturated_fat`, `fiber`, `dietary_sugar`, `sodium`, `potassium` — **this must be confirmed empirically** (log the raw payload once metrics are enabled, the same way the receiver already logs `[m.get("name") for m in metrics]` on every request) before wiring the parser, rather than trusting the inference blind.
   - **HealthKit identifiers (documented, authoritative fallback if the above needs cross-checking):** `HKQuantityTypeIdentifierDietaryEnergyConsumed`, `HKQuantityTypeIdentifierDietaryProtein`, `HKQuantityTypeIdentifierDietaryCarbohydrates`, `HKQuantityTypeIdentifierDietaryFatTotal`, `HKQuantityTypeIdentifierDietaryFatSaturated`, `HKQuantityTypeIdentifierDietaryFiber`, `HKQuantityTypeIdentifierDietarySugar`, `HKQuantityTypeIdentifierDietarySodium`, `HKQuantityTypeIdentifierDietaryPotassium`.
   - **Target column mapping** (`nutrition_daily`): `dietary_energy`→`calories_kcal`, `protein`→`protein_g`, `carbohydrates`→`carbs_g`, `total_fat`→`fat_g`, `saturated_fat`→`saturated_fat_g`, `fiber`→`fiber_g`, `dietary_sugar`→`sugar_g`, `sodium`→`sodium_mg`, `potassium`→`potassium_mg`; `date` from the sample's `date` field (truncated to `YYYY-MM-DD`, aggregating multiple meals/day by sum); `source` = `"apple"` (or `"foodnoms"` if the project wants to distinguish it from other Apple-sourced metrics — either is compatible with the existing composite primary key `(date, source)`).
6. **Receiver parses (future code change, not implemented here):** add an `elif name in (...)` branch to `_process_health_data()` in `collectors/apple_health_server.py`, parallel to the existing `heart_rate`/`sleep_analysis` branches, that accumulates same-day nutrition samples per metric name and writes/updates one `nutrition_daily` row per date via `INSERT ... ON CONFLICT(date, source) DO UPDATE`, mirroring the upsert pattern already used for `wellness.hrv`. Because Health Auto Export sends quantity samples (potentially several per day, one per logged meal) rather than a single daily total, the new code needs to **sum** same-day, same-metric samples before writing — unlike heart rate (which stores raw per-sample rows), `nutrition_daily` is one row per `(date, source)`.

## Sources consulted (live web research, retrieved during this session)

- [HealthyApps: Supported Data and Metrics](https://help.healthyapps.dev/en/health-auto-export/getting-started/supported-data/)
- [Health Auto Export wiki: Supported Data](https://github.com/Lybron/health-auto-export/wiki/Supported-Data)
- [HealthyApps: REST API automation](https://help.healthyapps.dev/en/health-auto-export/automations/rest-api/)
- [Apple Developer: Nutrition Type Identifiers](https://developer.apple.com/documentation/healthkit/nutrition-type-identifiers)
- [Apple Developer: dietaryEnergyConsumed](https://developer.apple.com/documentation/healthkit/hkquantitytypeidentifier/dietaryenergyconsumed)
- [cyberjunky/python-garminconnect, `garminconnect/__init__.py`](https://github.com/cyberjunky/python-garminconnect/blob/master/garminconnect/__init__.py)
- [Garmin newsroom: nutrition tracking announcement](https://www.garmin.com/en-US/newsroom/press-release/sports-fitness/stay-on-top-of-nutrition-goals-in-garmin-connect/)
- [DC Rainmaker: Garmin Connect+ nutrition logging review, Jan 2026](https://www.dcrainmaker.com/2026/01/garmin-connect-nutrition-logging-connect.html)
- [Garmin support: Garmin Connect + Nutrition FAQ](https://support.garmin.com/en-US/?faq=yve3hAUsxU1IEzbzo91Gt6&productID=125677&tab=topics)
- [Garmin forums: MFP nutrition sync broken since Feb 10 2026](https://forums.garmin.com/apps-software/mobile-apps-web/f/garmin-connect-web/431532/myfitnesspal---nutrition-sync-no-longer-working-in-garmin-connect-since-10th-feb-2026)
- [MyFitnessPal: Garmin Connect FAQ](https://support.myfitnesspal.com/hc/en-us/articles/360040110912-Garmin-Connect-FAQ-and-Troubleshooting)
- [Garmin Health API overview](https://developer.garmin.com/gc-developer-program/health-api/)
- [MyFitnessPal: Apple Health FAQ and Troubleshooting](https://support.myfitnesspal.com/hc/en-us/articles/360032271092-Apple-Health-FAQ-and-Troubleshooting)
- [Cronometer blog: Apple Health integration](https://cronometer.com/blog/apple-health/)
- [MacroFactor: nutrition export to Apple Health/Google Fit announcement](https://macrofactor.com/mm-march-2022/)
- [MacroFactor: Where is my Nutrition Synced?](https://help.macrofactorapp.com/en/articles/36-where-is-my-nutrition-synced)
- [FoodNoms: Can Foodnoms write nutrition data to Apple Health?](https://foodnoms.com/help/writing-to-health/)
- [FoodNoms vs. MyFitnessPal](https://foodnoms.com/vs/myfitnesspal)
- [FoodNoms+ pricing](https://foodnoms.com/plus)
- Assorted 2026 third-party pricing/review roundups for MyFitnessPal, Cronometer, MacroFactor, Lose It!, FoodNoms, Lifesum, YAZIO, and Cal AI (nutriscan.app, nutrola.app, fitbudd.com, amyfoodjournal.com, caloriappdirectory.com, and similar) — used only for cross-checking cost figures and friction/UX claims already corroborated by first-party sources above.

---

## Addendum (2026-07-17): Garmin nutrition payload empirically verified — recommendation revised for this deployment

The evaluation above scored apps on one axis: **Apple-Health-write cleanliness**, because
that rode the existing Health Auto Export → `:8095` receiver with zero new code. On that
axis, Garmin's verdict ("dead end") was and remains correct — it writes nothing to Apple
Health. After this memo shipped, the decision axis changed: the dashboard owner prefers a
**direct Garmin integration** (single ecosystem, no phone-side middleman) and accepts the
Connect+ subscription. The MyFitnessPal-sync breakage is irrelevant here (MFP is not used).

A **read-only, structure-only probe** (run live against Garmin Connect via the same
`python-garminconnect` auth this project's collectors already use — field *names* only,
no values retained) established what no vendor documentation states:

**`get_nutrition_daily_food_log(date)` returns, per logged food AND pre-aggregated per
meal (`mealDetails[].mealNutritionContent`), all of:** `calories`, `carbs`, `protein`,
`fat`, `fiber`, `sugar`, `addedSugars`, `saturatedFat`, `monounsaturatedFat`,
`polyunsaturatedFat`, `transFat`, `cholesterol`, **`sodium`**, **`potassium`**,
`vitaminD`, `calcium`, `iron` — plus serving unit/quantity and food provenance
(`foodMetaData`: name, brand, database source, region).

That is a **superset of every `nutrition_daily` column**, including the DASH-critical
sodium and potassium fields the comparison table above could not confirm for most apps.
Notable structural findings:

- Garmin's *daily* rollup (`dailyNutritionContent`) carries only calories/carbs/fat/
  protein — **no daily sodium rollup exists** (consistent with the DC Rainmaker review's
  "no sodium summary" complaint about the UI). The dashboard sums per-meal rollups
  itself, which is exactly what `nutrition_daily` is for.
- An empty day returns an empty-`mealDetails` shell (not an error), so the collector's
  silent-on-empty convention applies cleanly.
- Units are *presumed* label-standard (mg for sodium/potassium, g for macros) pending a
  one-time spot-check of a packaged food against its label — flagged as an open
  verification item before trend data is treated as authoritative.

**Revised recommendation for THIS deployment: Garmin Connect nutrition (Connect+),**
collected via `get_nutrition_daily_food_log` into `nutrition_daily` (`source='garmin'`),
riding the same token store, library, and collector pattern as the project's existing
BP/weight collection. Accepted trade-offs: subscription cost; unofficial
reverse-engineered endpoints (the same fragility class as every other Garmin Connect
call this project already makes); a v1 logger UX (photo-AI portions default to 100 g —
prefer barcode/manual entry).

**FoodNoms remains the documented recommendation on the original Apple-Health axis** and
the fallback path if the Garmin logger doesn't stick: the receiver-side integration path
in section 6 stays valid, unbuilt, and ready.

Additional sources for this addendum:

- [DC Rainmaker: Garmin Connect+ nutrition logging review, Jan 2026](https://www.dcrainmaker.com/2026/01/garmin-connect-nutrition-logging-connect.html)
- [cyberjunky/python-garminconnect — nutrition endpoints](https://github.com/cyberjunky/python-garminconnect)
- [sirredbeard/garmin-data-export — independent confirmation the nutrition log is pullable](https://github.com/sirredbeard/garmin-data-export)
- [Garmin support: How Do I Export Data Out of Garmin Connect?](https://support.garmin.com/en-US/?faq=W1TvTPW8JZ6LfJSfK512Q8)
