"""
make_test_households.py
=======================
Ten MADE-UP households (H901-H910) for typing into the Kobo form by hand, so
the whole Kobo -> fetch_kobo.py -> survey_to_schema.py path gets tested end
to end. They are not survey data. Between them they cover every scenario the
v5 form and the converter can produce (off grid, postpaid, unknown
phase/tariff, every backup, every appliance and choice, blank labels,
deliberate warnings, an interviewer-error case); see coverage.txt.

What this script does, using the CURRENT form and converter of this repo
(questionnaire/household_survey.xlsx, survey_to_schema.py; read only):

1. Builds each household as a Kobo-style submission.
2. Checks it against household_survey.xlsx the way Kobo would: every shown
   required question answered, nothing answered that Kobo would hide, every
   constraint met, every choice valid.
3. Writes them as a Kobo-style export (group paths), reads it back with
   survey_to_schema.load_submissions(), converts each, and requires
   validate_household() PASS and EXACTLY the warnings listed in the
   household's expect= (nothing unexpected, nothing missing). Prints the
   energy estimate next to the bill.
3b. Coverage: every choice of every question picked somewhere, every question
   shown somewhere and every conditional one hidden somewhere, and every
   line of survey_to_schema.py run (bar a short, reasoned exclusion list).
   Writes coverage.txt; exits non-zero on any failure or gap.
4. Writes the entry sheets (in form order, with Kobo's own labels) as HTML
   and PDF.

Run (from anywhere, in the load-model conda env):
    python questionnaire/test_households/make_test_households.py
Outputs written next to this script: test_households_kobo.json (reference
answers), entry_sheets.html, coverage.txt and converted/ (the last three are
regenerated each run and git-ignored). To make the PDF from the HTML:
    google-chrome --headless --no-pdf-header-footer \
        --print-to-pdf=test_households_entry_sheets.pdf entry_sheets.html
"""
import contextlib
import html
import io
import json
import math
import re
import sys
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent          # questionnaire/test_households/
REPO = Path(__file__).resolve().parents[2]      # load-model root
FORM = REPO / "questionnaire" / "household_survey.xlsx"
sys.path.insert(0, str(REPO))
import schema  # noqa: E402
from survey_to_schema import submission_to_household, write_outputs  # noqa: E402

# =============================================================================
# Building submissions (keys are the form's own field names)
# =============================================================================

def people(**counts):
    """counts: category=(n, weekday_away, weekend_away or 'same').
    away = (start, end) or None for home all day."""
    s = {}
    for cat in ("adults_working", "adults_non_working", "household_helpers",
                "school_children", "young_children", "elderly"):
        n, wd, we = counts.get(cat, (0, None, "same"))
        s[cat] = str(n)
        if not n:
            continue
        def away(day, win):
            if win is None:
                return {f"{cat}_{day}_away_start": "home_all_day"}
            return {f"{cat}_{day}_away_start": str(win[0]), f"{cat}_{day}_away_end": str(win[1])}
        s.update(away("weekday", wd))
        s[f"{cat}_weekend_same"] = "yes" if we == "same" else "no"
        if we != "same":
            s.update(away("weekend", we))
    s["number_of_residents"] = str(sum(int(v) for k, v in s.items() if k in
                                       ("adults_working", "adults_non_working", "household_helpers",
                                        "school_children", "young_children", "elderly")))
    return s


def room(rtype, bulbs, btype="LED", watts=9, weekday=(), weekend="same", pattern=True,
         other=None, security=None):
    r = {"room_type": rtype}
    if other:
        r["room_other"] = other
    r["bulbs"] = str(bulbs)
    if bulbs:
        if watts is not None:            # None = wattage not readable, left blank
            r["bulb_watts"] = str(watts)
        r["bulb_type"] = btype
    if rtype == "outside_security":
        if bulbs:
            r["security_mode"] = security[0]
            if security[0] in ("timer", "manual"):
                r["security_light_on"], r["security_light_off"] = str(security[1]), str(security[2])
        return r
    r["room_has_pattern"] = "yes" if pattern else "no"
    if not pattern:
        return r
    r["room_weekday"] = [{"room_weekday_start": str(a), "room_weekday_end": str(b)} for a, b in weekday]
    if weekend == "same":
        r["room_weekend_same"] = "yes"
    else:
        r["room_weekend_same"] = "no"
        r["room_weekend"] = [{"room_weekend_start": str(a), "room_weekend_end": str(b)} for a, b in weekend]
    return r


ALWAYS_ON = {"refrigerator", "chest_freezer", "wifi_router", "electric_fence_energiser", "cctv_system"}


def appl(atype, watts, count=1, minutes=None, wd="never", wdw=(), wds=1, we="never", wew="same", wes=1,
         other=None, other_always_on="no", other_home="yes", volts=None, amps=None):
    """windows: (start, end, peak, often); peak 'spread' or an hour.
    watts=None: no label watts; then volts/amps if given (amps only shows
    once volts is entered), else all blank (no readable label)."""
    a = {"appliance_type": atype}
    if atype == "other":
        a["appliance_other"] = other
        a["appliance_other_always_on"] = other_always_on
        if other_always_on == "no":
            a["appliance_other_home"] = other_home
    a["appliance_count"] = str(count)
    if watts is not None:
        a["label_watts"] = str(watts)
    else:
        if volts is not None:
            a["label_volts"] = str(volts)
            if amps is not None:
                a["label_amps"] = str(amps)
    if atype in ALWAYS_ON or (atype == "other" and other_always_on == "yes"):
        return a

    def win(day, ws):
        p = f"appliance_{day}_"
        return [{p + "start": str(s), p + "end": str(e), p + "peak": str(pk), p + "often": o}
                for s, e, pk, o in ws]

    a["duration_typical"] = str(minutes)
    a["weekday_frequency"] = wd
    if wd != "never":
        a["appliance_weekday"] = win("weekday", wdw)
        a["appliance_weekday_sessions"] = str(wds)
    a["weekend_frequency"] = we
    if we != "never":
        if wd != "never":
            a["weekend_same_as_weekday"] = "yes" if wew == "same" else "no"
        if wew != "same" or wd == "never":
            a["appliance_weekend"] = win("weekend", wew)
        a["appliance_weekend_sessions"] = str(wes)
    return a


def grid(spend, token_kes, token_kwh, tariff="domestic", phase="single", blackout=2,
         backup="none", metering="prepaid", **extra):
    """token_kes/token_kwh None = no token reading (prepaid) or not asked (postpaid)."""
    g = {"grid_connected": "yes", "phase": phase, "metering": metering, "tariff": tariff,
         "monthly_spend": str(spend), "blackout_hours": str(blackout), "backup": backup}
    if metering == "prepaid" and token_kwh is not None:
        g["token_kwh"] = str(token_kwh)
        if token_kes is not None:
            g["token_kes"] = str(token_kes)
    g.update({k: str(v) for k, v in extra.items()})
    return g


def off_grid(backup, **extra):
    g = {"grid_connected": "no", "backup": backup}
    g.update({k: str(v) for k, v in extra.items()})
    return g


def site(length, width, orientation="north_facing", tilt=15, shading="minimal",
         roof="iron_sheet", mounting="flush", cable=10):
    return {"roof_length": str(length), "roof_width": str(width), "orientation": orientation,
            "tilt": str(tilt), "shading": shading, "roof_type": roof, "mounting": mounting,
            "cable_length_meters": str(cable)}


def household(hid, sub_county, gps, dwelling, ppl, rooms, appliances, grd, ste, story,
              willing="yes", routine="", county="Nairobi", expect=()):
    """expect: the warnings this household is MEANT to produce, each a
    substring of exactly one or more report warnings. The checker fails on
    any warning not matched here, and on any expected warning that is missing."""
    s = {"consent": "OK", "household_id": hid, "survey_date": "2026-11-20",
         "sub_county": sub_county, "county": county, "gps": gps, "dwelling": dwelling}
    s.update(ppl)
    s["room"] = rooms
    s["appliance"] = appliances
    s.update(grd)
    s.update(ste)
    s["logger_willing"] = willing
    if routine:
        s["routine_change"] = routine
    s["interviewer_notes"] = f"MADE-UP TEST HOUSEHOLD. {story}"
    s["_story"] = story
    s["_expect"] = list(expect)
    return s


# =============================================================================
# The ten households
# =============================================================================
A, U, S, R = "always", "usually", "sometimes", "rarely"
EVERY, MOST, FEW, ONCE, RARE = "every_weekday", "most_weekdays", "few_times_a_week", "once_a_week", "rarely"
BOTH, ONE, OCC = "both_days", "usually_one_day", "occasionally"

HOUSEHOLDS = [
    # Ten households, between them covering every choice, every shown/hidden
    # question and every converter path the v5 form can produce. Each lists
    # in expect= the warnings it is MEANT to produce (H901, H904, H905, H909
    # produce some on purpose; the others none).

    # ------------------------------------------------------------ H901
    household(
        "H901", "Kasarani", "-1.2215 36.8968 1610 5", "flat",
        people(adults_working=(1, (7, 22), "same")),
        [room("other", 1, other="Bedsitter main room", weekday=[(8, 9)]),
         room("bathroom", 1, watts=5, pattern=False)],
        [appl("smartphone_charger", 10, minutes=90, wd=EVERY, wdw=[(22, 24, "spread", A)], we=BOTH),
         appl("electric_kettle", 1500, minutes=3, wd=EVERY, wdw=[(5, 8, 6, A)], we=BOTH)],
        grid(200, 200, 16.5, tariff="lifeline", blackout=1),
        site(4, 3, roof="concrete", tilt=0, orientation="flat", cable=15),
        "Interviewer-error case: a security guard in a bedsitter, away 07:00-22:00 every day, whose "
        "room time was keyed as 08:00 instead of 20:00, so the only room time is when nobody is home. "
        "The night run then covers every hour and the no-pattern bathroom has no awake hours to light. "
        "Lifeline tariff.",
        willing="no",
        expect=["bathroom: no awake at-home hours to spread visits over, light left off"]),

    # ------------------------------------------------------------ H902
    household(
        "H902", "Dagoretti North", "-1.2903 36.7843 1745 5", "flat",
        people(adults_working=(2, (7, 18), None)),
        [room("living_room", 2, weekday=[(18, 23)], weekend=[(9, 23)]),
         room("master_bedroom", 1, weekday=[(6, 7), (22, 23)], weekend=[(8, 9), (22, 24)]),
         room("kitchen", 1, watts=12, weekday=[(6, 7), (18, 20)], weekend=[(8, 10), (13, 14), (18, 20)]),
         room("bathroom", 1, watts=5, pattern=False)],
        [appl("refrigerator", 90),
         appl("wifi_router", 10),
         appl("electric_kettle", 1800, minutes=4, wd=EVERY, wdw=[(5, 8, 6, A), (19, 22, 20, S)], wds=2,
              we=BOTH, wew=[(8, 11, 9, A), (16, 19, "spread", U)], wes=2),
         appl("microwave", 800, minutes=3, wd=EVERY, wdw=[(19, 21, 19, A)],
              we=BOTH, wew=[(12, 14, 13, A), (19, 21, 19, A)], wes=2),
         appl("television", 85, minutes=180, wd=EVERY, wdw=[(19, 23, 21, A)],
              we=BOTH, wew=[(10, 23, 20, A)]),
         appl("dstv_decoder", 15, minutes=180, wd=EVERY, wdw=[(19, 23, 21, A)],
              we=BOTH, wew=[(10, 23, 20, A)]),
         appl("laptop", 65, count=2, minutes=120, wd=FEW, wdw=[(20, 23, "spread", A)],
              we=BOTH, wew=[(10, 17, "spread", A)]),
         appl("smartphone_charger", 10, count=2, minutes=120, wd=EVERY, wdw=[(21, 24, "spread", A)], we=BOTH),
         appl("washing_machine", 450, minutes=75, we=ONE, wew=[(9, 13, 10, A)])],
        grid(1800, 1000, 32.3), site(10, 8, roof="concrete", tilt=0, orientation="flat", cable=25),
        "Young couple, one-bedroom flat in Kilimani, both work in town."),

    # ------------------------------------------------------------ H903
    household(
        "H903", "Embakasi West", "-1.2836 36.8961 1625 5", "flat",
        people(adults_working=(1, (6, 19), None), adults_non_working=(1, (9, 13), "same"),
               school_children=(3, (7, 16), None)),
        [room("living_room", 2, weekday=[(16, 22)], weekend=[(9, 22)]),
         room("master_bedroom", 1, weekday=[(5, 6), (22, 23)]),
         room("children_bedroom", 1, weekday=[(6, 7), (20, 21)], weekend=[(8, 9), (20, 22)]),
         room("kitchen", 1, watts=12, weekday=[(5, 7), (18, 21)], weekend=[(7, 9), (12, 14), (18, 21)]),
         room("bathroom", 1, watts=5, weekday=[(5, 7), (19, 22)], weekend=[(7, 10), (19, 22)]),
         room("staircase_corridor", 1, watts=5, pattern=False)],
        [appl("refrigerator", 110),
         appl("television", 75, minutes=240, wd=EVERY, wdw=[(16, 22, 20, A)],
              we=BOTH, wew=[(9, 22, 20, A)]),
         appl("dstv_decoder", 12, minutes=240, wd=EVERY, wdw=[(16, 22, 20, A)],
              we=BOTH, wew=[(9, 22, 20, A)]),
         appl("electric_kettle", 1800, minutes=4, wd=EVERY, wdw=[(5, 8, 6, A), (18, 21, "spread", U)], wds=2,
              we=BOTH, wes=2),
         appl("iron_box", 1200, minutes=20, wd=FEW, wdw=[(5, 7, 5, A)], we=ONE,
              wew=[(14, 17, "spread", A)]),
         appl("smartphone_charger", 10, count=2, minutes=120, wd=EVERY, wdw=[(19, 24, "spread", A)], we=BOTH),
         appl("blender", 350, minutes=3, wd=FEW, wdw=[(6, 7, "spread", A)], we=OCC,
              wew=[(8, 11, "spread", A)])],
        grid(2000, 1000, 32.3), site(9, 7, orientation="east_facing", roof="concrete", tilt=0, cable=20),
        "Family of five in a two-bedroom flat in Umoja: father works long days, mother sells at the "
        "market 09:00-13:00 every day including weekends; cooks on gas."),

    # ------------------------------------------------------------ H904
    household(
        "H904", "Lang'ata", "-1.3418 36.7592 1790 5", "maisonette",
        people(adults_working=(2, (7, 19), None), household_helpers=(1, None, (14, 18)),
               school_children=(2, (7, 17), None), young_children=(1, None, (10, 16))),
        [room("living_room", 4, weekday=[(9, 12), (16, 22)], weekend=[(9, 22)]),
         room("dining_area", 2, weekday=[(7, 8), (13, 14), (19, 21)]),
         room("master_bedroom", 2, weekday=[(6, 7), (22, 23)]),
         room("children_bedroom", 2, weekday=[(6, 7), (20, 21)]),
         room("bedroom", 1, weekday=[(5, 6), (21, 22)]),
         room("kitchen", 2, watts=18, btype="fluorescent", weekday=[(6, 9), (12, 14), (17, 21)]),
         room("kitchen", 1, weekday=[(13, 14), (19, 20)]),
         room("bathroom", 1, watts=5, pattern=False),
         room("bathroom", 1, watts=5, pattern=False),
         room("bathroom", 1, watts=5, pattern=False),
         room("store_room", 1, pattern=False),
         room("outside_security", 4, watts=20, security=("motion",)),
         room("outside_security", 2, watts=15, security=("dusk_to_dawn",))],
        [appl("refrigerator", 160),
         appl("chest_freezer", 120),
         appl("wifi_router", 12),
         appl("cctv_system", 35),
         appl("electric_fence_energiser", 20),
         appl("borehole_pump", 1100, minutes=45, wd=EVERY, wdw=[(6, 9, 7, A)], we=BOTH),
         appl("immersion_water_heater", 4500, minutes=8, wd=EVERY, wdw=[(5, 7, 6, A), (19, 21, 20, S)],
              wds=4, we=BOTH, wew=[(7, 10, 8, A)], wes=4),
         appl("induction_cooker", 2000, minutes=30, wd=EVERY, wdw=[(12, 14, 13, A), (18, 20, 19, A)], wds=2,
              we=BOTH, wes=2),
         appl("microwave", 900, minutes=4, wd=EVERY, wdw=[(7, 9, "spread", U), (13, 14, "spread", A)], wds=2,
              we=BOTH, wes=2),
         appl("toaster", None, volts=230, amps=3.5, minutes=4, wd=EVERY, wdw=[(6, 8, 6, A)], we=BOTH,
              wew=[(8, 10, 9, A)]),
         appl("rice_cooker", 500, minutes=40, wd=FEW, wdw=[(17, 20, 18, A)], we=ONE, wew=[(12, 14, 12, A)]),
         appl("electric_oven", 2200, minutes=45, we=ONE, wew=[(12, 14, 13, A)]),
         appl("air_conditioner", 1200, minutes=120, wd=FEW, wdw=[(21, 1, 22, A)], we=OCC,
              wew=[(13, 16, 14, A)]),
         appl("ceiling_fan", 60, count=2, minutes=180, wd=EVERY, wdw=[(13, 17, 15, U), (20, 23, 21, A)],
              wds=2, we=BOTH, wes=2),
         appl("television", 110, minutes=300, wd=EVERY, wdw=[(9, 22, 20, A)], we=BOTH),
         appl("dstv_decoder", 18, minutes=300, wd=EVERY, wdw=[(9, 22, 20, A)], we=BOTH),
         appl("television", 60, minutes=60, wd=MOST, wdw=[(20, 24, 21, A)], we=BOTH),
         appl("television", None, minutes=120, wd=MOST, wdw=[(20, 23, 21, A)], we=BOTH),
         appl("gaming_console", 150, minutes=120, wd=FEW, wdw=[(17, 20, "spread", A)], we=BOTH,
              wew=[(10, 18, "spread", A)]),
         appl("washing_machine", 500, minutes=90, wd=FEW, wdw=[(9, 12, 10, A)], we=ONE,
              wew=[(9, 12, 10, A)]),
         appl("vacuum_cleaner", 1400, minutes=30, wd=RARE, wdw=[(10, 12, "spread", A)], we=ONE,
              wew=[(9, 12, 10, A)]),
         appl("iron_box", 1200, minutes=30, wd=FEW, wdw=[(14, 17, "spread", A)], we="never"),
         appl("electric_shaver", None, minutes=5, wd=EVERY, wdw=[(6, 8, 6, A)], we=ONE,
              wew=[(8, 11, "spread", A)]),
         appl("gate_motor", 250, minutes=1, wd=EVERY, wdw=[(6, 8, 7, A), (17, 20, 19, A)], wds=6,
              we=BOTH, wes=4),
         appl("other", 15, other="Aquarium pump", other_always_on="yes")],
        grid(9500, None, None, metering="postpaid", tariff="dont_know", phase="dont_know", blackout=3,
             backup="generator", backup_kilowatts=7.5,
             backup_loads="Borehole pump, fridge, freezer, lights, gate and CCTV"),
        site(14, 9, orientation="west_facing", roof="clay_tile", tilt=25, shading="severe",
             mounting="ground", cable=40),
        "Large maisonette family with a borehole and generator, on a monthly KPLC bill (tariff and phase "
        "not known); live-in house help; toddler at grandmother's on Saturdays; instant showers used "
        "four times each morning; three TVs, one with no label.",
        routine="Away 20 Dec to 3 Jan (upcountry for Christmas).",
        expect=["grid.phase unknown, assumed single-phase",
                "No token reading, import tariff assumed",
                "motion-sensor lights modelled",
                "electric_shaver: no label reading, used typical",
                "television_3: no label reading, used typical"]),

    # ------------------------------------------------------------ H905
    household(
        "H905", "Kajiado North", "-1.3962 36.7518 1785 8", "bungalow",
        people(elderly=(2, (9, 12), (8, 11)), adults_non_working=(1, (9, 13), None),
               school_children=(2, None, (8, 12))),
        [room("living_room", 1, btype="incandescent", watts=60, pattern=False),
         room("master_bedroom", 1, btype="incandescent", watts=40, pattern=False),
         room("children_bedroom", 1, watts=3, pattern=False),
         room("kitchen", 1, btype="CFL", watts=11, pattern=False),
         room("store_room", 0, pattern=False),
         room("other", 1, watts=5, other="Shop front", pattern=False),
         room("outside_security", 1, btype="CFL", watts=11, security=("timer", 19, 23))],
        [appl("smartphone_charger", 5, count=3, minutes=120, wd=EVERY, wdw=[(10, 16, "spread", A)], we=BOTH),
         appl("power_bank_charging", 10, minutes=180, wd=EVERY, wdw=[(10, 15, 12, A)], we=BOTH),
         appl("tablet_charger", 12, minutes=120, wd=ONCE, wdw=[(16, 19, "spread", A)], we=OCC,
              wew=[(10, 14, "spread", A)]),
         appl("bluetooth_speaker", 8, minutes=120, wd=EVERY, wdw=[(6, 9, 7, A), (19, 21, 20, U)], wds=2,
              we=BOTH, wes=2),
         appl("television", 20, minutes=180, wd=EVERY, wdw=[(18, 22, 19, A), (10, 13, "spread", R)],
              we=BOTH, wew=[(14, 22, 19, A)])],
        off_grid("solar_only", solar_panel_count=2, solar_panel_watts=150, backup_kilowatts=0.3,
                 backup_loads="Everything: lights, phones, radio, small TV"),
        site(9, 7, orientation="south_facing", tilt=20, shading="none", roof="other",
             mounting="elevated", cable=8),
        "Off-grid retired couple in Kajiado on solar panels only, with their son and two grandchildren: "
        "the couple goes to the market on weekdays and church on Sunday morning, the grandchildren do "
        "online school at home and Saturday tuition 08:00-12:00, a small shop at the front, old "
        "filament bulbs, no fixed times for any room.",
        county="Kajiado", willing="maybe",
        expect=["occupancy weekday: no room presence recorded",
                "occupancy weekend: no room presence recorded"]),

    # ------------------------------------------------------------ H906
    household(
        "H906", "Roysambu", "-1.2189 36.8792 1600 5", "bungalow",
        people(adults_working=(2, (7, 18), None), school_children=(2, (7, 16), None),
               young_children=(1, None, "same"), elderly=(1, (9, 11), None)),
        [room("living_room", 3, weekday=[(9, 12), (16, 22)], weekend=[(9, 22)]),
         room("master_bedroom", 1, weekday=[(6, 7), (22, 23)]),
         room("children_bedroom", 1, weekday=[(6, 7), (20, 21)]),
         room("kitchen", 1, watts=12, weekday=[(6, 7), (12, 13), (17, 20)]),
         room("bathroom", 2, watts=5, weekday=[(6, 7), (20, 22)], weekend=[(8, 10), (20, 22)]),
         room("outside_security", 2, watts=10, security=("timer", 18, 6))],
        [appl("refrigerator", 100),
         appl("wifi_router", 10),
         appl("solar_water_heater_pump", 40, minutes=120, wd=EVERY, wdw=[(10, 15, 12, A)], we=BOTH),
         appl("water_pump", 750, minutes=20, wd=EVERY, wdw=[(6, 8, 6, A), (18, 20, "spread", U)], wds=2,
              we=BOTH, wes=2),
         appl("television", 70, minutes=210, wd=EVERY, wdw=[(16, 22, 20, A)],
              we=BOTH, wew=[(9, 22, 20, A)]),
         appl("electric_pressure_cooker", 1000, minutes=40, wd=MOST, wdw=[(17, 20, 18, A)], we=BOTH),
         appl("electric_kettle", 1800, minutes=4, wd=EVERY, wdw=[(5, 8, 6, A)], we=BOTH,
              wew=[(8, 11, 9, A)]),
         appl("laptop", 45, minutes=120, wd=MOST, wdw=[(19, 22, "spread", A)], we=BOTH,
              wew=[(10, 17, "spread", A)]),
         appl("smartphone_charger", 10, count=2, minutes=120, wd=EVERY, wdw=[(19, 24, "spread", A)], we=BOTH),
         appl("washing_machine", 500, minutes=90, we=ONE, wew=[(9, 13, 10, A)])],
        grid(1800, 1000, 32.3, backup="solar_battery", solar_panel_count=4, solar_panel_watts=400,
             battery_volts=12, battery_amp_hours=200, battery_units=2, backup_kilowatts=3,
             backup_loads="Lights, fridge, TV, Wi-Fi"),
        site(12, 8, orientation="north_facing", tilt=15, shading="none", cable=12),
        "Family of six with rooftop solar and batteries (4 x 400 W, 2 x 12 V 200 Ah, 3 kW inverter): "
        "toddler and grandmother at home (she goes to the clinic 09:00-11:00 on weekdays); grid bill "
        "is low because solar covers the day."),

    # ------------------------------------------------------------ H907
    household(
        "H907", "Embakasi South", "-1.3172 36.8945 1620 5", "flat",
        people(adults_working=(1, (7, 18), None), adults_non_working=(1, (18, 8), (19, 7))),
        [room("living_room", 2, weekday=[(9, 13), (18, 22)], weekend=[(10, 22)]),
         room("master_bedroom", 1, weekday=[(6, 7), (22, 23)], weekend=[(8, 9), (22, 23)]),
         room("kitchen", 1, watts=12, weekday=[(8, 9), (14, 15), (18, 20)]),
         room("bathroom", 1, watts=5, pattern=False)],
        [appl("refrigerator", 90),
         appl("television", 60, minutes=150, wd=EVERY, wdw=[(9, 14, 10, U), (19, 22, 20, A)], wds=2,
              we=BOTH, wew=[(10, 22, 20, A)]),
         appl("electric_kettle", 1500, minutes=4, wd=EVERY, wdw=[(5, 7, 6, A), (8, 11, 8, A)], wds=2,
              we=BOTH, wes=2),
         appl("electric_hotplate", 1500, minutes=30, wd=MOST, wdw=[(13, 16, 14, A), (18, 21, 19, U)],
              we=BOTH, wew=[(12, 15, 13, A), (18, 21, 19, A)], wes=2),
         appl("smartphone_charger", 10, count=2, minutes=120, wd=EVERY, wdw=[(9, 13, "spread", A)], we=BOTH),
         appl("iron_box", 1200, minutes=20, wd=FEW, wdw=[(16, 18, "spread", A)], we="never")],
        grid(2200, 1000, 32.3, blackout=3), site(8, 6, roof="concrete", orientation="flat", tilt=0, cable=22),
        "Couple in Pipeline: a nurse on night shift (18:00 to 08:00) who is home and asleep in the day, "
        "and a partner on day shift.", willing="maybe"),

    # ------------------------------------------------------------ H908
    household(
        "H908", "Roysambu", "-1.2178 36.8890 1605 5", "flat",
        people(adults_working=(3, (8, 17), (12, 18))),
        [room("living_room", 2, weekday=[(18, 24)], weekend=[(9, 12), (18, 24)]),
         room("bedroom", 1, weekday=[(6, 7), (22, 24)]),
         room("bedroom", 1, weekday=[(6, 7), (22, 24)]),
         room("bedroom", 1, weekday=[(6, 7), (23, 24)]),
         room("kitchen", 1, weekday=[(7, 8), (19, 21)]),
         room("bathroom", 1, watts=5, pattern=False)],
        [appl("wifi_router", 10),
         appl("laptop", 60, count=3, minutes=180, wd=EVERY, wdw=[(18, 24, "spread", A)],
              we=BOTH, wew=[(9, 12, "spread", A), (18, 24, 21, A)], wes=2),
         appl("smartphone_charger", 10, count=3, minutes=120, wd=EVERY, wdw=[(19, 24, "spread", A)], we=BOTH),
         appl("electric_kettle", 1500, minutes=4, wd=EVERY, wdw=[(6, 9, 7, A), (19, 22, 20, U)], wds=2,
              we=BOTH, wes=2),
         appl("electric_hotplate", 1000, minutes=25, wd=MOST, wdw=[(19, 21, 19, A)], we=BOTH),
         appl("bluetooth_speaker", 20, minutes=120, wd=FEW, wdw=[(19, 23, "spread", A)], we=BOTH,
              wew=[(9, 12, "spread", A)]),
         appl("iron_box", 1000, minutes=15, wd=EVERY, wdw=[(6, 9, 7, A)], we="never")],
        grid(1500, 1000, 32.3, blackout=3), site(10, 7, roof="concrete", orientation="flat", tilt=0, cable=30),
        "Three university students sharing a three-bedroom flat near USIU, out at lectures in the day.",
        willing="no", routine="Campus closes 12 Dec; flat empty until 5 Jan."),

    # ------------------------------------------------------------ H909
    household(
        "H909", "Embakasi Central", "-1.2779 36.9135 1615 5", "other",
        people(adults_working=(2, (6, 19), (8, 14)), adults_non_working=(1, None, (10, 16)),
               household_helpers=(1, (13, 15), None), school_children=(3, (6, 17), "same"),
               young_children=(1, (8, 17), None), elderly=(1, None, "same")),
        [room("living_room", 2, watts=None, btype="LED", weekday=[(9, 13), (16, 22), (20, 23)],
              weekend=[(8, 22)]),
         room("master_bedroom", 1, watts=None, btype="incandescent", pattern=False),
         room("children_bedroom", 0, weekday=[(5, 6), (20, 21)]),
         room("bedroom", 1, watts=None, btype="fluorescent", weekday=[(6, 7), (20, 21)]),
         room("bedroom", 0, pattern=False),
         room("kitchen", 1, watts=None, btype="CFL", weekday=[(5, 6), (12, 13), (17, 20)]),
         room("other", 1, other="Prayer room", weekday=[(2, 4), (17, 18)]),
         room("other", 1, other="Prayer room", pattern=False),
         room("bathroom", 1, watts=5, weekday=[(5, 7), (20, 21)], weekend=[(7, 9), (20, 21)]),
         room("outside_security", 1, watts=10, security=("manual", 19, 23))],
        [appl("refrigerator", 100),
         appl("television", 60, minutes=150, wd=EVERY, wdw=[(21, 2, 0, A), (12, 14, "spread", S)], wds=2,
              we=BOTH, wew=[(10, 13, 11, R), (15, 23, 20, S)], wes=2),
         appl("dstv_decoder", 12, minutes=150, wd=EVERY, wdw=[(9, 13, 11, S), (16, 22, 20, A)], wds=2,
              we=BOTH, wew=[(8, 22, 20, A)]),
         appl("electric_kettle", None, volts=230, minutes=4, wd=EVERY, wdw=[(4, 7, 5, A), (17, 20, "spread", U)],
              wds=2, we=BOTH, wew=[(7, 10, 8, A), (17, 20, "spread", U)], wes=2),
         appl("sewing_machine", 100, minutes=180, wd=EVERY, wdw=[(9, 16, "spread", A)], we=ONE,
              wew=[(9, 13, "spread", A)]),
         appl("smartphone_charger", 10, count=4, minutes=120, wd=EVERY, wdw=[(19, 24, "spread", A)], we=BOTH),
         appl("iron_box", 1000, minutes=30, wd=FEW, wdw=[(18, 20, "spread", A)], we=ONE,
              wew=[(10, 13, "spread", A)]),
         appl("other", 1500, minutes=60, other="Electric room heater", other_always_on="no", other_home="yes",
              wd=FEW, wdw=[(19, 23, 21, A)], we=OCC, wew=[(19, 23, 21, A)]),
         appl("other", None, minutes=30, other="Massage chair", other_always_on="no", other_home="yes",
              wd=RARE, wdw=[(18, 21, "spread", A)], we="never"),
         appl("other", 20, minutes=600, other="Aquarium light on a timer", other_always_on="no",
              other_home="no", wd=EVERY, wdw=[(8, 20, "spread", A)], we=BOTH),
         appl("washing_machine", 500, minutes=120, we=ONE, wew=[(9, 12, 9, A)], wes=3)],
        grid(2500, None, None, blackout=6, backup="inverter_battery", battery_volts=12,
             battery_amp_hours=100, battery_units=1, backup_kilowatts=1,
             backup_loads="Lights, TV, decoder"),
        site(10, 6, cable=10, shading="moderate"),
        "Debugging household, a family of nine in a Kayole stone row house: the aunt is home in the "
        "week, the house help is out 13:00-15:00 on weekdays, the children have Saturday and Sunday "
        "school, the toddler is at day care on weekdays, and grandmother prays 02:00-04:00 (someone "
        "up at 03:00, so no night is taken out). Unreadable bulbs of every type, two rooms with the "
        "same name, overlapping living-room times, a TV block across midnight, three 'other' "
        "appliances, a kettle label with volts only, and three back-to-back washes on Saturday that "
        "the model cannot fit. No token SMS to hand.",
        expect=["living_room: bulb wattage missing, assumed 9 W (typical LED)",
                "kitchen: bulb wattage missing, assumed 15 W (typical CFL)",
                "master_bedroom: bulb wattage missing, assumed 60 W (typical incandescent)",
                "bedroom_3: bulb wattage missing, assumed 36 W (typical fluorescent)",
                "NOT MODELLED: other_appliance_2 (Massage chair)",
                "electric_kettle: no label reading, used typical",
                "NOT MODELLED, check with the household and re-run: washing_machine (weekend)",
                "No token reading, import tariff assumed"]),

    # ------------------------------------------------------------ H910
    household(
        "H910", "Westlands", "-1.2641 36.8047 1720 5", "flat",
        people(adults_working=(2, None, (10, 14)), household_helpers=(1, (14, 16), "same")),
        [room("living_room", 3, weekday=[(18, 23)], weekend=[(9, 10), (16, 23)]),
         room("other", 2, other="Home office", weekday=[(8, 17)], weekend=[(10, 12)]),
         room("master_bedroom", 2, weekday=[(6, 7), (22, 24)]),
         room("kitchen", 2, watts=12, weekday=[(6, 8), (12, 13), (18, 20)]),
         room("bathroom", 2, watts=5, pattern=False)],
        [appl("refrigerator", 140),
         appl("wifi_router", 12),
         appl("desktop_computer", 180, count=2, minutes=480, wd=EVERY, wdw=[(8, 17, "spread", A)], we="never"),
         appl("printer", 20, minutes=10, wd=FEW, wdw=[(9, 17, "spread", A)], we="never"),
         appl("electric_kettle", 2000, minutes=4, wd=EVERY,
              wdw=[(5, 8, 6, A), (10, 13, 10, U), (15, 18, 16, S)], wds=2, we=BOTH, wes=2,
              wew=[(8, 11, 9, A), (16, 19, "spread", U)]),
         appl("electric_pressure_cooker", 1000, minutes=40, wd=EVERY, wdw=[(18, 20, 18, A)], we=BOTH),
         appl("microwave", 900, minutes=3, wd=EVERY, wdw=[(12, 14, 12, A)], we=OCC,
              wew=[(12, 15, "spread", A)]),
         appl("television", 100, minutes=180, wd=EVERY, wdw=[(19, 23, 21, A)], we=BOTH,
              wew=[(16, 23, 20, A)]),
         appl("washing_machine", 2000, minutes=90, we=BOTH, wew=[(9, 12, 9, A)]),
         appl("hair_dryer", 1800, minutes=10, wd=EVERY, wdw=[(5, 8, 6, A)], we=ONE, wew=[(8, 11, 9, A)]),
         appl("standing_fan", 45, minutes=120, wd=FEW, wdw=[(12, 16, 14, A)], we=OCC,
              wew=[(16, 20, "spread", A)])],
        grid(5000, 2000, 64.5, phase="three", blackout=1),
        site(12, 10, roof="concrete", orientation="flat", tilt=0, cable=35),
        "Professional couple in a Westlands apartment who both work from a home office on weekdays "
        "and go out 10:00-14:00 at weekends; a day house help is out 14:00-16:00 every day; three-phase "
        "supply; no TV decoder (streams on Wi-Fi).", willing="yes"),
]


# =============================================================================
# Reading the form
# =============================================================================

def read_form(path=FORM):
    wb = openpyxl.load_workbook(path)
    ws = wb["survey"]
    head = [c.value for c in ws[1]]
    rows = [dict(zip(head, r)) for r in ws.iter_rows(min_row=2, values_only=True)]
    choices = {}
    for lst, name, label in wb["choices"].iter_rows(min_row=2, values_only=True):
        choices.setdefault(lst, {})[str(name)] = label
    return rows, choices


def tree(rows):
    """Nest the flat survey rows into groups/repeats."""
    root = {"type": "root", "children": []}
    stack = [root]
    for r in rows:
        t = (r["type"] or "").strip()
        if t in ("begin_group", "begin_repeat"):
            node = dict(r, children=[], kind=t.split("_")[1])
            stack[-1]["children"].append(node)
            stack.append(node)
        elif t in ("end_group", "end_repeat"):
            stack.pop()
        else:
            stack[-1]["children"].append(dict(r, kind="field"))
    return root


# ------------------------------------------------- tiny XPath for this form
class V:
    """An XPath node value: string, compared XPath-style."""
    def __init__(self, v):
        self.v = "" if v is None else str(v)

    def _n(self):
        try:
            return float(self.v)
        except ValueError:
            return math.nan

    def _cmp(self, o, op):
        if isinstance(o, V):
            o = o.v
        if isinstance(o, (int, float)):
            a = self._n()
            return op(a, float(o))
        if op.__name__ in ("eq", "ne"):
            return op(self.v, o)
        return op(self._n(), float(o) if o not in ("",) else math.nan)

    import operator as _o
    def __eq__(self, o): return self._cmp(o, V._o.eq)
    def __ne__(self, o): return self._cmp(o, V._o.ne)
    def __gt__(self, o): return self._cmp(o, V._o.gt)
    def __ge__(self, o): return self._cmp(o, V._o.ge)
    def __lt__(self, o): return self._cmp(o, V._o.lt)
    def __le__(self, o): return self._cmp(o, V._o.le)
    def __bool__(self): return self.v != ""


def number_(x):
    return x._n() if isinstance(x, V) else float(x)


def not_(x):
    return not bool(x)


def regex_(x, pat):
    return re.search(pat, x.v) is not None


def xeval(expr, lookup, cur=None):
    if expr in (None, ""):
        return True
    e = str(expr)
    if e.strip() in ("yes", "true()"):
        return True
    e = re.sub(r"\$\{(\w+)\}", lambda m: f"V(lookup('{m.group(1)}'))", e)
    e = re.sub(r"(?<![\w'.])\.(?![\w])", "V(cur)", e)
    e = re.sub(r"(?<![!<>=])=(?!=)", "==", e)
    e = re.sub(r"\bnot\(", "not_(", e)
    e = re.sub(r"\bnumber\(", "number_(", e)
    e = re.sub(r"\bregex\(", "regex_(", e)
    return bool(eval(e, {"V": V, "lookup": lookup, "cur": cur, "number_": number_,
                         "not_": not_, "regex_": regex_}))


# =============================================================================
# Checking a submission the way Kobo would
# =============================================================================

def kobo_check(sub, form_tree, choices, seen_rel=None):
    """seen_rel, if given, collects (field name, shown?) for every question
    Kobo would show or hide in this submission (coverage of relevant=)."""
    errors = []
    seen = set()

    def calc(name, scopes):
        if name == "resident_count_sum":
            return str(sum(int(scopes[0].get(c, 0) or 0) for c in
                           ("adults_working", "adults_non_working", "household_helpers",
                            "school_children", "young_children", "elderly")))
        if name == "always_on":
            sc = scopes[-1]
            return "yes" if (sc.get("appliance_type") in ALWAYS_ON or
                             sc.get("appliance_other_always_on") == "yes") else "no"
        return None

    def make_lookup(scopes):
        def lookup(name):
            if name in ("resident_count_sum", "always_on"):
                return calc(name, scopes)
            for sc in reversed(scopes):
                if name in sc:
                    return sc[name]
            return None
        return lookup

    def walk(node, scopes, relevant, path):
        for ch in node["children"]:
            name = ch.get("name")
            lk = make_lookup(scopes)
            rel = relevant and xeval(ch.get("relevant"), lk)
            if ch["kind"] == "group":
                walk(ch, scopes, rel, path)
                continue
            if ch["kind"] == "repeat":
                inst = scopes[-1].get(name)
                seen.add((id(scopes[-1]), name))
                if not rel and seen_rel is not None:
                    for f in all_fields(ch):
                        seen_rel.add((f, False))
                if not rel:
                    if inst:
                        errors.append(f"{path}{name}: answered but Kobo hides it")
                    continue
                if not inst:
                    errors.append(f"{path}{name}: no entries")
                    continue
                for i, sc in enumerate(inst, 1):
                    walk(ch, scopes + [sc], True, f"{path}{name}[{i}]/")
                    extra = set(sc) - {c.get("name") for c in iter_fields(ch)}
                    if extra:
                        errors.append(f"{path}{name}[{i}]: keys not in the form: {sorted(extra)}")
                continue
            t = (ch.get("type") or "").strip()
            if t in ("note", "calculate", "start", "end"):
                continue
            if seen_rel is not None:
                seen_rel.add((name, bool(rel)))
            val = scopes[-1].get(name)
            seen.add((id(scopes[-1]), name))
            if not rel:
                if val not in (None, ""):
                    errors.append(f"{path}{name}: answered ({val}) but Kobo hides it")
                continue
            req = ch.get("required")
            is_req = bool(req) and (req == "yes" or xeval(req, lk))
            if val in (None, ""):
                if is_req:
                    errors.append(f"{path}{name}: required but blank")
                continue
            if t.startswith("select_one"):
                lst = t.split()[1]
                if str(val) not in choices[lst]:
                    errors.append(f"{path}{name}: '{val}' not in choice list {lst}")
            if t == "integer" and not re.fullmatch(r"-?\d+", str(val)):
                errors.append(f"{path}{name}: '{val}' is not a whole number")
            if ch.get("constraint") and not xeval(ch["constraint"], lk, val):
                errors.append(f"{path}{name}: '{val}' breaks constraint {ch['constraint']}")

    top = {k: v for k, v in sub.items() if not k.startswith("_")}
    walk(form_tree, [top], True, "")
    names = {c.get("name") for c in iter_fields(form_tree)}
    extra = set(top) - names
    if extra:
        errors.append(f"keys not in the form: {sorted(extra)}")
    # the resident check note must not show
    if int(top["number_of_residents"]) != int(calc("resident_count_sum", [top])):
        errors.append("residents do not add up (Kobo shows the 'go back and correct' note)")
    return errors


def all_fields(node):
    """Every question name under node, nested repeats included."""
    for ch in node["children"]:
        if ch["kind"] in ("group", "repeat"):
            yield from all_fields(ch)
        elif (ch.get("type") or "").strip() not in ("note", "calculate", "start", "end"):
            yield ch.get("name")


def iter_fields(node):
    """Field and repeat names directly in this node (looking through groups,
    not into nested repeats)."""
    for ch in node["children"]:
        if ch["kind"] == "group":
            yield from iter_fields(ch)
        else:
            yield ch


# =============================================================================
# Entry sheets
# =============================================================================

def strip_md(label):
    return re.sub(r"\s+", " ", str(label or "")).strip()


def answer_label(field, val, choices):
    t = (field.get("type") or "").strip()
    if t.startswith("select_one"):
        return choices[t.split()[1]][str(val)]
    if t == "acknowledge":
        return "Ticked"
    if t == "geopoint":
        lat, lon, alt, acc = str(val).split()
        return f"Latitude {lat}, Longitude {lon}, Altitude {alt} m, Accuracy {acc} m"
    if t == "date":
        return "Date of entry (any date accepted)"
    return str(val)


def sheet_rows(sub, form_tree, choices):
    """(kind, depth, text, answer) rows in form order, only what Kobo shows."""
    out = []
    top = {k: v for k, v in sub.items() if not k.startswith("_")}

    def walk(node, scopes, depth):
        for ch in node["children"]:
            name = ch.get("name")
            lk = (lambda scs: (lambda n: next((s[n] for s in reversed(scs) if n in s), None)))(scopes)
            if ch["kind"] == "group":
                if ch.get("relevant") and not _rel(ch, scopes):
                    continue
                if ch.get("label"):
                    out.append(("section", depth, strip_md(ch["label"]), ""))
                walk(ch, scopes, depth)
                continue
            if ch["kind"] == "repeat":
                inst = scopes[-1].get(name) or []
                for i, sc in enumerate(inst, 1):
                    title = strip_md(ch.get("label") or name)
                    out.append(("repeat", depth, f"{title} {i} of {len(inst)}"
                                + ("" if depth else "  (new repeat entry)"), ""))
                    walk(ch, scopes + [sc], depth + 1)
                continue
            t = (ch.get("type") or "").strip()
            if t in ("note", "calculate", "start", "end"):
                continue
            val = scopes[-1].get(name)
            if val in (None, ""):
                if t in ("decimal", "integer", "text") and not ch.get("required") and _rel(ch, scopes):
                    out.append(("q", depth, strip_md(ch["label"]), "(blank)"))
                continue
            label = strip_md(ch["label"]).replace("${number_of_residents}", top["number_of_residents"])
            out.append(("q", depth, label, answer_label(ch, val, choices)))

    def _rel(ch, scopes):
        def lookup(n):
            if n == "always_on":
                sc = scopes[-1]
                return "yes" if (sc.get("appliance_type") in ALWAYS_ON or
                                 sc.get("appliance_other_always_on") == "yes") else "no"
            return next((s[n] for s in reversed(scopes) if n in s), None)
        return xeval(ch.get("relevant"), lookup)

    walk(form_tree, [top], 0)
    return out


CSS = """
@page { size: A4; margin: 14mm 13mm; }
body { font: 10.5pt/1.35 'DejaVu Sans', Arial, sans-serif; color: #1a1a1a; }
h1 { font-size: 19pt; margin: 0 0 4pt; }
h2 { font-size: 15pt; margin: 0 0 2pt; page-break-before: always; }
.story { color: #444; margin: 0 0 8pt; font-style: italic; }
.meta { font-size: 9.5pt; color: #333; margin-bottom: 8pt; }
table { width: 100%; border-collapse: collapse; }
td { border-bottom: 0.5pt solid #ddd; padding: 2.5pt 4pt; vertical-align: top; }
td.a { width: 38%; font-weight: 600; }
tr.section td { background: #e8eef5; font-weight: 700; padding-top: 6pt; border-bottom: 1pt solid #9ab; }
tr.repeat td { background: #f6f6f0; font-weight: 700; font-size: 10pt; }
tr.d1 td.qq { padding-left: 14pt; } tr.d2 td.qq { padding-left: 28pt; }
tr.d2.repeat td { padding-left: 14pt; font-weight: 600; background: #fbfbf8; }
tr { page-break-inside: avoid; }
.blank { color: #888; font-weight: 400; font-style: italic; }
ul { margin: 4pt 0 0 14pt; padding: 0; } li { margin-bottom: 3pt; }
table.sum td, table.sum th { border-bottom: 0.5pt solid #ccc; padding: 3pt 5pt; text-align: left; font-size: 9.5pt; }
"""


def render(households, results, form_tree, choices):
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>Test households</title>"
             f"<style>{CSS}</style></head><body>"]
    last = households[-1]['household_id']
    parts.append(f"<h1>Test procedure: household survey data entry and data checking</h1>")
    parts.append(
        "<h3>1. Purpose</h3>"
        "<p>To verify, end to end, the path from the KoboToolbox household survey (form version 5) "
        "through fetch_kobo.py to the data checking and conversion in survey_to_schema.py, using "
        f"{len(households)} constructed test households (H901 to {last}) whose answers and expected "
        "data-checking warnings are known in advance.</p>"
        "<h3>2. Scope</h3>"
        "<p>Data entry and data checking only. Load-model outputs (energy estimates, profiles) are "
        "outside the scope of this procedure. The test households are constructed, not survey data. "
        "Between them they exercise every answer option of every question, every question that can "
        "be shown or hidden, and every path through the data checking, including deliberately "
        "inconsistent answers.</p>"
        "<h3>3. Procedure</h3><ol>"
        "<li>Form version 5 of household_survey.xlsx is deployed in KoboToolbox.</li>"
        "<li>Each test household is entered as one new submission, following its sheet in form "
        "order. Answers are selected or entered exactly as listed, including those that appear "
        "unusual; these are intentional test conditions.</li>"
        "<li>Questions not listed on a sheet are not shown by the form for that household. Entries "
        "marked <i>(blank)</i> are shown by the form and left empty.</li>"
        "<li>Times are on the 24-hour clock. Each Room and Appliance is one entry of its repeat "
        "group, as are the time entries within a room or appliance.</li>"
        "<li>After entry, the submissions are downloaded with fetch_kobo.py and the download is "
        "compared with the reference answers in test_households_kobo.json.</li>"
        "<li>survey_to_schema.py is run on the download, and each household's report is compared "
        "with the expected data-checking warnings on its sheet.</li>"
        f"<li>On completion, submissions H901 to {last} are deleted in KoboToolbox (or excluded) "
        "before real data collection.</li></ol>"
        "<h3>4. Acceptance criteria</h3><ul>"
        "<li>The KoboToolbox download matches the reference answers for every household.</li>"
        "<li>validate_household() passes for every household.</li>"
        "<li>Each report lists exactly the expected data-checking warnings for that household. Any "
        "additional or missing warning is recorded as a defect.</li></ul>"
        "<h3>5. Test households</h3>")
    parts.append("<table class='sum'><tr><th>ID</th><th>Household</th><th>People</th>"
                 "<th>Rooms</th><th>Appliances</th></tr>")
    for s, r in zip(households, results):
        parts.append(f"<tr><td>{s['household_id']}</td><td>{html.escape(s['_story'])}</td>"
                     f"<td>{s['number_of_residents']}</td><td>{len(s['room'])}</td>"
                     f"<td>{len(s['appliance'])}</td></tr>")
    parts.append("</table>")
    for s in households:
        parts.append(f"<h2>{s['household_id']}</h2><p class='story'>{html.escape(s['_story'])}</p>")
        parts.append(f"<div class='meta'>{len(s['room'])} rooms, {len(s['appliance'])} appliances, "
                     f"{s['number_of_residents']} residents</div>")
        # No model results here (they are after this step; see coverage.txt), but the
        # data-checking warnings survey_to_schema.py should raise are shown.
        if s["_expect"]:
            parts.append("<div class='meta'><b>Expected data-checking warnings</b> (survey_to_schema.py "
                         "report, Defaults and warnings):<ul>"
                         + "".join(f"<li>{html.escape(e)}</li>" for e in s["_expect"]) + "</ul></div>")
        else:
            parts.append("<div class='meta'><b>Expected data-checking warnings:</b> none.</div>")
        parts.append("<table>")
        for kind, depth, text, ans in sheet_rows(s, form_tree, choices):
            cls = f"{kind} d{depth}"
            if kind in ("section", "repeat"):
                parts.append(f"<tr class='{cls}'><td colspan='2'>{html.escape(text)}</td></tr>")
            else:
                a = (f"<span class='blank'>{html.escape(ans)}</span>" if ans.startswith("(")
                     else html.escape(ans))
                parts.append(f"<tr class='{cls}'><td class='qq'>{html.escape(text)}</td>"
                             f"<td class='a'>{a}</td></tr>")
        parts.append("</table>")
    parts.append("</body></html>")
    return "".join(parts)


# =============================================================================
# Kobo-style export (group paths, as KoboToolbox's JSON download has them)
# =============================================================================

def kobo_paths(sub, form_tree):
    """{"room_type": ...} -> {"group_rooms/room/room_type": ...}, recursively,
    so survey_to_schema.clean() is exercised exactly as on a real export."""
    def walk(node, prefix, data):
        out = {}
        for ch in node["children"]:
            name = ch.get("name")
            if ch["kind"] == "group":
                out.update(walk(ch, f"{prefix}{name}/", data))
            elif ch["kind"] == "repeat":
                if name in data:
                    out[prefix + name] = [walk(ch, f"{prefix}{name}/", item) for item in data[name]]
            elif name in data:
                out[prefix + name] = data[name]
        return out
    top = {k: v for k, v in sub.items() if not k.startswith("_")}
    out = walk(form_tree, "", top)
    missing = set(top) - {k.split("/")[-1] for k in out}
    assert not missing, f"fields not placed in the form tree: {missing}"
    return out


# =============================================================================
# Coverage
# =============================================================================
# Converter lines that the v5 form cannot produce. They stay in the code for
# older submissions or as guards, and are covered by the repo's unit
# tests instead. Each entry: (first line's text, number of lines, reason).
CONVERTER_EXCLUDED = [
    ("if warnings is not None:", 4,
     "peak outside its window: v5's constraint blocks it; only older submissions (H001) reach it; "
     "unit test test_peak_outside_window_is_spread"),
    ('warnings.append(f"{label}: overlapping blocks at {h:02d}:00, kept the later answer ({people}).")', 1,
     "only fires when overlapping blocks give different head-counts; v5 no longer asks head-counts "
     "(every block is 1), so overlaps are harmless and silent (H913's living room overlaps)"),
    ('base["notes"] = ans["appliance_notes"]', 1, "appliance_notes was removed in v5 (older submissions only)"),
    ('warnings.append(f"{name}: {count} bulb(s) but no times anyone is in the room, "', 2,
     "a patterned room always has a time entry (Kobo shows one and its times are required)"),
    ('lines.append(f"validate_household(): FAIL\\n{e}")', 1,
     "guard: only reached if the converter builds an invalid record (would be a converter bug)", -1),
    ('lines.append(f"estimate_daily_energy_kwh() could not run: {e}")', 1,
     "guard: only reached if the estimator itself crashes", -1),
    ('warnings.append(line.strip())', 1,
     "calibration [WARN] lines: only printed when a profile needs more than 60 switch-ons an hour "
     "(the engine's ceiling); calibration then also fails, so it shows up as NOT MODELLED instead"),
]

# Choice lists whose every value need not appear (clock hours)...
CHOICE_LISTS_NOT_REQUIRED = {"hour", "hour_end", "peak", "hour_or_home"}
# ...but these special values must.
CHOICE_VALUES_REQUIRED = {("hour_or_home", "home_all_day"), ("peak", "spread")}


def converter_lines():
    import types
    import survey_to_schema as sts
    src_path = sts.__file__
    src = Path(src_path).read_text()
    lines = src.splitlines()

    def code_lines(co):
        out = {ln for _, _, ln in co.co_lines() if ln}
        for c in co.co_consts:
            if isinstance(c, types.CodeType):
                out |= code_lines(c)
        return out
    body = set()
    for c in compile(src, src_path, "exec").co_consts:     # lines inside functions only
        if isinstance(c, types.CodeType):
            body |= code_lines(c)
    excluded = {}
    for text, n, why, *off in CONVERTER_EXCLUDED:
        starts = [i + 1 for i, l in enumerate(lines) if l.strip() == text.strip()]
        assert len(starts) == 1, f"exclusion text not found exactly once: {text}"
        first = starts[0] + (off[0] if off else 0)
        for ln in range(first, first + n - (off[0] if off else 0)):
            excluded[ln] = why
    return src_path, lines, body, excluded


def func_of(lines, ln):
    for k in range(ln - 1, -1, -1):
        m = re.match(r"def (\w+)", lines[k])
        if m:
            return m.group(1)
    return "?"


def parents_of(node, name, chain=()):
    """Groups/repeats enclosing the question `name` (None if not under node)."""
    for ch in node["children"]:
        if ch["kind"] in ("group", "repeat"):
            r = parents_of(ch, name, chain + (ch,))
            if r is not None:
                return r
        elif ch.get("name") == name:
            return list(chain)
    return None


def warning_check(warnings, expect):
    """Every warning must be expected, every expectation must occur."""
    errs = []
    for w in warnings:
        if not any(e in w for e in expect):
            errs.append(f"UNEXPECTED warning: {w[:220]}")
    for e in expect:
        if not any(e in w for w in warnings):
            errs.append(f"EXPECTED warning missing: {e}")
    return errs


# =============================================================================
def main():
    import survey_to_schema as sts
    rows, choices = read_form()
    form_tree = tree(rows)
    out_dir = HERE / "converted"
    out_dir.mkdir(exist_ok=True)
    for f in out_dir.glob("H9*"):
        f.unlink()
    export_path = HERE / "test_households_kobo.json"
    export = {"results": [kobo_paths(s, form_tree) for s in HOUSEHOLDS]}
    export_path.write_text(json.dumps(export, indent=1))

    # ---- convert through the same entry points a real export uses, traced
    src_path, src_lines, body, excluded = converter_lines()
    hits = {}                       # line -> households that ran it
    current = [None]

    def tracer(frame, event, arg):
        if frame.f_code.co_filename != src_path:
            return None
        def local(frame, event, arg):
            if event == "line":
                hits.setdefault(frame.f_lineno, set()).add(current[0])
            return local
        hits.setdefault(frame.f_lineno, set()).add(current[0])
        return local

    converted = []
    argv = sys.argv
    sys.settrace(tracer)
    try:
        current[0] = "CLI"                       # the command line, on a one-household export
        one = HERE / "converted" / "_cli_check_export.json"
        one.write_text(json.dumps(export["results"][0]))     # a single submission object
        sys.argv = ["survey_to_schema.py", str(one), "--out", str(out_dir)]
        with contextlib.redirect_stdout(io.StringIO()):
            sts.main()
        sys.argv = argv
        one.unlink()
        for s, sub in zip(HOUSEHOLDS, sts.load_submissions(export_path)):
            current[0] = s["household_id"]
            h, warnings, follow_up, log = sts.submission_to_household(sub)
            with contextlib.redirect_stdout(io.StringIO()):
                status = sts.write_outputs(h, warnings, follow_up, log, out_dir)
            converted.append((h, warnings, status))
    finally:
        sys.settrace(None)
        sys.argv = argv

    results, bad, seen_rel = [], 0, {}
    for s, (h, warnings, status) in zip(HOUSEHOLDS, converted):
        hid = s["household_id"]
        rel = set()
        errs = kobo_check(s, form_tree, choices, rel)
        for name, shown in rel:
            seen_rel.setdefault((name, shown), set()).add(hid)
        errs += warning_check(warnings, s["_expect"])
        est = schema.estimate_daily_energy_kwh(h)["weighted_kwh"]
        g = h["grid"]
        bill = (round(g["monthly_bill_kes"] / g["import_tariff_kes_per_kwh"] / 30, 2)
                if g["connected"] and g["import_tariff_kes_per_kwh"] else None)
        results.append({"est": est, "bill": bill if bill is not None else "off grid"})
        ok = not errs and "PASS" in status
        bad += not ok
        ratio = f"(ratio {est / bill:.2f})" if bill else "(off grid, no bill)"
        print(f"{hid}: {'OK  ' if ok else 'FAIL'} {status}; {len(warnings)} warning(s), "
              f"{len(s['_expect'])} expected; model {est} kWh/day vs bill {bill} kWh/day {ratio}")
        for e in errs:
            print("    -", e)

    # ---- coverage
    gaps = []
    # 1. every choice value, per question
    used = {}

    def collect(d, hid):
        for k, v in d.items():
            if k.startswith("_"):
                continue
            if isinstance(v, list):
                for x in v:
                    collect(x, hid)
            else:
                used.setdefault((k, str(v)), set()).add(hid)
    for s in HOUSEHOLDS:
        collect(s, s["household_id"])
    choice_rows = []
    for r in rows:
        t = (r["type"] or "").strip()
        if not t.startswith("select_one"):
            continue
        lst = t.split()[1]
        for val in choices[lst]:
            if lst in CHOICE_LISTS_NOT_REQUIRED and (lst, val) not in CHOICE_VALUES_REQUIRED:
                continue
            who = sorted(used.get((r["name"], val), ()))
            choice_rows.append((f"{r['name']} = {val}", who))
            if not who:
                gaps.append(f"choice never picked: {r['name']} = {val}")
    # 2. every question shown somewhere; every conditional one also hidden somewhere
    rel_rows = []
    for r in rows:
        t = (r["type"] or "").strip()
        if not r.get("name") or t.startswith(("begin", "end")) or t in ("note", "calculate", "start", "end"):
            continue
        shown = sorted(seen_rel.get((r["name"], True), ()))
        hidden = sorted(seen_rel.get((r["name"], False), ()))
        rel_rows.append((r["name"], shown, hidden))
        if not shown:
            gaps.append(f"question never shown: {r['name']}")
        conditional = bool(r.get("relevant")) or any(
            p.get("relevant") for p in (parents_of(form_tree, r["name"]) or []))
        if conditional and not hidden:
            gaps.append(f"conditional question never hidden: {r['name']}")
    # 3. every converter line
    per_func = {}
    for ln in sorted(body):
        if ln in excluded:
            continue
        who = hits.get(ln, set())
        if not who:
            gaps.append(f"converter line never run: {Path(src_path).name}:{ln} "
                        f"[{func_of(src_lines, ln)}] {src_lines[ln - 1].strip()[:90]}")
        d = per_func.setdefault(func_of(src_lines, ln), [0, 0, set()])
        d[0] += 1
        d[1] += bool(who)
        d[2] |= who - {"CLI"}

    cov = ["COVERAGE OF THE TEST HOUSEHOLDS", "=" * 31, "",
           "Converter (survey_to_schema.py): lines run / lines, and the households that run them"]
    for f, (n, k, who) in per_func.items():
        cov.append(f"  {f:26s} {k:3d}/{n:<3d}  {', '.join(sorted(who)) or '(command line only)'}")
    cov.append("  Excluded (the v5 form cannot produce these):")
    for text, n, why, *_ in CONVERTER_EXCLUDED:
        cov.append(f"    - {text.strip()[:70]}  -- {why}")
    cov += ["", "Choices (question = value -> households):"]
    cov += [f"  {k:58s} {', '.join(v) or '-- NONE --'}" for k, v in choice_rows]
    cov += ["", "Questions (shown in | hidden in):"]
    cov += [f"  {n:38s} {', '.join(sh) or '-- NONE --'} | {', '.join(hd) or '-'}"
            for n, sh, hd in rel_rows]
    cov += ["", "Warnings each household is meant to produce:"]
    for s in HOUSEHOLDS:
        cov.append(f"  {s['household_id']}: " + ("; ".join(s["_expect"]) or "none"))
    cov += ["", f"GAPS: {len(gaps)}"] + [f"  - {g}" for g in gaps]
    (HERE / "coverage.txt").write_text("\n".join(cov) + "\n")

    (HERE / "entry_sheets.html").write_text(render(HOUSEHOLDS, results, form_tree, choices))
    for g in gaps:
        print("  GAP:", g)
    print(f"{len(HOUSEHOLDS)} households, {bad} failing, {len(gaps)} coverage gap(s); "
          f"wrote coverage.txt, test_households_kobo.json, converted/, entry_sheets.html")
    return bad + len(gaps)


if __name__ == "__main__":
    sys.exit(main())
