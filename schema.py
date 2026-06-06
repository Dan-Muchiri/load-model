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
Tier is derived by the researcher AFTER Monte Carlo simulation by
assign_tier(). It is based on simulated median daily energy.

    low    : simulated median daily consumption < 5 kWh/day
    medium : simulated median daily consumption 5–15 kWh/day
    high   : simulated median daily consumption > 15 kWh/day

COOKING MODULE NOTE
-------------------
Cooking is modelled in a dedicated BLOCK 4b ("cooking") separate from
the standard appliance framework. This is because:

  1. Cooking energy scales with the number of people fed — a household
     cooking for 6 people uses significantly more energy than one
     cooking for 2. The generic appliance tou_hourly framework has no
     mechanism for this.

  2. The load profile has a physically distinct shape: a high-power
     pre-heat phase followed by lower-power on/off cycling. This cannot
     be captured by mean_duration_min alone.

  3. Meal times are structured and household-specific. The survey
     captures actual meal windows directly — this is more accurate
     than probabilistic activity profiles derived from UK time-use
     survey data (which do not apply to Nairobi households).

  4. Fuel stacking is per-meal, not per-household. The same household
     may use charcoal for lunch and electric for dinner.

Cooking appliances (hotplate, EPC, rice cooker, etc.) remain in BLOCK 4a
(appliances list) so that their rated_power_w — read from the label
during the survey visit — is available to the cooking module. However
they carry the flag:

    "controlled_by_cooking_module": True

When this flag is True, the model IGNORES the appliance's tou_hourly and
mean_duration_min. The cooking module drives all load events for that
appliance. The tou_hourly values are kept in the appliance record for
documentation purposes only.

PHYSICS CONSTANTS IN THE COOKING MODULE
----------------------------------------
Two fields in each meal record are literature-derived physics constants
that are NEVER collected in the survey:

    energy_per_capita_kwh : kWh consumed per person fed per meal.
                            Source: MECS Kenya Cooking Diary Study
                            (Leary et al., 2019). Breakfast ≈ 0.04,
                            lunch ≈ 0.08, dinner ≈ 0.12 kWh/person.

    preheat_fraction      : fraction of total meal energy consumed
                            during the initial pre-heat phase.
                            Source: Leach et al. (2020). ≈ 0.75.

These are hardcoded here as authoritative constants. Do NOT survey them.
Do NOT allow the model to modify them at runtime.

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
- Meal times in minutes from midnight (e.g. 06:00 = 360, 19:30 = 1170)

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
For appliances with controlled_by_cooking_module = True, these
values are IGNORED by the model — they are retained for reference only.
"""

# =============================================================================
# SECTION 1: REFERENCE HOUSEHOLD
# =============================================================================
# This is a complete, realistic, validated example of a medium-tier
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

    "tier":           None,
    # Set to None at survey intake — not known yet.
    # Populated after Monte Carlo simulation by assign_tier().
    # Final values: "low" / "medium" / "high"
    # Based on simulated median daily energy from the ensemble.
    # Never ask the household directly.
    # Never derive from KPLC bill.

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

    "n_residents": 4,
    # Integer. Total number of people who live in this household.
    # Range: 1–10. Hard upper bound for all occupancy values.

    "resident_breakdown": {
        # Must sum to n_residents.
        "adults_working":     2,
        "adults_non_working": 0,
        "school_children":    2,
        "young_children":     0,
        "elderly":            0
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
             0,   0,   0,   0,   0,   0,
    #   Hr:  06   07   08   09   10   11
             2,   1,   0,   0,   0,   0,
    #   Hr:  12   13   14   15   16   17
             0,   0,   0,   0,   1,   2,
    #   Hr:  18   19   20   21   22   23
             3,   4,   4,   4,   3,   1
    ],
    # 00–05: Household asleep — occupancy = 0 for model purposes
    #        (fridge, router, security lights still run via needs_occupancy=False)
    # 06:    Two people up (parents preparing for work/school)
    # 07:    One still home (staggered departure)
    # 08–15: House empty
    # 16–17: Children returning, first parent home
    # 18–21: Full house
    # 22–23: Winding down

    "occupancy_weekend": [
    #   Hr:  00   01   02   03   04   05
             0,   0,   0,   0,   0,   0,
    #   Hr:  06   07   08   09   10   11
             0,   1,   2,   3,   4,   4,
    #   Hr:  12   13   14   15   16   17
             4,   3,   3,   4,   4,   4,
    #   Hr:  18   19   20   21   22   23
             4,   4,   4,   3,   2,   1
    ],

    # =========================================================================
    # BLOCK 4a: APPLIANCE INVENTORY
    # =========================================================================
    # Each appliance is a dict with exactly the fields shown below.
    # Appliances with count = 0 are included for completeness but
    # contribute zero load. Do not omit appliances — set count = 0.
    #
    # IMPORTANT — controlled_by_cooking_module FLAG:
    #   If True, this appliance's load is driven entirely by the
    #   cooking module (BLOCK 4b). The model ignores tou_hourly and
    #   mean_duration_min for this appliance. Only rated_power_w
    #   and count are used from this record.
    #   If False (default), normal tou_hourly switch-on logic applies.
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
    #   Group C: Cooking (controlled_by_cooking_module = True)
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
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
            "count":                        0,
            "rated_power_w":                120,
            "tou_hourly":       [1.0]*24,
            "mean_duration_min":    30,
            "std_duration_min":     5,
            "needs_occupancy":  False,
            "standby_power_w":  0,
            "notes": "Chest freezer. Less common in medium-tier households."
        },

        {
            "name":                         "wifi_router",
            "category":                     "always_on",
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
            "count":                        0,
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
            "controlled_by_cooking_module": False,
            "count":                        0,
            "rated_power_w":                30,
            # Read from label. 4-camera system with DVR: ~25–40W total.
            "tou_hourly":       [1.0]*24,
            "mean_duration_min":    1440,
            "std_duration_min":     0,
            "needs_occupancy":  False,
            "standby_power_w":  30,
            "notes": "CCTV DVR plus cameras. Runs 24/7."
        },

        {
            "name":                         "set_top_box_standby",
            "category":                     "always_on",
            "controlled_by_cooking_module": False,
            "count":                        1,
            "rated_power_w":                8,
            # DSTV/Zuku decoder phantom load in standby mode.
            "tou_hourly":       [1.0]*24,
            "mean_duration_min":    1440,
            "std_duration_min":     0,
            "needs_occupancy":  False,
            "standby_power_w":  8,
            "notes": (
                "DSTV/Zuku decoder phantom load in standby. "
                "Active draw captured separately in dstv_decoder. "
                "Many households leave decoders plugged in 24/7."
            )
        },

        # ── GROUP B: MORNING-PEAK APPLIANCES ──────────────────────────────

        {
            "name":                         "electric_kettle",
            "category":                     "morning_peak",
            "controlled_by_cooking_module": False,
            # Kettle is NOT controlled by the cooking module.
            # It is used for tea/coffee/uji preparation, not main meal cooking.
            # It does appear as an appliance_used in a cooking meal record
            # (breakfast), but the cooking module handles THAT instance.
            # This record covers additional non-meal kettle uses (mid-morning
            # tea, evening tea) via the standard tou_hourly mechanism.
            # The cooking module will NOT double-count: it only fires during
            # the meal window and uses the rated_power_w from this record.
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
            # These tou_hourly values represent NON-MEAL kettle uses only
            # (mid-morning tea, evening tea). The breakfast use is handled
            # by the cooking module — do not include that in these weights.
            "mean_duration_min":    4,
            "std_duration_min":     1,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": (
                "Electric kettle. Primary use is breakfast (cooking module). "
                "tou_hourly here covers non-meal uses: mid-morning and "
                "evening tea. Do not set tou_hourly high at meal times "
                "or energy will be double-counted."
            )
        },

        {
            "name":                         "electric_kettle_2",
            "category":                     "morning_peak",
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
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
        # These appliances carry controlled_by_cooking_module = True.
        # Their load events are ENTIRELY driven by BLOCK 4b (cooking).
        # The model ignores tou_hourly and mean_duration_min for these.
        # tou_hourly is kept here for documentation only.
        # rated_power_w MUST be read from the appliance label on site.

        {
            "name":                         "electric_hotplate",
            "category":                     "cooking",
            "controlled_by_cooking_module": True,
            "count":                        1,
            "rated_power_w":                1500,
            # READ FROM LABEL — this value will differ per household.
            # Typical single hotplate: 1000–2000W.
            # If multi-plate cooker, record the TOTAL rated draw when
            # all plates in use, or per-plate if used independently.
            "tou_hourly":       [
            # IGNORED by model. Retained for documentation only.
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.3,  0.4,  0.1,  0.0,  0.0,  0.0,
                     0.1,  0.2,  0.1,  0.0,  0.0,  0.2,
                     0.6,  0.5,  0.1,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    0,
            # IGNORED by model. Duration is calculated from cooking module:
            # preheat_duration = (energy_per_capita * n_people *
            #                     preheat_fraction) / rated_power_w * 60
            "std_duration_min":     0,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": (
                "Electric hotplate / resistance cooker. Load is driven "
                "entirely by the cooking module (BLOCK 4b). "
                "rated_power_w MUST be read from the appliance label "
                "during the survey visit — do not use the default."
            )
        },

        {
            "name":                         "induction_cooker",
            "category":                     "cooking",
            "controlled_by_cooking_module": True,
            "count":                        0,
            "rated_power_w":                2000,
            # READ FROM LABEL. Typical induction hob: 1200–2200W.
            "tou_hourly":       [
            # IGNORED by model.
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.3,  0.4,  0.1,  0.0,  0.0,  0.0,
                     0.1,  0.2,  0.1,  0.0,  0.0,  0.2,
                     0.6,  0.5,  0.1,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    0,
            "std_duration_min":     0,
            "needs_occupancy":  True,
            "standby_power_w":  2,
            "notes": (
                "Induction cooker. More efficient than resistance hotplate. "
                "Controlled by cooking module. "
                "rated_power_w from label."
            )
        },

        {
            "name":                         "electric_pressure_cooker",
            "category":                     "cooking",
            "controlled_by_cooking_module": True,
            "count":                        0,
            "rated_power_w":                800,
            # READ FROM LABEL. Typical EPC: 600–1200W.
            "tou_hourly":       [
            # IGNORED by model.
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.1,
                     0.3,  0.1,  0.0,  0.0,  0.0,  0.1,
                     0.5,  0.3,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    0,
            "std_duration_min":     0,
            "needs_occupancy":  True,
            "standby_power_w":  5,
            "notes": (
                "Electric pressure cooker (EPC/Instant Pot style). "
                "Controlled by cooking module. "
                "rated_power_w from label."
            )
        },

        {
            "name":                         "rice_cooker",
            "category":                     "cooking",
            "controlled_by_cooking_module": True,
            "count":                        0,
            "rated_power_w":                500,
            # READ FROM LABEL. Typical rice cooker: 300–700W.
            "tou_hourly":       [
            # IGNORED by model.
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.1,
                     0.3,  0.1,  0.0,  0.0,  0.0,  0.1,
                     0.4,  0.2,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    0,
            "std_duration_min":     0,
            "needs_occupancy":  True,
            "standby_power_w":  5,
            "notes": (
                "Rice cooker. Controlled by cooking module. "
                "rated_power_w from label."
            )
        },

        {
            "name":                         "microwave_oven",
            "category":                     "cooking",
            "controlled_by_cooking_module": False,
            # Microwave is NOT controlled by the cooking module.
            # It is used for reheating — short, standalone events
            # not tied to a primary cooking appliance for a meal.
            # It may be present in a household that also cooks with
            # a hotplate. The two are independent.
            "count":                        0,
            "rated_power_w":                900,
            # READ FROM LABEL. Typical microwave: 700–1200W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.2,  0.3,  0.1,  0.0,  0.0,  0.0,
                     0.1,  0.2,  0.0,  0.0,  0.0,  0.1,
                     0.3,  0.2,  0.1,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    5,
            "std_duration_min":     2,
            "needs_occupancy":  True,
            "standby_power_w":  3,
            "notes": (
                "Microwave. Used for reheating — not primary cooking. "
                "Uses standard tou_hourly mechanism. "
                "Set controlled_by_cooking_module = False deliberately."
            )
        },

        {
            "name":                         "blender",
            "category":                     "cooking",
            "controlled_by_cooking_module": False,
            # Blender is a food prep tool — short, activity-linked events.
            # Not a primary cooking appliance for a meal.
            "count":                        1,
            "rated_power_w":                350,
            # READ FROM LABEL. Typical blender: 250–500W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.3,  0.4,  0.1,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.1,
                     0.2,  0.1,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    3,
            "std_duration_min":     1,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": "Blender. Short food-prep bursts. Not a primary cooker."
        },

        {
            "name":                         "toaster",
            "category":                     "cooking",
            "controlled_by_cooking_module": False,
            "count":                        0,
            "rated_power_w":                800,
            # READ FROM LABEL.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.3,  0.5,  0.2,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.1,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    4,
            "std_duration_min":     1,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": "Pop-up toaster. Short breakfast use. Not a primary cooker."
        },

        # ── GROUP D: ENTERTAINMENT AND INFORMATION ─────────────────────────

        {
            "name":                         "television",
            "category":                     "entertainment",
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
            "count":                        0,
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
            "controlled_by_cooking_module": False,
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
            "notes": "DSTV/Zuku decoder. Active power only — standby in set_top_box_standby."
        },

        {
            "name":                         "laptop",
            "category":                     "entertainment",
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
            "count":                        0,
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
            "controlled_by_cooking_module": False,
            "count":                        0,
            "rated_power_w":                150,
            # READ FROM LABEL. Desktop + monitor: 100–250W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.2,  0.4,
                     0.5,  0.5,  0.3,  0.1,  0.0,  0.0
            ],
            "mean_duration_min":    120,
            "std_duration_min":     60,
            "needs_occupancy":  True,
            "standby_power_w":  5,
            "notes": "Desktop PC with monitor."
        },

        {
            "name":                         "gaming_console",
            "category":                     "entertainment",
            "controlled_by_cooking_module": False,
            "count":                        0,
            "rated_power_w":                150,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.1,  0.2,  0.3,
                     0.5,  0.6,  0.5,  0.3,  0.1,  0.0
            ],
            "mean_duration_min":    90,
            "std_duration_min":     45,
            "needs_occupancy":  True,
            "standby_power_w":  2,
            "notes": "Gaming console. Afternoon/evening use."
        },

        {
            "name":                         "bluetooth_speaker",
            "category":                     "entertainment",
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
            "count":                        4,
            # One per phone-owning member of household. Each simulated independently.
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
            "controlled_by_cooking_module": False,
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
            "name":                         "laptop_charger_overnight",
            "category":                     "charging",
            "controlled_by_cooking_module": False,
            "count":                        1,
            "rated_power_w":                20,
            # Laptop in maintenance/trickle mode: 15–25W.
            "tou_hourly":       [
                     0.7,  0.7,  0.7,  0.7,  0.6,  0.5,
                     0.2,  0.1,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.1,  0.2,  0.4,  0.6,  0.7
            ],
            "mean_duration_min":    240,
            "std_duration_min":     60,
            "needs_occupancy":  False,
            "standby_power_w":  5,
            "notes": "Laptop left plugged in overnight. Trickle draw."
        },

        {
            "name":                         "power_bank_charging",
            "category":                     "charging",
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
            "count":                        0,
            "rated_power_w":                500,
            # READ FROM LABEL. Front-loader cold-wash: 300–500W.
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.2,  0.4,  0.3,  0.2,  0.1,
                     0.0,  0.0,  0.0,  0.1,  0.1,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    45,
            "std_duration_min":     10,
            "needs_occupancy":  True,
            "standby_power_w":  3,
            "notes": (
                "Automatic washing machine. Morning weekend use dominant. "
                "Many Nairobi households hand-wash or use laundry services."
            )
        },

        {
            "name":                         "vacuum_cleaner",
            "category":                     "laundry",
            "controlled_by_cooking_module": False,
            "count":                        0,
            "rated_power_w":                1000,
            "tou_hourly":       [
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0,
                     0.0,  0.0,  0.3,  0.4,  0.3,  0.0,
                     0.0,  0.0,  0.0,  0.1,  0.1,  0.0,
                     0.0,  0.0,  0.0,  0.0,  0.0,  0.0
            ],
            "mean_duration_min":    20,
            "std_duration_min":     8,
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": "Vacuum cleaner. Morning cleaning. Rare in medium-tier."
        },

        # ── GROUP G: COMFORT APPLIANCES ───────────────────────────────────

        {
            "name":                         "ceiling_fan",
            "category":                     "comfort",
            "controlled_by_cooking_module": False,
            "count":                        2,
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
            "needs_occupancy":  True,
            "standby_power_w":  0,
            "notes": (
                "Ceiling fan. Nairobi 18–26°C so fans are for circulation. "
                "Count = number of fans in household."
            )
        },

        {
            "name":                         "standing_fan",
            "category":                     "comfort",
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
            "count":                        0,
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
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
            "count":                        0,
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
            "controlled_by_cooking_module": False,
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
            "controlled_by_cooking_module": False,
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
    # BLOCK 4b: COOKING MODULE
    # =========================================================================
    # Cooking is modelled separately from the appliance framework.
    # See the module docstring at the top of this file for the full
    # rationale. This block is the authoritative source of all cooking
    # load generation.
    #
    # HOW THE MODEL USES THIS BLOCK:
    #   For each meal where cooked_at_home = True and electric_fraction > 0:
    #
    #   1. Draw a random start time uniformly from:
    #         [earliest_start_min, latest_start_min]
    #
    #   2. Calculate meal energy needed:
    #         energy_kwh = energy_per_capita_kwh * n_people_fed
    #                      * proportionality_factor
    #
    #   3. Calculate pre-heat duration:
    #         preheat_energy_kwh = energy_kwh * preheat_fraction
    #         appliance_power_w  = rated_power_w of appliance_used
    #         preheat_min = (preheat_energy_kwh * 1000 / appliance_power_w) * 60
    #
    #   4. Model the pre-heat phase: appliance runs at rated_power_w
    #      for preheat_min minutes.
    #
    #   5. Model the cycling phase: the cooking module's physics constants
    #      (reheat_on_min, reheat_off_min, n_reheat_cycles from cook_type)
    #      drive on/off cycling for the remaining energy.
    #
    #   6. Occupancy check: if occupancy is zero at the scheduled meal start
    #      time, the meal start is delayed or skipped. This links the cooking
    #      module to the Markov occupancy chain.
    #
    # PHYSICS CONSTANTS (literature-derived, do NOT survey):
    #   energy_per_capita_kwh  Source: MECS Kenya Cooking Diary (Leary 2019)
    #   preheat_fraction       Source: Leach et al. (2020) ≈ 0.75
    #   proportionality_factor Source: Leach et al. (2020)
    #                          Accounts for the fact that cooking for more
    #                          people does not scale perfectly linearly —
    #                          you heat the pot regardless. Typically 0.7–0.9.
    #
    # CYCLING PHYSICS CONSTANTS (hardcoded in cooking_model.py):
    #   quick cook: reheat_on_min = 3, reheat_off_min = 7, n_cycles = 3
    #   long cook:  reheat_on_min = 5, reheat_off_min = 10, n_cycles = 6
    #   These are derived from appliance characterisation in Leach et al.
    #   and are not household-specific.

    "cooking": {

        # Global cooking context for this household.
        # These inform the cooking module about this household's
        # general cooking practices.

        "primary_cooking_fuel": "mixed",
        # Overall primary fuel for this household.
        # "electric" / "charcoal" / "lpg_gas" / "kerosene" / "mixed"
        # "mixed" means the household uses more than one fuel type.
        # Per-meal fuel is specified in each meal record below.
        # Collected at survey: "What is your main cooking fuel at home?"

        "has_dedicated_cooking_space": True,
        # Boolean. True if the household has a separate kitchen.
        # False if cooking happens in the main living area.
        # Informational — may be used in future thermal modelling.

        "meals": [

            # ── MEAL 1: BREAKFAST ─────────────────────────────────────────

            {
                "meal_id":          "breakfast",

                "cooked_at_home":   True,
                # Boolean. If False, model skips this meal entirely.
                # Collected at survey: "Do you cook breakfast at home
                # on a typical weekday / weekend?"

                "cooked_at_home_weekday": True,
                # Boolean. Can differ from weekend.
                "cooked_at_home_weekend": True,

                "earliest_start_min": 360,
                # Minutes from midnight. 06:00 = 360.
                # "What is the earliest time you start preparing breakfast?"
                # Collected at survey — household-specific.

                "latest_start_min": 450,
                # Minutes from midnight. 07:30 = 450.
                # "What is the latest time you start preparing breakfast?"
                # Collected at survey — household-specific.
                # Model draws start time uniformly from [earliest, latest].

                "n_people_fed":     4,
                # Integer. Number of people cooked for at this meal.
                # "How many people do you cook breakfast for on a typical day?"
                # Collected at survey. Critical for energy calculation.
                # May differ from n_residents (guests, workers, etc.)

                "primary_fuel":     "electric",
                # Fuel used for THIS meal specifically.
                # "electric" / "charcoal" / "lpg_gas" / "kerosene" / "mixed"
                # Collected at survey per meal — not assumed household-wide.

                "electric_fraction": 1.0,
                # Float [0.0–1.0].
                # Fraction of this meal's cooking energy from electricity.
                # 1.0 = fully electric.
                # 0.0 = fully non-electric (model generates no electrical load).
                # 0.5 = half electric, half other fuel.
                # For "mixed" primary_fuel, surveyor estimates this fraction.

                "appliance_used":   "electric_kettle",
                # Name matching an appliance in the appliances list above.
                # The cooking module reads rated_power_w from that record.
                # Only one primary appliance per meal. If multiple appliances
                # are used, record the dominant one here and add the secondary
                # as other_appliance_used below.
                # Collected at survey: "Which electric appliance do you use
                # to prepare breakfast?"

                "other_appliance_used": None,
                # Optional secondary appliance name, or None.
                # e.g. kettle boils water while hotplate cooks porridge.
                # If not None, both appliances run during the pre-heat phase.

                "cook_type":        "quick",
                # "quick" or "long".
                # quick: tea, porridge, simple reheating — short pre-heat,
                #        few or no re-heat cycles.
                # long:  full meal, stew, ugali with accompaniment — longer
                #        pre-heat, multiple cycling phases.
                # Collected at survey: "Is breakfast a quick or long cook?"

                # ── PHYSICS CONSTANTS (do NOT survey) ─────────────────────

                "energy_per_capita_kwh": 0.04,
                # kWh per person fed per meal.
                # Source: MECS Kenya Cooking Diary Study (Leary et al. 2019).
                # Breakfast is typically lighter — lower energy per person.
                # DO NOT change without a literature justification.

                "preheat_fraction": 0.75,
                # Fraction of meal energy in the pre-heat phase.
                # Source: Leach et al. (2020).
                # DO NOT survey this field.

                "proportionality_factor": 0.85,
                # Energy scaling factor for additional people.
                # Accounts for non-linear scaling (pot heating is fixed cost).
                # Source: Leach et al. (2020).
                # DO NOT survey this field.
            },

            # ── MEAL 2: LUNCH ─────────────────────────────────────────────

            {
                "meal_id":          "lunch",

                "cooked_at_home":   False,
                # Many working households do not cook lunch at home.
                # If False, model generates zero cooking load for lunch.

                "cooked_at_home_weekday": False,
                # Working adults and school children are away on weekdays.
                "cooked_at_home_weekend": True,
                # Family is home on weekends — lunch may be cooked.

                "earliest_start_min": 720,
                # 12:00
                "latest_start_min":   810,
                # 13:30

                "n_people_fed":       4,
                # On weekends when lunch is cooked, whole family is home.

                "primary_fuel":       "charcoal",
                "electric_fraction":  0.0,
                # This household uses charcoal for lunch — no electric load.

                "appliance_used":     None,
                # None if electric_fraction = 0.0.

                "other_appliance_used": None,

                "cook_type":          "long",

                # ── PHYSICS CONSTANTS ──────────────────────────────────────
                "energy_per_capita_kwh": 0.08,
                # Lunch is a fuller meal than breakfast.
                "preheat_fraction":   0.75,
                "proportionality_factor": 0.80,
            },

            # ── MEAL 3: DINNER ────────────────────────────────────────────

            {
                "meal_id":          "dinner",

                "cooked_at_home":   True,
                "cooked_at_home_weekday": True,
                "cooked_at_home_weekend": True,

                "earliest_start_min": 1020,
                # 17:00 — earliest dinner prep starts
                "latest_start_min":   1140,
                # 19:00 — latest dinner prep starts
                # Model draws uniformly from this window per day.

                "n_people_fed":       4,

                "primary_fuel":       "electric",
                "electric_fraction":  1.0,

                "appliance_used":     "electric_hotplate",
                # Primary cooking appliance for dinner.
                # rated_power_w is read from the appliance record above.

                "other_appliance_used": None,
                # Could be set to "electric_kettle" if boiling water
                # simultaneously, but keep None unless confirmed at survey.

                "cook_type":          "long",
                # Dinner is the main meal — full cooking cycle.

                # ── PHYSICS CONSTANTS ──────────────────────────────────────
                "energy_per_capita_kwh": 0.12,
                # Dinner consumes the most energy — full meal preparation.
                # Source: MECS Kenya Cooking Diary (Leary et al. 2019).
                "preheat_fraction":   0.75,
                "proportionality_factor": 0.80,
            },

            # ── MEAL 4: OPTIONAL ADDITIONAL MEAL ─────────────────────────
            # Some households have a fourth eating event (afternoon snack,
            # second breakfast, supper etc.). Add here if applicable.
            # Set cooked_at_home = False if not applicable to this household.

            {
                "meal_id":          "afternoon_snack",

                "cooked_at_home":   False,
                "cooked_at_home_weekday": False,
                "cooked_at_home_weekend": False,

                "earliest_start_min": 900,   # 15:00
                "latest_start_min":   960,   # 16:00

                "n_people_fed":       2,

                "primary_fuel":       "electric",
                "electric_fraction":  1.0,

                "appliance_used":     "electric_kettle",
                "other_appliance_used": None,

                "cook_type":          "quick",

                "energy_per_capita_kwh": 0.03,
                "preheat_fraction":   0.75,
                "proportionality_factor": 0.90,
            }

        ]  # end of meals list

    },  # end of cooking block

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
    #   usage_start_hour: hour at which lights are typically switched ON
    #   usage_end_hour  : hour at which lights are typically switched OFF
    #                     If end < start, lights run through midnight
    #                     e.g. start=18, end=6 → 18:00 to 06:00
    #   p_on_occupied   : probability lights are on when room is occupied
    #                     and it is dark enough (effective_light < threshold)
    #   p_on_daylight   : probability lights are on when room is occupied
    #                     but there is sufficient daylight
    #                     (captures lights left on during daytime)
    #   controls_separately: True if this room's lights are switched
    #                        independently (most rooms), False if they
    #                        are on one switch with another room
    #
    # NAIROBI DAYLIGHT NOTE:
    #   Sunrise ~06:30, Sunset ~18:30 year-round.
    #   The lighting model uses a sinusoidal daylight proxy
    #   scaled by the daily CSI value.
    #   Lights are considered necessary when effective_light < 0.3.
    #   This threshold is tunable in lighting_model.py.

    "bulbs": [

        {
            "room":              "living_room",
            "count":             2,
            "wattage_w":         9,
            "bulb_type":         "LED",
            "usage_start_hour":  18,
            "usage_end_hour":    23,
            # Lights on from 6pm until 11pm when family is in the lounge.
            "p_on_occupied":     0.90,
            # Very high — main social space, almost always lit in evenings.
            "p_on_daylight":     0.05,
            # Occasionally left on during overcast afternoons.
            "controls_separately": True,
            "notes": (
                "Main living room / lounge area. "
                "Dominant lighting load in evening hours. "
                "Two ceiling LED bulbs."
            )
        },

        {
            "room":              "dining_area",
            "count":             1,
            "wattage_w":         9,
            "bulb_type":         "LED",
            "usage_start_hour":  6,
            "usage_end_hour":    8,
            # Morning breakfast usage.
            "p_on_occupied":     0.70,
            "p_on_daylight":     0.10,
            "controls_separately": True,
            "notes": (
                "Dining area light. Morning breakfast and evening dinner. "
                "Add a second entry for this room with evening hours "
                "if dining area is used in the evening as well."
            )
        },

        {
            "room":              "dining_area_evening",
            "count":             1,
            "wattage_w":         9,
            "bulb_type":         "LED",
            "usage_start_hour":  18,
            "usage_end_hour":    21,
            "p_on_occupied":     0.80,
            "p_on_daylight":     0.05,
            "controls_separately": False,
            # Same switch as dining_area — modelled as separate entry
            # with different usage hours. Model should not sum both
            # simultaneously — handled in lighting_model.py.
            "notes": "Dining area evening usage (same bulb as morning entry)."
        },

        {
            "room":              "master_bedroom",
            "count":             2,
            "wattage_w":         9,
            "bulb_type":         "LED",
            "usage_start_hour":  19,
            "usage_end_hour":    23,
            "p_on_occupied":     0.85,
            "p_on_daylight":     0.05,
            "controls_separately": True,
            "notes": "Master bedroom. Evening use before sleep."
        },

        {
            "room":              "bedroom_2",
            "count":             1,
            "wattage_w":         9,
            "bulb_type":         "LED",
            "usage_start_hour":  19,
            "usage_end_hour":    22,
            "p_on_occupied":     0.80,
            "p_on_daylight":     0.05,
            "controls_separately": True,
            "notes": "Children's bedroom. Earlier lights-out than master."
        },

        {
            "room":              "bedroom_3",
            "count":             0,
            # Set count = 0 if bedroom does not exist.
            "wattage_w":         9,
            "bulb_type":         "LED",
            "usage_start_hour":  19,
            "usage_end_hour":    22,
            "p_on_occupied":     0.75,
            "p_on_daylight":     0.05,
            "controls_separately": True,
            "notes": "Third bedroom. Set count = 0 if not present."
        },

        {
            "room":              "kitchen",
            "count":             1,
            "wattage_w":         9,
            "bulb_type":         "LED",
            "usage_start_hour":  6,
            "usage_end_hour":    8,
            "p_on_occupied":     0.85,
            "p_on_daylight":     0.20,
            # Kitchen may need lights even in daytime if poorly lit.
            "controls_separately": True,
            "notes": "Kitchen morning lighting for breakfast preparation."
        },

        {
            "room":              "kitchen_evening",
            "count":             1,
            "wattage_w":         9,
            "bulb_type":         "LED",
            "usage_start_hour":  17,
            "usage_end_hour":    21,
            "p_on_occupied":     0.85,
            "p_on_daylight":     0.05,
            "controls_separately": False,
            # Same bulb as kitchen morning.
            "notes": "Kitchen evening lighting during dinner preparation."
        },

        {
            "room":              "bathroom",
            "count":             1,
            "wattage_w":         9,
            "bulb_type":         "LED",
            "usage_start_hour":  5,
            "usage_end_hour":    8,
            "p_on_occupied":     0.80,
            "p_on_daylight":     0.60,
            # Bathroom has no windows — high p_on_daylight.
            "controls_separately": True,
            "notes": (
                "Bathroom / toilet. Short duration use. "
                "High p_on_daylight because bathroom is typically interior "
                "with no natural light regardless of time of day."
            )
        },

        {
            "room":              "bathroom_evening",
            "count":             1,
            "wattage_w":         9,
            "bulb_type":         "LED",
            "usage_start_hour":  18,
            "usage_end_hour":    22,
            "p_on_occupied":     0.75,
            "p_on_daylight":     0.90,
            "controls_separately": False,
            "notes": "Bathroom evening use. Same bulb as morning entry."
        },

        {
            "room":              "outside_security_front",
            "count":             1,
            "wattage_w":         15,
            "bulb_type":         "LED",
            "usage_start_hour":  18,
            "usage_end_hour":    6,
            # Runs from 6pm to 6am — overnight security lighting.
            "p_on_occupied":     1.00,
            # Always on during usage window — security function.
            "p_on_daylight":     0.00,
            # Never on during daylight.
            "controls_separately": True,
            "notes": (
                "Front door / entrance security light. "
                "Runs from dusk to dawn. "
                "Some households use a dusk-to-dawn sensor. "
                "Model as usage_start=18, usage_end=6 with p_on=1.0."
            )
        },

        {
            "room":              "outside_security_back",
            "count":             1,
            "wattage_w":         15,
            "bulb_type":         "LED",
            "usage_start_hour":  18,
            "usage_end_hour":    6,
            "p_on_occupied":     1.00,
            "p_on_daylight":     0.00,
            "controls_separately": True,
            "notes": "Back yard / service entrance security light."
        },

        {
            "room":              "staircase_corridor",
            "count":             0,
            # Common in multi-storey or maisonette homes.
            "wattage_w":         9,
            "bulb_type":         "LED",
            "usage_start_hour":  18,
            "usage_end_hour":    23,
            "p_on_occupied":     0.60,
            "p_on_daylight":     0.20,
            "controls_separately": True,
            "notes": "Staircase or corridor light. Set count = 0 for single-storey."
        },

        {
            "room":              "store_room",
            "count":             1,
            "wattage_w":         9,
            "bulb_type":         "LED",
            "usage_start_hour":  6,
            "usage_end_hour":    22,
            "p_on_occupied":     0.10,
            # Low probability — used briefly and infrequently.
            "p_on_daylight":     0.50,
            # Interior room — may need light regardless.
            "controls_separately": True,
            "notes": "Store room or utility room. Low, occasional use."
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

        "supply_reliability": "poor",
        # Qualitative assessment from household: "good" / "fair" / "poor"
        # "poor" → frequent blackouts, high autonomy motivation
        # "good" → reliable, autonomy is less critical

        "avg_blackout_hours_per_week": 6.0,
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

        "metering_type": "postpaid",
        # "postpaid" or "prepaid" (token meter).
        # Most Nairobi residential connections are one or the other.

        "existing_backup": None,
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

        "existing_pv_kw": 0.0,
        # If existing_backup includes solar, installed PV capacity in kW.
        # Set to 0.0 if no existing PV.

        "existing_battery_kwh": 0.0,
        # If existing_backup includes battery, installed capacity in kWh.
        # Set to 0.0 if no existing battery.

        "existing_backup_capacity_kw": 0.0
        # Generator or inverter rated capacity in kW if applicable.
        # Set to 0.0 otherwise.
    },

    # =========================================================================
    # BLOCK 7: PHYSICAL SITE INFORMATION
    # =========================================================================

    # Used by Objective 2 (solar forecast) and Objective 3 (PV model).

    "site": {

        "roof_area_sqm": 40.0,
        # Available roof area for PV panels in square metres.
        # Practical limit on PPV even if optimizer wants more.
        # A 1kW PV array requires approximately 6–8 m² (depending on panel).
        # 40m² → practical maximum ~5–6 kWp.

        "roof_orientation": "south_facing",
        # Nairobi is south of the equator → north-facing is optimal.
        # Options: "north_facing" (optimal for Kenya), "south_facing",
        #          "east_facing", "west_facing", "flat"
        # Note: 'south_facing' is actually suboptimal for Kenya.
        # This field captures the real roof situation.

        "roof_tilt_degrees": 15,
        # Roof pitch angle from horizontal in degrees.
        # Typical Nairobi residential: 10–20 degrees.
        # Flat roof: 0–5 degrees.

        "shading": "minimal",
        # Qualitative: "none" / "minimal" / "moderate" / "severe"
        # Captures shading from trees, neighbouring buildings.

        "roof_type": "iron_sheet",
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
    # All costs in KES.
    # These are market prices as of mid-2025 Nairobi.
    # Update at survey time if prices have changed significantly.

    "costs": {

        "pv_panel_kes_per_kw":       80000,
        # Installed cost per kWp of PV panels including mounting.
        # Typical Nairobi market: KES 70,000–100,000 per kWp installed.

        "battery_kes_per_kwh":       60000,
        # Installed cost per kWh of LiFePO4 battery storage.
        # Typical Nairobi market: KES 50,000–80,000 per kWh installed.

        "inverter_kes_per_kw":       30000,
        # Installed cost per kW of hybrid inverter.
        # Typical: KES 25,000–40,000 per kW for hybrid inverter.

        "bos_kes":                   50000,
        # Balance of System fixed cost: wiring, breakers, mounting,
        # installation labour, commissioning. Flat fee.
        # Typical Nairobi residential BOS: KES 30,000–70,000.

        "om_kes_per_year":           5000,
        # Annual operation and maintenance cost.
        # Panel cleaning, inspection, minor repairs.

        "battery_replacement_years": 10,
        # Expected battery cycle life before replacement needed.
        # LiFePO4 at 80% DOD: 2000–3000 cycles → approximately 8–10 years.

        "pv_lifetime_years":         25,
        # Standard PV panel warranty and expected lifetime.

        "inverter_lifetime_years":   10,
        # Hybrid inverter expected lifetime before replacement.

        "discount_rate":             0.12,
        # Annual discount rate for NPV/NPC calculations.
        # Kenya commercial lending rate proxy: 10–14%.

        "electricity_price_escalation": 0.05
        # Annual electricity price escalation rate.
        # KPLC tariffs have been increasing: use 5% per year.
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
        # Set to None for production runs (true randomness).

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

        "duration_clip_min_minutes": 1,
        # Minimum duration for any appliance use event.
        # Prevents zero or negative duration samples.

        "validation_mae_threshold_pct":        10.0,
        # Maximum acceptable MAE as % of mean measured load.
        # Model fails validation if MAE exceeds this.

        "validation_variability_ratio_min":    0.90,
        "validation_variability_ratio_max":    1.10,
        # Acceptable range for σ_model / σ_measured.

        "validation_peak_avg_ratio_tolerance_pct": 10.0
        # Maximum acceptable % difference in peak-to-average ratio.
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

    if h.get("tier") not in ["low", "medium", "high", None]:
        errors.append(
            "tier must be one of: low, medium, high, or None. "
            f"Got: '{h.get('tier')}'"
        )

    # ── Block 2: Household composition ───────────────────────────────────────

    n = h.get("n_residents")
    if not isinstance(n, int) or not (1 <= n <= 10):
        errors.append(
            f"n_residents must be an integer between 1 and 10, got: {n}"
        )
    else:
        rb = h.get("resident_breakdown", {})
        rb_sum = sum([
            rb.get("adults_working", 0),
            rb.get("adults_non_working", 0),
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

            if not isinstance(appl.get("controlled_by_cooking_module"), bool):
                errors.append(
                    f"{prefix}: controlled_by_cooking_module must be "
                    f"True or False"
                )

            controlled = appl.get("controlled_by_cooking_module", False)

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

            # Only enforce duration constraints for non-cooking-module appliances
            if not controlled:
                if not isinstance(mean_d, (int, float)) or mean_d < 1:
                    if count and count > 0:
                        errors.append(
                            f"{prefix}: mean_duration_min must be ≥ 1 "
                            f"for non-cooking-module appliances, got: {mean_d}"
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

    # ── Block 4b: Cooking module ──────────────────────────────────────────────

    cooking = h.get("cooking", {})
    if not isinstance(cooking, dict):
        errors.append("cooking must be a dict")
    else:
        valid_fuels = ["electric", "charcoal", "lpg_gas", "kerosene", "mixed"]

        if cooking.get("primary_cooking_fuel") not in valid_fuels:
            errors.append(
                f"cooking.primary_cooking_fuel must be one of {valid_fuels}, "
                f"got: '{cooking.get('primary_cooking_fuel')}'"
            )

        meals = cooking.get("meals", [])
        if not isinstance(meals, list) or len(meals) == 0:
            errors.append("cooking.meals must be a non-empty list")
        else:
            seen_meal_ids = []
            for midx, meal in enumerate(meals):
                mprefix = f"cooking.meals[{midx}] ('{meal.get('meal_id', '?')}')"

                meal_id = meal.get("meal_id")
                if not isinstance(meal_id, str) or not meal_id:
                    errors.append(f"{mprefix}: meal_id must be a non-empty string")
                elif meal_id in seen_meal_ids:
                    errors.append(f"{mprefix}: duplicate meal_id '{meal_id}'")
                else:
                    seen_meal_ids.append(meal_id)

                if not isinstance(meal.get("cooked_at_home"), bool):
                    errors.append(f"{mprefix}: cooked_at_home must be True or False")

                for bool_field in ["cooked_at_home_weekday",
                                   "cooked_at_home_weekend"]:
                    if not isinstance(meal.get(bool_field), bool):
                        errors.append(
                            f"{mprefix}: {bool_field} must be True or False"
                        )

                for time_field in ["earliest_start_min", "latest_start_min"]:
                    val = meal.get(time_field)
                    if not isinstance(val, (int, float)) or \
                       not (0 <= val <= 1439):
                        errors.append(
                            f"{mprefix}: {time_field} must be 0–1439 "
                            f"(minutes from midnight), got: {val}"
                        )

                if isinstance(meal.get("earliest_start_min"), (int, float)) and \
                   isinstance(meal.get("latest_start_min"), (int, float)):
                    if meal["earliest_start_min"] > meal["latest_start_min"]:
                        errors.append(
                            f"{mprefix}: earliest_start_min "
                            f"({meal['earliest_start_min']}) must be ≤ "
                            f"latest_start_min ({meal['latest_start_min']})"
                        )

                n_people = meal.get("n_people_fed")
                if not isinstance(n_people, int) or n_people < 1:
                    errors.append(
                        f"{mprefix}: n_people_fed must be a positive integer, "
                        f"got: {n_people}"
                    )

                if meal.get("primary_fuel") not in valid_fuels:
                    errors.append(
                        f"{mprefix}: primary_fuel must be one of {valid_fuels}"
                    )

                ef = meal.get("electric_fraction")
                if not isinstance(ef, (int, float)) or not (0.0 <= ef <= 1.0):
                    errors.append(
                        f"{mprefix}: electric_fraction must be float in "
                        f"[0.0, 1.0], got: {ef}"
                    )

                # appliance_used must match an appliance name if electric_fraction > 0
                if isinstance(ef, (int, float)) and ef > 0:
                    appl_name = meal.get("appliance_used")
                    if appl_name is None:
                        errors.append(
                            f"{mprefix}: electric_fraction > 0 but "
                            f"appliance_used is None. Must specify an appliance."
                        )
                    elif isinstance(appliances, list):
                        known_names = [a.get("name") for a in appliances]
                        if appl_name not in known_names:
                            errors.append(
                                f"{mprefix}: appliance_used = '{appl_name}' "
                                f"does not match any appliance name in "
                                f"the appliances list."
                            )
                        else:
                            # Find the appliance and confirm it is cooking-module
                            # controlled
                            matching = [a for a in appliances
                                        if a.get("name") == appl_name]
                            if matching:
                                appl_rec = matching[0]
                                if not appl_rec.get(
                                    "controlled_by_cooking_module", False
                                ):
                                    # Allow kettles — they can appear in both
                                    # frameworks. Warn but do not fail.
                                    pass

                if meal.get("cook_type") not in ["quick", "long"]:
                    errors.append(
                        f"{mprefix}: cook_type must be 'quick' or 'long', "
                        f"got: '{meal.get('cook_type')}'"
                    )

                for phys_field in ["energy_per_capita_kwh",
                                   "preheat_fraction",
                                   "proportionality_factor"]:
                    val = meal.get(phys_field)
                    if not isinstance(val, (int, float)) or val <= 0:
                        errors.append(
                            f"{mprefix}: {phys_field} must be a positive "
                            f"number, got: {val}"
                        )

                if isinstance(meal.get("preheat_fraction"), (int, float)):
                    if not (0.0 < meal["preheat_fraction"] <= 1.0):
                        errors.append(
                            f"{mprefix}: preheat_fraction must be in (0, 1], "
                            f"got: {meal['preheat_fraction']}"
                        )

                if isinstance(meal.get("proportionality_factor"), (int, float)):
                    if not (0.0 < meal["proportionality_factor"] <= 1.0):
                        errors.append(
                            f"{mprefix}: proportionality_factor must be in "
                            f"(0, 1], got: {meal['proportionality_factor']}"
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

            for field in ["usage_start_hour", "usage_end_hour"]:
                val = b.get(field)
                if not isinstance(val, int) or not (0 <= val <= 23):
                    errors.append(
                        f"{prefix}: {field} must be integer 0–23, got: {val}"
                    )

            for prob_field in ["p_on_occupied", "p_on_daylight"]:
                val = b.get(prob_field)
                if not isinstance(val, (int, float)) or \
                   not (0.0 <= val <= 1.0):
                    errors.append(
                        f"{prefix}: {prob_field} must be float in [0,1]"
                    )

            if not isinstance(b.get("controls_separately"), bool):
                errors.append(
                    f"{prefix}: controls_separately must be True or False"
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
    required_cost_fields = [
        "pv_panel_kes_per_kw",
        "battery_kes_per_kwh",
        "inverter_kes_per_kw",
        "bos_kes"
    ]
    for field in required_cost_fields:
        val = costs.get(field)
        if not isinstance(val, (int, float)) or val <= 0:
            errors.append(
                f"costs.{field} must be a positive number, got: {val}"
            )

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

def assign_tier(household, simulation_results):
    """
    Assign consumption tier to a household after Monte Carlo simulation.

    This is the ONLY correct way to set the tier field.
    Call this after run_ensemble() has completed for both day types.

    Parameters
    ----------
    household : dict
        Household parameter dict. tier must currently be None.
    simulation_results : dict
        Must contain 'weighted_daily_energy_kwh' — the weighted median
        daily energy: (5 * weekday_p50 + 2 * weekend_p50) / 7

    Returns
    -------
    household dict with tier populated in place.

    Raises
    ------
    ValueError if tier is already set.
    """
    if household.get("tier") is not None:
        raise ValueError(
            f"tier is already set to '{household['tier']}'. "
            f"Set household['tier'] = None first if reassignment is needed."
        )

    median_kwh = simulation_results["weighted_daily_energy_kwh"]

    if not isinstance(median_kwh, (int, float)) or median_kwh < 0:
        raise ValueError(
            f"weighted_daily_energy_kwh must be non-negative, got: {median_kwh}"
        )

    if median_kwh < 5.0:
        tier = "low"
    elif median_kwh <= 15.0:
        tier = "medium"
    else:
        tier = "high"

    household["tier"] = tier
    print(
        f"Tier assigned: '{tier}' "
        f"(simulated median daily energy = {median_kwh:.2f} kWh/day)"
    )
    return household


def get_active_appliances(household):
    """Return only appliances with count > 0."""
    return [a for a in household["appliances"] if a.get("count", 0) > 0]


def get_cooking_module_appliances(household):
    """
    Return appliances controlled by the cooking module (count > 0).
    These are EXCLUDED from standard tou_hourly switch-on logic.
    """
    return [
        a for a in household["appliances"]
        if a.get("count", 0) > 0
        and a.get("controlled_by_cooking_module", False)
    ]


def get_standard_appliances(household):
    """
    Return active appliances NOT controlled by the cooking module.
    These use the standard tou_hourly switch-on mechanism.
    """
    return [
        a for a in household["appliances"]
        if a.get("count", 0) > 0
        and not a.get("controlled_by_cooking_module", False)
    ]


def get_active_meals(household, day_type="weekday"):
    """
    Return meals that generate electrical load for a given day type.

    Parameters
    ----------
    household : dict
    day_type : str
        "weekday" or "weekend"

    Returns
    -------
    List of meal dicts where cooked_at_home is True for the given day
    type and electric_fraction > 0.
    """
    meals = household.get("cooking", {}).get("meals", [])
    result = []
    for meal in meals:
        if day_type == "weekday":
            home = meal.get("cooked_at_home_weekday",
                            meal.get("cooked_at_home", False))
        else:
            home = meal.get("cooked_at_home_weekend",
                            meal.get("cooked_at_home", False))

        if home and meal.get("electric_fraction", 0.0) > 0:
            result.append(meal)
    return result


def get_active_bulbs(household):
    """Return only bulb entries with count > 0."""
    return [b for b in household["bulbs"] if b.get("count", 0) > 0]


def estimate_daily_energy_kwh(household):
    """
    Rough deterministic estimate of daily energy consumption.
    Used for sanity checking — NOT the model output.

    Includes both standard appliances and a cooking module estimate.

    Returns
    -------
    dict with 'weekday_kwh', 'weekend_kwh', 'weighted_kwh'
    """
    def estimate_for_day(day_type):
        total_wh = 0.0

        # Standard appliances via tou_hourly
        for appl in get_standard_appliances(household):
            tou  = appl["tou_hourly"]
            mean = appl["mean_duration_min"]
            pwr  = appl["rated_power_w"]
            energy_wh = 0.0
            for tou_h in tou:
                energy_wh += tou_h * (mean / 60.0) * pwr
            total_wh += appl["count"] * energy_wh

        # Lighting
        for bulb in get_active_bulbs(household):
            start = bulb["usage_start_hour"]
            end   = bulb["usage_end_hour"]
            hours = (end - start) if end >= start else (24 - start) + end
            energy_wh = (
                bulb["count"] *
                bulb["wattage_w"] *
                hours *
                bulb["p_on_occupied"]
            )
            total_wh += energy_wh

        # Cooking module estimate
        for meal in get_active_meals(household, day_type):
            appl_name = meal.get("appliance_used")
            if appl_name is None:
                continue
            # Find rated power
            matching = [a for a in household["appliances"]
                        if a.get("name") == appl_name and a.get("count", 0) > 0]
            if not matching:
                continue
            pwr_w = matching[0]["rated_power_w"]
            energy_kwh = (
                meal["energy_per_capita_kwh"] *
                meal["n_people_fed"] *
                meal["proportionality_factor"] *
                meal["electric_fraction"]
            )
            total_wh += energy_kwh * 1000

        return total_wh / 1000  # to kWh

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
    print(f"  Tier         : {h['tier']}")
    print(f"  Residents    : {h['n_residents']}")
    print(f"  Location     : {h['location']['sub_county']}, "
          f"{h['location']['county']}")
    print()

    print("OCCUPANCY (expected residents home by hour):")
    print("  Weekday:", h["occupancy_weekday"])
    print("  Weekend:", h["occupancy_weekend"])
    print()

    std_appl = get_standard_appliances(h)
    print(f"STANDARD APPLIANCES ({len(std_appl)} active, tou_hourly driven):")
    for a in std_appl:
        print(f"  {a['name']:35s} x{a['count']}  "
              f"{a['rated_power_w']:>6.0f}W  "
              f"{a['mean_duration_min']:>4d}min avg")

    print()
    cook_appl = get_cooking_module_appliances(h)
    print(f"COOKING MODULE APPLIANCES ({len(cook_appl)} active):")
    for a in cook_appl:
        print(f"  {a['name']:35s} x{a['count']}  "
              f"{a['rated_power_w']:>6.0f}W  [cooking module]")

    print()
    print("COOKING MEALS:")
    for meal in h.get("cooking", {}).get("meals", []):
        home_wd = meal.get("cooked_at_home_weekday", meal.get("cooked_at_home"))
        home_we = meal.get("cooked_at_home_weekend", meal.get("cooked_at_home"))
        ef = meal.get("electric_fraction", 0)
        print(f"  {meal['meal_id']:20s} "
              f"WD:{str(home_wd):5s} WE:{str(home_we):5s} "
              f"fuel:{meal.get('primary_fuel','?'):10s} "
              f"elec:{ef:.0%}  "
              f"appliance:{meal.get('appliance_used','none')}")

    print()
    active_bulbs = get_active_bulbs(h)
    print(f"LIGHTING ({len(active_bulbs)} zones):")
    for b in active_bulbs:
        print(f"  {b['room']:30s} x{b['count']} bulb(s)  "
              f"{b['wattage_w']}W  "
              f"{b['usage_start_hour']:02d}:00–{b['usage_end_hour']:02d}:00")

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

    # ── Test 2: tier is None at intake ────────────────────────────────────────
    if REFERENCE_HOUSEHOLD["tier"] is None:
        print("[PASS] Test 2: tier is None at intake (correct).")
    else:
        print(f"[FAIL] Test 2: tier should be None, got '{REFERENCE_HOUSEHOLD['tier']}'.")

    # ── Test 3: Cooking module helpers work ───────────────────────────────────
    print()
    wd_meals = get_active_meals(REFERENCE_HOUSEHOLD, "weekday")
    we_meals = get_active_meals(REFERENCE_HOUSEHOLD, "weekend")
    print(f"[INFO] Test 3: Active weekday meals with electric load: "
          f"{[m['meal_id'] for m in wd_meals]}")
    print(f"[INFO] Test 3: Active weekend meals with electric load: "
          f"{[m['meal_id'] for m in we_meals]}")
    if len(wd_meals) >= 1:
        print("[PASS] Test 3: get_active_meals() returns results.")
    else:
        print("[FAIL] Test 3: No active weekday meals found.")

    # ── Test 4: Standard vs cooking module appliance separation ───────────────
    std  = get_standard_appliances(REFERENCE_HOUSEHOLD)
    cook = get_cooking_module_appliances(REFERENCE_HOUSEHOLD)
    print(f"\n[INFO] Test 4: Standard appliances: {len(std)}")
    print(f"[INFO] Test 4: Cooking module appliances: {len(cook)}")
    if len(cook) >= 1:
        print("[PASS] Test 4: Cooking module appliances correctly separated.")
    else:
        print("[WARN] Test 4: No cooking module appliances active (count>0). "
              "Expected at least electric_hotplate for reference household.")

    # ── Test 5: Print summary ─────────────────────────────────────────────────
    print()
    print_household_summary(REFERENCE_HOUSEHOLD)

    # ── Test 6: assign_tier() ─────────────────────────────────────────────────
    import copy
    print()
    print("Testing assign_tier():")

    h_low = copy.deepcopy(REFERENCE_HOUSEHOLD)
    assign_tier(h_low, {"weighted_daily_energy_kwh": 3.2})
    print(f"  [{'PASS' if h_low['tier'] == 'low' else 'FAIL'}] "
          f"6a: 3.2 kWh/day → tier = '{h_low['tier']}'")

    h_med = copy.deepcopy(REFERENCE_HOUSEHOLD)
    assign_tier(h_med, {"weighted_daily_energy_kwh": 9.7})
    print(f"  [{'PASS' if h_med['tier'] == 'medium' else 'FAIL'}] "
          f"6b: 9.7 kWh/day → tier = '{h_med['tier']}'")

    h_high = copy.deepcopy(REFERENCE_HOUSEHOLD)
    assign_tier(h_high, {"weighted_daily_energy_kwh": 22.1})
    print(f"  [{'PASS' if h_high['tier'] == 'high' else 'FAIL'}] "
          f"6c: 22.1 kWh/day → tier = '{h_high['tier']}'")

    h_boundary = copy.deepcopy(REFERENCE_HOUSEHOLD)
    assign_tier(h_boundary, {"weighted_daily_energy_kwh": 5.0})
    print(f"  [{'PASS' if h_boundary['tier'] == 'medium' else 'FAIL'}] "
          f"6d: 5.0 kWh/day (boundary) → tier = '{h_boundary['tier']}'")

    try:
        assign_tier(h_low, {"weighted_daily_energy_kwh": 8.0})
        print("  [FAIL] 6e: Should have raised ValueError on double-assign")
    except ValueError:
        print("  [PASS] 6e: Double-assign correctly raises ValueError")

    # ── Test 7: Bad household rejected ───────────────────────────────────────
    bad = {
        "household_id": "",
        "tier": "ultra",
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
                "controlled_by_cooking_module": "yes",  # invalid
                "count": 1,
                "rated_power_w": -100,
                "tou_hourly": [0.5]*24,
                "mean_duration_min": 0,
                "std_duration_min": 100,
                "needs_occupancy": "yes"
            }
        ],
        "cooking": {
            "primary_cooking_fuel": "fire",  # invalid
            "meals": []                      # empty — invalid
        },
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
            "pv_panel_kes_per_kw": 0,
            "battery_kes_per_kwh": 0,
            "inverter_kes_per_kw": 0,
            "bos_kes": 0
        }
    }

    print()
    print("Testing validator with deliberately bad household:")
    try:
        validate_household(bad)
        print("[FAIL] Test 7: Bad household incorrectly passed validation.")
    except ValueError as e:
        print(f"[PASS] Test 7: Bad household correctly rejected:\n{e}")

    print("\nAll schema self-tests complete.")
