"""
calibrate_tou.py
================
Converts interview-elicited appliance usage (windows + frequency + typical
session length) into CALIBRATED tou_weekday and tou_weekend arrays —
following the same shape+scale logic CREST uses (activity profile x
calibration scalar), adapted for a single-household interview instead of
a population TUS.

PIPELINE
--------
1. build_raw_shape()         : window(s) + optional peak -> uncalibrated
                               24-hour relative-likelihood curve (the "shape")
2. calibrate_shape()         : scales the shape so the IMPLIED average daily
                               "on" minutes matches the household's reported
                               usage for that day type (the "scale")
3. verify_calibration()      : Monte Carlo check -- simulate N days using the
                               SAME per-minute Bernoulli mechanism the real
                               model uses, confirm the average converges to
                               the target.
4. calibrate_appliance()     : wrapper that runs steps 1-3 for BOTH weekday
                               and weekend separately, returning the final
                               tou_weekday and tou_weekend arrays ready to
                               paste into schema.py.

This does NOT change the appliance engine itself (still per-minute
independent draws, same as your existing schema/model). It only produces
correctly-calibrated tou_weekday / tou_weekend INPUT for that engine.

WHAT THE INTERVIEW MUST COLLECT (per appliance)
------------------------------------------------
The following fields are required to run this calibration pipeline.
Collect them SEPARATELY for weekdays (Mon-Fri) and weekends (Sat-Sun)
because schema.py models the two day types independently.

1. USAGE WINDOWS  →  feeds build_raw_shape()
   Ask for EACH of: weekday windows AND weekend windows.
   For each time block when the appliance is used:

   a. start_hour      : What time do you start using it?
                        (record as 24h integer, e.g. 6 for 06:00)

   b. end_hour        : What time do you stop using it?
                        (record as 24h integer, exclusive, e.g. 9 means up to 08:59)

   c. peak_hour       : Within that window, which single hour is it most
                        likely to be in use? (optional — if use is uniform
                        across the window, leave blank / None)

   d. peak_value      : How often is it used during this window relative
                        to other windows of the SAME day type?
                        ("always" = 1.0 / "usually" = 0.7 / "sometimes" = 0.4
                        / "rarely" = 0.2). Only matters for ranking windows
                        against each other — does NOT affect total energy.

   An appliance can have more than one window per day type.
   If the household says "I don't use it at all on weekends", set
   weekend_windows = [] and weekend_days_used = 0.

2. SESSION DURATION  →  feeds calibrate_shape() and verify_calibration()
   Duration is usually the same on weekdays and weekends — ask once.

   e. mean_duration_min : How long is a typical session?
                          (e.g. "about 4 minutes" for a kettle)
                          READ FROM APPLIANCE LABEL where possible.

   f. std_duration_min  : How much does that vary?
                          (e.g. "between 3 and 6 minutes" → std ≈ 1)
                          Can be estimated as ~25% of mean if unsure.

3. FREQUENCY  →  feeds calibrate_shape()
   Ask separately for weekdays and weekends using the tables below.

   g. weekday_days_used : Out of the 5 weekdays, how many does the
                          household use this appliance?
                          Use WEEKDAY_FREQUENCY to map the answer.

   h. weekend_days_used : Out of the 2 weekend days, how many does the
                          household use this appliance?
                          Use WEEKEND_FREQUENCY to map the answer.

NOTE: rated_power_w and count are collected separately in the main
schema survey (read from the appliance label). They are NOT inputs
to this calibration script — they are already in schema.py.
"""

import numpy as np


# =============================================================================
# FREQUENCY LOOKUP TABLES
# =============================================================================

# Weekday frequency: days used out of 5 (Mon-Fri)
WEEKDAY_FREQUENCY = {
    "every_weekday":       5.0,
    "most_weekdays":       4.0,
    "few_times_a_week":    2.5,
    "once_a_week":         1.0,
    "rarely":              0.5,
    "never":               0.0,
}

# Weekend frequency: days used out of 2 (Sat-Sun)
WEEKEND_FREQUENCY = {
    "both_days":           2.0,
    "usually_one_day":     1.0,
    "occasionally":        0.5,
    "never":               0.0,
}


# =============================================================================
# STEP 1: RAW SHAPE FROM INTERVIEW WINDOWS
# =============================================================================

def build_raw_shape(windows, decrement_per_hour=0.2):
    """
    Build an uncalibrated 24-value relative-likelihood shape from one or
    more reported usage windows.

    Parameters
    ----------
    windows : list of dict, each with:
        "start_hour" : int, window start (inclusive)
        "end_hour"   : int, window end (exclusive)
        "peak_hour"  : int or None. If given, a triangular taper is
                       applied around it. If None, the window is flat.
        "peak_value" : float in (0, 1]. Height of the taper peak
                       (or flat value if peak_hour is None). Default 1.0 --
                       relative height BETWEEN windows; the absolute scale
                       is fixed later by calibrate_shape().

    Returns
    -------
    np.ndarray of 24 floats (relative shape, NOT yet a valid probability
    array -- values may exceed what calibration will ultimately need).
    """
    shape = np.zeros(24)

    for w in windows:
        start, end = w["start_hour"], w["end_hour"]
        peak = w.get("peak_hour")
        peak_val = w.get("peak_value", 1.0)

        if peak is None:
            # Flat across the window.
            for h in range(start, end):
                # h % 24 wraps hours > 23 back to 0-based index (handles windows crossing midnight).
                # max() keeps the highest value if two windows overlap at the same hour.
                shape[h % 24] = max(shape[h % 24], peak_val)
        else:
            # Triangular taper centred on peak_hour.
            for h in range(start, end):
                dist = abs(h - peak)          # hours away from the peak
                val = max(peak_val - decrement_per_hour * dist, 0.0)  # drop by 0.2 per hour, floor at 0
                # max() again: if another window already set a higher value at this hour, keep it.
                shape[h % 24] = max(shape[h % 24], val)

    return shape


# =============================================================================
# STEP 2: CALIBRATION -- ANCHOR THE SHAPE TO REPORTED USAGE
# =============================================================================

def calibrate_shape(raw_shape, mean_duration_min, days_used, period_days,
                     appliance_name="appliance"):
    """
    Scale raw_shape so that the mechanism's IMPLIED average daily "on"
    minutes matches the household's reported usage for this day type.

    Mechanism assumption (matches the per-minute Bernoulli engine):
    expected number of switch-on events starting in hour h ~= tou[h]
    (60 independent per-minute trials at probability tou[h]/60).
    So implied daily total minutes = mean_duration_min * sum(tou).

    Parameters
    ----------
    raw_shape      : np.ndarray, 24 values, from build_raw_shape()
    mean_duration_min : float, typical session length (already in schema)
    days_used      : float, number of days this appliance is used within
                     the period (e.g. 4 out of 5 weekdays, or 1 out of 2
                     weekend days)
    period_days    : int, total days in the period being calibrated.
                     5 for weekday calibration, 2 for weekend calibration.
    appliance_name : str, used only for warning messages

    Returns
    -------
    dict with:
        "tou"            : calibrated 24-value array, all in [0, 1]
        "target_minutes" : the average daily "on" minutes this was
                           calibrated to hit
        "scalar_k"       : the calibration scalar applied
        "clipped"        : bool, True if any value was capped at 1.0
    """
    # Average "on" minutes per day of THIS day type.
    # e.g. used 4 out of 5 weekdays, 4 min/session → 4/5 × 4 = 3.2 min/weekday
    target_minutes = (days_used / period_days) * mean_duration_min

    raw_total = mean_duration_min * raw_shape.sum()

    if raw_total <= 0:
        raise ValueError(
            f"{appliance_name}: raw_shape sums to zero -- no window defined, "
            f"cannot calibrate."
        )

    k = target_minutes / raw_total
    calibrated = raw_shape * k

    clipped = bool((calibrated > 1.0).any())
    if clipped:
        over_hours = [h for h, v in enumerate(calibrated) if v > 1.0]
        print(
            f"[WARN] {appliance_name}: calibration requires tou > 1.0 "
            f"at hour(s) {over_hours} -- the reported window is too narrow "
            f"for the reported usage volume. Capping at 1.0 (this will "
            f"UNDER-represent the reported total). Consider widening the "
            f"usage window with the household."
        )
        calibrated = np.clip(calibrated, 0.0, 1.0)

    return {
        "tou":            [round(float(v), 4) for v in calibrated],
        "target_minutes": round(target_minutes, 3),
        "scalar_k":       round(float(k), 4),
        "clipped":        clipped,
    }


# =============================================================================
# STEP 3: VERIFICATION -- DOES THE ENGINE ACTUALLY HIT THE TARGET?
# =============================================================================

def verify_calibration(tou, mean_duration_min, std_duration_min,
                        target_minutes, n_days=500, restart_delay_min=10,
                        random_seed=None):
    """
    Simulate n_days using the SAME per-minute Bernoulli mechanism the real
    appliance engine uses, and check the average daily "on" minutes
    converges to target_minutes.

    Parameters
    ----------
    tou : list/array of 24 floats, from calibrate_shape()
    mean_duration_min, std_duration_min : appliance duration distribution
    target_minutes : float, the value this SHOULD converge to
    n_days : int, number of simulated days
    restart_delay_min : int, minimum minutes before the appliance can
                        fire again after an event ends
    random_seed : int or None — set for reproducible results

    Returns
    -------
    dict with simulated mean, std, and pass/fail vs. target (10% tolerance).
    """
    rng = np.random.default_rng(random_seed)
    daily_totals = []

    for _ in range(n_days):
        minute = 0
        total_on = 0.0
        next_allowed = 0  # minute at which appliance can next switch on

        while minute < 1440:
            hour = int(minute // 60)
            p_per_minute = tou[hour] / 60.0

            if minute >= next_allowed and rng.random() < p_per_minute:
                # Bernoulli trial succeeded — appliance switches on.
                duration = max(1.0, rng.normal(mean_duration_min, std_duration_min))
                total_on += duration
                minute += duration
                next_allowed = minute + restart_delay_min
            else:
                minute += 1

        daily_totals.append(total_on)

    sim_mean = float(np.mean(daily_totals))
    sim_std = float(np.std(daily_totals))

    pct_error = abs(sim_mean - target_minutes) / target_minutes if target_minutes > 0 else 0
    passed = pct_error <= 0.10  # 10% tolerance

    return {
        "simulated_mean_minutes": round(sim_mean, 2),
        "simulated_std_minutes":  round(sim_std, 2),
        "target_minutes":         round(target_minutes, 2),
        "pct_error":              round(pct_error * 100, 1),
        "passed":                 passed,
    }


def calibrate_shape_iterative(raw_shape, mean_duration_min, std_duration_min,
                               days_used, period_days, appliance_name="appliance",
                               restart_delay_min=10, n_days=300,
                               max_iterations=5, random_seed=None):
    """
    Refines calibrate_shape()'s closed-form scalar using the verification
    loop itself, correcting for the restart_delay bias (closed form
    slightly UNDER-estimates realised daily minutes since it ignores
    switch-on attempts blocked by the restart-delay lockout).

    Each iteration: simulate → compare to target → rescale → repeat.
    Stops early once within 10% tolerance or max_iterations is reached.
    """
    result = calibrate_shape(raw_shape, mean_duration_min, days_used,
                              period_days, appliance_name)
    tou = np.array(result["tou"])
    target = result["target_minutes"]

    for i in range(max_iterations):
        check = verify_calibration(
            tou, mean_duration_min, std_duration_min, target,
            n_days=n_days, restart_delay_min=restart_delay_min,
            random_seed=random_seed
        )
        print(f"  iteration {i+1}: simulated={check['simulated_mean_minutes']}"
              f" target={target} error={check['pct_error']}%")

        if check["passed"]:
            break

        if check["simulated_mean_minutes"] <= 0:
            print(f"[WARN] {appliance_name}: simulated mean is zero -- "
                  f"window/shape may be too narrow. Stopping iteration.")
            break

        correction = target / check["simulated_mean_minutes"]
        tou = np.clip(tou * correction, 0.0, 1.0)

    return {
        "tou":            [round(float(v), 4) for v in tou],
        "target_minutes": round(target, 3),
        "final_check":    check,
    }


# =============================================================================
# STEP 4: WRAPPER -- CALIBRATE BOTH DAY TYPES IN ONE CALL
# =============================================================================

def calibrate_appliance(weekday_windows, weekend_windows,
                         mean_duration_min, std_duration_min,
                         weekday_days_used, weekend_days_used,
                         appliance_name="appliance",
                         decrement_per_hour=0.2,
                         restart_delay_min=10, n_days=300,
                         random_seed=None):
    """
    Full calibration pipeline for both day types. Returns tou_weekday and
    tou_weekend arrays ready to paste directly into schema.py.

    Parameters
    ----------
    weekday_windows   : list of window dicts for weekday usage
    weekend_windows   : list of window dicts for weekend usage
                        (pass [] if never used on weekends)
    mean_duration_min : float, typical session length in minutes
    std_duration_min  : float, standard deviation of session length
    weekday_days_used : float, days used out of 5 weekdays
                        (from WEEKDAY_FREQUENCY)
    weekend_days_used : float, days used out of 2 weekend days
                        (from WEEKEND_FREQUENCY)
    appliance_name    : str, used in log messages
    decrement_per_hour: float, triangle taper rate (default 0.2)
    restart_delay_min : int, lockout minutes after each event
    n_days            : int, simulated days for verification
    random_seed       : int or None

    Returns
    -------
    dict with:
        "tou_weekday" : 24-value list, paste into schema.py tou_weekday field
        "tou_weekend" : 24-value list, paste into schema.py tou_weekend field
    """
    def _run_one(windows, days_used, period_days, label):
        # Appliance not used on this day type — return all zeros.
        if days_used == 0 or not windows:
            return [0.0] * 24

        raw = build_raw_shape(windows, decrement_per_hour)
        result = calibrate_shape(raw, mean_duration_min, days_used, period_days,
                                  f"{appliance_name} ({label})")

        check = verify_calibration(
            result["tou"], mean_duration_min, std_duration_min,
            result["target_minutes"], n_days=n_days,
            restart_delay_min=restart_delay_min, random_seed=random_seed
        )

        if check["passed"]:
            print(f"  [{label}] PASS — error {check['pct_error']}%")
            return result["tou"]

        # Closed-form missed — refine iteratively.
        print(f"  [{label}] closed-form missed ({check['pct_error']}%) — refining:")
        refined = calibrate_shape_iterative(
            raw, mean_duration_min, std_duration_min,
            days_used, period_days,
            f"{appliance_name} ({label})",
            restart_delay_min=restart_delay_min,
            n_days=n_days, random_seed=random_seed
        )
        return refined["tou"]

    print(f"\nCalibrating: {appliance_name}")
    tou_weekday = _run_one(weekday_windows, weekday_days_used, 5, "weekday")
    tou_weekend = _run_one(weekend_windows, weekend_days_used, 2, "weekend")

    return {
        "tou_weekday": tou_weekday,
        "tou_weekend": tou_weekend,
    }


# =============================================================================
# BULB CALIBRATION
# =============================================================================
# Bulbs use the SAME Bernoulli mechanism as appliances. A "switch-on event"
# for a bulb = a person entering the room. The session duration = how long
# they stay in the room before leaving and switching the light off.
#
# Use calibrate_appliance() for every bulb zone. The inputs are identical
# to appliances, with one change in interpretation:
#
#   mean_duration_min : typical room VISIT duration (not appliance session).
#                       Ask: "How long do you usually stay in the [room]
#                             in one visit?"
#
#   Example values (collect from interview):
#     living_room      : ~120 min (long evening sessions)
#     kitchen          :  ~40 min (cooking sessions)
#     bedroom          :  ~45 min (pre-sleep, morning routine)
#     bathroom         :  ~15 min (shower / grooming)
#     staircase        :  ~20 min (transit + short stays)
#     store_room       :   ~5 min (brief retrieval)
#     outside_security :  60 min  (dusk-to-dawn: always full hour, set fixed)
#
# The tou_weekday/tou_weekend values produced by calibrate_appliance() will
# represent the per-hour probability that someone ENTERS that room,
# which is the correct CREST switch-on interpretation for lighting.
#
# ALWAYS pass restart_delay_min=0 for bulbs. There is no physical
# cooldown on a light switch — the visit duration already provides
# natural spacing between entries. Adding a restart delay would make
# re-entries artificially infrequent, especially for short-visit rooms
# like bathrooms (15 min) and store rooms (5 min).


# =============================================================================
# WORKED EXAMPLE: THE KETTLE
# =============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("WORKED EXAMPLE: electric_kettle")
    print("=" * 70)

    # ── Interview answers ────────────────────────────────────────────────────
    # Weekday: rushed morning boil on the way out, quick evening cup.
    weekday_windows = [
        {"start_hour": 6, "end_hour": 9,  "peak_hour": 7,    "peak_value": 1.0},
        {"start_hour": 18, "end_hour": 20, "peak_hour": None, "peak_value": 0.4},
    ]
    # Weekend: more relaxed — later morning, longer window, more likely.
    weekend_windows = [
        {"start_hour": 7, "end_hour": 11, "peak_hour": 9,    "peak_value": 1.0},
        {"start_hour": 15, "end_hour": 17, "peak_hour": None, "peak_value": 0.5},
    ]

    mean_duration_min = 4
    std_duration_min  = 1
    weekday_days_used = WEEKDAY_FREQUENCY["every_weekday"]   # 5 out of 5
    weekend_days_used = WEEKEND_FREQUENCY["both_days"]        # 2 out of 2

    result = calibrate_appliance(
        weekday_windows   = weekday_windows,
        weekend_windows   = weekend_windows,
        mean_duration_min = mean_duration_min,
        std_duration_min  = std_duration_min,
        weekday_days_used = weekday_days_used,
        weekend_days_used = weekend_days_used,
        appliance_name    = "electric_kettle",
        restart_delay_min = 20,   # matches schema.py electric_kettle.restart_delay_min
        random_seed       = 42,
    )

    print(f"\ntou_weekday: {result['tou_weekday']}")
    print(f"tou_weekend: {result['tou_weekend']}")
    print("\n→ Paste these two lines into schema.py for electric_kettle.")

    # ── Bulb example: living room ────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("WORKED EXAMPLE: living_room bulb")
    print("=" * 70)
    print("Interview: enters room during morning gather (06-07h, always) and")
    print("evening (18-23h, usually). Typical stay: ~120 min. Same both days.")

    living_room_windows = [
        {"start_hour": 6,  "end_hour": 8,  "peak_hour": 6,    "peak_value": 1.0},
        {"start_hour": 18, "end_hour": 23, "peak_hour": None,  "peak_value": 0.9},
    ]

    bulb_result = calibrate_appliance(
        weekday_windows   = living_room_windows,
        weekend_windows   = living_room_windows,  # darkness same both days
        mean_duration_min = 120,  # long living-room visit; capped to 60 in estimator
        std_duration_min  = 40,
        weekday_days_used = WEEKDAY_FREQUENCY["every_weekday"],
        weekend_days_used = WEEKEND_FREQUENCY["both_days"],
        appliance_name    = "living_room bulb",
        restart_delay_min = 0,    # no cooldown on a light switch
        random_seed       = 42,
    )
    print(f"\ntou_weekday: {bulb_result['tou_weekday']}")
    print(f"tou_weekend: {bulb_result['tou_weekend']}")
    print("\n→ Paste these two lines into schema.py for living_room bulb.")

    # ── Bulb example: kitchen ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("WORKED EXAMPLE: kitchen bulb")
    print("=" * 70)
    print("Interview: weekday — quick morning prep (06-07h, always), evening")
    print("dinner prep (17-20h, always). Weekend — more cooking, longer window.")
    print("Typical kitchen stay: ~40 min.")

    kitchen_weekday = [
        {"start_hour": 6,  "end_hour": 8,  "peak_hour": None, "peak_value": 1.0},
        {"start_hour": 17, "end_hour": 21, "peak_hour": 18,   "peak_value": 1.0},
    ]
    kitchen_weekend = [
        {"start_hour": 7,  "end_hour": 11, "peak_hour": 9,    "peak_value": 1.0},
        {"start_hour": 12, "end_hour": 14, "peak_hour": 13,   "peak_value": 0.9},
        {"start_hour": 17, "end_hour": 21, "peak_hour": 18,   "peak_value": 1.0},
    ]

    kitchen_result = calibrate_appliance(
        weekday_windows   = kitchen_weekday,
        weekend_windows   = kitchen_weekend,
        mean_duration_min = 40,
        std_duration_min  = 12,
        weekday_days_used = WEEKDAY_FREQUENCY["every_weekday"],
        weekend_days_used = WEEKEND_FREQUENCY["both_days"],
        appliance_name    = "kitchen bulb",
        restart_delay_min = 0,    # no cooldown on a light switch
        random_seed       = 42,
    )
    print(f"\ntou_weekday: {kitchen_result['tou_weekday']}")
    print(f"tou_weekend: {kitchen_result['tou_weekend']}")
    print("\n→ Paste these two lines into schema.py for kitchen bulb.")

