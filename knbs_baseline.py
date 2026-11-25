"""
knbs_baseline.py
================
Population baseline from the Kenya Time Use Survey 2021 (KTUS 2021),
Kenya National Bureau of Statistics (KNBS), used to check interview answers
against how urban Kenyans actually spend the day.

Source: KNBS (2023). 2021 Kenya Time Use Survey, public-use dataset,
catalogue 127 (DDI-KEN-KNBS-KTUS-2021-V001), Kenya National Data Archive.
Cite as "Kenya Time Use Survey 2021 (KTUS 2021), KNBS".

Only the aggregated figures below are kept in this project. The microdata
is used under KNBS terms (no redistribution, aggregated use only) and stays
outside the repository; build_from_microdata() regenerates these numbers from
a local copy.

What KTUS gives: one diary day per person aged 15+ (24,004 people), as total
minutes per activity, with the day of the week, urban/rural and county, and a
person-day weight. It has NO time of day, so it can bound daily amounts
(sleep, cooking, TV/radio, work) but not when they happen; timing still
comes from the interview (and daylight from NASA POWER).

Figures: person-day weighted, urban residents aged 15+, minutes per day.
p5 ... p95 are weighted percentiles over all persons (zeros included);
participants_p50 / participants_p95 are over persons who did the activity
that day. Nairobi alone (306 weekday / 120 weekend diaries) is close to the
urban figures; urban is used for its much larger sample.
"""

URBAN = {
    "weekday": {
        "n_persons": 5129,
        "sleep":            {"mean": 539.2, "p5": 375, "p25": 480, "p50": 540, "p75": 600, "p95": 720},
        "food_preparation": {"mean": 86.5, "participation": 0.606, "p50": 60, "p95": 260,
                             "participants_p50": 120, "participants_p95": 300},
        "mass_media":       {"mean": 65.3, "participation": 0.509, "p50": 30, "p95": 240,
                             "participants_p50": 120, "participants_p95": 300},
        "employment":       {"mean": 288.8, "participation": 0.583, "p50": 180, "p95": 780},
        "commute":          {"mean": 34.1, "participation": 0.389, "p95": 120},
    },
    "weekend": {
        "n_persons": 2414,
        "sleep":            {"mean": 550.9, "p5": 410, "p25": 480, "p50": 540, "p75": 600, "p95": 740},
        "food_preparation": {"mean": 88.1, "participation": 0.621, "p50": 60, "p95": 240,
                             "participants_p50": 120, "participants_p95": 290},
        "mass_media":       {"mean": 91.4, "participation": 0.585, "p50": 60, "p95": 300,
                             "participants_p50": 120, "participants_p95": 360},
        "employment":       {"mean": 207.9, "participation": 0.453, "p50": 0, "p95": 720},
        "commute":          {"mean": 26.2, "participation": 0.282, "p95": 120},
    },
}

# Where the night is placed when the interview gives nothing to find it from
# (no room times and no home-only appliance use): KTUS gives its LENGTH (the
# urban median, 540 min = 9 h); KTUS has no time of day, so its END is an
# assumption -- 06:00, around Nairobi sunrise (06:30) and the earliest hour
# the test households report getting up.
DEFAULT_WAKE_HOUR = 6

# Appliances whose use is "mass media" (KTUS division 284) and "food and meals
# management and preparation" (KTUS 231), for the plausibility checks.
MASS_MEDIA_APPLIANCES = ("television", "dstv_decoder", "bluetooth_speaker", "gaming_console")
COOKING_APPLIANCES = ("electric_hotplate", "induction_cooker", "electric_pressure_cooker",
                      "rice_cooker", "microwave", "electric_oven", "toaster", "blender")


# Spread of daily minutes among urban persons who did the activity (both day
# types; weighted): standard deviation and coefficient of variation. These
# are BETWEEN-person spreads, so they are upper bounds for one household's
# day-to-day variation; simulate.py's behavioural variation is set well
# inside them (routine shift sd 30-45 min vs 107 min; intensity ~0.3 vs
# 0.57-0.74).
SPREAD = {
    "sleep":            {"sd_min": 107, "cv": 0.20},
    "food_preparation": {"sd_min": 82,  "cv": 0.57},
    "mass_media":       {"sd_min": 101, "cv": 0.74},
}


# Caps on the household-specific behavioural variation, applied by the
# converter (survey_to_schema.py) when it creates the values -- the
# simulator itself uses no KNBS data. No
# household's day-to-day spread may exceed the spread BETWEEN urban people.
# Routine shift: half the between-person sleep-time sd (a day-to-day shift
# is a fraction of person-to-person differences); busy/light-day factor:
# the log-normal sd whose coefficient of variation equals the largest
# activity CV above (mass media, 0.74).
ROUTINE_SHIFT_SD_CAP_MIN = 60.0
USE_INTENSITY_SD_CAP = 0.66


def sleep_bounds_min(day):
    """(p5, p95) of urban sleep minutes for "weekday" / "weekend"."""
    s = URBAN[day]["sleep"]
    return s["p5"], s["p95"]


def median_sleep_hours(day):
    return round(URBAN[day]["sleep"]["p50"] / 60)


def build_from_microdata(path):
    """Recompute the URBAN figures from a local copy of the KTUS 2021 .dta
    (needs pandas). Returns a dict shaped like URBAN, for checking or
    updating the numbers above. Nothing is written anywhere."""
    import numpy as np
    import pandas as pd

    cols = ["resid", "t02", "t291", "t231", "t284", "t11", "t218", "Person_Day_Weight"]
    df = pd.read_stata(path, columns=cols)
    df = df[df["resid"] == 2]                                   # 2 = urban
    df["weekend"] = df["t02"].isin(["Saturday", "Sunday"])

    def wq(x, w, q):
        o = np.argsort(x.values)
        x, w = x.values[o], w.values[o]
        c = np.cumsum(w) / w.sum()
        return float(x[min(np.searchsorted(c, q), len(x) - 1)])

    out = {}
    for label, weekend in (("weekday", False), ("weekend", True)):
        s = df[df["weekend"] == weekend]
        w = s["Person_Day_Weight"]
        d = {"n_persons": int(len(s))}
        for var, key in (("t291", "sleep"), ("t231", "food_preparation"), ("t284", "mass_media"),
                         ("t11", "employment"), ("t218", "commute")):
            x = s[var]
            d[key] = {"mean": round(float(np.average(x, weights=w)), 1),
                      "participation": round(float(np.average(x > 0, weights=w)), 3),
                      **{f"p{int(q * 100)}": wq(x, w, q) for q in (0.05, 0.25, 0.5, 0.75, 0.95)}}
            if key in ("food_preparation", "mass_media"):
                xp, wp = x[x > 0], w[x > 0]
                d[key]["participants_p50"] = wq(xp, wp, 0.5)
                d[key]["participants_p95"] = wq(xp, wp, 0.95)
        out[label] = d
    return out
