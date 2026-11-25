import csv
import json

import numpy as np
import pytest

import nasa_power
from calibrate_tou import verify_calibration
from simulate import (
    Ensemble,
    assign_tier,
    build_transition_matrices,
    compare_to_measured,
    convergence,
    darkness,
    daylight_proxy,
    expected_appliance_kwh,
    hourly_to_minute_irradiance,
    main,
    sample_csi,
    sample_irradiance_days,
    simulate_day,
    simulate_ensemble,
    simulate_occupancy,
    simulate_switching,
    stage1_check,
    target_occupancy_distribution,
    write_profiles_csv,
)

# A clear tropical day (W/m2, hour h = mean over h:00-h+1:00) and a dark storm day.
CLEAR = [0] * 6 + [10, 120, 330, 550, 730, 860, 930, 900, 780, 610, 400, 190, 25] + [0] * 5
STORM = [0] * 6 + [2, 20, 40, 50, 55, 55, 55, 50, 45, 40, 30, 10, 2] + [0] * 5
BRIGHT_ONLY = {m: np.array([CLEAR]) for m in range(1, 13)}
NO_BEHAVIOUR = {"routine_shift_sd_min_weekday": 0, "routine_shift_sd_min_weekend": 0,
                "appliance_jitter_sd_min": 0, "use_intensity_sd": 0}


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


def test_switching_rate_above_one_per_hour():
    """tou > 1 (several switch-ons an hour) is honoured, as calibrate_tou allows."""
    p = np.broadcast_to(np.repeat([0.0] * 7 + [4.0] + [0.0] * 16, 60) / 60.0, (2000, 1440))
    on = simulate_switching(p, 2, 0, 0, np.random.default_rng(3))
    starts = (np.diff(on.astype(int), axis=1) == 1).sum(axis=1)
    assert starts.mean() > 3


def test_switching_respects_restart_delay():
    p = np.ones((50, 1440))  # would switch on every free minute
    on = simulate_switching(p, 10, 0, 30, np.random.default_rng(0))
    # 10 min on, 30 min locked out -> 40-minute cycle from minute 0.
    expected = (np.arange(1440) % 40) < 10
    assert np.array_equal(on[0], expected)
    assert np.all(on == on[0])


# ── Natural light ────────────────────────────────────────────────────────────

def test_hourly_irradiance_interpolates_smoothly():
    ghi = hourly_to_minute_irradiance([CLEAR])[0]
    assert ghi.shape == (1440,)
    assert ghi[:5 * 60 + 30].max() == 0 and ghi[19 * 60 + 30:].max() == 0   # night
    assert ghi[12 * 60 + 30] == pytest.approx(930, abs=1)       # hour centre keeps its value
    assert np.all(np.abs(np.diff(ghi)) < 20)                     # no jumps between hours


def test_irradiance_days_come_from_the_requested_month():
    days = {1: np.array([CLEAR]), 7: np.array([STORM])}
    ghi, months = sample_irradiance_days(days, 50, np.random.default_rng(0), month=7)
    assert set(months) == {7}
    assert ghi.max() < 60
    ghi, months = sample_irradiance_days(days, 400, np.random.default_rng(0))
    assert set(months) == {1, 7}


def test_darkness_uses_threshold_and_real_day(household):
    params = dict(household["model_parameters"], daylight_threshold_wm2=60, daylight_threshold_wm2_sd=0)
    dark, info = darkness(params, 10, np.random.default_rng(0), {3: np.array([CLEAR])}, month=3)
    assert info["source"] == "nasa_power"
    assert dark[:, 3 * 60].all() and dark[:, 22 * 60].all()       # night
    assert not dark[:, 9 * 60:16 * 60].any()                      # clear day
    dark, _ = darkness(params, 10, np.random.default_rng(0), {3: np.array([STORM])}, month=3)
    assert dark[:, 12 * 60].all()                                  # storm: lights at noon


def test_csi_proxy_fallback(household):
    params = household["model_parameters"]
    csi = sample_csi(params, 500, np.random.default_rng(0), csi_samples=[0.2, 0.8])
    assert set(np.unique(csi)) == {0.2, 0.8}
    assert np.all(sample_csi(params, 5, np.random.default_rng(0)) == params["csi_fixed_value"])
    with pytest.warns(UserWarning):
        dark, info = darkness(params, 4, np.random.default_rng(0), None)
    assert info["source"] == "csi_proxy" and dark[:, 2 * 60].all()


def test_daylight_proxy_is_zero_at_night_and_peaks_at_noon():
    proxy = daylight_proxy(6.5, 18.5)
    assert proxy[: 6 * 60].max() == 0 and proxy[19 * 60:].max() == 0
    assert proxy[12 * 60 + 29] == pytest.approx(1.0, abs=1e-3)


def _lights_only(household):
    for a in household["appliances"]:
        a["count"] = 0
    return household


def test_no_lights_switch_on_in_bright_daytime(household):
    h = _lights_only(household)
    ens = simulate_ensemble(h, "weekday", n_runs=50, seed=3, irradiance_days=BRIGHT_ONLY)
    rises = np.diff(ens.power_w, axis=1) > 0
    assert not rises[:, 9 * 60:16 * 60].any()
    assert rises[:, 18 * 60:23 * 60].any()


def _one_bulb(household, **bulb):
    h = _lights_only(household)
    for b in h["bulbs"]:
        b["count"] = 0
    b = h["bulbs"][0]
    b.update(count=1, wattage_w=10, **bulb)
    return h, b["room"]


def test_presence_light_on_exactly_when_room_occupied_and_dark(household):
    h, room = _one_bulb(household, switching="presence", needs_occupancy=False)
    occ = [0] * 24
    occ[20] = 1                                            # someone in the room 20:00-21:00
    h["room_occupancy"][room] = {"weekday": occ, "weekend": occ}
    h["model_parameters"].update(NO_BEHAVIOUR)
    ens = simulate_ensemble(h, "weekday", n_runs=20, seed=0, irradiance_days=BRIGHT_ONLY)
    on = ens.power_w > 0
    assert on[:, 20 * 60:21 * 60].all() and not on[:, :20 * 60].any() and not on[:, 21 * 60:].any()


def test_dark_light_on_whenever_dark_in_its_hours(household):
    tou = [1.0] * 6 + [0.0] * 12 + [1.0] * 6
    h, _ = _one_bulb(household, switching="dark", needs_occupancy=False,
                     tou_weekday=tou, tou_weekend=tou, mean_duration_min=60, std_duration_min=0)
    ens = simulate_ensemble(h, "weekday", n_runs=10, seed=0, irradiance_days=BRIGHT_ONLY)
    on = ens.power_w > 0
    assert on[:, :6 * 60].all() and on[:, 19 * 60 + 30:].all() and not on[:, 9 * 60:16 * 60].any()
    np.testing.assert_allclose(ens.daily_energy_kwh, 10 * on.sum(axis=1) / 60 / 1000)


def test_tou_light_switches_per_visit_while_dark(household):
    tou = [0.0] * 20 + [3.0, 3.0] + [0.0] * 2                  # 3 visits an hour, 20:00-22:00
    h, _ = _one_bulb(household, switching="tou", needs_occupancy=False, tou_weekday=tou,
                     tou_weekend=tou, mean_duration_min=5, std_duration_min=0, restart_delay_min=0)
    ens = simulate_ensemble(h, "weekday", n_runs=500, seed=1, irradiance_days=BRIGHT_ONLY)
    on = ens.power_w > 0
    assert not on[:, :20 * 60].any()
    minutes = on.sum(axis=1).mean()
    expected = 2 * 60 * 5 / (60 / 3 + 5)                       # renewal cycle, about 24 min
    assert minutes == pytest.approx(expected, rel=0.15)


# ── Full household ───────────────────────────────────────────────────────────

def test_reference_household_produces_daily_profile(household):
    profile = simulate_day(household, "weekday", seed=42, irradiance_days=BRIGHT_ONLY)
    assert profile.shape == (1440,)
    assert np.all(profile >= 0)
    # Router + fence + CCTV standby always draw power.
    assert profile.min() >= 12 + 25 + 30
    energy_kwh = profile.sum() / 60 / 1000
    assert 2 < energy_kwh < 40


def test_simulation_is_reproducible_with_seed(household):
    a = simulate_day(household, "weekend", seed=7, irradiance_days=BRIGHT_ONLY)
    b = simulate_day(household, "weekend", seed=7, irradiance_days=BRIGHT_ONLY)
    c = simulate_day(household, "weekend", seed=8, irradiance_days=BRIGHT_ONLY)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_load_breakdown_sums_to_total(household):
    ens = simulate_ensemble(household, "weekday", n_runs=30, seed=5, irradiance_days=BRIGHT_ONLY)
    total = sum(ens.load_energy_kwh.values())
    np.testing.assert_allclose(total, ens.daily_energy_kwh)


def test_occupancy_gating_blocks_appliances_when_nobody_home(empty_household):
    h = empty_household
    h["occupancy_weekday"] = [0] * 24
    h["model_parameters"]["appliance_occupancy_scale_zero"] = 0.0
    kettle = next(a for a in h["appliances"] if a["name"] == "electric_kettle")
    kettle["count"] = 1
    ens = simulate_ensemble(h, "weekday", n_runs=100, seed=0, irradiance_days=BRIGHT_ONLY)
    assert ens.daily_energy_kwh.max() == 0

    h["occupancy_weekday"] = [h["n_residents"]] * 24
    ens = simulate_ensemble(h, "weekday", n_runs=100, seed=0, irradiance_days=BRIGHT_ONLY)
    assert ens.daily_energy_kwh.mean() > 0


def test_count_scales_energy(empty_household):
    h = empty_household
    fridge = next(a for a in h["appliances"] if a["name"] == "refrigerator")
    fridge["count"] = 1
    one = simulate_ensemble(h, "weekday", n_runs=300, seed=1, irradiance_days=BRIGHT_ONLY)
    fridge["count"] = 2
    two = simulate_ensemble(h, "weekday", n_runs=300, seed=1, irradiance_days=BRIGHT_ONLY)
    assert two.daily_energy_kwh.mean() == pytest.approx(2 * one.daily_energy_kwh.mean(), rel=0.1)


def test_invalid_household_is_rejected(household):
    household["n_residents"] = 0
    with pytest.raises(ValueError):
        simulate_ensemble(household, "weekday", n_runs=2)
    with pytest.raises(ValueError):
        simulate_ensemble(household, "holiday", n_runs=2, validate=False)


# ── Law of large numbers / ensemble size ─────────────────────────────────────

def test_convergence_matches_known_distribution():
    rng = np.random.default_rng(0)
    v = rng.normal(10.0, 2.0, size=4000)
    c = convergence(v, rel_precision=0.01)
    assert c["mean"] == pytest.approx(10.0, rel=0.01)
    # half-width 1.96*2/sqrt(4000)/10 ~ 0.62%; runs for 1%: (1.96*2/0.1)^2 ~ 1537
    assert c["rel_half_width_95"] == pytest.approx(0.0062, rel=0.1)
    assert c["runs_needed"] == pytest.approx(1537, rel=0.1)
    assert list(c["running_mean"]) == [50, 100, 250, 500, 1000, 2000]


def test_ensemble_mean_converges_as_runs_grow(empty_household):
    """Law of large numbers: more runs -> the mean settles (smaller standard error)."""
    h = empty_household
    next(a for a in h["appliances"] if a["name"] == "electric_kettle")["count"] = 1
    h["occupancy_weekday"] = [h["n_residents"]] * 24
    small = simulate_ensemble(h, "weekday", n_runs=100, seed=2, irradiance_days=BRIGHT_ONLY)
    large = simulate_ensemble(h, "weekday", n_runs=1600, seed=2, irradiance_days=BRIGHT_ONLY)
    cs, cl = convergence(small.daily_energy_kwh), convergence(large.daily_energy_kwh)
    assert cl["rel_half_width_95"] < cs["rel_half_width_95"] / 3


# ── Validation ───────────────────────────────────────────────────────────────

def test_stage1_matches_when_nobody_is_ever_away(empty_household):
    h = empty_household
    next(a for a in h["appliances"] if a["name"] == "electric_kettle")["count"] = 1
    h["occupancy_weekday"] = [h["n_residents"]] * 24
    ens = simulate_ensemble(h, "weekday", n_runs=1500, seed=4, irradiance_days=BRIGHT_ONLY)
    row = next(r for r in stage1_check(h, ens) if r["load"] == "electric_kettle")
    assert row["ratio"] == pytest.approx(1.0, abs=0.1) and not row["flag"]


def test_stage1_flags_use_lost_to_occupancy(empty_household):
    h = empty_household
    kettle = next(a for a in h["appliances"] if a["name"] == "electric_kettle")
    kettle["count"] = 1
    h["occupancy_weekday"] = [0] * 24                        # nobody ever home
    h["model_parameters"]["appliance_occupancy_scale_zero"] = 0.0
    ens = simulate_ensemble(h, "weekday", n_runs=300, seed=4, irradiance_days=BRIGHT_ONLY)
    row = next(r for r in stage1_check(h, ens) if r["load"] == "electric_kettle")
    assert row["simulated_kwh"] == 0 and row["flag"]
    assert expected_appliance_kwh(kettle, "weekday") > 0


def test_percentile_days_are_ordered(household):
    ens = simulate_ensemble(household, "weekday", n_runs=200, seed=11, irradiance_days=BRIGHT_ONLY)
    e = ens.daily_energy_kwh
    assert e[ens.percentile_run(10)] <= e[ens.percentile_run(50)] <= e[ens.percentile_run(90)]
    assert ens.peak_w[ens.percentile_run(90, "peak")] >= ens.peak_w[ens.percentile_run(10, "peak")]
    assert ens.max_profile().max() == ens.peak_w.max()
    assert ens.min_profile().max() == ens.peak_w.min()
    s = ens.summary()
    assert s["energy_kwh_p10"] <= s["energy_kwh_p50"] <= s["energy_kwh_p90"]


def _flat_ensemble(day_type, watts):
    power = np.full((3, 1440), float(watts))
    return Ensemble("H", day_type, power, np.zeros((3, 1440)))


@pytest.mark.parametrize("watts,tier", [(100, "low"), (400, "medium"), (800, "high")])
def test_assign_tier(watts, tier):
    # 100 W -> 2.4 kWh/day, 400 W -> 9.6, 800 W -> 19.2
    got, median = assign_tier(_flat_ensemble("weekday", watts), _flat_ensemble("weekend", watts))
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


# ── Output ───────────────────────────────────────────────────────────────────

def test_write_profiles_csv(household, tmp_path):
    ensembles = [simulate_ensemble(household, d, n_runs=20, seed=1, irradiance_days=BRIGHT_ONLY)
                 for d in ("weekday", "weekend")]
    path = tmp_path / "profiles.csv"
    write_profiles_csv(path, ensembles)
    rows = list(csv.reader(open(path)))
    assert len(rows) == 1441
    assert rows[0][:3] == ["minute", "time", "weekday_mean_w"]
    assert "weekend_p90_energy_w" in rows[0] and "weekday_p10_peak_w" in rows[0]
    assert rows[-1][1] == "23:59"


def test_command_line_on_a_household_file(household, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("simulate.load_default_irradiance", lambda: BRIGHT_ONLY)
    path = tmp_path / "H999.json"
    household["household_id"] = "H999"
    path.write_text(json.dumps(household))
    main([str(path), "--runs", "30", "--seed", "1", "--month", "7", "--out", str(tmp_path)])
    s = json.loads((tmp_path / "H999_summary.json").read_text())
    assert s["month"] == 7 and len(s["ensembles"]) == 2
    assert s["ensembles"][0]["convergence"]["energy_kwh"]["n_runs"] == 30
    assert (tmp_path / "H999_profiles.csv").exists()
    assert "Tier:" in capsys.readouterr().out


# ── NASA POWER cache ─────────────────────────────────────────────────────────

def test_nasa_cache_reader(tmp_path):
    path = tmp_path / "cache.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_eat", "ghi_allsky_wm2", "ghi_clearsky_wm2"])
        for d, values in (("2024-03-01", CLEAR), ("2024-03-02", STORM)):
            for h, g in enumerate(values):
                w.writerow([f"{d} {h:02d}:00", g, CLEAR[h]])
        w.writerow(["2024-03-03 00:00", -999.0, -999.0])          # incomplete day: dropped
    days = nasa_power.load_irradiance_days(path)
    assert list(days) == [3] and days[3].shape == (2, 24)
    csi = nasa_power.daily_csi(path)
    assert csi[min(csi)] == pytest.approx(1.0)
    assert 0 < csi[max(csi)] < 0.1


# ── Adaptive run count (law of large numbers) ────────────────────────────────

def test_simulate_until_converged_stops_at_precision(empty_household):
    from simulate import simulate_until_converged
    h = empty_household
    next(a for a in h["appliances"] if a["name"] == "refrigerator")["count"] = 1
    ens, ok = simulate_until_converged(h, "weekday", rel_precision=0.02, min_runs=200, max_runs=3000,
                                       batch=200, seed=3, irradiance_days=BRIGHT_ONLY)
    c = convergence(ens.daily_energy_kwh)
    assert ok and c["rel_half_width_95"] <= 0.02
    assert ens.power_w.shape == (ens.n_runs, 1440)
    assert all(len(v) == ens.n_runs for v in ens.load_energy_kwh.values())


def test_simulate_until_converged_respects_max_runs(empty_household):
    from simulate import simulate_until_converged
    h = empty_household
    next(a for a in h["appliances"] if a["name"] == "electric_kettle")["count"] = 1
    h["occupancy_weekday"] = [h["n_residents"]] * 24
    ens, ok = simulate_until_converged(h, "weekday", rel_precision=0.0001, min_runs=100, max_runs=300,
                                       batch=100, seed=3, irradiance_days=BRIGHT_ONLY)
    assert not ok and ens.n_runs == 300


# ── Validation stage 2 (power logger) ────────────────────────────────────────

def test_logger_csv_and_stage2(household, tmp_path):
    from simulate import load_measured_csv, validate_against_measured
    path = tmp_path / "logger.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "power_w"])
        for day, base in (("2026-11-23", 100), ("2026-11-24", 300), ("2026-11-28", 200)):  # Mon, Tue, Sat
            for m in range(0, 1440):
                for s in (0, 30):                                   # two readings a minute
                    w.writerow([f"{day} {m // 60:02d}:{m % 60:02d}:{s:02d}", base + (m // 60)])
        w.writerow(["2026-11-25 10:00:00", 50])                     # incomplete day: dropped
    measured = load_measured_csv(path)
    assert measured["weekday"].shape == (2, 1440) and measured["weekend"].shape == (1, 1440)
    assert measured["weekday"][0][0] == 100 and measured["weekday"][1][-1] == 323

    ens = simulate_ensemble(household, "weekday", n_runs=20, seed=1, irradiance_days=BRIGHT_ONLY)
    v = validate_against_measured(ens, measured["weekday"])
    assert v["n_measured_days"] == 2
    assert v["max"]["peak_w_measured"] == 323 and v["min"]["peak_w_measured"] == 123
    assert v["mean"]["energy_kwh_measured"] == pytest.approx((111.5 + 311.5) / 2 * 24 / 1000)
    for name in ("mean", "max", "min"):
        assert {"mae_w", "variability_ratio", "par_model", "par_measured"} <= set(v[name])


def test_profiles_svg(household, tmp_path):
    from plot_profiles import write_profiles_svg
    ens = [simulate_ensemble(household, d, n_runs=20, seed=1, irradiance_days=BRIGHT_ONLY)
           for d in ("weekday", "weekend")]
    path = write_profiles_svg(tmp_path / "p.svg", ens, title="Household H999")
    text = open(path).read()
    assert text.startswith("<svg") and text.rstrip().endswith("</svg>")
    assert "Weekday" in text and "Weekend" in text and "prefers-color-scheme: dark" in text
    import xml.dom.minidom
    xml.dom.minidom.parseString(text)                                # well-formed


# ── KNBS baseline ────────────────────────────────────────────────────────────

def test_knbs_baseline_figures_are_consistent():
    import knbs_baseline as k
    for day in ("weekday", "weekend"):
        s = k.URBAN[day]["sleep"]
        assert s["p5"] < s["p25"] <= s["p50"] <= s["p75"] < s["p95"]
        lo, hi = k.sleep_bounds_min(day)
        assert lo < k.median_sleep_hours(day) * 60 < hi
        assert k.URBAN[day]["mass_media"]["participants_p95"] >= k.URBAN[day]["mass_media"]["p95"]


# ── Behavioural day-to-day variation ─────────────────────────────────────────

def test_presence_light_moves_with_the_routine_shift(household):
    h, room = _one_bulb(household, switching="presence", needs_occupancy=False)
    occ = [0] * 24
    occ[20] = 1
    h["room_occupancy"][room] = {"weekday": occ, "weekend": occ}
    h["model_parameters"].update(NO_BEHAVIOUR, routine_shift_sd_min_weekday=30)
    ens = simulate_ensemble(h, "weekday", n_runs=200, seed=0, irradiance_days=BRIGHT_ONLY)
    shift = ens.light_info["routine_shift_min"]
    on = ens.power_w > 0
    assert np.all(on.sum(axis=1) == 60)                        # still one hour each day...
    first_on = on.argmax(axis=1)
    np.testing.assert_array_equal(first_on, 20 * 60 + shift)   # ...moved by that day's shift
    assert shift.std() == pytest.approx(30, rel=0.2)


def test_behavioural_variation_keeps_the_mean_and_widens_the_spread(empty_household):
    h = empty_household
    for name in ("electric_kettle", "television", "iron_box"):
        next(a for a in h["appliances"] if a["name"] == name)["count"] = 1
    h["occupancy_weekday"] = [h["n_residents"]] * 24
    h["model_parameters"].update(NO_BEHAVIOUR)
    flat = simulate_ensemble(h, "weekday", n_runs=3000, seed=5, irradiance_days=BRIGHT_ONLY)
    h["model_parameters"].update(routine_shift_sd_min_weekday=30, appliance_jitter_sd_min=15,
                                 use_intensity_sd=0.3)
    varied = simulate_ensemble(h, "weekday", n_runs=3000, seed=5, irradiance_days=BRIGHT_ONLY)
    assert varied.daily_energy_kwh.mean() == pytest.approx(flat.daily_energy_kwh.mean(), rel=0.05)
    assert varied.daily_energy_kwh.std() > flat.daily_energy_kwh.std()


def test_intensity_factor_has_mean_one():
    from simulate import _intensity
    f = _intensity(np.random.default_rng(0), 200_000, 0.3)
    assert f.mean() == pytest.approx(1.0, abs=0.005) and f.min() > 0


def test_guest_and_away_days(empty_household):
    h = empty_household
    next(a for a in h["appliances"] if a["name"] == "television")["count"] = 1
    h["occupancy_weekday"] = [2] * 24
    h["model_parameters"].update(NO_BEHAVIOUR, guest_day_prob=0.3, away_day_prob=0.3)
    ens = simulate_ensemble(h, "weekday", n_runs=600, seed=2, irradiance_days=BRIGHT_ONLY)
    g, a = ens.light_info["guest_day"], ens.light_info["away_day"]
    assert 0.2 < g.mean() < 0.4 and 0.2 < a.mean() < 0.4 and not (g & a).any()
    assert ens.occupancy[g].max() >= 4                          # +2 guests
    assert ens.occupancy[a][:, 9 * 60:18 * 60].max() == 0       # out 09:00-18:00
    tv = ens.load_energy_kwh["television"]
    normal = ~(g | a)
    assert tv[g].mean() > tv[normal].mean() > tv[a].mean()


# ── Representative real days per percentile (MILP input) ─────────────────────

def test_percentile_runs_are_the_nearest_real_days(household):
    ens = simulate_ensemble(household, "weekday", n_runs=300, seed=4, irradiance_days=BRIGHT_ONLY)
    e = ens.daily_energy_kwh
    for q in (10, 50, 90):
        runs = ens.percentile_runs(q, "energy", n_days=7)
        assert len(runs) == len(set(runs)) == 7
        assert runs[0] == ens.percentile_run(q, "energy")
        target = np.percentile(e, q)
        gaps = np.abs(e[runs] - target)
        others = np.abs(np.delete(e, runs) - target)
        assert gaps.max() <= others.min() + 1e-12              # nobody closer was left out
        np.testing.assert_array_equal(ens.percentile_days(q, n_days=7), ens.power_w[runs])
    assert len(ens.percentile_runs(90, "peak", n_days=5)) == 5


def test_percentile_days_csv(household, tmp_path):
    from simulate import write_percentile_days_csv
    ens = [simulate_ensemble(household, d, n_runs=60, seed=1, irradiance_days=BRIGHT_ONLY)
           for d in ("weekday", "weekend")]
    info = write_percentile_days_csv(tmp_path / "days.csv", ens, n_days=6)
    rows = list(csv.reader(open(tmp_path / "days.csv")))
    assert len(rows) == 1441 and len(rows[0]) == 2 + 2 * 2 * 3 * 6
    assert "weekend_p90_energy_d6" in rows[0] and "weekday_p10_peak_d1" in rows[0]
    assert len(info) == 72 and {d["rank"] for d in info} == set(range(1, 7))


def test_household_specific_variation(household):
    from simulate import behaviour_params
    b = behaviour_params({"routine_shift_sd_min_weekday": 15}, "weekday")
    assert b["routine_shift_sd_min"] == 15 and b["appliance_jitter_sd_min"] == 7.5   # jitter = half


def test_simulator_uses_no_knbs_data():
    """KNBS checks and caps live in the converter only."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("simulate.py").read_text()
    assert "import knbs_baseline" not in src and "knbs_baseline." not in src


def test_per_appliance_intensity_sd_is_used(empty_household):
    h = empty_household
    k = next(a for a in h["appliances"] if a["name"] == "electric_kettle")
    k["count"] = 1
    h["occupancy_weekday"] = [h["n_residents"]] * 24
    h["model_parameters"].update(NO_BEHAVIOUR)
    k["use_intensity_sd"] = 0.0
    calm = simulate_ensemble(h, "weekday", n_runs=3000, seed=9, irradiance_days=BRIGHT_ONLY)
    k["use_intensity_sd"] = 0.6
    busy = simulate_ensemble(h, "weekday", n_runs=3000, seed=9, irradiance_days=BRIGHT_ONLY)
    assert busy.daily_energy_kwh.std() > calm.daily_energy_kwh.std()
