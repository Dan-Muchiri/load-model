"""Converter checks on the test households H901-H910.

The households, and the warnings each one is meant to produce, live in
questionnaire/test_households/make_test_households.py; the Kobo-style
export of them is questionnaire/test_households/test_households_kobo.json.
These tests convert that export with survey_to_schema.py exactly as a real
KoboToolbox download is converted.
"""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path

import pytest

import schema
import survey_to_schema as sts

TEST_DIR = Path(__file__).resolve().parents[1] / "questionnaire" / "test_households"
EXPORT = TEST_DIR / "test_households_kobo.json"


def _load_generator():
    spec = importlib.util.spec_from_file_location("make_test_households",
                                                  TEST_DIR / "make_test_households.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MTH = _load_generator()
EXPECT = {s["household_id"]: s["_expect"] for s in MTH.HOUSEHOLDS}
IDS = list(EXPECT)


@pytest.fixture(scope="module")
def converted():
    """Every household converted once (a few minutes: calibration)."""
    out = {}
    for sub in sts.load_submissions(EXPORT):
        with contextlib.redirect_stdout(io.StringIO()):
            h, warnings, follow, log = sts.submission_to_household(sub)
        out[h["household_id"]] = (h, warnings, follow, log)
    return out


def by_name(items, key="name"):
    return {x[key]: x for x in items}


# ---------------------------------------------------------------- the export itself
def test_export_matches_households_and_form():
    """The committed JSON is what make_test_households.py builds for the current form."""
    rows, _choices = MTH.read_form()
    tree = MTH.tree(rows)
    rebuilt = {"results": [MTH.kobo_paths(s, tree) for s in MTH.HOUSEHOLDS]}
    assert json.loads(EXPORT.read_text()) == rebuilt


@pytest.mark.parametrize("hid", IDS)
def test_household_passes_kobo_form_check(hid):
    rows, choices = MTH.read_form()
    s = next(s for s in MTH.HOUSEHOLDS if s["household_id"] == hid)
    assert MTH.kobo_check(s, MTH.tree(rows), choices) == []


# ---------------------------------------------------------------- every household
@pytest.mark.parametrize("hid", IDS)
def test_household_validates(converted, hid):
    h, *_ = converted[hid]
    schema.validate_household(h)   # raises ValueError on any schema error


@pytest.mark.parametrize("hid", IDS)
def test_household_gives_exactly_its_expected_warnings(converted, hid):
    _h, warnings, *_ = converted[hid]
    assert MTH.warning_check(warnings, EXPECT[hid]) == []


def test_warnings_not_duplicated(converted):
    for _h, warnings, *_ in converted.values():
        assert len(warnings) == len(set(warnings))


# ---------------------------------------------------------------- specific paths
def test_blank_bulb_watts_uses_bulb_type(converted):
    h, *_ = converted["H909"]
    watts = {b["room"]: b["wattage_w"] for b in h["bulbs"]}
    assert watts["living_room"] == 9          # LED, blank
    assert watts["kitchen"] == 15             # CFL, blank
    assert watts["master_bedroom"] == 60      # incandescent, blank
    assert watts["bedroom_3"] == 36           # fluorescent, blank
    assert watts["bathroom"] == 5             # read off the bulb


def test_other_appliances(converted):
    h, *_ = converted["H909"]
    a = by_name(h["appliances"])
    assert a["other_appliance_1"]["rated_power_w"] == 1500             # room heater, labelled
    assert a["other_appliance_2"]["count"] == 0                         # massage chair, no label
    assert a["other_aquarium_light_on_a_timer"]["needs_occupancy"] is False


def test_volts_times_amps_label(converted):
    h, *_ = converted["H904"]
    assert by_name(h["appliances"])["toaster"]["rated_power_w"] == 805    # 230 V x 3.5 A


def test_several_uses_in_one_hour_calibrate(converted):
    """Gate motor 6x a weekday and instant shower 4x a morning need tou > 1."""
    h, warnings, *_ = converted["H904"]
    a = by_name(h["appliances"])
    assert max(a["gate_motor"]["tou_weekday"]) > 1.0
    assert max(a["immersion_water_heater"]["tou_weekday"]) > 1.0
    assert not any("NOT MODELLED" in w and ("gate_motor" in w or "immersion" in w) for w in warnings)


def test_rare_appliance_calibrates(converted):
    """Tablet charged once a week (0.2 uses a weekday) used to fail the 10% check on noise."""
    h, *_ = converted["H905"]
    assert sum(by_name(h["appliances"])["tablet_charger"]["tou_weekday"]) > 0


def test_off_grid(converted):
    g = converted["H905"][0]["grid"]
    assert g["connected"] is False and g["import_tariff_kes_per_kwh"] == 0.0
    assert g["existing_backup"] == "solar_only" and g["existing_pv_kw"] == 0.3


def test_presence_and_dark_bulb_tou_stays_at_most_one(converted):
    for h, *_ in converted.values():
        for b in h["bulbs"]:
            if b["switching"] != "tou":
                assert max(b["tou_weekday"] + b["tou_weekend"]) <= 1.0


def test_reliability_not_asked_uses_reference_default(converted):
    h, *_ = converted["H902"]
    assert h["grid"]["supply_reliability"] == schema.REFERENCE_HOUSEHOLD["grid"]["supply_reliability"]


# ---------------------------------------------------------------- converter units
def test_peak_outside_window_is_spread():
    """Only older submissions (form v4 and before) can have this; v5 blocks it."""
    warnings = []
    rows = [{"x_start": "6", "x_end": "18", "x_peak": "22", "x_often": "always"}]
    w = sts.to_windows(rows, "x", warnings, "blender weekday")
    assert w[0]["peak_hour"] is None
    assert "outside the window" in warnings[0]


def test_peak_inside_midnight_window_kept():
    rows = [{"x_start": "20", "x_end": "1", "x_peak": "0", "x_often": "always"}]
    w = sts.to_windows(rows, "x", [], "tv")
    assert (w[0]["start_hour"], w[0]["end_hour"], w[0]["peak_hour"]) == (20, 25, 24)


def test_busy_unpatterned_bathroom_calibrates():
    """12 people sharing a no-pattern bathroom: 24 visits a day need more than
    one switch-on an hour, which a 1.0 cap made impossible (NOT MODELLED)."""
    import calibrate_tou
    w = [{"start_hour": 6, "end_hour": 22, "peak_hour": None, "peak_value": 1.0}]
    with contextlib.redirect_stdout(io.StringIO()):
        r = calibrate_tou.calibrate_appliance(w, [], 15, 3.75, 5, 0, weekday_sessions_per_day=24,
                                              appliance_name="bathroom bulb", restart_delay_min=0,
                                              random_seed=42)
    assert max(r["tou_weekday"]) > 1.0
    h = copy.deepcopy(schema.REFERENCE_HOUSEHOLD)
    b = h["bulbs"][0]
    b.update(switching="tou", tou_weekday=r["tou_weekday"], tou_weekend=r["tou_weekday"])
    schema.validate_household(h)


# ---------------------------------------------------------------- tou as a switch-on rate
def test_tou_max_constants_agree():
    import calibrate_tou
    assert schema.TOU_MAX_APPLIANCE == calibrate_tou.TOU_MAX_APPLIANCE


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


# ---------------------------------------------------------------- time blocks (form v7 slots)
def test_time_blocks_from_v7_slots():
    item = {"appliance_weekday_count": "2",
            "appliance_weekday_1_start": "5", "appliance_weekday_1_end": "8",
            "appliance_weekday_1_peak": "6", "appliance_weekday_1_often": "always",
            "appliance_weekday_2_start": "19", "appliance_weekday_2_end": "22",
            "appliance_weekday_2_peak": "spread", "appliance_weekday_2_often": "sometimes",
            "appliance_weekday_sessions": "2"}
    assert sts.time_blocks(item, "appliance_weekday") == [
        {"appliance_weekday_start": "5", "appliance_weekday_end": "8",
         "appliance_weekday_peak": "6", "appliance_weekday_often": "always"},
        {"appliance_weekday_start": "19", "appliance_weekday_end": "22",
         "appliance_weekday_peak": "spread", "appliance_weekday_often": "sometimes"}]


def test_time_blocks_old_repeat_format_still_read():
    """Submissions from forms v6 and earlier keep converting."""
    old = {"room_weekday": [{"room_weekday_start": "6", "room_weekday_end": "7"}]}
    assert sts.time_blocks(old, "room_weekday") == old["room_weekday"]
