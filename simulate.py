"""
simulate.py
===========
Stochastic bottom-up residential load simulator (CREST-style, household level)
MSc Thesis: Multi-Objective Sizing for Hybrid Solar Systems
Author: Dan Munene Muchiri | JKUAT | ENM321-2049/2024

Turns one household (schema.py format, e.g. households/H902.json from
survey_to_schema.py) into Monte Carlo ensembles of 1-minute daily load
profiles, separately for weekdays and weekends, as described in the
proposal methodology (Residential Load Modelling).

SUB-MODELS
----------
1. Occupancy  : first-order Markov chain over n_t = number of active people
                (0 .. n_max), transition probabilities P(n_t | n_{t-1}).
                One transition matrix per hour, built from THIS household's
                interview occupancy: the chain's stationary distribution for
                hour h is the interview count occupancy[h] (split between the
                two nearest whole numbers when fractional), so its mean is
                the interview value. Moves are +/-1 person per minute
                (Metropolis birth-death chain), so occupancy changes
                smoothly between hours instead of jumping.

2. Appliances : per-minute Bernoulli switch-on with probability
                    p_i,t = f(TOU_i,t, n_t) = tou_i[h] / 60 * g(n_t)
                tou is the hourly switch-on rate (calibrate_tou.py);
                g(n_t) = 1 when someone is active, and
                model_parameters.appliance_occupancy_scale_zero when
                nobody is (needs_occupancy=True appliances only).
                Each event lasts Normal(mean_duration_min, std_duration_min)
                minutes, after which the appliance is locked out for
                restart_delay_min. This is the SAME mechanism
                calibrate_tou.verify_calibration() uses, so calibrated TOU
                arrays reproduce the interview's usage here. Each unit
                (count) is independent; standby_power_w is drawn whenever a
                unit is not running.

3. Lighting   : p_light,t = g(irradiance_t, bulbs, n_t). Artificial light is
                needed when the outdoor global horizontal irradiance is below
                the household's threshold (CREST: Normal(60, 10) W/m2, drawn
                per run). Irradiance is a REAL historical day from NASA POWER
                hourly data (nasa_power.py) for the same calendar month, so
                every run carries real cloud timing and the ensemble carries
                seasonal variation. Without the NASA cache it falls back to
                a half-sine daylight curve x clear-sky index (CSI) proxy.
                How each room's light switches (bulb "switching"):
                  presence : on while the room is occupied (room_occupancy),
                             it is dark and, if needs_occupancy, someone is
                             home -- interview rooms with known times;
                  dark     : on whenever it is dark in the hours tou > 0 --
                             dusk-to-dawn / timer security lights;
                  tou      : per-visit switch-ons at rate tou (may exceed 1
                             per hour) while dark (and someone home if
                             needs_occupancy) -- rooms with no fixed pattern
                             and motion-sensor lights.

ENSEMBLE SIZE (law of large numbers)
------------------------------------
Each run is an independent day, so the ensemble mean of any daily quantity
converges to its expectation as runs grow, with standard error sd/sqrt(n).
convergence() reports the running mean, the relative 95% half-width at the
chosen run count, and the runs needed for a target precision; the thesis's
1000 runs can be checked against it household by household.

VALIDATION
----------
Stage 1 (consistency): stage1_check() compares each appliance's simulated
mean daily energy with the energy implied by its interview parameters
(rated power x calibrated usage). Stage 2 (case study):
compare_to_measured() gives MAE, variability ratio and peak-to-average
ratio against power-logger data.

USAGE
-----
    python simulate.py                                  # reference household
    python simulate.py households/H902.json --runs 1000 --out results/
    python simulate.py households/H902.json --month 7   # July irradiance only
"""

import argparse
import csv
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from schema import get_active_bulbs, get_standard_appliances, validate_household

MINUTES_PER_DAY = 1440
DAY_TYPES = ("weekday", "weekend")
DEFAULT_THRESHOLD_WM2 = (60.0, 10.0)      # CREST lighting irradiance threshold, mean and sd


# =============================================================================
# SECTION 1: OCCUPANCY — FIRST-ORDER MARKOV CHAIN
# =============================================================================

def target_occupancy_distribution(expected, n_max):
    """
    Distribution of active occupants for one hour, from the interview value.

    The interview gives a count per hour (Section C groups, night taken
    out), so the target is that count itself: a whole number becomes a
    single state, and a fractional value x (e.g. 1.5) is split between
    floor(x) and ceil(x) with mean exactly x. This is the least-spread
    distribution with the interview's mean. (A Binomial(n_max, x/n_max)
    target was used before; it put nobody home 25% of the time when the
    interview said 1 of 2 residents is home, blocking home-only appliances
    the interview reports in use.)
    """
    x = min(max(float(expected), 0.0), float(n_max))
    lo = int(np.floor(x))
    pi = np.zeros(n_max + 1)
    if lo >= n_max:
        pi[n_max] = 1.0
    else:
        frac = x - lo
        pi[lo] = 1.0 - frac
        pi[lo + 1] += frac
    return pi


def build_transition_matrices(occupancy_hourly, n_max):
    """
    Build one (n_max+1) x (n_max+1) transition matrix P(n_t | n_{t-1}) per hour.

    Metropolis birth-death chain: from state i propose i+1 or i-1 with
    probability 1/2 each and accept with min(1, pi_h(j) / pi_h(i)), where
    pi_h is target_occupancy_distribution() for hour h. pi_h is therefore
    the stationary distribution of hour h's matrix. When the current state
    is impossible under pi_h (e.g. people still home at 00:00 when the
    interview says 0), moves towards the target are always accepted.

    Returns
    -------
    np.ndarray of shape (24, n_max+1, n_max+1); every row sums to 1.
    """
    n_states = n_max + 1
    mats = np.zeros((24, n_states, n_states))

    for h in range(24):
        pi = target_occupancy_distribution(occupancy_hourly[h], n_max)
        support = np.flatnonzero(pi > 0)
        for i in range(n_states):
            for j in (i - 1, i + 1):
                if not 0 <= j < n_states:
                    continue
                if pi[i] > 0:
                    accept = min(1.0, pi[j] / pi[i])
                else:
                    # Distance to the nearest state the target allows.
                    dist_i = np.min(np.abs(support - i))
                    dist_j = np.min(np.abs(support - j))
                    accept = 1.0 if dist_j < dist_i else 0.0
                mats[h, i, j] = 0.5 * accept
            mats[h, i, i] = 1.0 - mats[h, i].sum()

    return mats


def simulate_occupancy(occupancy_hourly, n_max, n_runs, rng):
    """
    Simulate minute-by-minute active occupancy for n_runs independent days.

    Minute 0 is drawn from hour 0's target distribution; each later minute
    is one step of the Markov chain using the matrix of the hour it falls in.

    Returns
    -------
    np.ndarray of int, shape (n_runs, 1440).
    """
    mats = build_transition_matrices(occupancy_hourly, n_max)
    cum = np.cumsum(mats, axis=2)
    cum[:, :, -1] = 1.0  # guard against round-off in the last column

    occ = np.zeros((n_runs, MINUTES_PER_DAY), dtype=np.int16)
    pi0 = target_occupancy_distribution(occupancy_hourly[0], n_max)
    state = rng.choice(n_max + 1, size=n_runs, p=pi0 / pi0.sum())
    occ[:, 0] = state

    u = rng.random((n_runs, MINUTES_PER_DAY))
    for m in range(1, MINUTES_PER_DAY):
        rows = cum[m // 60, state]
        state = (u[:, m, None] > rows).sum(axis=1)
        occ[:, m] = state

    return occ


# =============================================================================
# SECTION 2: SWITCHING ENGINE (shared by appliances and "tou" lights)
# =============================================================================

def simulate_switching(p_on, mean_duration_min, std_duration_min,
                       restart_delay_min, rng, min_duration_min=1):
    """
    Per-minute Bernoulli switch-on with event durations and restart lockout.

    Same mechanism as calibrate_tou.verify_calibration(): at every minute the
    unit is free, it switches on with probability p_on[r, m]; it then stays
    on for a Normal(mean, std) duration (rounded to whole minutes, at least
    min_duration_min) and cannot switch on again until restart_delay_min
    minutes after it switched off. Events are truncated at midnight.

    Parameters
    ----------
    p_on : np.ndarray (n_runs, 1440) of per-minute switch-on probabilities

    Returns
    -------
    np.ndarray of bool, shape (n_runs, 1440): True while the unit is on.
    """
    n_runs = p_on.shape[0]
    on = np.zeros(p_on.shape, dtype=bool)
    candidates = rng.random(p_on.shape) < np.minimum(p_on, 1.0)

    for r in range(n_runs):
        next_allowed = 0
        for m in np.flatnonzero(candidates[r]):
            if m < next_allowed:
                continue
            d = mean_duration_min
            if std_duration_min > 0:
                d = rng.normal(mean_duration_min, std_duration_min)
            d = max(int(round(d)), int(min_duration_min), 1)
            on[r, m:m + d] = True
            next_allowed = m + d + restart_delay_min

    return on


def _hourly_to_minutes(hourly):
    """Repeat a 24-value hourly array to 1440 minute values."""
    return np.repeat(np.asarray(hourly, dtype=float), 60)


# =============================================================================
# SECTION 3: LIGHTING — NATURAL LIGHT
# =============================================================================

def hourly_to_minute_irradiance(ghi_hourly):
    """
    Minute GHI (n, 1440) from hourly means (n, 24): linear interpolation
    between hour centres (h + 0.5), held flat before the first and after the
    last centre. Night hours are 0, so dawn and dusk ramp smoothly.
    """
    ghi_hourly = np.atleast_2d(np.asarray(ghi_hourly, dtype=float))
    centres = np.arange(24) + 0.5
    t = (np.arange(MINUTES_PER_DAY) + 0.5) / 60.0
    return np.array([np.interp(t, centres, row) for row in ghi_hourly])


def sample_irradiance_days(irradiance_days, n_runs, rng, month=None):
    """
    One real historical day of hourly GHI per run.

    irradiance_days : dict month -> (n_days, 24) array (nasa_power.load_irradiance_days)
    month           : 1-12 to use that month only (seasonal scaling), or None
                      for the whole year (each run draws a random day of any month)
    Returns (ghi_minutes (n_runs, 1440), months (n_runs,))
    """
    if month is not None:
        if month not in irradiance_days:
            raise ValueError(f"No irradiance days for month {month}")
        pool = irradiance_days[month]
        idx = rng.integers(0, len(pool), size=n_runs)
        return hourly_to_minute_irradiance(pool[idx]), np.full(n_runs, month)
    months = np.array(sorted(irradiance_days))
    counts = np.array([len(irradiance_days[m]) for m in months], dtype=float)
    chosen = rng.choice(months, size=n_runs, p=counts / counts.sum())
    days = np.array([irradiance_days[m][rng.integers(0, len(irradiance_days[m]))] for m in chosen])
    return hourly_to_minute_irradiance(days), chosen


def load_default_irradiance():
    """NASA POWER days from the cache (nasa_power.py), or None if absent."""
    try:
        from nasa_power import CACHE, load_irradiance_days
        if CACHE.exists():
            return load_irradiance_days()
    except Exception as e:                     # a broken cache must not stop the model
        warnings.warn(f"could not read NASA POWER cache: {e}", stacklevel=2)
    return None


def daylight_proxy(sunrise_hour, sunset_hour):
    """
    Fallback only. Relative natural light at each minute: a half-sine that
    is 0 at sunrise and sunset and 1 at solar noon, 0 at night. Shape (1440,).
    """
    t = (np.arange(MINUTES_PER_DAY) + 0.5) / 60.0
    frac = (t - sunrise_hour) / (sunset_hour - sunrise_hour)
    return np.where((frac > 0) & (frac < 1), np.sin(np.pi * frac), 0.0)


def sample_csi(model_params, n_runs, rng, csi_samples=None):
    """Fallback only: daily clear-sky index per run (fixed, or drawn from samples)."""
    if csi_samples is not None and len(csi_samples) > 0:
        return rng.choice(np.asarray(csi_samples, dtype=float), size=n_runs)
    return np.full(n_runs, float(model_params.get("csi_fixed_value", 0.7)))


def darkness(model_params, n_runs, rng, irradiance_days=None, month=None, csi_samples=None):
    """
    Where artificial light is needed, per run and minute, plus what drove it.

    With NASA POWER days: dark = GHI_t < threshold, threshold ~ Normal(mean, sd)
    W/m2 per run (model_parameters daylight_threshold_wm2 / _sd, CREST 60 / 10).
    Without: dark = CSI x daylight_proxy < daylight_threshold_csi (old proxy).

    Returns (dark bool (n_runs, 1440), info dict)
    """
    if irradiance_days:
        ghi, months = sample_irradiance_days(irradiance_days, n_runs, rng, month)
        mean = model_params.get("daylight_threshold_wm2", DEFAULT_THRESHOLD_WM2[0])
        sd = model_params.get("daylight_threshold_wm2_sd", DEFAULT_THRESHOLD_WM2[1])
        thr = np.clip(rng.normal(mean, sd, size=n_runs), 0, None)
        return ghi < thr[:, None], {"source": "nasa_power", "ghi_w_m2": ghi, "month": months,
                                    "threshold_w_m2": thr}
    warnings.warn("No NASA POWER irradiance (run `python nasa_power.py`); using the "
                  "CSI x daylight-curve proxy for lighting.", stacklevel=3)
    csi = sample_csi(model_params, n_runs, rng, csi_samples)
    proxy = daylight_proxy(model_params["nairobi_sunrise_hour"], model_params["nairobi_sunset_hour"])
    dark = (csi[:, None] * proxy[None, :]) < model_params["daylight_threshold_csi"]
    return dark, {"source": "csi_proxy", "csi": csi}


# =============================================================================
# SECTION 4: ENSEMBLE
# =============================================================================

@dataclass
class Ensemble:
    """Monte Carlo ensemble for one household and one day type."""

    household_id: str
    day_type: str
    power_w: np.ndarray            # (n_runs, 1440) total household power, W
    occupancy: np.ndarray          # (n_runs, 1440) active occupants
    light_info: dict = field(default_factory=dict)        # irradiance source, months, thresholds
    load_energy_kwh: dict = field(default_factory=dict)   # name -> (n_runs,)

    @property
    def n_runs(self):
        return self.power_w.shape[0]

    @property
    def daily_energy_kwh(self):
        """Daily energy of every run in kWh (1-minute steps)."""
        return self.power_w.sum(axis=1) / 60.0 / 1000.0

    @property
    def peak_w(self):
        """Peak 1-minute power of every run in W."""
        return self.power_w.max(axis=1)

    def mean_profile(self):
        """Average power at each minute across all runs, W."""
        return self.power_w.mean(axis=0)

    def max_profile(self):
        """The run with the highest peak (proposal: 'maximum' profile)."""
        return self.power_w[int(np.argmax(self.peak_w))]

    def min_profile(self):
        """The run with the lowest peak (proposal: 'minimum' profile)."""
        return self.power_w[int(np.argmin(self.peak_w))]

    def percentile_run(self, q, metric="energy"):
        """
        Index of the run closest to the q-th percentile of daily energy
        (metric="energy") or peak power (metric="peak").
        """
        if metric not in ("energy", "peak"):
            raise ValueError("metric must be 'energy' or 'peak'")
        values = self.daily_energy_kwh if metric == "energy" else self.peak_w
        target = np.percentile(values, q)
        return int(np.argmin(np.abs(values - target)))

    def percentile_profile(self, q, metric="energy"):
        """The single run closest to percentile q (e.g. 10, 50, 90), W."""
        return self.power_w[self.percentile_run(q, metric)]

    def percentile_runs(self, q, metric="energy", n_days=7):
        """
        Indices of the n_days REAL simulated days closest to the q-th
        percentile of daily energy (or peak), nearest first.

        One day chosen by its total alone has an almost arbitrary SHAPE --
        many days share the same kWh with very different timing (routine
        shift sensitivity test, 2026-11-23) -- so sizing uses several real
        days around each percentile instead of one, without averaging them
        into an artificial smooth day.
        """
        if metric not in ("energy", "peak"):
            raise ValueError("metric must be 'energy' or 'peak'")
        values = self.daily_energy_kwh if metric == "energy" else self.peak_w
        target = np.percentile(values, q)
        n_days = int(min(max(n_days, 1), len(values)))
        return [int(i) for i in np.argsort(np.abs(values - target), kind="stable")[:n_days]]

    def percentile_days(self, q, metric="energy", n_days=7):
        """(n_days, 1440) W: the real days from percentile_runs()."""
        return self.power_w[self.percentile_runs(q, metric, n_days)]

    def summary(self):
        """Daily energy and peak statistics as a plain dict."""
        e, p = self.daily_energy_kwh, self.peak_w
        return {
            "household_id":   self.household_id,
            "day_type":       self.day_type,
            "n_runs":         self.n_runs,
            "energy_kwh_p10": round(float(np.percentile(e, 10)), 3),
            "energy_kwh_p50": round(float(np.percentile(e, 50)), 3),
            "energy_kwh_p90": round(float(np.percentile(e, 90)), 3),
            "energy_kwh_mean": round(float(e.mean()), 3),
            "peak_w_p10":     round(float(np.percentile(p, 10)), 1),
            "peak_w_p50":     round(float(np.percentile(p, 50)), 1),
            "peak_w_p90":     round(float(np.percentile(p, 90)), 1),
            "irradiance":     self.light_info.get("source"),
        }

    def load_breakdown(self):
        """Mean daily kWh per load (appliance name or 'light:<room>'), largest first."""
        means = {k: float(v.mean()) for k, v in self.load_energy_kwh.items()}
        return dict(sorted(means.items(), key=lambda kv: -kv[1]))


# Behavioural day-to-day variation (defaults; model_parameters can override).
# Each simulated day nudges the household's habits around the interview:
#   routine shift   : one shift for the whole household that day (wake, leave,
#                     return, bedtime, room times, appliance windows together)
#   appliance jitter: a small extra shift per appliance
#   intensity       : a "busy / light day" factor per appliance, log-normal
#                     with mean exactly 1 (so mean energy is unchanged)
#   unusual days    : rare guest days / away days (off unless set)
# The sizes come from the HOUSEHOLD: survey_to_schema.py sets the routine
# shift from the interview's "how regular is your routine?" answer and each
# appliance's busy/light-day spread from its own "how likely" answers. The
# values below are only used when a household record has none (older forms,
# the reference household). The simulator itself uses no population data:
# the converter caps the household values against the KNBS KTUS 2021
# between-person spreads when it creates them. The power logger never sets
# them: it only tests the model (stage-2 validation).
BEHAVIOUR_DEFAULTS = {
    "routine_shift_sd_min_weekday": 30.0,
    "routine_shift_sd_min_weekend": 45.0,
    "appliance_jitter_sd_min": None,        # None = half the routine shift
    "use_intensity_sd": 0.3,
    "guest_day_prob": 0.0,
    "away_day_prob": 0.0,
}
GUEST_EXTRA_PEOPLE = 2          # people added on a guest day, while the household is home
GUEST_INTENSITY = 1.5           # home-only appliance use on a guest day
AWAY_HOURS = (9, 18)            # household out on an away day (no one home)


def behaviour_params(params, day_type):
    b = {k: params.get(k, v) for k, v in BEHAVIOUR_DEFAULTS.items()}
    b["routine_shift_sd_min"] = float(b[f"routine_shift_sd_min_{day_type}"])
    if b["appliance_jitter_sd_min"] is None:
        b["appliance_jitter_sd_min"] = b["routine_shift_sd_min"] / 2
    return b


def _shift_rows(arr, shifts):
    """Each row of arr (n, 1440) or a single (1440,) curve moved later by
    shifts[r] minutes (negative = earlier), wrapping round midnight."""
    arr = np.asarray(arr)
    idx = (np.arange(MINUTES_PER_DAY)[None, :] - np.asarray(shifts)[:, None]) % MINUTES_PER_DAY
    if arr.ndim == 1:
        return arr[idx]
    return np.take_along_axis(arr, idx, axis=1)


def _intensity(rng, n, sd):
    """Log-normal factors with mean exactly 1 (sd of the log = `sd`)."""
    if sd <= 0:
        return np.ones(n)
    return np.exp(rng.normal(-sd * sd / 2, sd, size=n))


def simulate_ensemble(household, day_type, n_runs=None, seed=None, month=None,
                      irradiance_days="default", csi_samples=None, validate=True):
    """
    Run the Monte Carlo load model for one household and one day type.

    Parameters
    ----------
    household       : dict following schema.py
    day_type        : "weekday" or "weekend"
    n_runs          : simulated days; default model_parameters.n_monte_carlo_runs
    seed            : int for reproducible runs; default model_parameters.random_seed
    month           : 1-12 to draw irradiance from that calendar month only
                      (seasonal case), None for the whole year
    irradiance_days : NASA POWER days (nasa_power.load_irradiance_days());
                      "default" reads the cache, None forces the CSI proxy
    csi_samples     : fallback only, daily CSI values for the proxy
    validate        : run schema.validate_household() first

    Returns
    -------
    Ensemble
    """
    if day_type not in DAY_TYPES:
        raise ValueError(f"day_type must be one of {DAY_TYPES}, got {day_type!r}")
    if validate:
        validate_household(household)

    params = household["model_parameters"]
    if params.get("timestep_minutes", 1) != 1:
        raise ValueError("The simulator only supports timestep_minutes = 1")
    if n_runs is None:
        n_runs = int(params["n_monte_carlo_runs"])
    if seed is None:
        seed = params.get("random_seed")
    rng = np.random.default_rng(seed)
    if isinstance(irradiance_days, str) and irradiance_days == "default":
        irradiance_days = load_default_irradiance()

    n_max = params.get("markov_n_max") or household["n_residents"]
    beh = behaviour_params(params, day_type)
    sd = beh["routine_shift_sd_min"]
    routine = np.clip(np.round(rng.normal(0, sd, n_runs)), -3 * sd, 3 * sd).astype(int) if sd > 0 \
        else np.zeros(n_runs, dtype=int)
    occupancy = _shift_rows(simulate_occupancy(household[f"occupancy_{day_type}"], n_max, n_runs, rng),
                            routine)
    u = rng.random(n_runs)
    guest = u < beh["guest_day_prob"]
    away = (u >= beh["guest_day_prob"]) & (u < beh["guest_day_prob"] + beh["away_day_prob"])
    if guest.any():
        occupancy[guest] = np.where(occupancy[guest] > 0, occupancy[guest] + GUEST_EXTRA_PEOPLE, 0)
    if away.any():
        a, b = (h * 60 for h in AWAY_HOURS)
        occupancy[np.ix_(away, np.arange(a, b))] = 0
    someone_home = occupancy > 0

    power = np.zeros((n_runs, MINUTES_PER_DAY))
    load_energy = {}
    tou_key = f"tou_{day_type}"
    clip = params.get("duration_clip_min_minutes", 1)

    # ── Appliances ───────────────────────────────────────────────────────────
    scale_zero = params["appliance_occupancy_scale_zero"]
    for appl in get_standard_appliances(household):
        base = _hourly_to_minutes(appl[tou_key]) / 60.0
        if appl["needs_occupancy"]:
            # habits move with the household's routine, plus the appliance's own jitter
            jit = beh["appliance_jitter_sd_min"]
            p = _shift_rows(base, routine)
            if jit > 0:
                # The appliance's own jitter moves use, but never INTO hours nobody
                # is home: a day whose jitter would put less of the appliance's use
                # in occupied minutes than the routine shift alone keeps the routine
                # shift. (Otherwise a random 15-min nudge would silently delete
                # reported use and lower the mean below the interview.)
                pj = _shift_rows(base, routine + np.round(rng.normal(0, jit, n_runs)).astype(int))
                home = someone_home.astype(float)
                keep = (pj * home).sum(axis=1) >= (p * home).sum(axis=1) - 1e-12
                p = np.where(keep[:, None], pj, p)
            isd = float(appl.get("use_intensity_sd", beh["use_intensity_sd"]))
            p = p * _intensity(rng, n_runs, isd)[:, None]
            p = p * np.where(guest, GUEST_INTENSITY, 1.0)[:, None]
            p = p * np.where(someone_home, 1.0, scale_zero)
        else:
            # always-on / timer / machine-driven loads keep their clock times
            p = np.broadcast_to(base, (n_runs, MINUTES_PER_DAY))

        appl_power = np.zeros((n_runs, MINUTES_PER_DAY))
        for _ in range(appl["count"]):
            on = simulate_switching(p, appl["mean_duration_min"], appl["std_duration_min"],
                                    appl["restart_delay_min"], rng, clip)
            appl_power += np.where(on, appl["rated_power_w"], appl.get("standby_power_w", 0))
        power += appl_power
        load_energy[appl["name"]] = appl_power.sum(axis=1) / 60.0 / 1000.0

    # ── Lighting ─────────────────────────────────────────────────────────────
    dark, light_info = darkness(params, n_runs, rng, irradiance_days, month, csi_samples)
    room_occ = household.get("room_occupancy", {})

    for bulb in get_active_bulbs(household):
        switching = bulb.get("switching", "tou")
        tou_minutes = _hourly_to_minutes(bulb[tou_key])
        allowed = dark
        if bulb["needs_occupancy"]:
            allowed = allowed & someone_home

        if switching == "presence":
            room = room_occ.get(bulb["room"], {}).get(day_type)
            occupied = _hourly_to_minutes(room if room is not None else bulb[tou_key]) > 0
            on = allowed & _shift_rows(occupied, routine)             # room times move with the routine
        elif switching == "dark":
            on = allowed & (tou_minutes > 0)[None, :]                 # timers / dusk-to-dawn: clock time
        else:  # "tou": per-visit switch-ons at rate tou while light is needed
            rate = _shift_rows(tou_minutes / 60.0, routine) if bulb["needs_occupancy"] \
                else (tou_minutes / 60.0)[None, :]                    # motion lights: clock time
            on = simulate_switching(rate * allowed, bulb["mean_duration_min"], bulb["std_duration_min"],
                                    bulb["restart_delay_min"], rng, clip)
        bulb_power = on * (bulb["count"] * bulb["wattage_w"])
        power += bulb_power
        load_energy[f"light:{bulb['room']}"] = bulb_power.sum(axis=1) / 60.0 / 1000.0

    light_info = dict(light_info, routine_shift_min=routine, guest_day=guest, away_day=away)
    return Ensemble(household_id=household["household_id"], day_type=day_type, power_w=power,
                    occupancy=occupancy, light_info=light_info, load_energy_kwh=load_energy)


def simulate_day(household, day_type="weekday", seed=None, month=None, irradiance_days="default"):
    """One simulated day: household power in W at each of the 1440 minutes."""
    ens = simulate_ensemble(household, day_type, n_runs=1, seed=seed, month=month,
                            irradiance_days=irradiance_days)
    return ens.power_w[0]


# =============================================================================
# SECTION 5: ENSEMBLE SIZE — LAW OF LARGE NUMBERS
# =============================================================================

def convergence(values, rel_precision=0.01, z=1.96, checkpoints=(50, 100, 250, 500, 1000, 2000)):
    """
    How well the ensemble mean of a daily quantity has converged.

    Runs are independent days, so by the law of large numbers the running
    mean converges to the expectation, and its standard error is sd/sqrt(n)
    (central limit theorem). Returns the mean, the relative 95% half-width
    z*sd/(sqrt(n)*mean) at the ensemble size, the runs needed for
    `rel_precision`, and the running mean at the checkpoints reached.
    """
    v = np.asarray(values, dtype=float)
    n = len(v)
    mean, sd = float(v.mean()), float(v.std(ddof=1)) if n > 1 else 0.0
    half = z * sd / np.sqrt(n) if n > 0 else float("nan")
    rel = half / mean if mean else float("nan")
    needed = int(np.ceil((z * sd / (rel_precision * mean)) ** 2)) if mean else 0
    running = {k: round(float(v[:k].mean()), 4) for k in checkpoints if k <= n}
    return {"n_runs": n, "mean": round(mean, 4), "sd": round(sd, 4),
            "rel_half_width_95": round(float(rel), 5), "runs_needed": needed,
            "target_rel_precision": rel_precision, "running_mean": running}


def merge_ensembles(parts):
    """One Ensemble from batches of the same household and day type."""
    first = parts[0]
    info = {"source": first.light_info.get("source")}
    for k in ("ghi_w_m2", "month", "threshold_w_m2", "csi", "routine_shift_min", "guest_day", "away_day"):
        if k in first.light_info:
            info[k] = np.concatenate([p.light_info[k] for p in parts])
    return Ensemble(household_id=first.household_id, day_type=first.day_type,
                    power_w=np.concatenate([p.power_w for p in parts]),
                    occupancy=np.concatenate([p.occupancy for p in parts]),
                    light_info=info,
                    load_energy_kwh={k: np.concatenate([p.load_energy_kwh[k] for p in parts])
                                     for k in first.load_energy_kwh})


def simulate_until_converged(household, day_type, rel_precision=0.01, min_runs=1000, max_runs=5000,
                             batch=500, seed=None, month=None, irradiance_days="default"):
    """
    Law of large numbers in practice: add independent runs in batches until
    the 95% confidence half-width of BOTH the mean daily energy and the mean
    daily peak is within `rel_precision` of the mean (or max_runs is reached).
    Batches use seeds seed, seed+1, ... so a seeded run is reproducible.
    Returns (Ensemble, converged: bool).
    """
    if isinstance(irradiance_days, str) and irradiance_days == "default":
        irradiance_days = load_default_irradiance()
    parts, k = [], 0
    while True:
        n = min_runs if not parts else batch
        s = None if seed is None else seed + k
        parts.append(simulate_ensemble(household, day_type, n_runs=n, seed=s, month=month,
                                       irradiance_days=irradiance_days, validate=(k == 0)))
        k += 1
        ens = merge_ensembles(parts) if len(parts) > 1 else parts[0]
        c = ensemble_convergence(ens, rel_precision)
        done = all(c[q]["rel_half_width_95"] <= rel_precision for q in ("energy_kwh", "peak_w"))
        if done or ens.n_runs >= max_runs:
            return ens, done


def ensemble_convergence(ens, rel_precision=0.01):
    """convergence() for daily energy and daily peak of one ensemble."""
    return {"energy_kwh": convergence(ens.daily_energy_kwh, rel_precision),
            "peak_w": convergence(ens.peak_w, rel_precision)}


# =============================================================================
# SECTION 6: VALIDATION
# =============================================================================

def expected_appliance_kwh(appl, day_type, n_runs=2000, seed=0, min_duration_min=1, per_run=False):
    """
    Interview-implied mean daily energy of one appliance (kWh): its rated
    power and calibrated usage run through the same switching engine, but
    WITHOUT the occupancy gate -- i.e. what the interview answers alone
    imply (calibrate_tou.py calibrates tou to exactly this). Comparing it
    with the full simulation shows how much the occupancy model removes
    or adds (proposal: validation stage 1).
    """
    rng = np.random.default_rng(seed)
    p = np.broadcast_to(_hourly_to_minutes(appl[f"tou_{day_type}"]) / 60.0, (n_runs, MINUTES_PER_DAY))
    on_min = np.zeros(n_runs)
    for _ in range(appl["count"]):
        on = simulate_switching(p, appl["mean_duration_min"], appl["std_duration_min"],
                                appl["restart_delay_min"], rng, min_duration_min)
        on_min += on.sum(axis=1)
    standby_min = appl["count"] * MINUTES_PER_DAY - on_min
    kwh = (appl["rated_power_w"] * on_min + appl.get("standby_power_w", 0) * standby_min) / 60.0 / 1000.0
    return kwh if per_run else float(kwh.mean())


def stage1_check(household, ens, tolerance=0.10, n_runs=2000):
    """
    Validation stage 1: per appliance, simulated vs interview-implied kWh/day.
    Rows are flagged when they differ by more than `tolerance` (relative) AND
    by more than the Monte Carlo noise of both estimates (3 combined standard
    errors), so rarely used appliances do not raise false alarms.
    """
    rows = []
    clip = household["model_parameters"].get("duration_clip_min_minutes", 1)
    for appl in get_standard_appliances(household):
        x = expected_appliance_kwh(appl, ens.day_type, n_runs, min_duration_min=clip, per_run=True)
        exp = float(x.mean())
        e = ens.load_energy_kwh[appl["name"]]
        sim = float(e.mean())
        se = float(np.sqrt((e.var(ddof=1) / len(e) if len(e) > 1 else 0.0) + x.var(ddof=1) / len(x)))
        ratio = sim / exp if exp > 0 else float("nan")
        rows.append({"load": appl["name"], "expected_kwh": round(exp, 4),
                     "simulated_kwh": round(sim, 4), "ratio": round(ratio, 3),
                     "flag": bool(exp > 0 and abs(ratio - 1) > tolerance and abs(sim - exp) > 3 * se),
                     "needs_occupancy": appl["needs_occupancy"]})
    return rows


def load_measured_csv(path):
    """
    Power-logger data -> complete days at 1-minute resolution, per day type.

    CSV with columns `timestamp` (YYYY-MM-DD HH:MM[:SS], East Africa Time) and
    `power_w`. Readings are averaged into 1-minute bins; a day is kept when at
    least 95% of its minutes have data (gaps filled by interpolation).
    Returns {"weekday": (n_days, 1440), "weekend": (n_days, 1440)}.
    """
    import datetime as dt
    days = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ts = row["timestamp"].strip()
            t = dt.datetime.strptime(ts[:16], "%Y-%m-%d %H:%M")
            d = days.setdefault(t.date(), [[0.0, 0] for _ in range(MINUTES_PER_DAY)])
            cell = d[t.hour * 60 + t.minute]
            cell[0] += float(row["power_w"])
            cell[1] += 1
    out = {"weekday": [], "weekend": []}
    for date, cells in sorted(days.items()):
        vals = np.array([c[0] / c[1] if c[1] else np.nan for c in cells])
        ok = ~np.isnan(vals)
        if ok.mean() < 0.95:
            continue
        vals = np.interp(np.arange(MINUTES_PER_DAY), np.flatnonzero(ok), vals[ok])
        out["weekend" if date.weekday() >= 5 else "weekday"].append(vals)
    return {k: np.array(v) for k, v in out.items() if v}


def validate_against_measured(ens, measured_days):
    """
    Validation stage 2 (proposal): the ensemble's mean, maximum-peak and
    minimum-peak profiles against the measured mean day, highest-peak day and
    lowest-peak day of the same day type. Returns {profile: metrics} using
    compare_to_measured() (MAE, variability ratio, peak-to-average ratios),
    plus daily-energy and peak comparisons.
    """
    m = np.atleast_2d(np.asarray(measured_days, dtype=float))
    peaks = m.max(axis=1)
    pairs = {"mean": (ens.mean_profile(), m.mean(axis=0)),
             "max": (ens.max_profile(), m[int(np.argmax(peaks))]),
             "min": (ens.min_profile(), m[int(np.argmin(peaks))])}
    out = {}
    for name, (model, meas) in pairs.items():
        r = compare_to_measured(model, meas)
        r["energy_kwh_model"] = round(float(model.sum() / 60000), 3)
        r["energy_kwh_measured"] = round(float(meas.sum() / 60000), 3)
        r["peak_w_model"], r["peak_w_measured"] = float(model.max()), float(meas.max())
        out[name] = r
    out["n_measured_days"] = int(m.shape[0])
    return out


def assign_tier(weekday, weekend):
    """
    Consumption tier from the two ensembles (schema.py tier definitions).

    Uses the week-weighted median daily energy (5 weekdays + 2 weekend days).
    Returns (tier, median_kwh_per_day).
    """
    median = (5 * np.median(weekday.daily_energy_kwh) + 2 * np.median(weekend.daily_energy_kwh)) / 7
    tier = "low" if median < 5 else "medium" if median <= 15 else "high"
    return tier, round(float(median), 3)


def compare_to_measured(model_w, measured_w):
    """
    Validation metrics between a modelled and a measured 1-minute profile
    (proposal: Model Validation, stage 2).

    Returns dict with MAE (W), variability ratio sigma_model / sigma_meas,
    and the peak-to-average ratio of each profile.
    """
    model_w = np.asarray(model_w, dtype=float)
    measured_w = np.asarray(measured_w, dtype=float)
    if model_w.shape != measured_w.shape:
        raise ValueError(f"Profiles differ in length: {model_w.shape} vs {measured_w.shape}")

    def par(x):
        return float(x.max() / x.mean()) if x.mean() > 0 else float("nan")

    sigma_meas = measured_w.std()
    return {
        "mae_w":             float(np.mean(np.abs(model_w - measured_w))),
        "variability_ratio": float(model_w.std() / sigma_meas) if sigma_meas > 0 else float("nan"),
        "par_model":         par(model_w),
        "par_measured":      par(measured_w),
    }


# =============================================================================
# SECTION 7: OUTPUT AND COMMAND LINE
# =============================================================================

def write_profiles_csv(path, ensembles):
    """
    Mean, max/min-peak and 10th/50th/90th percentile-day profiles (W) for
    each ensemble, one row per minute. Percentile days are chosen by daily
    energy (p*_energy) and by peak power (p*_peak) for the MILP.
    """
    columns = {}
    for ens in ensembles:
        d = ens.day_type
        columns[f"{d}_mean_w"] = ens.mean_profile()
        columns[f"{d}_max_w"] = ens.max_profile()
        columns[f"{d}_min_w"] = ens.min_profile()
        for metric in ("energy", "peak"):
            for q in (10, 50, 90):
                columns[f"{d}_p{q}_{metric}_w"] = ens.percentile_profile(q, metric)

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["minute", "time"] + list(columns))
        for m in range(MINUTES_PER_DAY):
            writer.writerow([m, f"{m // 60:02d}:{m % 60:02d}"]
                            + [round(float(v[m]), 2) for v in columns.values()])


PERCENTILES = (10, 50, 90)


def write_percentile_days_csv(path, ensembles, n_days=7):
    """
    The real representative days for the MILP: for each day type, metric
    (energy, peak) and percentile (10, 50, 90), the n_days simulated days
    closest to that percentile, one column each (W), one row per minute.
    Column names: <day_type>_p<q>_<metric>_d<k>, k = 1 (closest) .. n_days.
    Returns a list of dicts describing every day (for the summary JSON).
    """
    columns, info = {}, []
    for ens in ensembles:
        e, pk = ens.daily_energy_kwh, ens.peak_w
        for metric in ("energy", "peak"):
            for q in PERCENTILES:
                for k, r in enumerate(ens.percentile_runs(q, metric, n_days), 1):
                    name = f"{ens.day_type}_p{q}_{metric}_d{k}"
                    columns[name] = ens.power_w[r]
                    info.append({"column": name, "day_type": ens.day_type, "metric": metric,
                                 "percentile": q, "rank": k, "run": r,
                                 "energy_kwh": round(float(e[r]), 3), "peak_w": round(float(pk[r]), 1)})
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["minute", "time"] + list(columns))
        for m in range(MINUTES_PER_DAY):
            writer.writerow([m, f"{m // 60:02d}:{m % 60:02d}"]
                            + [round(float(v[m]), 2) for v in columns.values()])
    return info


def _print_ensemble(household, ens, top=8):
    s = ens.summary()
    print(f"\n{s['day_type'].upper()}  ({s['n_runs']} runs, lighting from {s['irradiance']})")
    print(f"  Daily energy kWh  p10 {s['energy_kwh_p10']:.2f}   p50 {s['energy_kwh_p50']:.2f}   "
          f"p90 {s['energy_kwh_p90']:.2f}   mean {s['energy_kwh_mean']:.2f}")
    print(f"  Peak power W      p10 {s['peak_w_p10']:.0f}   p50 {s['peak_w_p50']:.0f}   "
          f"p90 {s['peak_w_p90']:.0f}")
    hourly = ens.mean_profile().reshape(24, 60).mean(axis=1)
    print("  Mean hourly W     " + " ".join(f"{v:.0f}" for v in hourly))
    c = ensemble_convergence(ens)
    for k, label in (("energy_kwh", "energy"), ("peak_w", "peak")):
        cc = c[k]
        print(f"  Convergence {label:6s} mean +/- {100 * cc['rel_half_width_95']:.2f}% (95%), "
              f"{cc['runs_needed']} runs for +/-1%")
    print("  Largest loads (mean kWh/day):")
    for name, kwh in list(ens.load_breakdown().items())[:top]:
        print(f"    {name:32s} {kwh:6.2f}")
    flagged = [r for r in stage1_check(household, ens) if r["flag"]]
    if flagged:
        print("  Stage-1 check, simulated vs interview-implied (>10% apart):")
        for r in flagged:
            print(f"    {r['load']:28s} sim {r['simulated_kwh']:.3f}  expected {r['expected_kwh']:.3f}"
                  f"  ratio {r['ratio']:.2f}{'  (occupancy-gated)' if r['needs_occupancy'] else ''}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("household", nargs="?", help="household JSON (default: schema reference household)")
    parser.add_argument("--day-type", choices=DAY_TYPES + ("both",), default="both")
    parser.add_argument("--runs", type=int, default=None,
                        help="Monte Carlo runs per day type (default: model_parameters.n_monte_carlo_runs)")
    parser.add_argument("--month", type=int, choices=range(1, 13), default=None,
                        help="irradiance from this calendar month only (default: whole year)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--precision", type=float, default=None,
                        help="adaptive run count: add runs until mean energy AND mean peak are known to "
                             "this relative 95%% precision (e.g. 0.01), from --runs (default 1000) up to "
                             "--max-runs")
    parser.add_argument("--max-runs", type=int, default=5000)
    parser.add_argument("--days-per-percentile", type=int, default=7, choices=range(5, 11),
                        help="real representative days kept per percentile for the MILP (5-10, default 7)")
    parser.add_argument("--measured", default=None,
                        help="power-logger CSV (timestamp, power_w) for validation stage 2")
    parser.add_argument("--out", default=None,
                        help="folder for <id>_profiles.csv, <id>_summary.json and <id>_profiles.svg")
    args = parser.parse_args(argv)

    if args.household:
        household = json.loads(Path(args.household).read_text())
    else:
        from schema import REFERENCE_HOUSEHOLD
        household = REFERENCE_HOUSEHOLD
    day_types = DAY_TYPES if args.day_type == "both" else (args.day_type,)
    irr = load_default_irradiance()
    converged = {}
    if args.precision:
        ensembles = []
        for d in day_types:
            ens, ok = simulate_until_converged(household, d, args.precision, min_runs=args.runs or 1000,
                                               max_runs=args.max_runs, seed=args.seed, month=args.month,
                                               irradiance_days=irr)
            ensembles.append(ens)
            converged[d] = ok
    else:
        ensembles = [simulate_ensemble(household, d, n_runs=args.runs, seed=args.seed, month=args.month,
                                       irradiance_days=irr) for d in day_types]
    measured = load_measured_csv(args.measured) if args.measured else {}
    stage2 = {e.day_type: validate_against_measured(e, measured[e.day_type])
              for e in ensembles if e.day_type in measured}

    print(f"Household {household['household_id']}")
    for ens in ensembles:
        _print_ensemble(household, ens)
        if ens.day_type in converged:
            print(f"  Adaptive run count: {ens.n_runs} runs, "
                  f"{'converged' if converged[ens.day_type] else 'NOT converged (max runs reached)'} "
                  f"to +/-{100 * args.precision:g}%")
        if ens.day_type in stage2:
            v = stage2[ens.day_type]
            print(f"  Stage 2 vs logger ({v['n_measured_days']} measured days):")
            for name in ("mean", "max", "min"):
                r = v[name]
                print(f"    {name:4s} MAE {r['mae_w']:.0f} W  variability {r['variability_ratio']:.2f}  "
                      f"P/A model {r['par_model']:.2f} vs measured {r['par_measured']:.2f}  "
                      f"kWh {r['energy_kwh_model']:.2f} vs {r['energy_kwh_measured']:.2f}")
    tier = assign_tier(*ensembles) if len(ensembles) == 2 else None
    if tier:
        print(f"\nTier: {tier[0]} (week-weighted median {tier[1]:.2f} kWh/day)")
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        hid = household["household_id"]
        write_profiles_csv(out / f"{hid}_profiles.csv", ensembles)
        days = write_percentile_days_csv(out / f"{hid}_percentile_days.csv", ensembles,
                                         args.days_per_percentile)
        summary = {"household_id": hid, "month": args.month,
                   "percentile_days": days,
                   "tier": tier[0] if tier else None, "week_weighted_median_kwh": tier[1] if tier else None,
                   "ensembles": [dict(e.summary(), convergence=ensemble_convergence(e, args.precision or 0.01),
                                      converged=converged.get(e.day_type),
                                      stage1=stage1_check(household, e),
                                      stage2=stage2.get(e.day_type),
                                      load_breakdown_kwh=e.load_breakdown()) for e in ensembles]}
        (out / f"{hid}_summary.json").write_text(json.dumps(summary, indent=1))
        from plot_profiles import write_profiles_svg
        write_profiles_svg(out / f"{hid}_profiles.svg", ensembles, title=f"Household {hid}")
        print(f"\nWrote {hid}_profiles.csv, {hid}_percentile_days.csv ({args.days_per_percentile} real days "
              f"per percentile), {hid}_summary.json and {hid}_profiles.svg in {out}")


if __name__ == "__main__":
    main()
