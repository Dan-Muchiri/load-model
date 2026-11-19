"""
survey_to_schema.py
===================
Turns KoboToolbox / ODK submissions of household_survey.xlsx into
household records in the schema.py format, running calibrate_tou.py on
every appliance so tou_weekday / tou_weekend come out calibrated. Room
lights follow the people in the room (switching = "presence").

It sits in the load-model root next to schema.py and calibrate_tou.py;
the form it reads is built by questionnaire/build_form.py, whose
APPLIANCE_POWER_FALLBACK_W and APPLIANCE_NEEDS_OCCUPANCY tables it uses.

USAGE
-----
    python survey_to_schema.py kobo_export.json --out households/
    python survey_to_schema.py questionnaire/sample_households.json --out households/   # made-up test homes

kobo_export.json is the JSON download from KoboToolbox (the API's
/api/v2/assets/<uid>/data/?format=json). A single submission, a list of
submissions or the API's {"results": [...]} wrapper all work.

For each household it writes:
    households/H003.json          the schema record (validated)
    households/H003_report.txt    defaults used, calibration warnings,
                                  anything NOT MODELLED, energy estimate
                                  vs bill, follow-up answers

Answers that are not schema fields (consent, photos, logger willingness,
notes) go only in the report, so the record stays exactly the schema.
"""

import argparse
import contextlib
import copy
import io
import json
import re
import sys
from pathlib import Path

import schema
from calibrate_tou import (APPLIANCE_RESTART_DELAY_MIN, DEFAULT_RESTART_DELAY_MIN,
                           WEEKDAY_FREQUENCY, WEEKEND_FREQUENCY, calibrate_appliance)

sys.path.insert(0, str(Path(__file__).parent / "questionnaire"))
from build_form import (ALWAYS_ON, APPLIANCE_NEEDS_OCCUPANCY,  # noqa: E402
                        APPLIANCE_POWER_FALLBACK_W, BEDROOM_SLEEPERS, RESIDENT_CATEGORIES,
                        ROOM_VISIT_MINUTES_FALLBACK, ROOM_VISITS_FALLBACK)

REF = schema.REFERENCE_HOUSEHOLD

# Typical wattage by bulb type, used when the bulb label can't be read.
BULB_TYPE_FALLBACK_W = {"LED": 9, "CFL": 15, "fluorescent": 36, "incandescent": 60}

# Relative height of a usage window versus the household's other windows
# (calibrate_tou.py, field d. peak_value).
OFTEN_TO_PEAK = {"always": 1.0, "usually": 0.7, "sometimes": 0.4, "rarely": 0.2}

# Researcher-set values, not asked in the interview. Check before each batch.
DEFAULT_IMPORT_TARIFF_KES = 25.0
DEFAULT_ELEVATION_M = 1795
DEFAULT_DERATING = 0.85
NIGHT_HOURS = list(range(0, 6)) + list(range(18, 24))   # Nairobi dusk to dawn
NIGHT_ANCHOR_HOUR = 3       # an hour nearly every household is asleep


# =============================================================================
# Reading Kobo JSON
# =============================================================================

def clean(obj):
    """Strip Kobo group paths ("group_rooms/room/room_type" -> "room_type")."""
    if isinstance(obj, dict):
        return {k.split("/")[-1]: clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj


def load_submissions(path):
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and "results" in data:
        data = data["results"]
    if isinstance(data, dict):
        data = [data]
    return [clean(s) for s in data]


def num(v, default=None):
    if v in (None, ""):
        return default
    f = float(v)
    return int(f) if f.is_integer() else f


def num_required(v, field):
    """Like num(), but for a field build_form.py marks required="yes"
    unconditionally (or required for the branch we're already in) -- a
    missing value here means the submission itself is malformed, not that
    the household skipped an optional question. Asserts instead of
    returning None, so that failure shows up clearly as this field's name
    rather than a cryptic TypeError once the None reaches arithmetic."""
    n = num(v)
    assert n is not None, f"required field missing from submission: {field}"
    return n


# =============================================================================
# Time blocks and usage windows
# =============================================================================

def hour_span(start, end):
    """Hours covered by a block. end is exclusive; end <= start crosses midnight."""
    start, end = int(start), int(end)
    if end <= start:
        end += 24
    return [h % 24 for h in range(start, end)]


def blocks_to_array(blocks, prefix, warnings, label):
    """Expand {prefix}_start / _end blocks into a 24-value array: 1 in the
    hours someone is in the room. Only "in use or not" is used anywhere, so
    the form no longer asks how many people; an older submission's
    {prefix}_people answer is kept if present."""
    arr = [0] * 24
    seen = set()
    for b in blocks or []:
        people = int(num(b.get(f"{prefix}_people"), 1))  # headcount is always whole
        for h in hour_span(b[f"{prefix}_start"], b[f"{prefix}_end"]):
            if h in seen and arr[h] != people:
                warnings.append(f"{label}: overlapping blocks at {h:02d}:00, kept the later answer ({people}).")
            arr[h] = people
            seen.add(h)
    return arr


def to_windows(rows, prefix, warnings=None, label=""):
    """Form usage windows -> calibrate_tou.build_raw_shape() window dicts.
    A peak outside its window (older forms allowed it) would taper the whole
    shape to zero, so it is treated as "spread" with a warning."""
    out = []
    for r in rows or []:
        start, end = int(r[f"{prefix}_start"]), int(r[f"{prefix}_end"])
        if end <= start:
            end += 24
        peak = r.get(f"{prefix}_peak")
        if peak in (None, "", "spread"):
            peak = None
        else:
            peak = int(peak)
            if peak < start:
                peak += 24
            if not start <= peak < end:
                if warnings is not None:
                    warnings.append(f"{label}: peak {peak % 24:02d}:00 is outside the window "
                                    f"{start:02d}:00-{end % 24:02d}:00, treated as spread evenly.")
                peak = None
        out.append({"start_hour": start, "end_hour": end, "peak_hour": peak,
                    "peak_value": OFTEN_TO_PEAK[r.get(f"{prefix}_often", "always")]})
    return out


def duration_std(mean, short=None, long_=None):
    """Range/4 approximates one standard deviation; else 25% of the mean.
    Capped at mean/2 because validate_household() rejects anything larger."""
    std = (long_ - short) / 4 if short and long_ and long_ > short else 0.25 * mean
    return round(min(std, mean / 2), 1)


def calibrate_quietly(warnings, **kw):
    """Run calibrate_appliance() and keep its console log for the report.

    Weekday and weekend are calibrated separately. If one cannot converge
    (calibrate_tou raises ValueError when the answers need more "on" time
    than the windows can hold), that day type gets an all-zero profile and
    the reason goes into the report, so one bad answer does not stop the
    rest of the household being converted.
    """
    buf = io.StringIO()
    res = {}
    for day, other in (("weekday", "weekend"), ("weekend", "weekday")):
        one_day = dict(kw, **{f"{other}_windows": [], f"{other}_days_used": 0})
        try:
            with contextlib.redirect_stdout(buf):
                res[f"tou_{day}"] = calibrate_appliance(**one_day)[f"tou_{day}"]
        except ValueError as e:
            warnings.append(f"NOT MODELLED, check with the household and re-run: {e}")
            res[f"tou_{day}"] = [0.0] * 24
    return res, buf.getvalue()


def lookup(table, name, default=None):
    """Exact name first ("television_2"), then the base type with any
    trailing _N stripped ("television_3" -> "television"), as build_form.py
    describes for APPLIANCE_POWER_FALLBACK_W / APPLIANCE_NEEDS_OCCUPANCY."""
    if name in table:
        return table[name]
    return table.get(re.sub(r"_\d+$", "", name), default)


def sessions(answer):
    """Separate switch-ons per day of use (calibrate_tou interview note i/j).
    Older submissions without the question count as 1."""
    return num(answer, 1)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "item"


# =============================================================================
# Blocks of the schema
# =============================================================================

def build_occupancy(s):
    """Section C: each resident group's count, at home in every hour outside
    its away window, summed per hour. This counts sleepers as home;
    active_occupancy() then takes the night out using the room answers."""
    wd, we = [0] * 24, [0] * 24
    for cat, _who in RESIDENT_CATEGORIES:
        n = int(num(s.get(cat), 0))
        if n == 0:
            continue
        for day, arr in (("weekday", wd), ("weekend", we)):
            src = "weekday" if day == "weekend" and s.get(f"{cat}_weekend_same") == "yes" else day
            start = s.get(f"{cat}_{src}_away_start")
            away = set()
            if start not in (None, "", "home_all_day"):
                away = set(hour_span(start, s[f"{cat}_{src}_away_end"]))
            for h in range(24):
                if h not in away:
                    arr[h] += n
    return wd, we


def active_occupancy(home, room_occ, day, warnings):
    """Active occupants per hour from Section C's home count, with the night
    taken out. Room presence (Section D) is awake, active use only, but it
    cannot place every person all day, so it is only used to find the night:
    the unbroken run of hours with nobody in any indoor room that contains
    03:00 counts as asleep (0). Every other hour keeps the home count, so a
    daytime gap in the room answers does not erase people who are home.
    If someone is in a room at 03:00 (e.g. night shift), nothing is zeroed.
    The anchor matters: the longest empty run is often the working day
    (07-18), not the night, when the rooms are only answered for mornings
    and evenings."""
    in_rooms = [sum(r[day][h] for r in room_occ.values()) for h in range(24)]
    asleep = set()
    h = NIGHT_ANCHOR_HOUR
    while in_rooms[h % 24] == 0 and len(asleep) < 24:     # forwards to waking
        asleep.add(h % 24); h += 1
    h = NIGHT_ANCHOR_HOUR - 1
    while in_rooms[h % 24] == 0 and len(asleep) < 24:     # backwards to bedtime
        asleep.add(h % 24); h -= 1
    if len(asleep) == 24:                                 # no room answers at all
        warnings.append(f"occupancy {day}: no room presence recorded, so sleep could not be "
                        f"separated; Section C home counts used for every hour.")
        asleep = set()
    return [0 if h in asleep else home[h] for h in range(24)]


def has_pattern(room):
    """Room used at predictable times? Submissions from before the
    room_has_pattern question count as yes (they all gave time blocks)."""
    return room.get("room_has_pattern", "yes") != "no"


def unpatterned_visits(s, rtype, n_same_type):
    """Visits a day for a room with no fixed pattern (build_form.py's
    ROOM_VISITS_FALLBACK / BEDROOM_SLEEPERS), at least 1."""
    n_res = int(num_required(s["number_of_residents"], "number_of_residents"))
    basis, rate = ROOM_VISITS_FALLBACK.get(rtype, ROOM_VISITS_FALLBACK["other"])
    if basis == "per_resident":
        total, split = n_res * rate, True
    elif basis == "per_sleeper":
        sleepers = BEDROOM_SLEEPERS.get(rtype, 1)
        if sleepers == "children":
            sleepers = max(1, int(num(s.get("school_children"), 0)) + int(num(s.get("young_children"), 0)))
            split = True                    # the children share their bedrooms
        else:
            split = False                   # each master/other bedroom has its own sleepers
        total = min(sleepers, n_res) * rate
    else:                                   # per_household
        total, split = rate, True
    if split:
        total /= max(1, n_same_type)
    return max(1, round(total))


def awake_windows(occ):
    """Runs of hours with someone awake at home, as build_raw_shape() windows
    (flat; a run past midnight gets end_hour > 24)."""
    hours = [h for h in range(24) if occ[h] > 0]
    if not hours:
        return []
    if len(hours) == 24:
        return [{"start_hour": 0, "end_hour": 24, "peak_hour": None, "peak_value": 1.0}]
    starts = [h for h in hours if occ[(h - 1) % 24] == 0]
    windows = []
    for start in starts:
        end = start
        while occ[end % 24] > 0:
            end += 1
        windows.append({"start_hour": start, "end_hour": end, "peak_hour": None, "peak_value": 1.0})
    return windows


def calibrate_unpatterned(pending, s, occ_wd, occ_we, room_occ, warnings, log):
    """Lights in rooms with no fixed pattern: the fallback visits a day, each
    of the fallback length, spread over the household's awake at-home hours
    and calibrated like an appliance (switching = "tou"). Corridors and stairs
    ("awake_hours") are instead lit whenever it is dark while the household
    is awake at home (switching = "presence")."""
    same_type = {}                  # every unpatterned room of a type, lit or not
    for r in s.get("room", []):
        if r["room_type"] != "outside_security" and not has_pattern(r):
            same_type[r["room_type"]] = same_type.get(r["room_type"], 0) + 1
    wd_w, we_w = awake_windows(occ_wd), awake_windows(occ_we)
    for bulb, rtype in pending:
        if ROOM_VISITS_FALLBACK.get(rtype, (None,))[0] == "awake_hours":
            # Corridors and stairs: lit whenever it is dark and the household
            # is awake at home, so the room counts as occupied in those hours.
            room_occ[bulb["room"]] = {"weekday": occ_wd[:], "weekend": occ_we[:]}
            bulb.update(tou_weekday=[1.0 if n else 0.0 for n in occ_wd],
                        tou_weekend=[1.0 if n else 0.0 for n in occ_we],
                        mean_duration_min=60, std_duration_min=0, needs_occupancy=True,
                        switching="presence")
            bulb["notes"] = f"{bulb.get('notes') or ''} No fixed pattern: lit whenever dark " \
                            f"while the household is awake at home.".strip()
            continue
        if not wd_w and not we_w:
            warnings.append(f"{bulb['room']}: no awake at-home hours to spread visits over, light left off.")
            bulb.update(tou_weekday=[0.0] * 24, tou_weekend=[0.0] * 24, mean_duration_min=0,
                        std_duration_min=0, needs_occupancy=True, switching="tou")
            continue
        visits = unpatterned_visits(s, rtype, same_type.get(rtype, 1))
        minutes = ROOM_VISIT_MINUTES_FALLBACK.get(rtype, ROOM_VISIT_MINUTES_FALLBACK["other"])
        res, text = calibrate_quietly(
            warnings,
            weekday_windows=wd_w, weekend_windows=we_w,
            mean_duration_min=minutes, std_duration_min=duration_std(minutes),
            weekday_days_used=WEEKDAY_FREQUENCY["every_weekday"] if wd_w else 0,
            weekend_days_used=WEEKEND_FREQUENCY["both_days"] if we_w else 0,
            weekday_sessions_per_day=visits, weekend_sessions_per_day=visits,
            appliance_name=f"{bulb['room']} bulb", restart_delay_min=0, random_seed=42)
        # (no max_tou=TOU_MAX_BULB: these lights are switched like appliances, one
        # switch-on per room visit, and a busy shared bathroom needs more than 1/hour)
        log.append(text)
        bulb.update(tou_weekday=res["tou_weekday"], tou_weekend=res["tou_weekend"],
                    mean_duration_min=minutes, std_duration_min=duration_std(minutes),
                    needs_occupancy=True, switching="tou")
        bulb["notes"] = f"{bulb.get('notes') or ''} No fixed pattern: about {visits} visit(s) " \
                        f"a day of {minutes} min (fallback).".strip()


def build_rooms_and_bulbs(s, warnings):
    """Returns room_occupancy, the bulbs, and the bulbs of rooms with no fixed
    pattern, which are filled in once occupancy is known
    (calibrate_unpatterned)."""
    room_occ, bulbs, used, pending = {}, [], set(), []
    bedroom_no = 3
    for r in s.get("room", []):
        rtype = r["room_type"]
        if rtype == "bedroom":
            name = f"bedroom_{bedroom_no}"; bedroom_no += 1
        elif rtype == "other":
            name = slug(r.get("room_other", "room"))
        else:
            name = rtype
        base, k = name, 2
        while name in used:
            name = f"{base}_{k}"; k += 1
        used.add(name)

        # ---- people in the room (Block 3b); outside lights and rooms with
        # no fixed pattern have no times, so no room_occupancy entry
        same = r.get("room_weekend_same") == "yes"
        if rtype != "outside_security" and has_pattern(r):
            wd = blocks_to_array(r.get("room_weekday"), "room_weekday", warnings, f"{name} weekday")
            we = wd[:] if same else blocks_to_array(r.get("room_weekend"), "room_weekend",
                                                     warnings, f"{name} weekend")
            room_occ[name] = {"weekday": wd, "weekend": we}

        # ---- bulbs (Block 5)
        count = int(num(r.get("bulbs"), 0))
        if count == 0:
            continue
        bulb = {"room": name, "count": count, "wattage_w": num(r.get("bulb_watts")),
                "bulb_type": r.get("bulb_type"), "restart_delay_min": 0}
        if bulb["wattage_w"] is None:
            btype = bulb["bulb_type"] or "LED"
            bulb["wattage_w"] = BULB_TYPE_FALLBACK_W.get(btype, 9)
            warnings.append(f"{name}: bulb wattage missing, assumed {bulb['wattage_w']} W "
                            f"(typical {btype}).")

        if rtype == "outside_security":
            mode = r.get("security_mode", "dusk_to_dawn")
            if mode in ("timer", "manual"):
                on = set(hour_span(r["security_light_on"], r["security_light_off"]))
                tou = [1.0 if h in on else 0.0 for h in range(24)]
            elif mode == "motion":
                tou = [0.1 if h in NIGHT_HOURS else 0.0 for h in range(24)]
                warnings.append(f"{name}: motion-sensor lights modelled as short 3-minute triggers "
                                f"(tou 0.1 at night). Adjust if you measure them.")
            else:
                tou = [1.0 if h in NIGHT_HOURS else 0.0 for h in range(24)]
            bulb.update(tou_weekday=tou, tou_weekend=tou[:],
                        mean_duration_min=3 if mode == "motion" else 60,
                        std_duration_min=0, needs_occupancy=False,
                        switching="tou" if mode == "motion" else "dark",
                        notes=f"Outside lights, {mode.replace('_', ' ')}.")
        elif not has_pattern(r):
            bulb["notes"] = r.get("room_notes", "")
            pending.append((bulb, rtype))   # calibrated once occupancy is known
        else:
            # The light is on whenever someone is in the room and it is dark
            # (switching = "presence"; the simulator works out darkness).
            # tou marks the occupied hours for estimate_daily_energy_kwh().
            occ = room_occ[name]
            if not any(occ["weekday"]) and not any(occ["weekend"]):
                warnings.append(f"{name}: {count} bulb(s) but no times anyone is in the room, "
                                f"light left off.")
            bulb.update(tou_weekday=[1.0 if n else 0.0 for n in occ["weekday"]],
                        tou_weekend=[1.0 if n else 0.0 for n in occ["weekend"]],
                        mean_duration_min=60, std_duration_min=0, needs_occupancy=True,
                        switching="presence", notes=r.get("room_notes", ""))
        bulbs.append(bulb)
    return room_occ, bulbs, pending


def build_appliances(s, warnings, log):
    catalogue = {a["name"]: a for a in REF["appliances"]}
    # Every catalogue appliance is listed, count 0 unless the household has it.
    out = {}
    for a in REF["appliances"]:
        e = copy.deepcopy(a)
        e["count"] = 0
        out[e["name"]] = e
    filled = set()
    extra_n = 0

    for ans in s.get("appliance", []):
        atype = ans["appliance_type"]
        is_other = atype == "other"
        if is_other:
            extra_n += 1
            name = f"other_appliance_{extra_n}" if f"other_appliance_{extra_n}" in out \
                else f"other_{slug(ans.get('appliance_other', ''))}"
            base = copy.deepcopy(catalogue["other_appliance_1"])
            base.update(name=name, category="other", notes=ans.get("appliance_other", ""))
            always_on = ans.get("appliance_other_always_on") == "yes"
            base["needs_occupancy"] = (not always_on) and ans.get("appliance_other_home") == "yes"
        else:
            name, k = atype, 2
            while name in filled:
                name = f"{atype}_{k}"; k += 1
            base = copy.deepcopy(catalogue.get(name, catalogue[atype]))
            base["name"] = name
            always_on = atype in ALWAYS_ON
            base["needs_occupancy"] = lookup(APPLIANCE_NEEDS_OCCUPANCY, name,
                                             base.get("needs_occupancy", True))
        filled.add(name)
        base["restart_delay_min"] = 0 if is_other and always_on else \
            lookup(APPLIANCE_RESTART_DELAY_MIN, name, DEFAULT_RESTART_DELAY_MIN)
        base["count"] = int(num(ans.get("appliance_count"), 1))
        if ans.get("appliance_notes"):
            base["notes"] = ans["appliance_notes"]

        # ---- power from the label, else build_form's typical value
        w = num(ans.get("label_watts"))
        volts, amps = num(ans.get("label_volts")), num(ans.get("label_amps"))
        if w is None and volts and amps:
            w = round(volts * amps, 1)
        if w is None and not is_other:
            w = lookup(APPLIANCE_POWER_FALLBACK_W, name)
            warnings.append(f"{name}: no label reading, used typical {w} W.")
        if w is None:
            # No literature value for an unnamed appliance: leave it out
            # rather than guess (build_form.py, label note).
            warnings.append(f"NOT MODELLED: {name} ({base['notes'] or 'other appliance'}) has no "
                            f"label reading and no typical value. Get the wattage and re-run.")
            base["count"] = 0
            out[name] = base
            continue
        base["rated_power_w"] = w

        # ---- time of use
        if always_on:
            if is_other:     # runs continuously, like the router in schema.py
                base.update(tou_weekday=[1.0] * 24, tou_weekend=[1.0] * 24,
                            mean_duration_min=1440, std_duration_min=0)
            # named always-on appliances keep their catalogue duty cycle
        else:
            mean = int(num_required(ans["duration_typical"], "duration_typical"))
            std = duration_std(mean)   # form asks only the typical time
            wd_freq, we_freq = ans["weekday_frequency"], ans["weekend_frequency"]
            wd_w = to_windows(ans.get("appliance_weekday"), "appliance_weekday", warnings, f"{name} weekday")
            if ans.get("weekend_same_as_weekday") == "yes":
                we_w = wd_w
            else:
                we_w = to_windows(ans.get("appliance_weekend"), "appliance_weekend", warnings, f"{name} weekend")
            res, text = calibrate_quietly(
                warnings,
                weekday_windows=wd_w, weekend_windows=we_w,
                mean_duration_min=mean, std_duration_min=std,
                weekday_days_used=WEEKDAY_FREQUENCY[wd_freq],
                weekend_days_used=WEEKEND_FREQUENCY[we_freq],
                weekday_sessions_per_day=sessions(ans.get("appliance_weekday_sessions")),
                weekend_sessions_per_day=sessions(ans.get("appliance_weekend_sessions")),
                appliance_name=name, restart_delay_min=base["restart_delay_min"],
                random_seed=42)
            log.append(text)
            base.update(tou_weekday=res["tou_weekday"], tou_weekend=res["tou_weekend"],
                        mean_duration_min=mean, std_duration_min=std)
        out[name] = base

    for a in out.values():          # unused entries: zero profile, clearly off
        if a["count"] == 0:
            a["tou_weekday"] = [0.0] * 24
            a["tou_weekend"] = [0.0] * 24
    return list(out.values())


def build_grid(s, warnings):
    connected = s.get("grid_connected") == "yes"
    backup = s.get("backup", "none")
    phase = s.get("phase", "single")
    if phase == "dont_know":
        phase = "single"
        warnings.append("grid.phase unknown, assumed single-phase.")
    tariff = s.get("tariff", "domestic") if connected else "none"
    if tariff == "dont_know":
        tariff = "domestic"
    # A token SMS gives the real all-in price per kWh; use it when recorded.
    tariff_kes = DEFAULT_IMPORT_TARIFF_KES
    token_kwh, token_kes = num(s.get("token_kwh")), num(s.get("token_kes"))
    if token_kwh and token_kes:
        tariff_kes = round(token_kes / token_kwh, 2)
    elif connected:
        warnings.append(f"No token reading, import tariff assumed KES {DEFAULT_IMPORT_TARIFF_KES}/kWh.")
    pv_kw = num(s.get("solar_panel_count"), 0) * num(s.get("solar_panel_watts"), 0) / 1000
    bat_kwh = (num(s.get("battery_volts"), 0) * num(s.get("battery_amp_hours"), 0)
               * num(s.get("battery_units"), 0) / 1000)
    return {
        "connected": connected,
        "phase": phase,
        "tariff_type": tariff,
        "import_tariff_kes_per_kwh": tariff_kes if connected else 0.0,
        "export_tariff_kes_per_kwh": 0.0,
        "monthly_fixed_charge_kes": REF["grid"]["monthly_fixed_charge_kes"] if connected else 0.0,
        "supply_reliability": s.get("reliability") or REF["grid"]["supply_reliability"],  # no longer asked
        "avg_blackout_hours_per_week": num(s.get("blackout_hours"), 0),
        "monthly_bill_kes": num(s.get("monthly_spend"), 0),
        "metering_type": s.get("metering"),
        "existing_backup": None if backup == "none" else backup,
        "existing_pv_kw": round(pv_kw, 3),
        "existing_battery_kwh": round(bat_kwh, 3),
        "existing_backup_capacity_kw": num(s.get("backup_kilowatts"), 0.0),
    }


def build_site(s):
    return {
        "roof_area_sqm": round(num_required(s["roof_length"], "roof_length")
                               * num_required(s["roof_width"], "roof_width"), 1),
        "roof_orientation": s.get("orientation"),
        "roof_tilt_degrees": num(s.get("tilt")),
        "shading": s.get("shading"),
        "roof_type": s.get("roof_type"),
        "mounting_type": s.get("mounting"),
        "panel_derating_factor": DEFAULT_DERATING,
        "cable_length_m": num(s.get("cable_length_meters")),
    }


# =============================================================================
# One submission -> one household
# =============================================================================

def submission_to_household(s):
    warnings, log = [], []
    n_res = int(num_required(s["number_of_residents"], "number_of_residents"))

    lat = lon = None
    elev = DEFAULT_ELEVATION_M
    if s.get("gps"):
        parts = [float(x) for x in s["gps"].split()]
        lat, lon = parts[0], parts[1]
        if len(parts) > 2 and parts[2]:
            elev = round(parts[2])

    home_wd, home_we = build_occupancy(s)
    room_occ, bulbs, pending = build_rooms_and_bulbs(s, warnings)
    occ_wd = active_occupancy(home_wd, room_occ, "weekday", warnings)
    occ_we = active_occupancy(home_we, room_occ, "weekend", warnings)
    calibrate_unpatterned(pending, s, occ_wd, occ_we, room_occ, warnings, log)

    h = {
        "household_id": s["household_id"],
        "survey_date": s["survey_date"],
        "location": {"sub_county": s.get("sub_county"), "county": s.get("county"),
                     "country": "Kenya", "latitude": lat, "longitude": lon, "elevation_m": elev},
        "n_residents": n_res,
        "resident_breakdown": {cat: int(num(s.get(cat), 0)) for cat, _ in RESIDENT_CATEGORIES},
        "occupancy_weekday": occ_wd,
        "occupancy_weekend": occ_we,
        "room_occupancy": room_occ,
        "appliances": build_appliances(s, warnings, log),
        "bulbs": bulbs,
        "grid": build_grid(s, warnings),
        "site": build_site(s),
        "costs": copy.deepcopy(REF["costs"]),
        "model_parameters": copy.deepcopy(REF["model_parameters"]),
    }
    for line in "".join(log).splitlines():
        if "WARN" in line:
            warnings.append(line.strip())
    warnings = list(dict.fromkeys(warnings))   # iterative calibration repeats its [WARN] lines

    follow_up = {k: s.get(k) for k in ("dwelling", "logger_willing", "routine_change",
                                         "backup_loads", "interviewer_notes", "token_kwh", "token_kes")}
    return h, warnings, follow_up, "".join(log)


def write_outputs(h, warnings, follow_up, log, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hid = h["household_id"]

    lines = [f"Household {hid}  surveyed {h['survey_date']}", ""]
    try:
        schema.validate_household(h)
        lines.append("validate_household(): PASS")
    except ValueError as e:
        lines.append(f"validate_household(): FAIL\n{e}")

    try:
        est = schema.estimate_daily_energy_kwh(h)
        lines.append(f"estimate_daily_energy_kwh(): {est}")
    except Exception as e:  # keep the report even if the estimator changes
        lines.append(f"estimate_daily_energy_kwh() could not run: {e}")
    bill = h["grid"]["monthly_bill_kes"]
    tariff = h["grid"]["import_tariff_kes_per_kwh"]
    if bill and tariff:
        lines.append(f"Bill cross-check: KES {bill}/month at KES {tariff}/kWh is about "
                     f"{bill / tariff / 30:.1f} kWh/day from the grid (before any solar offset).")

    lines += ["", "Defaults and warnings:"] + [f"  - {w}" for w in warnings or ["none"]]
    lines += ["", "Follow-up answers (not schema fields):"] + \
             [f"  {k}: {v}" for k, v in follow_up.items() if v not in (None, "")]
    lines += ["", "Calibration log:", log]

    (out_dir / f"{hid}.json").write_text(json.dumps(h, indent=2))
    (out_dir / f"{hid}_report.txt").write_text("\n".join(lines))
    return lines[2]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", help="Kobo JSON export")
    ap.add_argument("--out", default="households", help="output folder")
    args = ap.parse_args()
    for s in load_submissions(args.export):
        assert isinstance(s, dict)  # each submission is a JSON object; clean() just can't say so statically
        h, warnings, follow_up, log = submission_to_household(s)
        status = write_outputs(h, warnings, follow_up, log, args.out)
        print(f"{h['household_id']}: {status}; {len(warnings)} warning(s) -> {args.out}/{h['household_id']}_report.txt")


if __name__ == "__main__":
    main()
