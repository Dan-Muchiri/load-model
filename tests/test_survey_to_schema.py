"""Converter checks on the made-up households in questionnaire/sample_households.py."""
import copy

import pytest

import schema
import survey_to_schema as sts
from questionnaire.sample_households import SAMPLES


def convert(raw):
    return sts.submission_to_household(sts.clean(copy.deepcopy(raw)))


@pytest.fixture(scope="module")
def converted():
    return {raw["group_start/household_id"]: convert(raw) for raw in SAMPLES}


@pytest.mark.parametrize("hid", [s["group_start/household_id"] for s in SAMPLES])
def test_sample_validates(converted, hid):
    h, _warnings, _follow, _log = converted[hid]
    schema.validate_household(h)   # raises ValueError on any schema error


def test_blank_bulb_watts_uses_bulb_type(converted):
    h, warnings, _, _ = converted["S105"]
    watts = {b["room"]: b["wattage_w"] for b in h["bulbs"]}
    assert watts["living_room"] == 60          # incandescent, blank
    assert watts["outside_security"] == 15     # CFL, blank
    assert watts["kitchen"] == 9               # read off the bulb
    assert any("assumed 60 W (typical incandescent)" in w for w in warnings)


def test_peak_outside_window_is_spread():
    warnings = []
    rows = [{"x_start": "6", "x_end": "18", "x_peak": "22", "x_often": "always"}]
    w = sts.to_windows(rows, "x", warnings, "blender weekday")
    assert w[0]["peak_hour"] is None
    assert "outside the window" in warnings[0]


def test_peak_inside_midnight_window_kept():
    rows = [{"x_start": "20", "x_end": "1", "x_peak": "0", "x_often": "always"}]
    w = sts.to_windows(rows, "x", [], "tv")
    assert (w[0]["start_hour"], w[0]["end_hour"], w[0]["peak_hour"]) == (20, 25, 24)


def test_other_appliance_without_label_not_modelled(converted):
    h, warnings, _, _ = converted["S106"]
    names = {a["name"]: a for a in h["appliances"]}
    assert names["other_appliance_1"]["rated_power_w"] == 15       # aquarium pump, labelled
    assert names["other_appliance_2"]["count"] == 0                 # hair clipper, no label
    assert any(w.startswith("NOT MODELLED: other_appliance_2") for w in warnings)


def test_reliability_not_asked_uses_reference_default(converted):
    h, *_ = converted["S102"]
    assert h["grid"]["supply_reliability"] == schema.REFERENCE_HOUSEHOLD["grid"]["supply_reliability"]


def test_warnings_not_duplicated(converted):
    for h, warnings, *_ in converted.values():
        assert len(warnings) == len(set(warnings))


# ---------------------------------------------------------------- tou as a switch-on rate
def test_tou_max_constants_agree():
    import calibrate_tou
    assert schema.TOU_MAX_APPLIANCE == calibrate_tou.TOU_MAX_APPLIANCE


def test_several_uses_in_one_hour_calibrate(converted):
    """Gate motor 4x/day in two 2-hour windows needs tou > 1 at the peaks."""
    h, warnings, *_ = converted["S104"]
    gate = next(a for a in h["appliances"] if a["name"] == "gate_motor")
    assert max(gate["tou_weekday"]) > 1.0
    assert not any("gate_motor" in w and "NOT MODELLED" in w for w in warnings)


def test_bulb_tou_stays_at_most_one(converted):
    for h, *_ in converted.values():
        for b in h["bulbs"]:
            assert max(b["tou_weekday"] + b["tou_weekend"]) <= 1.0


def test_validator_rejects_appliance_tou_above_ceiling():
    h = copy.deepcopy(schema.REFERENCE_HOUSEHOLD)
    a = next(a for a in h["appliances"] if a["count"] > 0)
    a["tou_weekday"][7] = schema.TOU_MAX_APPLIANCE + 1
    with pytest.raises(ValueError):
        schema.validate_household(h)


def test_estimate_matches_engine_for_fridge():
    """Fridge at tou 1, 25-min runs: the engine is on 25/(60+25) of the time."""
    h = copy.deepcopy(schema.REFERENCE_HOUSEHOLD)
    for a in h["appliances"]:
        a["count"] = 1 if a["name"] == "refrigerator" else 0
    for b in h["bulbs"]:
        b["count"] = 0
    fridge = next(a for a in h["appliances"] if a["name"] == "refrigerator")
    expected = fridge["rated_power_w"] * 24 * 25 / 85 / 1000
    assert schema.estimate_daily_energy_kwh(h)["weekday_kwh"] == pytest.approx(expected, abs=0.01)
