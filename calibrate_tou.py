"""
calibrate_tou.py
================
Converts interview-elicited appliance usage (windows + frequency + typical
session length) into a CALIBRATED tou_hourly array — following the same
shape+scale logic CREST uses (activity profile x calibration scalar),
adapted for a single-household interview instead of a population TUS.

PIPELINE
--------
1. build_raw_shape()    : window(s) + optional peak -> uncalibrated
                           24-hour relative-likelihood curve (the "shape")
2. calibrate_shape()     : scales the shape so the IMPLIED average daily
                           "on" minutes matches the household's reported
                           usage (the "scale") -> final tou_hourly
3. verify_calibration()  : Monte Carlo check -- simulate N days using the
                           SAME per-minute Bernoulli mechanism the real
                           model uses, confirm the average converges to
                           the target.

This does NOT change the appliance engine itself (still per-minute
independent draws, same as your existing schema/model). It only produces
correctly-calibrated tou_hourly INPUT for that engine.

WHAT THE INTERVIEW MUST COLLECT (per appliance)
------------------------------------------------
The following fields are required to run this calibration pipeline.
These are the ONLY interview inputs this module uses.

1. USAGE WINDOWS  →  feeds build_raw_shape()
   For each time block when the appliance is used:

   a. start_hour      : What time do you start using it?
                        (record as 24h integer, e.g. 6 for 06:00)

   b. end_hour        : What time do you stop using it?
                        (record as 24h integer, exclusive, e.g. 9 means up to 08:59)

   c. peak_hour       : Within that window, which single hour is it most
                        likely to be in use? (optional — if use is uniform
                        across the window, leave blank)

   d. peak_value      : How often is it used during this window relative
                        to other windows?
                        ("always" = 1.0 / "usually" = 0.7 / "sometimes" = 0.4
                        / "rarely" = 0.2). Only matters for ranking windows
                        against each other — does NOT affect total energy.

   An appliance can have more than one window (e.g. morning AND evening).
   Collect a separate window entry for each distinct time block.

2. SESSION DURATION  →  feeds calibrate_shape() and verify_calibration()

   e. mean_duration_min : How long is a typical session?
                          (e.g. "about 4 minutes" for a kettle)
                          READ FROM APPLIANCE LABEL where possible.

   f. std_duration_min  : How much does that vary?
                          (e.g. "between 3 and 6 minutes" → std ≈ 1)
                          Can be estimated as ~25% of mean if unsure.

3. FREQUENCY  →  feeds calibrate_shape()

   g. days_per_week     : How many days per week is it used?
                          Collected as a category and mapped:
                          "daily"            → 7.0
                          "most days"        → 6.0
                          "a few times/week" → 3.0
                          "weekly"           → 1.0
                          "rarely"           → 0.5

NOTE: rated_power_w and count are collected separately in the main
schema survey (read from the appliance label). They are NOT inputs
to this calibration script — they are already in schema.py.
"""

import numpy as np


# =============================================================================
# STEP 1: RAW SHAPE FROM INTERVIEW WINDOWS
# =============================================================================

FREQUENCY_TO_DAYS_PER_WEEK = {
    "daily":            7.0,
    "most_days":        6.0,
    "few_times_a_week": 3.0,
    "weekly":           1.0,
    "rarely":           0.5,
}


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
                       relative height BETWEEN windows/appliances; the
                       absolute scale is fixed later by calibrate_shape().

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

def calibrate_shape(raw_shape, mean_duration_min, days_per_week,
                     appliance_name="appliance"):
    """
    Scale raw_shape so that the mechanism's IMPLIED average daily "on"
    minutes matches the household's reported usage.

    Mechanism assumption (matches the per-minute Bernoulli engine):
    expected number of switch-on events starting in hour h ~= tou_hourly[h]
    (60 independent per-minute trials at probability tou_hourly[h]/60).
    So implied daily total minutes = mean_duration_min * sum(tou_hourly).

    Parameters
    ----------
    raw_shape : np.ndarray, 24 values, from build_raw_shape()
    mean_duration_min : float, typical session length (already in schema)
    days_per_week : float, from FREQUENCY_TO_DAYS_PER_WEEK or a direct value
    appliance_name : str, used only for warning messages

    Returns
    -------
    dict with:
        "tou_hourly"      : calibrated 24-value array, all in [0, 1]
        "target_minutes"  : the T_target this was calibrated to hit
        "scalar_k"        : the calibration scalar applied
        "clipped"         : bool, True if calibration required capping
                            at 1.0 (shape/window too narrow for the
                            reported usage -- see warning)
    """
    target_minutes = (days_per_week / 7.0) * mean_duration_min

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
            f"[WARN] {appliance_name}: calibration requires tou_hourly > 1.0 "
            f"at hour(s) {over_hours} -- the reported window is too narrow "
            f"for the reported usage volume. Capping at 1.0 (this will "
            f"UNDER-represent the reported total). Consider re-checking "
            f"the interview window with the household, or accept the "
            f"under-representation as a documented limitation."
        )
        calibrated = np.clip(calibrated, 0.0, 1.0)

    return {
        "tou_hourly":     [round(float(v), 4) for v in calibrated],
        "target_minutes": round(target_minutes, 3),
        "scalar_k":       round(float(k), 4),
        "clipped":        clipped,
    }


# =============================================================================
# STEP 3: VERIFICATION -- DOES THE ENGINE ACTUALLY HIT THE TARGET?
# =============================================================================

def verify_calibration(tou_hourly, mean_duration_min, std_duration_min,
                        target_minutes, n_days=500, restart_delay_min=10,
                        random_seed=None):
    """
    Simulate n_days using the SAME per-minute Bernoulli mechanism the real
    appliance engine uses, and check the average daily "on" minutes
    converges to target_minutes.

    This is the calibration-side analogue of CREST's own validation of
    their calibration scalars against known appliance-usage statistics --
    except here the "known statistic" is the household's own interview
    answer.

    Parameters
    ----------
    tou_hourly : list/array of 24 floats, from calibrate_shape()
    mean_duration_min, std_duration_min : appliance duration distribution
    target_minutes : float, the value this SHOULD converge to
    n_days : int, number of simulated days (independent of the model's
             own Monte Carlo run count -- this is just for verifying the
             calibration itself, so a few hundred is enough)
    restart_delay_min : int, minimum minutes before the appliance can
                        fire again after an event ends
    random_seed : int or None

    Returns
    -------
    dict with simulated mean, std, and pass/fail vs. target (using the
    same 10% MAE-style tolerance convention as the schema's validation
    thresholds).
    """
    rng = np.random.default_rng(random_seed)
    daily_totals = []

    for _ in range(n_days):
        minute = 0
        total_on = 0.0
        next_allowed = 0  # minute at which appliance can next switch on

        while minute < 1440:
            hour = int(minute // 60)
            p_per_minute = tou_hourly[hour] / 60.0

            if minute >= next_allowed and rng.random() < p_per_minute:
                duration = max(
                    1.0, rng.normal(mean_duration_min, std_duration_min)
                )
                total_on += duration
                minute += duration
                next_allowed = minute + restart_delay_min
            else:
                minute += 1

        daily_totals.append(total_on)

    sim_mean = float(np.mean(daily_totals))
    sim_std = float(np.std(daily_totals))

    pct_error = abs(sim_mean - target_minutes) / target_minutes if target_minutes > 0 else 0
    passed = pct_error <= 0.10  # same 10% convention as the schema's MAE threshold

    return {
        "simulated_mean_minutes": round(sim_mean, 2),
        "simulated_std_minutes":  round(sim_std, 2),
        "target_minutes":         round(target_minutes, 2),
        "pct_error":              round(pct_error * 100, 1),
        "passed":                 passed,
    }


def calibrate_shape_iterative(raw_shape, mean_duration_min, std_duration_min,
                               days_per_week, appliance_name="appliance",
                               restart_delay_min=10, n_days=300,
                               max_iterations=5, random_seed=None):
    """
    Refines calibrate_shape()'s closed-form scalar using the verification
    loop itself, correcting for the restart_delay bias (closed form
    slightly UNDER-estimates realised daily minutes, since it doesn't
    account for switch-on attempts blocked by an appliance still being
    in its restart-delay lockout).

    Each iteration: simulate with the current tou_hourly, compare
    simulated mean to target, rescale by (target / simulated), repeat.
    Stops early once within 10% tolerance or max_iterations is reached.
    """
    result = calibrate_shape(raw_shape, mean_duration_min, days_per_week,
                              appliance_name)
    tou = np.array(result["tou_hourly"])
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
        "tou_hourly":     [round(float(v), 4) for v in tou],
        "target_minutes": round(target, 3),
        "final_check":    check,
    }


# =============================================================================
# WORKED EXAMPLE: THE KETTLE
# =============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("WORKED EXAMPLE: electric_kettle")
    print("=" * 70)

    # Interview answers (this is what an interviewer would actually record):
    windows = [
        {"start_hour": 6, "end_hour": 9, "peak_hour": 7, "peak_value": 1.0},
        {"start_hour": 18, "end_hour": 20, "peak_hour": None, "peak_value": 0.4},
    ]
    mean_duration_min = 4
    std_duration_min = 1
    days_per_week = FREQUENCY_TO_DAYS_PER_WEEK["most_days"]  # 6.0

    raw_shape = build_raw_shape(windows)
    print(f"\nRaw shape (before calibration):\n{np.round(raw_shape, 3)}")
    print(f"Raw shape sum: {raw_shape.sum():.3f}")

    result = calibrate_shape(
        raw_shape, mean_duration_min, days_per_week,
        appliance_name="electric_kettle"
    )
    print(f"\nTarget daily minutes: {result['target_minutes']}")
    print(f"Calibration scalar k: {result['scalar_k']}")
    print(f"Calibrated tou_hourly:\n{result['tou_hourly']}")
    print(f"Clipped: {result['clipped']}")

    print("\nRunning verification (500 simulated days)...")
    check = verify_calibration(
        result["tou_hourly"], mean_duration_min, std_duration_min,
        result["target_minutes"], n_days=500, random_seed=42
    )
    print(f"\nSimulated mean daily 'on' minutes : {check['simulated_mean_minutes']}")
    print(f"Simulated std  daily 'on' minutes : {check['simulated_std_minutes']}")
    print(f"Target                            : {check['target_minutes']}")
    print(f"Percent error                     : {check['pct_error']}%")
    print(f"[{'PASS' if check['passed'] else 'FAIL'}] "
          f"within 10% tolerance: {check['passed']}")

    if not check["passed"]:
        print("\nClosed-form calibration missed tolerance -- refining "
              "iteratively using the verification loop itself:")
        refined = calibrate_shape_iterative(
            raw_shape, mean_duration_min, std_duration_min, days_per_week,
            appliance_name="electric_kettle", random_seed=42
        )
        print(f"\nRefined tou_hourly: {refined['tou_hourly']}")
        print(f"Final check: {refined['final_check']}")

