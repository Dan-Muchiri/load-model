import csv

import numpy as np
import pytest

from calibrate_tou import verify_calibration
from simulate import (
    Ensemble,
    assign_tier,
    build_transition_matrices,
    compare_to_measured,
    daylight_proxy,
    sample_csi,
    simulate_day,
    simulate_ensemble,
    simulate_occupancy,
    simulate_switching,
    target_occupancy_distribution,
    write_profiles_csv,
)


# ── Occupancy Markov chain ───────────────────────────────────────────────────

def test_transition_matrices_are_stochastic_with_target_stationary_distribution(household):
    occ = household["occupancy_weekday"]
    n_max = household["n_residents"]
    mats = build_transition_matrices(occ, n_max)

    assert mats.shape == (24, n_max + 1, n_max + 1)
    assert np.all(mats >= 0)
    np.testing.assert_allclose(mats.sum(axis=2), 1.0)
    for h in range(24):
        pi = target_occupancy_distribution(occ[h], n_max)
        np.testing.assert_allclose(pi @ mats[h], pi, atol=1e-12)
        assert pi @ np.arange(n_max + 1) == pytest.approx(occ[h])


def test_simulated_occupancy_follows_interview_schedule(household):
    occ = household["occupancy_weekday"]
    n_max = household["n_residents"]
    sim = simulate_occupancy(occ, n_max, 400, np.random.default_rng(0))

    assert sim.shape == (400, 1440)
    assert sim.min() >= 0 and sim.max() <= n_max
    # Second half of each hour, once the chain has settled.
    hourly = sim.reshape(400, 24, 60)[:, :, 30:].mean(axis=(0, 2))
    np.testing.assert_allclose(hourly, occ, atol=0.6)
    # Hours reported as 0 (asleep) are simulated as nobody active.
    assert np.all(sim[:, 60:300] == 0)


# ── Switching engine ─────────────────────────────────────────────────────────

def test_switching_matches_calibration_verification():
    """Calibrated TOU arrays must hit the same usage in the simulator as in calibrate_tou."""
    tou = [0.0] * 6 + [0.3, 0.5, 0.3, 0.1, 0.1] + [0.0] * 7 + [0.2, 0.1] + [0.0] * 4
    mean_d, std_d, restart = 4, 1, 20

    p = np.broadcast_to(np.repeat(tou, 60) / 60.0, (2000, 1440))
    on = simulate_switching(p, mean_d, std_d, restart, np.random.default_rng(1))
    sim_minutes = on.sum(axis=1).mean()

    ref = verify_calibration(tou, mean_d, std_d, target_minutes=1.0, n_days=2000,
                             restart_delay_min=restart, random_seed=2)
    assert sim_minutes == pytest.approx(ref["simulated_mean_minutes"], rel=0.1)


def test_switching_respects_restart_delay():
    p = np.ones((50, 1440))  # would switch on every free minute
    on = simulate_switching(p, 10, 0, 30, np.random.default_rng(0))
    # 10 min on, 30 min locked out -> 40-minute cycle from minute 0.
    expected = (np.arange(1440) % 40) < 10
    assert np.array_equal(on[0], expected)
    assert np.all(on == on[0])


# ── Lighting ─────────────────────────────────────────────────────────────────

def test_daylight_proxy_is_zero_at_night_and_peaks_at_noon():
    proxy = daylight_proxy(6.5, 18.5)
    assert proxy[: 6 * 60].max() == 0 and proxy[19 * 60:].max() == 0
    assert proxy[12 * 60 + 29] == pytest.approx(1.0, abs=1e-3)


def test_no_lighting_in_bright_daytime(household):
    household["model_parameters"]["csi_fixed_value"] = 1.0
    ens = simulate_ensemble(household, "weekday", n_runs=50, seed=3)
    lights = sum(v for k, v in ens.load_energy_kwh.items() if k.startswith("light:"))
    assert lights.mean() > 0
    # Rebuild with only bulbs: no light may switch ON between 09:00 and 16:00
    # (one switched on in the dark morning may still be finishing its visit).
    for a in household["appliances"]:
        a["count"] = 0
    only_lights = simulate_ensemble(household, "weekday", n_runs=50, seed=3)
    rises = np.diff(only_lights.power_w, axis=1) > 0
    assert not rises[:, 9 * 60:16 * 60].any()
    assert rises[:, 18 * 60:22 * 60].any()


def test_csi_sampled_from_nasa_power_values(household):
    params = household["model_parameters"]
    params["csi_source"] = "nasa_power"
    csi = sample_csi(params, 500, np.random.default_rng(0), csi_samples=[0.2, 0.8])
    assert set(np.unique(csi)) == {0.2, 0.8}
    with pytest.warns(UserWarning):
        fallback = sample_csi(params, 5, np.random.default_rng(0))
    assert np.all(fallback == params["csi_fixed_value"])


# ── Full household ───────────────────────────────────────────────────────────

def test_reference_household_produces_daily_profile(household):
    profile = simulate_day(household, "weekday", seed=42)
    assert profile.shape == (1440,)
    assert np.all(profile >= 0)
    # Router + fence + CCTV standby always draw power.
    assert profile.min() >= 12 + 25 + 30
    energy_kwh = profile.sum() / 60 / 1000
    assert 2 < energy_kwh < 40


def test_simulation_is_reproducible_with_seed(household):
    a = simulate_day(household, "weekend", seed=7)
    b = simulate_day(household, "weekend", seed=7)
    c = simulate_day(household, "weekend", seed=8)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_load_breakdown_sums_to_total(household):
    ens = simulate_ensemble(household, "weekday", n_runs=30, seed=5)
    total = sum(ens.load_energy_kwh.values())
    np.testing.assert_allclose(total, ens.daily_energy_kwh)


def test_occupancy_gating_blocks_appliances_when_nobody_home(empty_household):
    h = empty_household
    h["occupancy_weekday"] = [0] * 24
    h["model_parameters"]["appliance_occupancy_scale_zero"] = 0.0
    kettle = next(a for a in h["appliances"] if a["name"] == "electric_kettle")
    kettle["count"] = 1
    ens = simulate_ensemble(h, "weekday", n_runs=100, seed=0)
    assert ens.daily_energy_kwh.max() == 0

    h["occupancy_weekday"] = [h["n_residents"]] * 24
    ens = simulate_ensemble(h, "weekday", n_runs=100, seed=0)
    assert ens.daily_energy_kwh.mean() > 0


def test_count_scales_energy(empty_household):
    h = empty_household
    fridge = next(a for a in h["appliances"] if a["name"] == "refrigerator")
    fridge["count"] = 1
    one = simulate_ensemble(h, "weekday", n_runs=300, seed=1).daily_energy_kwh.mean()
    fridge["count"] = 2
    two = simulate_ensemble(h, "weekday", n_runs=300, seed=1).daily_energy_kwh.mean()
    assert two == pytest.approx(2 * one, rel=0.1)


def test_invalid_household_is_rejected(household):
    household["n_residents"] = 0
    with pytest.raises(ValueError):
        simulate_ensemble(household, "weekday", n_runs=2)
    with pytest.raises(ValueError):
        simulate_ensemble(household, "holiday", n_runs=2, validate=False)


# ── Ensemble statistics ──────────────────────────────────────────────────────

def test_percentile_days_are_ordered(household):
    ens = simulate_ensemble(household, "weekday", n_runs=200, seed=11)
    e = ens.daily_energy_kwh
    assert e[ens.percentile_run(10)] <= e[ens.percentile_run(50)] <= e[ens.percentile_run(90)]
    assert ens.peak_w[ens.percentile_run(90, "peak")] >= ens.peak_w[ens.percentile_run(10, "peak")]
    assert ens.max_profile().max() == ens.peak_w.max()
    assert ens.min_profile().max() == ens.peak_w.min()
    s = ens.summary()
    assert s["energy_kwh_p10"] <= s["energy_kwh_p50"] <= s["energy_kwh_p90"]


def _flat_ensemble(day_type, watts):
    power = np.full((3, 1440), float(watts))
    return Ensemble("H", day_type, power, np.zeros((3, 1440)), np.ones(3))


@pytest.mark.parametrize("watts,tier", [(100, "low"), (400, "medium"), (800, "high")])
def test_assign_tier(watts, tier):
    # 100 W -> 2.4 kWh/day, 400 W -> 9.6, 800 W -> 19.2
    got, median = assign_tier(_flat_ensemble("weekday", watts),
                              _flat_ensemble("weekend", watts))
    assert got == tier
    assert median == pytest.approx(watts * 24 / 1000)


def test_compare_to_measured():
    measured = np.array([100.0, 200.0, 300.0, 400.0])
    same = compare_to_measured(measured, measured)
    assert same["mae_w"] == 0 and same["variability_ratio"] == 1
    assert same["par_model"] == pytest.approx(400 / 250)

    shifted = compare_to_measured(measured + 50, measured)
    assert shifted["mae_w"] == 50
    with pytest.raises(ValueError):
        compare_to_measured(measured, measured[:3])


def test_write_profiles_csv(household, tmp_path):
    ensembles = [simulate_ensemble(household, d, n_runs=20, seed=1)
                 for d in ("weekday", "weekend")]
    path = tmp_path / "profiles.csv"
    write_profiles_csv(path, ensembles)
    rows = list(csv.reader(open(path)))
    assert len(rows) == 1441
    assert rows[0][:3] == ["minute", "time", "weekday_mean_w"]
    assert "weekend_p90_w" in rows[0]
    assert rows[-1][1] == "23:59"
