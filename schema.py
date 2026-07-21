"""
schema.py
=========
Residential Load Model — Household Parameter Schema
MSc Thesis: Multi-Objective Sizing for Hybrid Solar Systems
Author: Dan Munene Muchiri | JKUAT | ENM321-2049/2024

PURPOSE
-------
This file defines the COMPLETE and AUTHORITATIVE contract of the components
of the project:

    1. The online survey  →  must collect every field defined here
    2. The load model     →  must only use fields defined here

Nothing in the model may depend on information not in this schema.
Nothing in the survey should collect information not used here.

If you find yourself wanting to add a field mid-project,
add it here first, then update the validator, survey parser,
and model in that order.

NAIROBI-SPECIFIC ASSUMPTIONS
-----------------------------
- Single-phase 240V AC supply (standard Kenya Power residential)
- LV network: 50 Hz
- Sunrise: ~06:30, Sunset: ~18:30 (equatorial, low seasonal variation)
- Typical Nairobi elevation: ~1,795 m above sea level
- Linke turbidity for Nairobi: TL = 3.5 (used in CSI calculation)
- Grid reference: KPLC flat residential tariff
- Schema applies equally to: unmetered households, KPLC-connected households,
  and households with existing solar systems.
- Battery chemistry assumed: LiFePO4
- Currency: Kenya Shillings (KES)

CONSUMPTION TIER DEFINITIONS
----------------------------
Tier is assigned by the simulation after the Monte Carlo ensemble runs,
based on simulated median daily energy across both day types.

    low    : simulated median daily consumption < 5 kWh/day
    medium : simulated median daily consumption 5–15 kWh/day
    high   : simulated median daily consumption > 15 kWh/day

FIELD CONVENTIONS
-----------------
- All power values in Watts (W)
- All energy values in Watt-hours (Wh) or kWh where noted
- All durations in minutes
- All probabilities as floats in [0.0, 1.0]
- All hourly arrays have exactly 24 elements (index = hour 0–23)
- All minute arrays have exactly 1440 elements (index = minute 0–1439)
- Boolean fields use Python True/False
- String identifiers are lowercase with underscores

OCCUPANCY NOTE
--------------
Occupancy values represent the EXPECTED NUMBER of people home
at that hour on a typical day of that type.
Values are floats (the Markov chain samples integers around these).
They do not need to sum to anything across the day.
They must always be between 0 and n_residents inclusive.

TOU_HOURLY NOTE
---------------
tou_hourly values represent the RELATIVE LIKELIHOOD that this
appliance will be switched on during this hour.
They are NOT per-minute switch-on probabilities.
The model divides by 60 internally to convert to per-minute.
A value of 1.0 means maximum likelihood of a switch-on event
in that hour. A value of 0.0 means the appliance is NEVER
switched on in that hour.
"""

# =============================================================================
# SECTION 1: REFERENCE HOUSEHOLD
# =============================================================================
# This is a complete, realistic, validated example of a high-tier
# Nairobi household. It is used for:
#   - Testing the model before survey data arrives
#   - Verifying the validator
#   - Onboarding new developers to the schema
#   - Serving as the template for the survey parser output

REFERENCE_HOUSEHOLD = {

    # =========================================================================
    # BLOCK 1: IDENTITY AND METADATA
    # =========================================================================

    "household_id":   "H001",
    # String. Assigned by the researcher at survey intake.
    # Format: H + zero-padded integer. H001, H002, ... H999.
    # Never assigned by the respondent.


    "survey_date":    "2026-06-06",
    # String. ISO 8601 date (YYYY-MM-DD).

    "location": {
        "sub_county":  "Kasarani",
        "county":      "Nairobi",
        "country":     "Kenya",
        "latitude":    -1.2200,
        "longitude":   36.8970,
        "elevation_m": 1795
    },

    # =========================================================================
    # BLOCK 2: HOUSEHOLD COMPOSITION
    # =========================================================================

    "n_residents": 8,
    # Integer. Total number of people who live in this household.
    # Range: 1–10. Hard upper bound for all occupancy values.

    "resident_breakdown": {
        # Must sum to n_residents.
        "adults_working":     2,
        "adults_non_working": 1,
        "household_helpers":  1,
        "school_children":    2,
        "young_children":     1,
        "elderly":            1
    },

    # =========================================================================
    # BLOCK 3: OCCUPANCY SCHEDULES
    # =========================================================================
    # Two arrays of 24 values each.
    # Index i = hour i (index 0 = 00:00–00:59).
    # Value = expected number of people home during that hour.
    # The Markov chain samples integers around these expected values.

    "occupancy_weekday": [
    #   Hr:  00   01   02   03   04   05
             0,   0,   0,   0,   0,   2,
    #   Hr:  06   07   08   09   10   11
             5,   5,   4,   4,   4,   4,
    #   Hr:  12   13   14   15   16   17
             4,   4,   4,   6,   6,   7,
    #   Hr:  18   19   20   21   22   23
             8,   8,   8,   7,   6,   4
    ],
    # 00–04: Household asleep — occupancy = 0 for model purposes
    #        (fridge, router, security lights still run via needs_occupancy=False)
    # 05:    First working adult + helper up early (2); second adult still asleep
    # 06:    Working adults + school children + helper getting ready (5);
    #        young child, non-working adult, and elderly still asleep
    # 07:    School children depart, one working adult departs; remaining:
    #        1 working adult (staggered) + non-working adult + helper +
    #        young child + elderly (5)
    # 08–14: Both working adults at work, school children at school;
    #        non-working adult + helper + young child + elderly home (4)
    # 15–16: School children return (6)
    # 17:    First working adult returns (7)
    # 18–20: Full house (8)
    # 21:    Elderly to bed (7)
    # 22–23: Winding down

    "occupancy_weekend": [
    #   Hr:  00   01   02   03   04   05
             0,   0,   0,   0,   0,   0,
    #   Hr:  06   07   08   09   10   11
             0,   3,   4,   6,   8,   8,
    #   Hr:  12   13   14   15   16   17
             8,   7,   6,   6,   8,   8,
    #   Hr:  18   19   20   21   22   23
             8,   8,   8,   7,   5,   3
    ],
    # 00–06: Asleep / slow weekend morning
    # 07:    Early risers — elderly + helper + one adult (3)
    # 08–09: Household gradually wakes up
    # 10–12: Full house (8); possibly church
    # 13:    Some go out in the afternoon (7)
    # 14–15: Mix of home/out (6)
    # 16–20: Full house for evening (8)
    # 21–23: Winding down

    # =========================================================================
    # BLOCK 4a: APPLIANCE INVENTORY
    # =========================================================================
    # Each appliance is a dict with exactly the fields shown below.
    # Appliances with count = 0 are included for completeness but
    # contribute zero load. Do not omit appliances — set count = 0.
    #
    # rated_power_w SURVEY NOTE:
    #   For ALL appliances with count > 0, the surveyor must read the
    #   rated wattage from the label on the physical appliance during
    #   the survey visit. Do NOT use a literature default if the label
    #   is readable. The value here is a fallback for cases where
    #   the label is missing or illegible.
    #
    # APPLIANCE GROUPS:
    #   Group A: Always-on baseline (fridge, router, standby)
    #   Group B: Morning-peak (kettle, iron, pump, water heater)
    #   Group C: Cooking (standard tou_hourly-driven, same as other appliances)
    #   Group D: Entertainment and information
    #   Group E: Phone and device charging
    #   Group F: Laundry and cleaning
    #   Group G: Comfort (fans, AC)
    #   Group H: Outdoor and security
    #   Group I: Personal care and miscellaneous

    "appliances": [

        # ── GROUP A: ALWAYS-ON BASELINE ───────────────────────────────────

        {
            "name":                         "refrigerator",
            "category":                     "always_on",
            "count":                        1,
            "rated_power_w":                150,
            # Read from label. Typical Nairobi fridge: 100–200W running draw.
            # Compressor cycles on/off — model as repeated duty cycles.
            "tou_hourly":       [1.0]*24,
            "mean_duration_min":    25,
            "std_duration_min":     5,
            "needs_occupancy":  False,
            "standby_power_w":  0,
            "notes": (
                "Single-door or double-door household fridge. "
                "Cycles approximately 2x per hour at full duty. "
                "Actual consumption varies with ambient temperature."
            )
        },

        {
            "name":                         "chest_freezer",
            "category":                     "always_on",
            "count":                        0,
            "rated_power_w":                120,
            "tou_hourly":       [1.0]*24,
            "mean_duration_min":    30,
            "std_duration_min":     5,
            "needs_occupancy":  False,
            "standby_power_w":  0,
            "notes": "Chest freezer. Less common in households."
        },

        {
            "name":                         "wifi_router",
            "category":                     "always_on",
            "count":                        1,
            "rated_power_w":                12,
            # Read from label. Typical home router: 8–15W.
            "tou_hourly":       [1.0]*24,
            "mean_duration_min":    1440,
            "std_duration_min":     0,
            "needs_occupancy":  False,
            "standby_power_w":  12,
            "notes": (
                "Home broadband router. Runs 24/7. "
                "Some households switch off at night — if so, reduce "
                "tou_hourly for hours 23–05 to 0.1."
            )
        },

        {
            "name":                         "electric_fence_energiser",
            "category":                     "always_on",
            "count":                        1,
            "rated_power_w":                25,
            "tou_hourly":       [1.0]*24,
            "mean_duration_min":    1440,
            "std_duration_min":     0,
            "needs_occupancy":  False,
            "standby_power_w":  25,
            "notes": "Electric fence energiser. Common in gated estates."
        },

        {
            "name":                         "cctv_system",
            "category":                     "always_on",
            "count":                        1,
            "rated_power_w":                30,
            # Read from label. 4-camera system with DVR: ~25–40W total.
            "tou_hourly":       [1.0]*24,
            "mean_duration_min":    1440,
            "std_duration_min":     0,
            "needs_occupancy":  False,
            "standby_power_w":  30,
            "notes": "CCTV DVR plus cameras. Runs 24/7."
        },


        # ── GROUP B: MORNING-PEAK APPLIANCES ──────────────────────────────

        {
            "name":                         "electric_kettle",
            "category":                     "morning_peak",
            "count":                        1,
            "rated_power_w":                2000,
            # Read from label. Typical Kenyan kettle: 1800–2200W.
            "tou_hourly":       [
            #   Hr:  00    01    02    03    04    05
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
            #   Hr:  06    07    08    09    10    11
                     0.3,  0.5,  0.3,  0.1,  0.1,  0.0,
            #   Hr:  12    13    14    15    16    17
                     0.0,  0.0,  0.0,  0.0,  0.1,  0.1,
            #   Hr:  18    19    20    21    22    23
                     0.2,  0.1,  0.0,  0.0,  0.0,  0.0
            ],
            # tou_hourly represents ALL kettle uses across the day —
            # morning peak (breakfast tea/porridge water), plus smaller
            # mid-morning and evening tea use.
            "mean_duration_min":    4,
            "std_duration_min":     1,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": (
                "Electric kettle. Used for tea/coffee/porridge water at "
                "breakfast, plus smaller mid-morning and evening tea uses. "
                "All usage is captured by tou_hourly — standard mechanism."
            )
        },

        {
            "name":                         "electric_kettle_2",
            "category":                     "morning_peak",
            "count":                        0,
            "rated_power_w":                2000,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.2,  0.3,  0.1,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.1,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    4,
            "std_duration_min":     1,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": "Second kettle for large households."
        },

        {
            "name":                         "iron_box",
            "category":                     "morning_peak",
            "count":                        1,
            "rated_power_w":                1200,
            # Read from label. Typical iron: 1000–1500W.
            "tou_hourly":       [
            #   Hr:  00    01    02    03    04    05
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
            #   Hr:  06    07    08    09    10    11
                     0.4,  0.5,  0.2,  0.0,  0.0,  0.0,
            #   Hr:  12    13    14    15    16    17
                     0.0,  0.0,  0.0,  0.0,  0.1,  0.3,
            #   Hr:  18    19    20    21    22    23
                     0.2,  0.1,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    25,
            "std_duration_min":     10,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": (
                "Clothes iron. Heavy morning use on weekdays. "
                "High instantaneous load — important for peak sizing."
            )
        },

        {
            "name":                         "water_pump",
            "category":                     "morning_peak",
            "count":                        1,
            "rated_power_w":                750,
            # Read from label. Typical single-phase pump: 500–1000W.
            "tou_hourly":       [
            #   Hr:  00    01    02    03    04    05
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.1,
            #   Hr:  06    07    08    09    10    11
                     0.5,  0.6,  0.3,  0.1,  0.1,  0.0,
            #   Hr:  12    13    14    15    16    17
                     0.0,  0.0,  0.0,  0.0,  0.2,  0.3,
            #   Hr:  18    19    20    21    22    23
                     0.2,  0.1,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    15,
            "std_duration_min":     5,
            "needs_occupancy":  False,
            "standby_power_w":  0,
            "notes": (
                "Water pressure booster or tank-filling pump. "
                "Very common in Nairobi due to NCWSC supply unreliability. "
                "High startup inrush current (~3–6x rated) — critical for "
                "inverter sizing."
            )
        },

        {
            "name":                         "immersion_water_heater",
            "category":                     "morning_peak",
            "count":                        0,
            "rated_power_w":                3000,
            # Read from label. Typical 50L element: 2000–3500W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.5,
                     0.8,  0.6,  0.2,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.2,
                     0.4,  0.2,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    40,
            "std_duration_min":     10,
            "needs_occupancy":  True,
            "standby_power_w":  50,
            "notes": (
                "Electric geyser or immersion water heater. "
                "One of the largest loads when active. "
                "Set count = 1 if household has electric shower or geyser."
            )
        },

        {
            "name":                         "solar_water_heater_pump",
            "category":                     "morning_peak",
            "count":                        0,
            "rated_power_w":                50,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.5,  0.8,  1.0,  1.0,  1.0,  1.0,
                     1.0,  1.0,  1.0,  1.0,  0.8,  0.5,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    60,
            "std_duration_min":     20,
            "needs_occupancy":  False,
            "standby_power_w":  0,
            "notes": "Circulation pump for solar thermal system. Very low load."
        },

        # ── GROUP C: COOKING APPLIANCES ───────────────────────────────────
        # Cooking appliances use the SAME standard tou_hourly /
        # mean_duration_min mechanism as every other appliance.
        # rated_power_w MUST be read from the appliance label on site.

        {
            "name":                         "electric_hotplate",
            "category":                     "cooking",
            "count":                        1,
            "rated_power_w":                1500,
            # READ FROM LABEL — this value will differ per household.
            # Typical single hotplate: 1000–2000W.
            # If multi-plate cooker, record the TOTAL rated draw when
            # all plates in use, or per-plate if used independently.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.3,  0.4,  0.1,  0.0,  0.0,  0.0,
                     0.1,  0.2,  0.1,  0.0,  0.0,  0.2,
                     0.6,  0.5,  0.1,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    40,
            # Approximate full main-meal cook cycle (preheat + simmer/cycle).
            "std_duration_min":     10,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": (
                "Electric hotplate / resistance cooker. Uses the standard "
                "tou_hourly switch-on mechanism, same as any other "
                "appliance. rated_power_w MUST be read from the appliance "
                "label during the survey visit — do not use the default."
            )
        },

        {
            "name":                         "induction_cooker",
            "category":                     "cooking",
            "count":                        0,
            "rated_power_w":                2000,
            # READ FROM LABEL. Typical induction hob: 1200–2200W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.3,  0.4,  0.1,  0.0,  0.0,  0.0,
                     0.1,  0.2,  0.1,  0.0,  0.0,  0.2,
                     0.6,  0.5,  0.1,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    35,
            "std_duration_min":     10,
            "needs_occupancy":  True,
            "standby_power_w":  2,
            "notes": (
                "Induction cooker. More efficient than resistance hotplate. "
                "Uses standard tou_hourly mechanism. rated_power_w from label."
            )
        },

        {
            "name":                         "electric_pressure_cooker",
            "category":                     "cooking",
            "count":                        0,
            "rated_power_w":                800,
            # READ FROM LABEL. Typical EPC: 600–1200W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.1,
                     0.3,  0.1,  0.0,  0.0,  0.0,  0.1,
                     0.5,  0.3,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    25,
            "std_duration_min":     8,
            "needs_occupancy":  True,
            "standby_power_w":  5,
            "notes": (
                "Electric pressure cooker (EPC/Instant Pot style). "
                "Uses standard tou_hourly mechanism. rated_power_w from label."
            )
        },

        {
            "name":                         "rice_cooker",
            "category":                     "cooking",
            "count":                        0,
            "rated_power_w":                500,
            # READ FROM LABEL. Typical rice cooker: 300–700W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.1,
                     0.3,  0.1,  0.0,  0.0,  0.0,  0.1,
                     0.4,  0.2,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    30,
            "std_duration_min":     8,
            "needs_occupancy":  True,
            "standby_power_w":  5,
            "notes": (
                "Rice cooker. Uses standard tou_hourly mechanism. "
                "rated_power_w from label."
            )
        },

        {
            "name":                         "microwave",
            "category":                     "cooking",
            # Microwave is used for reheating — short, standalone events
            # not tied to a primary cooking appliance for a meal.
            # It may be present in a household that also cooks with
            # a hotplate. The two are independent.
            "count":                        1,
            "rated_power_w":                900,
            # READ FROM LABEL. Typical microwave: 700–1200W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.2,  0.2,  0.1,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            # Shifted to lunchtime (12-14h) 
            # dinner peak (18-19h). ~3x/week.
            "mean_duration_min":    5,
            "std_duration_min":     2,
            "needs_occupancy":  True,
            "standby_power_w":  3,
            "notes": (
                "Microwave. Lunchtime reheating only — avoids hotplate dinner peak. "
                "Uses standard tou_hourly mechanism."
            )
        },

        {
            "name":                         "blender",
            "category":                     "cooking",
            # Blender is a food prep tool — short, activity-linked events.
            # Not a primary cooking appliance for a meal.
            "count":                        1,
            "rated_power_w":                350,
            # READ FROM LABEL. Typical blender: 250–500W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.3,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.2,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            # ~3x/week: not a daily appliance.
            "mean_duration_min":    3,
            "std_duration_min":     1,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": "Blender. Short food-prep bursts. Not a primary cooker."
        },

        {
            "name":                         "toaster",
            "category":                     "cooking",
            "count":                        1,
            "rated_power_w":                800,
            # READ FROM LABEL.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.2,  0.1,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            # ~2x/week: occasional breakfast use, not daily.
            "mean_duration_min":    4,
            "std_duration_min":     1,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": "Pop-up toaster. Short breakfast use. Not a primary cooker."
        },

        {
            "name":                         "electric_oven",
            "category":                     "cooking",
            "count":                        1,
            "rated_power_w":                2000,
            # READ FROM LABEL. Typical electric oven: 1500–3000W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.1,  0.1,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            # ~1x/week: occasional baking only.
            "mean_duration_min":    50,
            "std_duration_min":     15,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": (
                "Electric oven. Weekend baking use dominant. "
                "One of the highest instantaneous loads — important for "
                "inverter sizing. rated_power_w MUST be read from label."
            )
        },

        # ── GROUP D: ENTERTAINMENT AND INFORMATION ─────────────────────────

        {
            "name":                         "television",
            "category":                     "entertainment",
            "count":                        1,
            "rated_power_w":                80,
            # READ FROM LABEL. LED TV 32–43 inch: 50–120W.
            "tou_hourly":       [
            #   Hr:  00    01    02    03    04    05
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
            #   Hr:  06    07    08    09    10    11
                     0.0,  0.1,  0.1,  0.1,  0.1,  0.1,
            #   Hr:  12    13    14    15    16    17
                     0.2,  0.2,  0.2,  0.2,  0.3,  0.4,
            #   Hr:  18    19    20    21    22    23
                     0.6,  0.8,  0.9,  0.9,  0.7,  0.3
            ],
            "mean_duration_min":    120,
            "std_duration_min":     40,
            "needs_occupancy":  True,
            "standby_power_w":  1,
            "notes": (
                "Primary household television. Evening peak dominant. "
                "Adjust rated_power_w to match label on actual TV."
            )
        },

        {
            "name":                         "television_2",
            "category":                     "entertainment",
            "count":                        1,
            "rated_power_w":                60,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.2,  0.4,  0.5,  0.5,  0.3,  0.1
            ],
            "mean_duration_min":    90,
            "std_duration_min":     30,
            "needs_occupancy":  True,
            "standby_power_w":  1,
            "notes": "Second/bedroom TV. Evening use only."
        },

        {
            "name":                         "dstv_decoder",
            "category":                     "entertainment",
            "count":                        1,
            "rated_power_w":                18,
            # READ FROM LABEL. DSTV active: 15–22W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.1,  0.1,  0.1,  0.1,  0.1,
                     0.2,  0.2,  0.2,  0.2,  0.3,  0.4,
                     0.6,  0.8,  0.9,  0.9,  0.7,  0.3
            ],
            "mean_duration_min":    120,
            "std_duration_min":     40,
            "needs_occupancy":  True,
            "standby_power_w":  8,
            "notes": "DSTV/Zuku decoder. Switched off at the wall when not in use — no standby draw."
        },

        {
            "name":                         "laptop",
            "category":                     "entertainment",
            "count":                        1,
            "rated_power_w":                45,
            # READ FROM LABEL (adapter brick).
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.1,  0.1,  0.1,  0.1,  0.1,
                     0.1,  0.1,  0.1,  0.1,  0.2,  0.3,
                     0.4,  0.4,  0.3,  0.2,  0.1,  0.0
            ],
            "mean_duration_min":    120,
            "std_duration_min":     60,
            "needs_occupancy":  True,
            "standby_power_w":  2,
            "notes": "Personal laptop. Evening use dominant."
        },

        {
            "name":                         "laptop_2",
            "category":                     "entertainment",
            "count":                        1,
            "rated_power_w":                45,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.1,  0.1,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.1,  0.3,
                     0.4,  0.4,  0.3,  0.2,  0.1,  0.0
            ],
            "mean_duration_min":    90,
            "std_duration_min":     45,
            "needs_occupancy":  True,
            "standby_power_w":  2,
            "notes": "Second laptop."
        },

        {
            "name":                         "desktop_computer",
            "category":                     "entertainment",
            "count":                        1,
            "rated_power_w":                150,
            # READ FROM LABEL. Desktop + monitor: 100–250W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.3,  0.3,  0.2,  0.0,  0.0,  0.0
            ],
            # ~4-5x/week: regular but not guaranteed daily.
            "mean_duration_min":    120,
            "std_duration_min":     60,
            "needs_occupancy":  True,
            "standby_power_w":  5,
            "notes": "Desktop PC with monitor. Evening use. 4-5x per week."
        },

        {
            "name":                         "gaming_console",
            "category":                     "entertainment",
            "count":                        1,
            "rated_power_w":                150,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.2,  0.2,  0.1,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            # Shifted to afternoon (15-17h) to avoid overlap with peak TV/decoder
            # hours (18-22h). ~3x/week.
            "mean_duration_min":    90,
            "std_duration_min":     45,
            "needs_occupancy":  True,
            "standby_power_w":  2,
            "notes": "Gaming console. Afternoon use before peak TV hours. 3x per week."
        },

        {
            "name":                         "bluetooth_speaker",
            "category":                     "entertainment",
            "count":                        1,
            "rated_power_w":                10,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.1,  0.2,  0.1,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.1,  0.2,
                     0.4,  0.5,  0.4,  0.2,  0.1,  0.0
            ],
            "mean_duration_min":    90,
            "std_duration_min":     40,
            "needs_occupancy":  True,
            "standby_power_w":  2,
            "notes": "Bluetooth speaker or radio. Evening/weekend use."
        },

        # ── GROUP E: PHONE AND DEVICE CHARGING ────────────────────────────

        {
            "name":                         "smartphone_charger",
            "category":                     "charging",
            "count":                        6,
            # One per phone-owning member of household. Each simulated independently.
            # 2 working adults + 1 non-working adult + 1 helper + 1 elderly + 1 school child.
            # Young child and second school child excluded. Confirm count at survey.
            "rated_power_w":                10,
            # READ FROM LABEL on charger brick. Varies: 5W–25W.
            "tou_hourly":       [
            #   Overnight charging dominant in Kenya.
            #   Hr:  00    01    02    03    04    05
                     0.9,  0.9,  0.9,  0.9,  0.8,  0.7,
            #   Hr:  06    07    08    09    10    11
                     0.4,  0.2,  0.1,  0.1,  0.1,  0.1,
            #   Hr:  12    13    14    15    16    17
                     0.1,  0.1,  0.1,  0.1,  0.1,  0.2,
            #   Hr:  18    19    20    21    22    23
                     0.3,  0.5,  0.6,  0.7,  0.8,  0.9
            ],
            "mean_duration_min":    120,
            "std_duration_min":     40,
            "needs_occupancy":  False,
            "standby_power_w":  1,
            "notes": (
                "Smartphone charger. Overnight and evening charging common. "
                "Count = 1 per phone-owning household member."
            )
        },

        {
            "name":                         "tablet_charger",
            "category":                     "charging",
            "count":                        1,
            "rated_power_w":                18,
            "tou_hourly":       [
                     0.5,  0.5,  0.5,  0.5,  0.4,  0.3,
                     0.2,  0.1,  0.1,  0.1,  0.1,  0.1,
                     0.1,  0.1,  0.1,  0.1,  0.1,  0.2,
                     0.4,  0.5,  0.6,  0.6,  0.5,  0.5
            ],
            "mean_duration_min":    150,
            "std_duration_min":     50,
            "needs_occupancy":  False,
            "standby_power_w":  2,
            "notes": "Tablet charger. Evening use and overnight charging."
        },

        {
            "name":                         "power_bank_charging",
            "category":                     "charging",
            "count":                        2,
            "rated_power_w":                10,
            "tou_hourly":       [
                     0.3,  0.3,  0.3,  0.3,  0.2,  0.1,
                     0.1,  0.1,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.1,
                     0.2,  0.3,  0.4,  0.4,  0.3,  0.3
            ],
            "mean_duration_min":    180,
            "std_duration_min":     60,
            "needs_occupancy":  False,
            "standby_power_w":  1,
            "notes": "Power bank charging. Common due to KPLC outages."
        },

        # ── GROUP F: LAUNDRY AND CLEANING ─────────────────────────────────

        {
            "name":                         "washing_machine",
            "category":                     "laundry",
            "count":                        1,
            "rated_power_w":                500,
            # READ FROM LABEL. Front-loader cold-wash: 300–500W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.1,  0.2,  0.1,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            # ~2-3x/week: laundry is not a daily activity.
            "mean_duration_min":    45,
            "std_duration_min":     10,
            "needs_occupancy":  True,
            "standby_power_w":  3,
            "notes": (
                "Automatic washing machine. Morning use dominant. "
                "Many Nairobi households hand-wash or use laundry services."
            )
        },

        {
            "name":                         "vacuum_cleaner",
            "category":                     "laundry",
            "count":                        1,
            "rated_power_w":                1000,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.2,  0.2,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            # ~2-3x/week: cleaning is not a daily activity.
            "mean_duration_min":    20,
            "std_duration_min":     8,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": "Vacuum cleaner. Morning cleaning. 2-3x per week."
        },

        # ── GROUP G: COMFORT APPLIANCES ───────────────────────────────────

        {
            "name":                         "ceiling_fan",
            "category":                     "comfort",
            "count":                        0,
            "rated_power_w":                60,
            # READ FROM LABEL. Typical ceiling fan: 40–75W at high speed.
            "tou_hourly":       [
            #   Peak use: late afternoon/evening when indoor heat builds.
            #   Hr:  00    01    02    03    04    05
                     0.3,  0.3,  0.3,  0.2,  0.1,  0.1,
            #   Hr:  06    07    08    09    10    11
                     0.1,  0.1,  0.1,  0.2,  0.3,  0.3,
            #   Hr:  12    13    14    15    16    17
                     0.3,  0.3,  0.3,  0.4,  0.4,  0.4,
            #   Hr:  18    19    20    21    22    23
                     0.5,  0.5,  0.5,  0.5,  0.4,  0.3
            ],
            "mean_duration_min":    180,
            "std_duration_min":     60,
            "needs_occupancy":  False,
            # Fans run while people sleep — left on overnight in warm weather.
            "standby_power_w":  0,
            "notes": (
                "Ceiling fan. Nairobi 18–26°C so fans are for circulation. "
                "needs_occupancy=False — fans run while people sleep. "
                "Count = number of fans in household."
            )
        },

        {
            "name":                         "standing_fan",
            "category":                     "comfort",
            "count":                        0,
            "rated_power_w":                50,
            "tou_hourly":       [
                     0.2,  0.2,  0.2,  0.1,  0.1,  0.0,
                     0.0,  0.0,  0.0,  0.1,  0.2,  0.2,
                     0.3,  0.3,  0.3,  0.4,  0.4,  0.4,
                     0.5,  0.5,  0.4,  0.4,  0.3,  0.2
            ],
            "mean_duration_min":    120,
            "std_duration_min":     60,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": "Portable standing fan."
        },

        {
            "name":                         "air_conditioner",
            "category":                     "comfort",
            "count":                        0,
            "rated_power_w":                1500,
            # READ FROM LABEL. 1-ton split unit: 1000–1800W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.1,  0.2,  0.3,
                     0.4,  0.4,  0.4,  0.3,  0.2,  0.1,
                     0.1,  0.1,  0.1,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    120,
            "std_duration_min":     60,
            "needs_occupancy":  True,
            "standby_power_w":  5,
            "notes": "Air conditioner. Very rare in medium-tier Nairobi."
        },

        # ── GROUP H: OUTDOOR AND SECURITY ─────────────────────────────────

        {
            "name":                         "gate_motor",
            "category":                     "outdoor",
            "count":                        1,
            "rated_power_w":                200,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.3,  0.5,  0.3,  0.1,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.2,  0.4,
                     0.4,  0.3,  0.1,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    1,
            "std_duration_min":     0,
            "needs_occupancy":  False,
            "standby_power_w":  10,
            "notes": "Automated gate motor. Short high-power events."
        },

        {
            "name":                         "borehole_pump",
            "category":                     "outdoor",
            "count":                        0,
            "rated_power_w":                1500,
            # READ FROM LABEL. Submersible borehole: 750–3000W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.1,
                     0.5,  0.6,  0.4,  0.2,  0.1,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.2,  0.4,
                     0.3,  0.1,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    30,
            "std_duration_min":     10,
            "needs_occupancy":  False,
            "standby_power_w":  0,
            "notes": "Borehole pump. Set count = 1 only if compound has borehole."
        },

        # ── GROUP I: PERSONAL CARE AND MISCELLANEOUS ──────────────────────

        {
            "name":                         "hair_dryer",
            "category":                     "personal_care",
            "count":                        0,
            "rated_power_w":                1500,
            # READ FROM LABEL. Typical: 1200–2000W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.4,  0.5,  0.2,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.1,
                     0.2,  0.1,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    10,
            "std_duration_min":     4,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": "Hair dryer. Morning and evening use."
        },

        {
            "name":                         "electric_shaver",
            "category":                     "personal_care",
            "count":                        1,
            "rated_power_w":                15,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.5,  0.4,  0.1,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    5,
            "std_duration_min":     2,
            "needs_occupancy":  True,
            "standby_power_w":  1,
            "notes": "Electric shaver/trimmer. Morning grooming."
        },

        {
            "name":                         "sewing_machine",
            "category":                     "other",
            "count":                        0,
            "rated_power_w":                100,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.1,  0.2,  0.2,
                     0.1,  0.1,  0.2,  0.2,  0.1,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    60,
            "std_duration_min":     30,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": "Domestic sewing machine. Daytime use."
        },

        {
            "name":                         "printer",
            "category":                     "other",
            "count":                        1,
            "rated_power_w":                15,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.1,  0.2,  0.2,  0.1,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.1,  0.2,
                     0.2,  0.1,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    5,
            "std_duration_min":     2,
            "needs_occupancy":  True,
            "standby_power_w":  5,
            "notes": "Inkjet or laser printer. Low load, occasional use."
        },

        {
            "name":                         "other_appliance_1",
            "category":                     "other",
            "count":                        0,
            "rated_power_w":                0,
            "tou_hourly":       [0.0]*24,
            "mean_duration_min":    0,
            "std_duration_min":     0,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": (
                "Placeholder for any appliance not in the standard list. "
                "Surveyor must fill in ALL fields including rated_power_w "
                "from the appliance label."
            )
        },

        {
            "name":                         "other_appliance_2",
            "category":                     "other",
            "count":                        0,
            "rated_power_w":                0,
            "tou_hourly":       [0.0]*24,
            "mean_duration_min":    0,
            "std_duration_min":     0,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": "Second catch-all placeholder."
        }

    ],  # end of appliances list

    # =========================================================================
    # BLOCK 5: LIGHTING INVENTORY
    # =========================================================================
    # Lighting is modelled separately from appliances because it depends
    # on natural light availability (via CSI from NASA POWER),
    # not just time-of-day usage patterns.
    #
    # ROOM FIELD CONVENTIONS:
    #   room            : identifier string (lowercase, underscores)
    #   count           : number of bulbs in this room/zone
    #   wattage_w       : rated power per bulb in watts
    #   bulb_type       : "LED" / "CFL" / "incandescent" / "fluorescent"
    #   tou_hourly      : 24-element array (same as appliances).
    #                     Value = probability the light is on in that hour.
    #                     0.0 = never on; 1.0 = always on.
    #                     Interior rooms (bathroom, kitchen, store) may have
    #                     non-zero daytime values — they need light regardless
    #                     of natural daylight.
    #   needs_occupancy : False for security lights — they switch on
    #                     whenever it is dark, regardless of who is home.
    #                     True for interior rooms — only on when someone
    #                     is home and using the room.
    #
    # NAIROBI DAYLIGHT NOTE:
    #   Sunrise ~06:30, Sunset ~18:30 year-round.
    #   The lighting model uses a sinusoidal daylight proxy
    #   scaled by the daily CSI value.
    #   Lights are considered necessary when effective_light < 0.3.
    #   This threshold is tunable in lighting_model.py.

    "bulbs": [

        {
            "room":             "living_room",
            "count":            2,
            "wattage_w":        9,
            "bulb_type":        "LED",
            "tou_hourly":       [
            #   Hr:  00    01    02    03    04    05
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
            #   Hr:  06    07    08    09    10    11
                     0.4,  0.3,  0.0,  0.0,  0.0,  0.0,
            #   Hr:  12    13    14    15    16    17
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
            #   Hr:  18    19    20    21    22    23
                     0.9,  0.9,  0.9,  0.9,  0.9,  0.0
            ],
            "needs_occupancy":  True,
            "notes": "Main living room / lounge. Two ceiling LED bulbs. Morning gathering (06-07h) and evening (18-22h)."
        },

        {
            "room":             "dining_area",
            "count":            1,
            "wattage_w":        9,
            "bulb_type":        "LED",
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.7,  0.7,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.8,  0.8,  0.8,  0.0,  0.0,  0.0
            ],
            "needs_occupancy":  True,
            "notes": "Dining area. Morning breakfast (06-07h) and evening dinner (18-20h)."
        },

        {
            "room":             "master_bedroom",
            "count":            2,
            "wattage_w":        9,
            "bulb_type":        "LED",
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.7,
                     0.6,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.85, 0.85, 0.0
            ],
            "needs_occupancy":  True,
            "notes": "Master bedroom. Morning wakeup (05-06h) and late evening before sleep (21-22h)."
        },

        {
            "room":             "children_bedroom",
            "count":            1,
            "wattage_w":        9,
            "bulb_type":        "LED",
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.7,  0.6,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.8,  0.8,  0.0,  0.0,  0.0
            ],
            "needs_occupancy":  True,
            "notes": "Children's bedroom. Morning wakeup for school (06-07h) and early evening before sleep (19-20h)."
        },

        {
            "room":             "bedroom_3",
            "count":            1,
            "wattage_w":        9,
            "bulb_type":        "LED",
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.7,
                     0.6,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.85, 0.85, 0.0
            ],
            "needs_occupancy":  True,
            "notes": "Third bedroom. Household_helpers. Morning wakeup (05-06h) and late evening before sleep (21-22h)"
        },
        {
            "room":             "bedroom_4",
            "count":            1,
            "wattage_w":        9,
            "bulb_type":        "LED",
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.6,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.85, 0.85, 0.0
            ],
            # Non-working adult — wakes later (07h), sleeps late (21-22h).
            "needs_occupancy":  True,
            "notes": "Non-working adult bedroom. Later morning wakeup (07h) and late evening (21-22h)."
        },
        {
            "room":             "bedroom_5",
            "count":            1,
            "wattage_w":        9,
            "bulb_type":        "LED",
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.6,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.8,  0.8,  0.0,  0.0,  0.0
            ],
            # Elderly — wakes at 06h (after working adults), early to bed (19-20h).
            "needs_occupancy":  True,
            "notes": "Elderly bedroom. Early morning wakeup (05-06h) and early evening sleep (19-20h)."
        },

        {
            "room":             "kitchen",
            "count":            1,
            "wattage_w":        9,
            "bulb_type":        "LED",
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.85, 0.85, 0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.85,
                     0.85, 0.85, 0.85, 0.0,  0.0,  0.0
            ],
            "needs_occupancy":  True,
            "notes": "Kitchen. Morning prep (06-07h) and evening dinner prep (17-20h)."
        },

        {
            "room":             "bathroom",
            "count":            1,
            "wattage_w":        9,
            "bulb_type":        "LED",
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.8,
                     0.8,  0.8,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.75, 0.75, 0.75, 0.75, 0.0,  0.0
            ],
            "needs_occupancy":  True,
            "notes": "Bathroom. Morning rush (05-07h) and evening (18-21h) only."
        },

        {
            "room":             "bathroom_2",
            "count":            1,
            "wattage_w":        9,
            "bulb_type":        "LED",
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.8,
                     0.8,  0.8,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.75, 0.75, 0.75, 0.75, 0.0,  0.0
            ],
            "needs_occupancy":  True,
            "notes": "Bathroom. Morning rush (05-07h) and evening (18-21h) only."
        },

        {
            "room":             "outside_security",
            "count":            2,
            "wattage_w":        50,
            "bulb_type":        "LED",
            "tou_hourly":       [
                     1.0,  1.0,  1.0,  1.0,  1.0,  1.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     1.0,  1.0,  1.0,  1.0,  1.0,  1.0
            ],
            # Dusk-to-dawn: 18:00–05:59.
            "needs_occupancy":  False,
            "notes": ("Runs dusk to dawn regardless of occupancy."
            )
        },

        {
            "room":             "staircase_corridor",
            "count":            1,
            "wattage_w":        9,
            "bulb_type":        "LED",
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.6,  0.6,  0.6,  0.6,  0.6,  0.0
            ],
            "needs_occupancy":  True,
            "notes": "Staircase or corridor light. Evening use only (18-22h)."
        },

        {
            "room":             "store_room",
            "count":            1,
            "wattage_w":        9,
            "bulb_type":        "LED",
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.1,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.1,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            # Brief access: morning fetch (07h) and early evening return (16h).
            "needs_occupancy":  True,
            "notes": "Store room or utility room. Brief access morning and early evening."
        }

    ],  # end of bulbs list

    # =========================================================================
    # BLOCK 6: GRID AND TARIFF INFORMATION
    # =========================================================================

    # This block is used by Objective 3 (MILP optimizer) to determine
    # grid import/export costs and the financial objective.
    # It is collected at survey time since it varies by household.

    "grid": {

        "connected":        True,
        # Boolean. Is the household currently connected to KPLC grid?
        # True  = grid-connected (grid-tied or hybrid)
        # False = off-grid only (no KPLC meter at all)
        # Note: a household with existing solar may still be grid-connected.
        # connected = True means a physical KPLC connection exists,
        # regardless of whether solar already covers most consumption.

        "phase":            "single",
        # "single" or "three".
        # Nearly all Nairobi residential connections are single-phase.

        "tariff_type":      "domestic",
        # Relevant only when grid.connected = True.
        # KPLC tariff categories:
        # "lifeline"  : 0–50 kWh/month (heavily subsidised)
        # "domestic"  : standard residential
        # "none"      : household has solar only, no KPLC connection
        # "commercial": not applicable here
        # Set to "none" if household is off-grid solar only.

        "import_tariff_kes_per_kwh": 25.0,
        # Cost of importing 1 kWh from the grid (KES/kWh).
        # Relevant only when grid.connected = True.
        # Current KPLC domestic tariff: approximately KES 25/kWh
        # including all levies and fuel cost adjustment (2025).
        # Verify at survey time — tariffs change periodically.
        # Set to 0.0 if grid.connected = False (no import cost).

        "export_tariff_kes_per_kwh": 0.0,
        # Kenya does not currently have a residential feed-in tariff.
        # Set to 0. Update if net-metering policy changes.

        "monthly_fixed_charge_kes": 150.0,
        # KPLC fixed meter charge per month.

        "supply_reliability": "fair",
        # Qualitative assessment from household: "good" / "fair" / "poor"
        # "poor" → frequent blackouts, high autonomy motivation
        # "good" → reliable, autonomy is less critical

        "avg_blackout_hours_per_week": 4.0,
        # Household's estimate of how many hours per week
        # the grid is unavailable.
        # Used qualitatively to justify autonomy objective.

        "monthly_bill_kes": 3500,
        # Average monthly KPLC electricity bill in KES.
        # Informational only — NOT used to classify tier.
        # For solar-only households, set to 0.
        # For hybrid households, this is the residual grid bill
        # after solar offset. Will be low even for high-consumption
        # households that already have substantial solar.
        # Cross-check only: if this is high and estimated consumption
        # is low, investigate the appliance survey responses.

        "metering_type": "prepaid",
        # "postpaid" or "prepaid" (token meter).
        # Most Nairobi residential connections are one or the other.

        "existing_backup": "solar_battery",
        # Any existing power system the household currently has.
        # This schema is used for BOTH unmetered households AND
        # households with existing solar that want to resize or upgrade.
        # Options:
        #   None                 : no backup, grid-only
        #   "generator"          : petrol/diesel generator
        #   "inverter_battery"   : battery inverter only (no PV)
        #   "solar_only"         : grid-tied PV, no battery
        #   "solar_battery"      : existing hybrid PV+battery system
        # Used to understand current situation and design context.

        "existing_pv_kw": 11.0,
        # Installed PV panel capacity in kW-peak. READ FROM 
        # installation certificate. Set to 0.0 if no existing PV.

        "existing_battery_kwh": 10.0,
        # Installed battery capacity in kWh. READ FROM BATTERY LABEL.
        # Set to 0.0 if no existing battery.

        "existing_backup_capacity_kw": 10.0
        # Inverter rated output in kW. READ FROM INVERTER LABEL.
        # Set to 0.0 if no inverter/generator.
    },

    # =========================================================================
    # BLOCK 7: PHYSICAL SITE INFORMATION
    # =========================================================================

    # Used by Objective 2 (solar forecast) and Objective 3 (PV model).

    "site": {

        "roof_area_sqm": 60.0,
        # Available roof area for PV panels in square metres.
        # Practical limit on PPV even if optimizer wants more.
        # A 1kW PV array requires approximately 6–8 m² (depending on panel).
        # 40m² → practical maximum ~5–6 kWp.

        "roof_orientation": "north_facing",
        # Nairobi is south of the equator → north-facing is optimal.
        # Options: "north_facing" (optimal for Kenya), "south_facing",
        #          "east_facing", "west_facing", "flat"

        "roof_tilt_degrees": 15,
        # Roof pitch angle from horizontal in degrees.
        # Typical Nairobi residential: 10–20 degrees.
        # Flat roof: 0–5 degrees.

        "shading": "minimal",
        # Qualitative: "none" / "minimal" / "moderate" / "severe"
        # Captures shading from trees, neighbouring buildings.

        "roof_type": "clay_tile",
        # Material: "iron_sheet" / "concrete" / "clay_tile" / "other"
        # Affects mounting method and structural considerations.

        "mounting_type": "flush",
        # "flush"    : panels laid close to roof surface
        # "elevated" : panels on racking with air gap (better cooling)
        # "ground"   : ground-mounted (uncommon residential)

        "panel_derating_factor": 0.85,
        # Combined system efficiency factor accounting for:
        # wiring losses (~2%), inverter efficiency (~96%),
        # soiling (~3%), mismatch (~2%), temperature derating.
        # Typical residential system: 0.80–0.90.
        # Used in Objective 3 PV output model.

        "cable_length_m": 15.0
        # Approximate DC cable run from PV array to inverter in metres.
        # Used for cable sizing recommendation (not in MILP directly).
    },

    # =========================================================================
    # BLOCK 8: COMPONENT COST DATA
    # =========================================================================

    # Used directly in Objective 3 MILP cost objective (f1).
    # All costs in KES, mid-2026 Nairobi market prices.
    # Update at survey time if prices have changed significantly.
    # Optimizer selects model and count from each catalog list.

    "costs": {

        # ── PV Panels ─────────────────────────────────────────────────────────
        # Price per panel (supply + mounting hardware). Sorted by wattage asc.
        # Optimizer picks one model and an integer panel count.
        "pv_panels": [
            {"model": "LONGi_Hi-MO6_405W",         "wattage_w": 405, "price_kes": 12000},
            {"model": "JA_Solar_JAM54S31_410W",     "wattage_w": 410, "price_kes": 12500},
            {"model": "Canadian_Solar_CS3L_420W",   "wattage_w": 420, "price_kes": 12500},
            {"model": "Jinko_Tiger_Pro_450W",        "wattage_w": 450, "price_kes": 13500},
            {"model": "LONGi_Hi-MO6_540W",          "wattage_w": 540, "price_kes": 15500},
            {"model": "JA_Solar_JAM72S30_545W",     "wattage_w": 545, "price_kes": 15500},
            {"model": "Canadian_Solar_CS6W_550W",   "wattage_w": 550, "price_kes": 15000},
            {"model": "Jinko_Tiger_Neo_580W",        "wattage_w": 580, "price_kes": 16500},
            {"model": "LONGi_Hi-MO_X6_610W",        "wattage_w": 610, "price_kes": 18500},
            {"model": "JA_Solar_JAM72S30_620W",     "wattage_w": 620, "price_kes": 18000},
            {"model": "LONGi_Hi-MO_X6_640W",        "wattage_w": 640, "price_kes": 20000},
        ],

        # ── Batteries ─────────────────────────────────────────────────────────
        # LiFePO4 units (supply + installation). Sorted by capacity asc.
        # Optimizer picks one model; units stack in parallel to reach target kWh.
        "batteries": [
            {"model": "Pylontech_US2000C",       "capacity_kwh":  2.40, "price_kes":  85000},
            {"model": "Pylontech_US3000C",       "capacity_kwh":  3.50, "price_kes": 120000},
            {"model": "SRNE_BSLBLP48100",        "capacity_kwh":  4.80, "price_kes": 130000},
            {"model": "Pylontech_US5000",        "capacity_kwh":  4.80, "price_kes": 155000},
            {"model": "SRNE_HES5K-B",            "capacity_kwh":  5.00, "price_kes": 140000},
            {"model": "Deye_BOS-GM5.1",          "capacity_kwh":  5.12, "price_kes": 148000},
            {"model": "Must_PV18-5048_EX_5kWh",  "capacity_kwh":  5.00, "price_kes": 138000},
            {"model": "Solax_T-BAT_H5.8",        "capacity_kwh":  5.80, "price_kes": 195000},
            {"model": "SRNE_HES10K-B",           "capacity_kwh": 10.00, "price_kes": 270000},
            {"model": "Deye_BOS-GM10.2",         "capacity_kwh": 10.24, "price_kes": 285000},
            {"model": "Must_PV18-5048_EX_10kWh", "capacity_kwh": 10.00, "price_kes": 265000},
            {"model": "SRNE_HES15K-B",           "capacity_kwh": 15.00, "price_kes": 390000},
            {"model": "Deye_BOS-GM15.4",         "capacity_kwh": 15.36, "price_kes": 410000},
            {"model": "Must_PV18-5048_EX_15kWh", "capacity_kwh": 15.00, "price_kes": 380000},
            {"model": "SRNE_HES20K-B",           "capacity_kwh": 20.00, "price_kes": 510000},
            {"model": "Deye_BOS-GM20.5",         "capacity_kwh": 20.48, "price_kes": 530000},
            {"model": "Must_PV18-5048_EX_20kWh", "capacity_kwh": 20.00, "price_kes": 500000},
            {"model": "SRNE_HES25K-B",           "capacity_kwh": 25.00, "price_kes": 630000},
            {"model": "Deye_BOS-GM25.6",         "capacity_kwh": 25.60, "price_kes": 655000},
            {"model": "Must_PV18-5048_EX_25kWh", "capacity_kwh": 25.00, "price_kes": 618000},
            {"model": "SRNE_HES30K-B",           "capacity_kwh": 30.00, "price_kes": 750000},
            {"model": "Deye_BOS-GM30.7",         "capacity_kwh": 30.72, "price_kes": 780000},
            {"model": "Must_PV18-5048_EX_30kWh", "capacity_kwh": 30.00, "price_kes": 735000},
        ],

        # ── Inverters ─────────────────────────────────────────────────────────
        # Hybrid inverters (supply + installation). Sorted by rated kW asc.
        # Optimizer picks one model sized to cover peak load.
        "inverters": [
            {"model": "Must_PH1800_PLUS_3K",   "rated_kw":  3, "price_kes":  58000},
            {"model": "SRNE_HF2430U60-100_3K", "rated_kw":  3, "price_kes":  62000},
            {"model": "Deye_SUN-3K-SG04LP3",   "rated_kw":  3, "price_kes":  70000},
            {"model": "Must_PH1800_PLUS_5K",   "rated_kw":  5, "price_kes":  82000},
            {"model": "SRNE_HF2430U60-100_5K", "rated_kw":  5, "price_kes":  88000},
            {"model": "Deye_SUN-5K-SG04LP3",   "rated_kw":  5, "price_kes":  95000},
            {"model": "Must_EP3000_PRO_8K",    "rated_kw":  8, "price_kes": 118000},
            {"model": "SRNE_HF2430U60-100_8K", "rated_kw":  8, "price_kes": 125000},
            {"model": "Deye_SUN-8K-SG04LP3",   "rated_kw":  8, "price_kes": 135000},
            {"model": "Must_EP3000_PRO_10K",   "rated_kw": 10, "price_kes": 148000},
            {"model": "SRNE_ML2448_10K",        "rated_kw": 10, "price_kes": 155000},
            {"model": "Deye_SUN-10K-SG04LP3",  "rated_kw": 10, "price_kes": 165000},
            {"model": "Must_EP3000_PRO_12K",   "rated_kw": 12, "price_kes": 175000},
            {"model": "SRNE_ML2448_12K",        "rated_kw": 12, "price_kes": 182000},
            {"model": "Deye_SUN-12K-SG04LP3",  "rated_kw": 12, "price_kes": 195000},
            {"model": "Must_EP3000_PRO_15K",   "rated_kw": 15, "price_kes": 215000},
            {"model": "SRNE_ML2448_15K",        "rated_kw": 15, "price_kes": 225000},
            {"model": "Deye_SUN-15K-SG04LP3",  "rated_kw": 15, "price_kes": 235000},
            {"model": "Must_EP3000_PRO_20K",   "rated_kw": 20, "price_kes": 275000},
            {"model": "SRNE_ML2448_20K",        "rated_kw": 20, "price_kes": 285000},
            {"model": "Deye_SUN-20K-SG04LP3",  "rated_kw": 20, "price_kes": 300000},
            {"model": "Must_EP3000_PRO_25K",   "rated_kw": 25, "price_kes": 335000},
            {"model": "SRNE_ML2448_25K",        "rated_kw": 25, "price_kes": 348000},
            {"model": "Deye_SUN-25K-SG04LP3",  "rated_kw": 25, "price_kes": 365000},
            {"model": "Must_EP3000_PRO_30K",   "rated_kw": 30, "price_kes": 395000},
            {"model": "SRNE_ML2448_30K",        "rated_kw": 30, "price_kes": 410000},
            {"model": "Deye_SUN-30K-SG04LP3",  "rated_kw": 30, "price_kes": 430000},
        ],

        # ── Balance of System ─────────────────────────────────────────────────
        # Wiring, breakers, mounting rails, earthing, labour, commissioning.
        # Tiered by system size; pick the bracket that covers the inverter kW.
        "bos": [
            {"system_size_kw_max":  5, "price_kes":  35000},
            {"system_size_kw_max": 10, "price_kes":  55000},
            {"system_size_kw_max": 15, "price_kes":  75000},
            {"system_size_kw_max": 20, "price_kes":  95000},
            {"system_size_kw_max": 25, "price_kes": 115000},
            {"system_size_kw_max": 30, "price_kes": 135000},
        ],

        # ── Financial Parameters ───────────────────────────────────────────────
        "om_kes_per_year":              2000,
        # Annual O&M: panel cleaning, inspection, minor repairs.

        "battery_replacement_years":    10,
        # LiFePO4 at 80% DOD: 2000–3000 cycles → ~8–10 years.

        "pv_lifetime_years":            25,
        "inverter_lifetime_years":      10,

        "discount_rate":                0.12,
        # Annual discount rate for NPC calculations — converts future costs to
        # today's money: PV = cost / (1 + r)^year. Set to the cost of borrowing
        # in Kenya (10–14%) so the solar investment is compared fairly against
        # what that money would cost if financed by a loan. A higher rate
        # discounts long-term savings more, favouring smaller systems.

        "electricity_price_escalation": 0.05
        # KPLC tariffs historically rising ~5% per year.
    },


    # =========================================================================
    # BLOCK 9: MODEL CONTROL PARAMETERS
    # =========================================================================
    # These control how the load model runs for this household.
    # They are not survey inputs — the researcher sets them.

    "model_parameters": {

        "n_monte_carlo_runs":    1000,
        # Number of Monte Carlo simulations per day type.
        # 1000 is the minimum for stable statistics.
        # 2000 for final thesis results.

        "timestep_minutes":      1,
        # Simulation resolution in minutes.
        # 1 minute = 1440 steps per day.
        # Do not change — model is built for 1-minute resolution.

        "random_seed":           None,
        # Set to an integer for reproducible results during debugging.
        # Set to None for production runs (true randomness). None means each run is truly random, which is what you want for production.

        "markov_n_max":          None,
        # Maximum occupancy state for Markov chain.
        # If None, defaults to n_residents.
        # Set explicitly if you want to allow occasional
        # guest occupancy above n_residents.

        "daylight_threshold_csi": 0.3,
        # CSI value below which artificial lighting is considered
        # necessary regardless of clock time.
        # 0.3 ≈ overcast conditions.
        # Tunable during model validation.

        "nairobi_sunrise_hour":  6.5,
        # Fractional hour of approximate sunrise (06:30).
        # Used in daylight proxy calculation.

        "nairobi_sunset_hour":   18.5,
        # Fractional hour of approximate sunset (18:30).

        "csi_source":            "nasa_power",
        # Source of CSI values for lighting model sampling.
        # "nasa_power" → sample from processed historical CSI array.
        # "fixed"      → use a fixed CSI value (for debugging).

        "csi_fixed_value":       0.7,
        # Used only if csi_source = "fixed". Ignored otherwise.

        "appliance_occupancy_scale_zero": 0.02,
        # When no one is home (occupancy = 0) and needs_occupancy = True,
        # scale switch-on probability by this factor.
        # 0.02 = 2% of normal probability (appliance almost never runs).
        # Captures rare events like appliances accidentally left on.

        "duration_clip_min_minutes": 1
        # Minimum duration for any appliance use event.
        # Prevents zero or negative duration samples.
    }

}  # end of REFERENCE_HOUSEHOLD


# =============================================================================
# SECTION 2: VALIDATOR
# =============================================================================

def validate_household(h):
    """
    Validate a household parameter dict against all schema constraints.

    Returns True if all checks pass.
    Raises ValueError with a descriptive message listing ALL failures.
    """
    errors = []

    # ── Block 1: Identity ────────────────────────────────────────────────────

    if not isinstance(h.get("household_id"), str) or \
       not h.get("household_id"):
        errors.append("household_id must be a non-empty string")

    # ── Block 2: Household composition ───────────────────────────────────────

    n = h.get("n_residents")
    if not isinstance(n, int) or not (1 <= n <= 15):
        errors.append(
            f"n_residents must be an integer between 1 and 15, got: {n}"
        )
    else:
        rb = h.get("resident_breakdown", {})
        rb_sum = sum([
            rb.get("adults_working", 0),
            rb.get("adults_non_working", 0),
            rb.get("household_helpers", 0),
            rb.get("school_children", 0),
            rb.get("young_children", 0),
            rb.get("elderly", 0)
        ])
        if rb_sum != n:
            errors.append(
                f"resident_breakdown values sum to {rb_sum} "
                f"but n_residents = {n}. They must match."
            )
        for key, val in rb.items():
            if not isinstance(val, int) or val < 0:
                errors.append(
                    f"resident_breakdown['{key}'] must be a "
                    f"non-negative integer, got: {val}"
                )

    # ── Block 3: Occupancy ───────────────────────────────────────────────────

    for day_type in ["weekday", "weekend"]:
        key = f"occupancy_{day_type}"
        occ = h.get(key, [])
        if len(occ) != 24:
            errors.append(
                f"{key} must have exactly 24 values, got {len(occ)}"
            )
        else:
            for i, v in enumerate(occ):
                if not isinstance(v, (int, float)):
                    errors.append(
                        f"{key}[{i}] must be numeric, got: {type(v)}"
                    )
                elif v < 0:
                    errors.append(
                        f"{key}[{i}] = {v} is negative."
                    )
                elif isinstance(n, int) and v > n:
                    errors.append(
                        f"{key}[{i}] = {v} exceeds n_residents = {n}."
                    )

    # ── Block 4a: Appliances ──────────────────────────────────────────────────

    appliances = h.get("appliances", [])
    if not isinstance(appliances, list):
        errors.append("appliances must be a list")
    else:
        seen_names = []
        for idx, appl in enumerate(appliances):
            prefix = f"appliances[{idx}] ('{appl.get('name', '?')}')"

            name = appl.get("name")
            if not isinstance(name, str) or not name:
                errors.append(f"{prefix}: name must be a non-empty string")
            elif name in seen_names:
                errors.append(
                    f"{prefix}: duplicate appliance name '{name}'."
                )
            else:
                seen_names.append(name)

            count = appl.get("count")
            if not isinstance(count, int) or count < 0:
                errors.append(
                    f"{prefix}: count must be a non-negative integer, "
                    f"got: {count}"
                )

            pwr = appl.get("rated_power_w")
            if count and count > 0:
                if not isinstance(pwr, (int, float)) or pwr <= 0:
                    errors.append(
                        f"{prefix}: rated_power_w must be > 0 "
                        f"when count > 0, got: {pwr}"
                    )
                if pwr and pwr > 10000:
                    errors.append(
                        f"{prefix}: rated_power_w = {pwr}W seems "
                        f"unrealistically high (> 10 kW). Check units."
                    )

            tou = appl.get("tou_hourly", [])
            if len(tou) != 24:
                errors.append(
                    f"{prefix}: tou_hourly must have 24 values, got {len(tou)}"
                )
            else:
                for i, v in enumerate(tou):
                    if not isinstance(v, (int, float)):
                        errors.append(
                            f"{prefix}: tou_hourly[{i}] must be numeric"
                        )
                    elif not (0.0 <= v <= 1.0):
                        errors.append(
                            f"{prefix}: tou_hourly[{i}] = {v} outside [0,1]"
                        )

            mean_d = appl.get("mean_duration_min")
            std_d  = appl.get("std_duration_min")

            if not isinstance(mean_d, (int, float)) or mean_d < 1:
                if count and count > 0:
                    errors.append(
                        f"{prefix}: mean_duration_min must be ≥ 1 "
                        f"when count > 0, got: {mean_d}"
                    )
            if not isinstance(std_d, (int, float)) or std_d < 0:
                errors.append(
                    f"{prefix}: std_duration_min must be ≥ 0, got: {std_d}"
                )
            if isinstance(mean_d, (int, float)) and \
               isinstance(std_d, (int, float)) and \
               mean_d > 0 and std_d > mean_d / 2:
                errors.append(
                    f"{prefix}: std_duration_min ({std_d}) exceeds "
                    f"mean_duration_min / 2 ({mean_d / 2:.1f}). "
                    f"Risk of negative duration samples."
                )

            if not isinstance(appl.get("needs_occupancy"), bool):
                errors.append(
                    f"{prefix}: needs_occupancy must be True or False"
                )

    # ── Block 5: Lighting ────────────────────────────────────────────────────

    bulbs = h.get("bulbs", [])
    if not isinstance(bulbs, list):
        errors.append("bulbs must be a list")
    else:
        for idx, b in enumerate(bulbs):
            prefix = f"bulbs[{idx}] ('{b.get('room', '?')}')"

            if not isinstance(b.get("room"), str) or not b.get("room"):
                errors.append(f"{prefix}: room must be a non-empty string")

            count = b.get("count")
            if not isinstance(count, int) or count < 0:
                errors.append(
                    f"{prefix}: count must be a non-negative integer, "
                    f"got: {count}"
                )

            watt = b.get("wattage_w")
            if count and count > 0:
                if not isinstance(watt, (int, float)) or watt <= 0:
                    errors.append(
                        f"{prefix}: wattage_w must be > 0 when count > 0"
                    )
                if watt and watt > 200:
                    errors.append(
                        f"{prefix}: wattage_w = {watt}W is very high "
                        f"for a single bulb. Check units."
                    )

            if b.get("bulb_type") not in [
                "LED", "CFL", "incandescent", "fluorescent", None
            ]:
                errors.append(
                    f"{prefix}: bulb_type must be 'LED', 'CFL', "
                    f"'incandescent', or 'fluorescent'"
                )

            tou = b.get("tou_hourly", [])
            if len(tou) != 24:
                errors.append(
                    f"{prefix}: tou_hourly must have 24 values, got {len(tou)}"
                )
            else:
                for i, v in enumerate(tou):
                    if not isinstance(v, (int, float)) or \
                       not (0.0 <= v <= 1.0):
                        errors.append(
                            f"{prefix}: tou_hourly[{i}] = {v} must be "
                            f"float in [0,1]"
                        )

            if not isinstance(b.get("needs_occupancy"), bool):
                errors.append(
                    f"{prefix}: needs_occupancy must be True or False"
                )

    # ── Block 6: Grid ────────────────────────────────────────────────────────

    grid = h.get("grid", {})

    if not isinstance(grid.get("connected"), bool):
        errors.append("grid.connected must be True or False")

    if grid.get("phase") not in ["single", "three"]:
        errors.append("grid.phase must be 'single' or 'three'")

    if not isinstance(grid.get("import_tariff_kes_per_kwh"),
                       (int, float)) or \
       grid.get("import_tariff_kes_per_kwh", -1) < 0:
        errors.append(
            "grid.import_tariff_kes_per_kwh must be a non-negative number"
        )

    if not isinstance(grid.get("monthly_bill_kes"),
                       (int, float)) or \
       grid.get("monthly_bill_kes", -1) < 0:
        errors.append(
            "grid.monthly_bill_kes must be a non-negative number"
        )

    for solar_field in ["existing_pv_kw", "existing_battery_kwh",
                         "existing_backup_capacity_kw"]:
        val = grid.get(solar_field)
        if not isinstance(val, (int, float)) or val < 0:
            errors.append(
                f"grid.{solar_field} must be a non-negative number, "
                f"got: {val}"
            )

    valid_backup_options = [
        None, "generator", "inverter_battery",
        "solar_only", "solar_battery"
    ]
    if grid.get("existing_backup") not in valid_backup_options:
        errors.append(
            f"grid.existing_backup must be one of {valid_backup_options}, "
            f"got: '{grid.get('existing_backup')}'"
        )

    # ── Block 7: Site ─────────────────────────────────────────────────────────

    site = h.get("site", {})

    if not isinstance(site.get("roof_area_sqm"),
                       (int, float)) or \
       site.get("roof_area_sqm", -1) <= 0:
        errors.append("site.roof_area_sqm must be a positive number")

    if not isinstance(site.get("panel_derating_factor"),
                       (int, float)) or \
       not (0.5 <= site.get("panel_derating_factor", 0) <= 1.0):
        errors.append(
            "site.panel_derating_factor must be between 0.5 and 1.0"
        )

    # ── Block 8: Costs ────────────────────────────────────────────────────────

    costs = h.get("costs", {})

    def _check_catalog(catalog, name, required_fields):
        if not isinstance(catalog, list) or len(catalog) == 0:
            errors.append(f"costs.{name} must be a non-empty list")
            return
        for i, entry in enumerate(catalog):
            for field, kind in required_fields.items():
                val = entry.get(field)
                if kind == "str":
                    if not isinstance(val, str) or not val:
                        errors.append(f"costs.{name}[{i}].{field} must be a non-empty string")
                else:
                    if not isinstance(val, (int, float)) or val <= 0:
                        errors.append(f"costs.{name}[{i}].{field} must be a positive number, got: {val}")

    _check_catalog(costs.get("pv_panels"),  "pv_panels",  {"model": "str", "wattage_w": "num", "price_kes": "num"})
    _check_catalog(costs.get("batteries"),  "batteries",  {"model": "str", "capacity_kwh": "num", "price_kes": "num"})
    _check_catalog(costs.get("inverters"),  "inverters",  {"model": "str", "rated_kw": "num", "price_kes": "num"})
    _check_catalog(costs.get("bos"),        "bos",        {"system_size_kw_max": "num", "price_kes": "num"})

    bos = costs.get("bos")
    if isinstance(bos, list) and len(bos) > 1:
        sizes = [e.get("system_size_kw_max", 0) for e in bos]
        if sizes != sorted(sizes):
            errors.append("costs.bos entries must be sorted ascending by system_size_kw_max")

    # ── Final result ──────────────────────────────────────────────────────────

    if errors:
        raise ValueError(
            f"Household validation failed with {len(errors)} error(s):\n"
            + "\n".join(f"  [{i+1}] {e}" for i, e in enumerate(errors))
        )

    return True


# =============================================================================
# SECTION 3: HELPER UTILITIES
# =============================================================================
def get_standard_appliances(household):
    """
    Return active appliances (count > 0).
    """
    return [a for a in household["appliances"] if a.get("count", 0) > 0]


def get_active_bulbs(household):
    """Return only bulb entries with count > 0."""
    return [b for b in household["bulbs"] if b.get("count", 0) > 0]


def estimate_daily_energy_kwh(household):
    """
    Rough deterministic estimate of daily energy consumption.
    Used for sanity checking — NOT the model output.

    Duration is capped at 60 min per tou slot: each slot represents one
    hour, so an appliance can contribute at most 1 hour of runtime per
    slot regardless of mean_duration_min. This correctly handles always-on
    appliances (fridge, router) whose duration field is 1440 min.

    For appliances with needs_occupancy=True, expected energy is scaled by
    the mean occupancy fraction for that day type, giving different weekday
    and weekend estimates.

    Returns
    -------
    dict with 'weekday_kwh', 'weekend_kwh', 'weighted_kwh'
    """
    def estimate_for_day(day_type):
        total_wh = 0.0
        occ = household[f"occupancy_{day_type}"]

        for appl in get_standard_appliances(household):
            tou      = appl["tou_hourly"]
            pwr      = appl["rated_power_w"]
            duration = min(appl["mean_duration_min"], 60) / 60.0
            if appl.get("needs_occupancy"):
                effective_tou = sum(t for t, o in zip(tou, occ) if o > 0)
            else:
                effective_tou = sum(tou)
            total_wh += appl["count"] * pwr * duration * effective_tou

        for bulb in get_active_bulbs(household):
            if bulb.get("needs_occupancy"):
                effective_tou = sum(t for t, o in zip(bulb["tou_hourly"], occ) if o > 0)
            else:
                effective_tou = sum(bulb["tou_hourly"])
            total_wh += bulb["count"] * bulb["wattage_w"] * effective_tou

        return total_wh / 1000

    wd = estimate_for_day("weekday")
    we = estimate_for_day("weekend")
    weighted = (5 * wd + 2 * we) / 7

    return {
        "weekday_kwh":  round(wd, 2),
        "weekend_kwh":  round(we, 2),
        "weighted_kwh": round(weighted, 2)
    }


def print_household_summary(household):
    """Print a human-readable summary of the household schema."""
    h = household
    print("=" * 60)
    print(f"HOUSEHOLD SUMMARY: {h['household_id']}")
    print("=" * 60)
    print(f"  Residents    : {h['n_residents']}")
    print(f"  Location     : {h['location']['sub_county']}, "
          f"{h['location']['county']}")
    print()

    print("OCCUPANCY (expected residents home by hour):")
    print("  Weekday:", h["occupancy_weekday"])
    print("  Weekend:", h["occupancy_weekend"])
    print()

    std_appl = get_standard_appliances(h)
    print(f"APPLIANCES ({len(std_appl)} active, tou_hourly driven, "
          f"includes cooking appliances):")
    for a in std_appl:
        print(f"  {a['name']:35s} x{a['count']}  "
              f"{a['rated_power_w']:>6.0f}W  "
              f"{a['mean_duration_min']:>4d}min avg")

    print()
    active_bulbs = get_active_bulbs(h)
    print(f"LIGHTING ({len(active_bulbs)} zones):")
    for b in active_bulbs:
        peak_hours = [i for i, v in enumerate(b['tou_hourly']) if v >= 0.5]
        peak_str = (f"peak {peak_hours[0]:02d}h–{peak_hours[-1]:02d}h"
                    if peak_hours else "low use")
        print(f"  {b['room']:30s} x{b['count']} bulb(s)  "
              f"{b['wattage_w']}W  {peak_str}")

    print()
    est = estimate_daily_energy_kwh(h)
    print("ESTIMATED DAILY ENERGY (rough, pre-Monte Carlo):")
    print(f"  Weekday : {est['weekday_kwh']:.2f} kWh")
    print(f"  Weekend : {est['weekend_kwh']:.2f} kWh")
    print(f"  Weighted: {est['weighted_kwh']:.2f} kWh/day")
    print("=" * 60)


# =============================================================================
# SECTION 4: SELF-TEST
# =============================================================================

if __name__ == "__main__":
    print("Running schema self-test...\n")

    # ── Test 1: Reference household validates cleanly ─────────────────────────
    try:
        validate_household(REFERENCE_HOUSEHOLD)
        print("[PASS] Test 1: Reference household passes validation.")
    except ValueError as e:
        print(f"[FAIL] Test 1: Reference household validation failed:\n{e}")

    # ── Test 2: Print summary ─────────────────────────────────────────────────
    print()
    print_household_summary(REFERENCE_HOUSEHOLD)

    # ── Test 3: Bad household rejected ───────────────────────────────────────
    bad = {
        "household_id": "",
        "n_residents": 0,
        "resident_breakdown": {
            "adults_working": 0, "adults_non_working": 0,
            "school_children": 0, "young_children": 0, "elderly": 0
        },
        "occupancy_weekday": [0]*23,
        "occupancy_weekend": [0]*24,
        "appliances": [
            {
                "name": "bad_appliance",
                "category": "other",
                "count": 1,
                "rated_power_w": -100,
                "tou_hourly": [0.5]*24,
                "mean_duration_min": 0,
                "std_duration_min": 100,
                "needs_occupancy": "yes"
            }
        ],
        "bulbs": [],
        "grid": {
            "connected": "yes",
            "phase": "five",
            "import_tariff_kes_per_kwh": -5,
            "monthly_bill_kes": 0,
            "existing_pv_kw": -1,
            "existing_battery_kwh": -1,
            "existing_backup_capacity_kw": -1,
            "existing_backup": "nuclear"
        },
        "site": {
            "roof_area_sqm": -10,
            "panel_derating_factor": 1.5
        },
        "costs": {
            "pv_panels":  [],
            "batteries":  [{"model": "", "capacity_kwh": -1, "price_kes": 0}],
            "inverters":  "not_a_list",
            "bos":        [{"system_size_kw_max": 10, "price_kes": 55000},
                           {"system_size_kw_max":  5, "price_kes": 35000}]
        }
    }

    print()
    print("Testing validator with deliberately bad household:")
    try:
        validate_household(bad)
        print("[FAIL] Test 3: Bad household incorrectly passed validation.")
    except ValueError as e:
        print(f"[PASS] Test 3: Bad household correctly rejected:\n{e}")

    print("\nAll schema self-tests complete.")