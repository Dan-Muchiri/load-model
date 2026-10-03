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

   i. weekday_sessions_per_day : On a day they DO use it, how many
                          SEPARATE times do they switch it on that day?
                          (e.g. kettle: "once for breakfast, once more
                          for evening tea" = 2). This is different from
                          "days used" above and different from the
                          number of windows in (1) — a household can
                          report two windows meaning "it happens once,
                          at either time" (sessions_per_day = 1), or
                          two windows meaning "it happens at both
                          times, every day it's used" (sessions_per_day
                          = 2). Ask explicitly; do not infer it from
                          the window count. Default to 1 if the
                          household only describes a single routine
                          use, however many windows it spans.

   j. weekend_sessions_per_day : Same question, for weekend days.
                          Can differ from the weekday answer (e.g. an
                          extra lazy-morning tea on weekends).

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
# RESTART-DELAY DEFAULTS (per appliance)
# =============================================================================
# restart_delay_min is a physical/behavioural property of the appliance
# itself -- tank reheat time, wash-cycle length, "just had tea, not
# thirsty again yet" -- not a household preference, so it is NOT an
# interview question. These defaults mirror REFERENCE_HOUSEHOLD's own
# appliance entries in schema.py and should be used as-is unless a
# technician has a specific, noted reason to believe a household's
# behaviour genuinely differs (e.g. a large household running
# back-to-back laundry loads) -- in which case pass an explicit
# restart_delay_min to calibrate_appliance() to override the table.
#
# Bulbs are NOT in this table and must never use it -- they always use
# restart_delay_min=0 (see BULB CALIBRATION section below). There is no
# physical cooldown on a light switch; always pass 0 explicitly.
APPLIANCE_RESTART_DELAY_MIN = {
    # ── Group A: always-on baseline ──
    "refrigerator":               0,
    "chest_freezer":               0,
    "wifi_router":                 0,
    "electric_fence_energiser":    0,
    "cctv_system":                 0,
    # ── Group B: morning-peak ──
    "electric_kettle":            20,
    "electric_kettle_2":          20,
    "iron_box":                   20,
    "water_pump":                 20,
    "immersion_water_heater":     20,
    "solar_water_heater_pump":     0,
    # ── Group C: cooking ──
    "electric_hotplate":          30,
    "induction_cooker":           30,
    "electric_pressure_cooker":   60,
    "rice_cooker":                60,
    "microwave":                  30,
    "blender":                    30,
    "toaster":                    30,
    "electric_oven":              60,
    # ── Group D: entertainment and information ──
    "television":                 30,
    "television_2":               30,
    "dstv_decoder":                30,
    "laptop":                     60,
    "laptop_2":                   60,
    "desktop_computer":           60,
    "gaming_console":             60,
    "bluetooth_speaker":          30,
    # ── Group E: phone and device charging ──
    "smartphone_charger":        120,
    "tablet_charger":            120,
    "power_bank_charging":       120,
    # ── Group F: laundry and cleaning ──
    "washing_machine":           240,
    "vacuum_cleaner":            120,
    # ── Group G: comfort ──
    "ceiling_fan":                30,
    "standing_fan":               30,
    "air_conditioner":            30,
    # ── Group H: outdoor and security ──
    "gate_motor":                  2,
    "borehole_pump":              30,
    # ── Group I: personal care and miscellaneous ──
    "hair_dryer":                 30,
    "electric_shaver":            60,
    "sewing_machine":             30,
    "printer":                    10,
}

# Fallback for anything not in the table above -- e.g. the
# "other_appliance_1"/"other_appliance_2" catch-all slots in schema.py,
# or a genuinely new appliance type. 20 minutes is the single most
# common value among the standard appliances above -- a reasonable
# guess, not a substitute for adding the real appliance's value to the
# table once its task-completion time is known.
DEFAULT_RESTART_DELAY_MIN = 20


def get_restart_delay_min(appliance_name, override=None):
    """
    Look up the default restart_delay_min for a named appliance.

    Parameters
    ----------
    appliance_name : str, must match a schema.py appliance "name" field
                     to hit the table; anything else falls back to
                     DEFAULT_RESTART_DELAY_MIN (with a warning, since
                     a miss is often a typo rather than a genuinely new
                     appliance).
    override : int or None. If given, takes precedence over the table --
               use this when a technician has a specific, noted reason
               to believe this household's behaviour genuinely differs
               from the standard default.

    Returns
    -------
    int, minutes.
    """
    if override is not None:
        return override
    if appliance_name in APPLIANCE_RESTART_DELAY_MIN:
        return APPLIANCE_RESTART_DELAY_MIN[appliance_name]
    print(
        f"[WARN] '{appliance_name}' not found in APPLIANCE_RESTART_DELAY_MIN -- "
        f"falling back to the generic default of {DEFAULT_RESTART_DELAY_MIN} min. "
        f"Check for a typo, or add this appliance's real value to the table."
    )
    return DEFAULT_RESTART_DELAY_MIN


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
        "end_hour"   : int, window end (exclusive). May be <= start_hour
                       to mean the window crosses midnight (e.g.
                       start_hour=22, end_hour=2 means 22:00-02:00) --
                       detected automatically, no need to write 26 by hand.
        "peak_hour"  : int or None. If given, a triangular taper is
                       applied around it. If None, the window is flat.
                       If the window crosses midnight, pass the peak as
                       its literal 24h-clock hour (e.g. 1 for 01:00) --
                       it is re-aligned to the wrapped window internally.
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

        if end <= start:
            # Window crosses midnight (e.g. 22:00-02:00 given as
            # start_hour=22, end_hour=2). Push end past 24 so
            # range(start, end) is non-empty; h % 24 below wraps the
            # overflow hours back to a valid 0-based index. If the peak
            # falls on the post-midnight side, shift it the same way so
            # the taper distance in the triangular branch stays correct.
            end += 24
            if peak is not None and peak < start:
                peak += 24

        if peak is None:
            # Flat across the window.
            for h in range(start, end):
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
                     sessions_per_day=1.0, appliance_name="appliance"):
    """
    Scale raw_shape so that the mechanism's IMPLIED average daily "on"
    minutes matches the household's reported usage for this day type.

    Mechanism assumption (matches the per-minute Bernoulli engine):
    expected number of switch-on events starting in hour h ~= tou[h]
    (60 independent per-minute trials at probability tou[h]/60).
    So implied daily total minutes = mean_duration_min * sum(tou).
    sum(tou) is therefore pinned to exactly (days_used/period_days) *
    sessions_per_day -- the expected NUMBER of switch-on events per day,
    not a count per window. Multiple windows only shape WHEN those
    expected events are likely to land; they do not add extra events on
    their own -- that is what sessions_per_day is for.

    Parameters
    ----------
    raw_shape      : np.ndarray, 24 values, from build_raw_shape()
    mean_duration_min : float, typical length of ONE session (already in schema)
    days_used      : float, number of days this appliance is used within
                     the period (e.g. 4 out of 5 weekdays, or 1 out of 2
                     weekend days)
    period_days    : int, total days in the period being calibrated.
                     5 for weekday calibration, 2 for weekend calibration.
    sessions_per_day : float, default 1.0. How many separate times per
                     day (on a day it IS used) this is actually switched
                     on -- e.g. a kettle boiled once for breakfast AND
                     once for evening tea = 2.0. Multiplies directly
                     into target_minutes. NOT inferred from the number
                     of windows in raw_shape: two windows can mean
                     "happens once, at either time" (sessions_per_day
                     stays 1) just as easily as "happens at both."
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
    # e.g. used 4 out of 5 weekdays, 2 sessions/day, 4 min/session
    #      → 4/5 × 2 × 4 = 6.4 min/weekday
    target_minutes = (days_used / period_days) * mean_duration_min * sessions_per_day

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
                        target_minutes, n_days=500,
                        restart_delay_min=DEFAULT_RESTART_DELAY_MIN,
                        random_seed=None):
    """
    Simulate n_days using the SAME per-minute Bernoulli mechanism the real
    appliance engine uses, and check the average daily "on" minutes
    converges to target_minutes.

    This function has no appliance_name, so it cannot resolve
    restart_delay_min from APPLIANCE_RESTART_DELAY_MIN itself -- its
    default is the same generic DEFAULT_RESTART_DELAY_MIN used elsewhere
    as a last resort. In practice every caller in this file always
    passes restart_delay_min explicitly (already resolved via
    get_restart_delay_min() upstream), so this default only matters if
    this function is called standalone.

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
                               days_used, period_days, sessions_per_day=1.0,
                               appliance_name="appliance",
                               restart_delay_min=None, n_days=300,
                               max_iterations=5, random_seed=None):
    """
    Refines calibrate_shape()'s closed-form scalar using the verification
    loop itself, correcting for the restart_delay bias (closed form
    slightly UNDER-estimates realised daily minutes since it ignores
    switch-on attempts blocked by the restart-delay lockout).

    Each iteration: simulate → compare to target → rescale → repeat.
    Stops early once within 10% tolerance or max_iterations is reached.

    Raises ValueError instead of returning silently if it never converges --
    this happens when restart_delay_min, mean_duration_min and
    sessions_per_day jointly require more total "on" time than can
    physically fit in the given window(s), even with tou clipped to 1.0
    (switch-on certain every minute). A badly off-target tou must never
    be pasted into schema.py without this being surfaced loudly.

    restart_delay_min : int or None. As in calibrate_appliance() -- leave
                        as None to resolve via APPLIANCE_RESTART_DELAY_MIN
                        for appliance_name; pass a number to override.
    """
    restart_delay_min = get_restart_delay_min(appliance_name, override=restart_delay_min)

    result = calibrate_shape(raw_shape, mean_duration_min, days_used,
                              period_days, sessions_per_day, appliance_name)
    tou = np.array(result["tou"])
    target = result["target_minutes"]

    stalled = False
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
            stalled = True
            break

        correction = target / check["simulated_mean_minutes"]
        tou = np.clip(tou * correction, 0.0, 1.0)
    else:
        # Loop ran out of iterations without breaking -- check is the
        # last attempt, and it did not pass.
        stalled = not check["passed"]

    if stalled or not check["passed"]:
        raise ValueError(
            f"{appliance_name}: calibration did not converge after "
            f"{i + 1} iteration(s) -- simulated {check['simulated_mean_minutes']} "
            f"min/day vs. target {target} min/day ({check['pct_error']}% error). "
            f"This means mean_duration_min={mean_duration_min}, "
            f"sessions_per_day and restart_delay_min={restart_delay_min} "
            f"jointly require more total 'on' time than the reported "
            f"window(s) can physically hold, even with tou at 1.0 "
            f"(switch-on certain every minute). Widen the usage window(s) "
            f"with the household, reduce sessions_per_day, or reconsider "
            f"restart_delay_min for this appliance before pasting a result "
            f"into schema.py."
        )

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
                         weekday_sessions_per_day=1.0,
                         weekend_sessions_per_day=1.0,
                         appliance_name="appliance",
                         decrement_per_hour=0.2,
                         restart_delay_min=None, n_days=300,
                         random_seed=None):
    """
    Full calibration pipeline for both day types. Returns tou_weekday and
    tou_weekend arrays ready to paste directly into schema.py.

    Parameters
    ----------
    weekday_windows   : list of window dicts for weekday usage
    weekend_windows   : list of window dicts for weekend usage
                        (pass [] if never used on weekends)
    mean_duration_min : float, typical length of ONE session in minutes
    std_duration_min  : float, standard deviation of session length
    weekday_days_used : float, days used out of 5 weekdays
                        (from WEEKDAY_FREQUENCY)
    weekend_days_used : float, days used out of 2 weekend days
                        (from WEEKEND_FREQUENCY)
    weekday_sessions_per_day : float, default 1.0. Separate times per
                        day this is switched on, on a weekday it IS
                        used (e.g. kettle: breakfast + evening tea = 2.0).
                        See the interview note (i) in this file's module
                        docstring — not inferred from window count.
    weekend_sessions_per_day : float, default 1.0. Same, for weekend days.
    appliance_name    : str, used in log messages. Also used to look up
                        restart_delay_min in APPLIANCE_RESTART_DELAY_MIN
                        when restart_delay_min is not given explicitly --
                        so it should match the appliance's schema.py
                        "name" field for the lookup to hit.
    decrement_per_hour: float, triangle taper rate (default 0.2)
    restart_delay_min : int or None. Lockout minutes after each event.
                        This is a physical property of the appliance, not
                        an interview question -- leave as None (default)
                        to use APPLIANCE_RESTART_DELAY_MIN's value for
                        appliance_name. Pass an explicit number only to
                        override the table (e.g. for bulbs, which must
                        always use 0, or a household with noted unusual
                        behaviour).
    n_days            : int, simulated days for verification
    random_seed       : int or None

    Returns
    -------
    dict with:
        "tou_weekday" : 24-value list, paste into schema.py tou_weekday field
        "tou_weekend" : 24-value list, paste into schema.py tou_weekend field
    """
    restart_delay_min = get_restart_delay_min(appliance_name, override=restart_delay_min)

    def _run_one(windows, days_used, period_days, sessions_per_day, label):
        # Appliance not used on this day type — return all zeros.
        if days_used == 0 or not windows:
            return [0.0] * 24

        raw = build_raw_shape(windows, decrement_per_hour)
        result = calibrate_shape(raw, mean_duration_min, days_used, period_days,
                                  sessions_per_day, f"{appliance_name} ({label})")

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
            days_used, period_days, sessions_per_day,
            f"{appliance_name} ({label})",
            restart_delay_min=restart_delay_min,
            n_days=n_days, random_seed=random_seed
        )
        return refined["tou"]

    print(f"\nCalibrating: {appliance_name}")
    tou_weekday = _run_one(weekday_windows, weekday_days_used, 5,
                            weekday_sessions_per_day, "weekday")
    tou_weekend = _run_one(weekend_windows, weekend_days_used, 2,
                            weekend_sessions_per_day, "weekend")

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
# sessions_per_day matters MORE for rooms than for most appliances --
# rooms are routinely entered multiple separate times a day (kitchen:
# breakfast prep AND dinner prep; bathroom: morning AND evening). Ask
# "how many separate times a day do they go into the [room]?" per room,
# per day type, same as for appliances -- do not assume 1.
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
    # Both windows are real, separate boils every day used (not "either/or"),
    # so sessions_per_day = 2 -- see interview note (i)/(j) in the module docstring.
    weekday_sessions_per_day = 2.0
    weekend_sessions_per_day = 2.0

    result = calibrate_appliance(
        weekday_windows   = weekday_windows,
        weekend_windows   = weekend_windows,
        mean_duration_min = mean_duration_min,
        std_duration_min  = std_duration_min,
        weekday_days_used = weekday_days_used,
        weekend_days_used = weekend_days_used,
        weekday_sessions_per_day = weekday_sessions_per_day,
        weekend_sessions_per_day = weekend_sessions_per_day,
        appliance_name    = "electric_kettle",
        # restart_delay_min omitted -- resolves to 20 via
        # APPLIANCE_RESTART_DELAY_MIN["electric_kettle"].
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
        weekday_sessions_per_day = 2.0,  # morning gather AND evening session, every day
        weekend_sessions_per_day = 2.0,
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
        weekday_sessions_per_day = 2.0,  # morning prep AND dinner prep
        weekend_sessions_per_day = 3.0,  # brunch, lunch, AND dinner prep
        appliance_name    = "kitchen bulb",
        restart_delay_min = 0,    # no cooldown on a light switch
        random_seed       = 42,
    )
    print(f"\ntou_weekday: {kitchen_result['tou_weekday']}")
    print(f"tou_weekend: {kitchen_result['tou_weekend']}")
    print("\n→ Paste these two lines into schema.py for kitchen bulb.")

