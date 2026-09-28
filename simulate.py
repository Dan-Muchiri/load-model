"""
simulate.py
===========
Stochastic bottom-up residential load simulator (CREST-style, household level)
MSc Thesis: Multi-Objective Sizing for Hybrid Solar Systems
Author: Dan Munene Muchiri | JKUAT | ENM321-2049/2024

Turns one household dict (see schema.py) into Monte Carlo ensembles of
1-minute daily load profiles, separately for weekdays and weekends, as
described in the proposal methodology (Residential Load Modelling).

SUB-MODELS
----------
1. Occupancy  : first-order Markov chain over n_t = number of active people
                (0 .. n_max). One transition matrix per hour, built so that
                the chain's stationary distribution for hour h is
                Binomial(n_max, occupancy[h] / n_max), whose mean is the
                interview value occupancy[h]. Moves are +/-1 person per
                minute (Metropolis birth-death chain), so occupancy changes
                smoothly between hours instead of jumping.

2. Appliances : per-minute Bernoulli switch-on with probability
                    p_i,t = tou_i[h] / 60 * f(n_t)
                f(n_t) = 1 when someone is active, and
                model_parameters.appliance_occupancy_scale_zero when
                nobody is (only for needs_occupancy=True appliances).
                Each event lasts Normal(mean_duration_min, std_duration_min)
                minutes (clipped at duration_clip_min_minutes), after which
                the appliance is locked out for restart_delay_min.
                This is the SAME mechanism verify_calibration() in
                calibrate_tou.py uses, so calibrated TOU arrays reproduce
                their target usage here. Each unit (count) is independent.
                standby_power_w is drawn whenever a unit is not running.

3. Lighting   : same Bernoulli mechanism per room (all bulbs in a room
                switch together; a switch-on = someone entering the room),
                gated by
                  - darkness: daylight_proxy(t) * CSI < daylight_threshold_csi
                    where daylight_proxy is a half-sine between sunrise and
                    sunset, and CSI is the day's clear-sky index;
                  - occupancy (needs_occupancy=True only): household n_t > 0
                    and, when the room appears in room_occupancy, that room
                    being occupied in hour h.

OUTPUT
------
simulate_ensemble() returns an Ensemble holding total household power in W
at 1-minute resolution for every run (shape n_runs x 1440), the occupancy
trajectories, and each load's daily energy per run. Helpers give the
daily energy / peak statistics, the 10th/50th/90th percentile
representative days used by the sizing stage, and the validation metrics
(MAE, variability ratio, peak-to-average ratio) used against the power
logger data.

USAGE
-----
    python simulate.py                       # reference household, both day types
    python simulate.py --runs 200 --seed 1 --csv profiles.csv

    from schema import REFERENCE_HOUSEHOLD
    from simulate import simulate_ensemble, simulate_day
    ens = simulate_ensemble(REFERENCE_HOUSEHOLD, "weekday", n_runs=1000, seed=1)
    profile_w = simulate_day(REFERENCE_HOUSEHOLD, "weekday", seed=1)
"""

import argparse
import csv
import warnings
from dataclasses import dataclass, field

import numpy as np

from schema import get_active_bulbs, get_standard_appliances, validate_household

MINUTES_PER_DAY = 1440
DAY_TYPES = ("weekday", "weekend")


# =============================================================================
# SECTION 1: OCCUPANCY — FIRST-ORDER MARKOV CHAIN
# =============================================================================

def target_occupancy_distribution(expected, n_max):
    """
    Distribution of active occupants for one hour: Binomial(n_max, expected/n_max).

    Its mean is exactly the interview value `expected`. An hour reported as
    0 (everyone out or asleep) or n_max (everyone home) becomes a single state.
    """
    from math import comb

    p = min(max(expected / n_max, 0.0), 1.0) if n_max > 0 else 0.0
    return np.array([comb(n_max, k) * p**k * (1 - p)**(n_max - k)
                     for k in range(n_max + 1)])


def build_transition_matrices(occupancy_hourly, n_max):
    """
    Build one (n_max+1) x (n_max+1) transition matrix per hour.

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
# SECTION 2: SWITCHING ENGINE (shared by appliances and lighting)
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
    candidates = rng.random(p_on.shape) < p_on

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
# SECTION 3: LIGHTING — DAYLIGHT
# =============================================================================

def daylight_proxy(sunrise_hour, sunset_hour):
    """
    Relative natural light at each minute: a half-sine that is 0 at sunrise
    and sunset and 1 at solar noon, 0 at night. Shape (1440,).
    """
    t = (np.arange(MINUTES_PER_DAY) + 0.5) / 60.0
    frac = (t - sunrise_hour) / (sunset_hour - sunrise_hour)
    return np.where((frac > 0) & (frac < 1), np.sin(np.pi * frac), 0.0)


def darkness_mask(csi, model_params):
    """
    True where artificial light is needed, for each run's clear-sky index.

    csi : np.ndarray (n_runs,) of daily CSI values.
    Returns np.ndarray of bool, shape (n_runs, 1440).
    """
    proxy = daylight_proxy(model_params["nairobi_sunrise_hour"],
                           model_params["nairobi_sunset_hour"])
    effective = csi[:, None] * proxy[None, :]
    return effective < model_params["daylight_threshold_csi"]


def sample_csi(model_params, n_runs, rng, csi_samples=None):
    """
    Daily clear-sky index per run.

    csi_source = "fixed"      -> csi_fixed_value for every run.
    csi_source = "nasa_power" -> drawn with replacement from csi_samples
                                 (historical daily CSI for the site). If no
                                 samples are passed, falls back to
                                 csi_fixed_value with a warning.
    """
    source = model_params.get("csi_source", "fixed")
    if source == "nasa_power":
        if csi_samples is not None and len(csi_samples) > 0:
            return rng.choice(np.asarray(csi_samples, dtype=float), size=n_runs)
        warnings.warn(
            "csi_source is 'nasa_power' but no csi_samples were given; "
            "using csi_fixed_value for every run.",
            stacklevel=3,
        )
    elif source != "fixed":
        raise ValueError(f"Unknown csi_source: {source!r}")
    return np.full(n_runs, float(model_params["csi_fixed_value"]))


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
    csi: np.ndarray                # (n_runs,) clear-sky index used per run
    load_energy_kwh: dict = field(default_factory=dict)  # name -> (n_runs,)

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
        """The representative day for percentile q (e.g. 10, 50, 90), W."""
        return self.power_w[self.percentile_run(q, metric)]

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
        }

    def load_breakdown(self):
        """Mean daily kWh per load (appliance name or 'light:<room>'), largest first."""
        means = {k: float(v.mean()) for k, v in self.load_energy_kwh.items()}
        return dict(sorted(means.items(), key=lambda kv: -kv[1]))


def simulate_ensemble(household, day_type, n_runs=None, seed=None,
                      csi_samples=None, validate=True):
    """
    Run the Monte Carlo load model for one household and one day type.

    Parameters
    ----------
    household   : dict following schema.py
    day_type    : "weekday" or "weekend"
    n_runs      : number of simulated days; defaults to
                  model_parameters.n_monte_carlo_runs
    seed        : int for reproducible runs; defaults to
                  model_parameters.random_seed (None = fresh randomness)
    csi_samples : optional array of historical daily clear-sky index values
                  (used when model_parameters.csi_source = "nasa_power")
    validate    : run schema.validate_household() first

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

    n_max = params.get("markov_n_max") or household["n_residents"]
    occupancy = simulate_occupancy(household[f"occupancy_{day_type}"], n_max,
                                   n_runs, rng)
    someone_home = occupancy > 0

    power = np.zeros((n_runs, MINUTES_PER_DAY))
    load_energy = {}
    tou_key = f"tou_{day_type}"
    clip = params.get("duration_clip_min_minutes", 1)

    # ── Appliances ───────────────────────────────────────────────────────────
    scale_zero = params["appliance_occupancy_scale_zero"]
    for appl in get_standard_appliances(household):
        p = np.broadcast_to(_hourly_to_minutes(appl[tou_key]) / 60.0,
                            (n_runs, MINUTES_PER_DAY))
        if appl["needs_occupancy"]:
            p = p * np.where(someone_home, 1.0, scale_zero)

        appl_power = np.zeros((n_runs, MINUTES_PER_DAY))
        for _ in range(appl["count"]):
            on = simulate_switching(p, appl["mean_duration_min"],
                                    appl["std_duration_min"],
                                    appl["restart_delay_min"], rng, clip)
            appl_power += np.where(on, appl["rated_power_w"],
                                   appl.get("standby_power_w", 0))
        power += appl_power
        load_energy[appl["name"]] = appl_power.sum(axis=1) / 60.0 / 1000.0

    # ── Lighting ─────────────────────────────────────────────────────────────
    csi = sample_csi(params, n_runs, rng, csi_samples)
    dark = darkness_mask(csi, params)
    room_occ = household.get("room_occupancy", {})

    for bulb in get_active_bulbs(household):
        allowed = dark
        if bulb["needs_occupancy"]:
            allowed = allowed & someone_home
            schedule = room_occ.get(bulb["room"], {}).get(day_type)
            if schedule is not None:
                allowed = allowed & (_hourly_to_minutes(schedule) > 0)[None, :]

        p = (_hourly_to_minutes(bulb[tou_key]) / 60.0)[None, :] * allowed
        on = simulate_switching(p, bulb["mean_duration_min"],
                                bulb["std_duration_min"],
                                bulb["restart_delay_min"], rng, clip)
        bulb_power = on * (bulb["count"] * bulb["wattage_w"])
        power += bulb_power
        load_energy[f"light:{bulb['room']}"] = bulb_power.sum(axis=1) / 60.0 / 1000.0

    return Ensemble(
        household_id=household["household_id"],
        day_type=day_type,
        power_w=power,
        occupancy=occupancy,
        csi=csi,
        load_energy_kwh=load_energy,
    )


def simulate_day(household, day_type="weekday", seed=None, csi_samples=None):
    """One simulated day: household power in W at each of the 1440 minutes."""
    ens = simulate_ensemble(household, day_type, n_runs=1, seed=seed,
                            csi_samples=csi_samples)
    return ens.power_w[0]


# =============================================================================
# SECTION 5: TIER AND VALIDATION METRICS
# =============================================================================

def assign_tier(weekday, weekend):
    """
    Consumption tier from the two ensembles (schema.py tier definitions).

    Uses the week-weighted median daily energy (5 weekdays + 2 weekend days).
    Returns (tier, median_kwh_per_day).
    """
    median = (5 * np.median(weekday.daily_energy_kwh)
              + 2 * np.median(weekend.daily_energy_kwh)) / 7
    if median < 5:
        tier = "low"
    elif median <= 15:
        tier = "medium"
    else:
        tier = "high"
    return tier, round(float(median), 3)


def compare_to_measured(model_w, measured_w):
    """
    Validation metrics between a modelled and a measured 1-minute profile
    (proposal: Model Validation).

    Returns dict with MAE (W), variability ratio sigma_model / sigma_meas,
    and the peak-to-average ratio of each profile.
    """
    model_w = np.asarray(model_w, dtype=float)
    measured_w = np.asarray(measured_w, dtype=float)
    if model_w.shape != measured_w.shape:
        raise ValueError(
            f"Profiles differ in length: {model_w.shape} vs {measured_w.shape}"
        )

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
# SECTION 6: COMMAND LINE
# =============================================================================

def write_profiles_csv(path, ensembles):
    """
    Write mean, 10th/50th/90th percentile-day and max/min profiles (W) for
    each ensemble to one CSV, one row per minute.
    """
    columns = {}
    for ens in ensembles:
        d = ens.day_type
        columns[f"{d}_mean_w"] = ens.mean_profile()
        for q in (10, 50, 90):
            columns[f"{d}_p{q}_w"] = ens.percentile_profile(q)
        columns[f"{d}_max_w"] = ens.max_profile()
        columns[f"{d}_min_w"] = ens.min_profile()

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["minute", "time"] + list(columns))
        for m in range(MINUTES_PER_DAY):
            writer.writerow([m, f"{m // 60:02d}:{m % 60:02d}"]
                            + [round(float(v[m]), 2) for v in columns.values()])


def _print_ensemble(ens, top=8):
    s = ens.summary()
    print(f"\n{s['day_type'].upper()}  ({s['n_runs']} runs)")
    print(f"  Daily energy kWh  p10 {s['energy_kwh_p10']:.2f}   "
          f"p50 {s['energy_kwh_p50']:.2f}   p90 {s['energy_kwh_p90']:.2f}")
    print(f"  Peak power W      p10 {s['peak_w_p10']:.0f}   "
          f"p50 {s['peak_w_p50']:.0f}   p90 {s['peak_w_p90']:.0f}")
    hourly = ens.mean_profile().reshape(24, 60).mean(axis=1)
    print("  Mean hourly W     " + " ".join(f"{v:.0f}" for v in hourly))
    print(f"  Largest loads (mean kWh/day):")
    for name, kwh in list(ens.load_breakdown().items())[:top]:
        print(f"    {name:32s} {kwh:6.2f}")


def main(argv=None):
    from schema import REFERENCE_HOUSEHOLD

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[3])
    parser.add_argument("--day-type", choices=DAY_TYPES + ("both",), default="both")
    parser.add_argument("--runs", type=int, default=None,
                        help="Monte Carlo runs per day type "
                             "(default: model_parameters.n_monte_carlo_runs)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--csv", default=None,
                        help="write representative profiles to this CSV file")
    args = parser.parse_args(argv)

    household = REFERENCE_HOUSEHOLD
    day_types = DAY_TYPES if args.day_type == "both" else (args.day_type,)
    ensembles = [simulate_ensemble(household, d, n_runs=args.runs, seed=args.seed)
                 for d in day_types]

    print(f"Household {household['household_id']}")
    for ens in ensembles:
        _print_ensemble(ens)
    if len(ensembles) == 2:
        tier, median = assign_tier(*ensembles)
        print(f"\nTier: {tier} (week-weighted median {median:.2f} kWh/day)")
    if args.csv:
        write_profiles_csv(args.csv, ensembles)
        print(f"\nProfiles written to {args.csv}")


if __name__ == "__main__":
    main()
