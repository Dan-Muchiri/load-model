"""
sample_households.py
====================
Made-up Kobo-style submissions for testing survey_to_schema.py. They are NOT
survey data. Each one is a typical Nairobi household chosen to exercise a
different part of the form and converter:

    S101  bedsitter, one working adult, bulb wattage blank (LED fallback)
    S102  young couple in a 1-bed flat, appliances used at the weekend only
    S103  family of five with a house help, CFL/fluorescent bulbs left blank,
          dusk-to-dawn security lights, washing machine at the weekend
    S104  maisonette, elderly parent and toddler at home, store room with no
          fixed pattern, motion-sensor lights, generator backup
    S105  elderly couple home all day, incandescent bulbs, lifeline tariff,
          outside lights on a timer
    S106  solar + battery home, night-shift adult away 18:00-07:00 (crosses
          midnight; tests the 03:00 night anchor), TV window crossing midnight, two "other" appliances
          (one with a label, one without -> NOT MODELLED)
    S107  off-grid house on solar only

Usage:
    python questionnaire/sample_households.py          # writes sample_households.json
    python survey_to_schema.py questionnaire/sample_households.json --out households_sample/

Keys use Kobo's group paths ("group_rooms/room/room_type"), as a real
export does, so the converter's path stripping is exercised too.
"""

import json
from pathlib import Path

HERE = Path(__file__).parent


# ---------------------------------------------------------------- helpers
def occupancy(cat, n, weekday=None, weekend="same"):
    """weekday/weekend: (away_start, away_end), or None = home all day;
    weekend="same" copies the weekday answer."""
    d = {f"group_people/{cat}": str(n)}
    if not n:
        return d
    g = "group_occupancy/"

    def away(day, win):
        if win is None:
            return {f"{g}{cat}_{day}_away_start": "home_all_day"}
        return {f"{g}{cat}_{day}_away_start": str(win[0]), f"{g}{cat}_{day}_away_end": str(win[1])}

    d.update(away("weekday", weekday))
    if weekend == "same":
        d[f"{g}{cat}_weekend_same"] = "yes"
    else:
        d[f"{g}{cat}_weekend_same"] = "no"
        d.update(away("weekend", weekend))
    return d


def room(rtype, bulbs, btype="LED", watts=None, weekday=(), weekend="same", pattern=True,
         other=None, security=None):
    """weekday/weekend: list of (start, end) presence blocks.
    security: ("dusk_to_dawn"|"motion",) or ("timer"|"manual", on, off)."""
    p = "group_rooms/room/"
    r = {p + "room_type": rtype, p + "bulbs": str(bulbs)}
    if other:
        r[p + "room_other"] = other
    if bulbs:
        r[p + "bulb_type"] = btype
        if watts is not None:
            r[p + "bulb_watts"] = str(watts)
    if rtype == "outside_security":
        if bulbs:
            r[p + "security_mode"] = security[0]
            if security[0] in ("timer", "manual"):
                r[p + "security_light_on"], r[p + "security_light_off"] = str(security[1]), str(security[2])
        return r
    r[p + "room_has_pattern"] = "yes" if pattern else "no"
    if not pattern:
        return r

    def blocks(day, bl):
        rp = f"{p}room_{day}_grp/room_{day}/"
        return [{f"{rp}room_{day}_start": str(s), f"{rp}room_{day}_end": str(e)} for s, e in bl]

    r[f"{p}room_weekday_grp/room_weekday"] = blocks("weekday", weekday)
    if weekend == "same":
        r[p + "room_weekend_same"] = "yes"
    else:
        r[p + "room_weekend_same"] = "no"
        r[f"{p}room_weekend_grp/room_weekend"] = blocks("weekend", weekend)
    return r


def appliance(atype, count=1, watts=None, volts=None, amps=None, minutes=None,
              wd_freq="never", weekday=(), wd_sessions=1,
              we_freq="never", weekend="same", we_sessions=1,
              other=None, other_always_on="no", other_home="yes"):
    """weekday/weekend windows: (start, end, peak, often); peak "spread" or an hour."""
    p = "group_appliances/appliance/"
    a = {p + "appliance_type": atype, p + "appliance_count": str(count)}
    if atype == "other":
        a[p + "appliance_other"] = other
        a[p + "appliance_other_always_on"] = other_always_on
        if other_always_on == "no":
            a[p + "appliance_other_home"] = other_home
    for k, v in (("label_watts", watts), ("label_volts", volts), ("label_amps", amps)):
        if v is not None:
            a[p + k] = str(v)
    if minutes is None:          # always-on appliance: no time-of-use questions
        return a
    a[p + "duration_typical"] = str(minutes)

    def windows(day, ws):
        wp = f"{p}appliance_{day}_grp/appliance_{day}/appliance_{day}_"
        return [{wp + "start": str(s), wp + "end": str(e), wp + "peak": str(pk), wp + "often": o}
                for s, e, pk, o in ws]

    a[p + "weekday_frequency"] = wd_freq
    if wd_freq != "never":
        a[f"{p}appliance_weekday_grp/appliance_weekday"] = windows("weekday", weekday)
        a[p + "appliance_weekday_sessions"] = str(wd_sessions)
    a[p + "weekend_frequency"] = we_freq
    if we_freq != "never":
        if wd_freq != "never":
            a[p + "weekend_same_as_weekday"] = "yes" if weekend == "same" else "no"
        if weekend != "same" or wd_freq == "never":
            a[f"{p}appliance_weekend_grp/appliance_weekend"] = windows("weekend", weekend)
        a[p + "appliance_weekend_sessions"] = str(we_sessions)
    return a


def household(hid, date, sub_county, dwelling, residents, people, rooms, appliances, grid, site,
              county="Nairobi", gps="-1.2700 36.8100 1700 5", notes=""):
    s = {"group_start/consent": "OK", "group_start/household_id": hid,
         "group_start/survey_date": date, "group_start/sub_county": sub_county,
         "group_start/county": county, "group_start/gps": gps, "group_start/dwelling": dwelling,
         "group_people/number_of_residents": str(residents)}
    for p in people:
        s.update(p)
    s["group_rooms/room"] = rooms
    s["group_appliances/appliance"] = appliances
    s.update({f"group_grid/{k}": str(v) for k, v in grid.items()})
    s.update({f"group_site/{k}": str(v) for k, v in site.items()})
    s.update({"group_close/logger_willing": "maybe",
              "group_close/interviewer_notes": f"MADE-UP TEST HOUSEHOLD. {notes}"})
    return s


def grid_prepaid(spend, kwh=None, kes=None, tariff="domestic", backup="none", **extra):
    g = {"grid_connected": "yes", "phase": "single", "metering": "prepaid", "tariff": tariff,
         "monthly_spend": spend, "blackout_hours": 2, "backup": backup}
    if kwh:
        g.update(token_kwh=kwh, token_kes=kes)
    g.update(extra)
    return g


def site(length, width, orientation="north_facing", roof="iron_sheet", tilt=15,
         shading="minimal", mounting="flush", cable=10):
    return {"roof_length": length, "roof_width": width, "orientation": orientation, "tilt": tilt,
            "shading": shading, "roof_type": roof, "mounting": mounting, "cable_length_meters": cable}


EVE = [(18, 23)]
MORN = [(6, 7)]
BATH = [(6, 7), (20, 21)]

# ---------------------------------------------------------------- households
SAMPLES = [
    household(
        "S101", "2026-11-19", "Kasarani", "flat", 1,
        [occupancy("adults_working", 1, (7, 19), (10, 14))],
        [room("other", 1, other="Bedsitter", weekday=[(6, 7), (19, 23)], weekend=[(7, 10), (14, 23)]),
         room("bathroom", 1, watts=5, weekday=MORN)],
        [appliance("smartphone_charger", minutes=90, wd_freq="every_weekday",
                   weekday=[(20, 24, "spread", "always")], we_freq="both_days"),
         appliance("television", watts=60, minutes=150, wd_freq="every_weekday",
                   weekday=[(19, 23, 21, "always")], we_freq="both_days",
                   weekend=[(15, 23, 20, "always")]),
         appliance("electric_hotplate", watts=1000, minutes=30, wd_freq="most_weekdays",
                   weekday=[(19, 21, 20, "always")], we_freq="both_days",
                   weekend=[(8, 9, "spread", "usually"), (18, 20, 19, "always")], we_sessions=2),
         appliance("iron_box", minutes=20, we_freq="usually_one_day",
                   weekend=[(10, 12, "spread", "always")])],
        grid_prepaid(800, kwh=30, kes=750), site(4, 3, roof="concrete")),

    household(
        "S102", "2026-11-19", "Westlands", "flat", 2,
        [occupancy("adults_working", 2, (7, 18), None)],
        [room("living_room", 2, watts=9, weekday=EVE, weekend=[(9, 23)]),
         room("master_bedroom", 1, watts=9, weekday=[(6, 7), (22, 23)], weekend=[(8, 9), (22, 24)]),
         room("kitchen", 1, watts=12, weekday=[(6, 7), (18, 20)], weekend=[(8, 10), (13, 14), (18, 20)]),
         room("bathroom", 1, watts=5, weekday=BATH)],
        [appliance("refrigerator", watts=110),
         appliance("wifi_router"),
         appliance("electric_kettle", watts=1800, minutes=4, wd_freq="every_weekday",
                   weekday=[(6, 7, "spread", "always"), (19, 21, 20, "sometimes")], wd_sessions=2,
                   we_freq="both_days", weekend=[(8, 10, 8, "always"), (16, 18, "spread", "usually")],
                   we_sessions=2),
         appliance("microwave", watts=800, minutes=3, wd_freq="every_weekday",
                   weekday=[(19, 21, 19, "always")], we_freq="both_days",
                   weekend=[(12, 14, 13, "always"), (19, 21, 19, "always")], we_sessions=2),
         appliance("television", watts=85, minutes=180, wd_freq="every_weekday",
                   weekday=[(19, 23, 21, "always")], we_freq="both_days",
                   weekend=[(10, 23, 20, "always")]),
         appliance("dstv_decoder", minutes=180, wd_freq="every_weekday",
                   weekday=[(19, 23, 21, "always")], we_freq="both_days",
                   weekend=[(10, 23, 20, "always")]),
         appliance("laptop", count=2, watts=65, minutes=120, wd_freq="few_times_a_week",
                   weekday=[(20, 23, "spread", "always")], we_freq="both_days",
                   weekend=[(10, 17, "spread", "always")]),
         appliance("washing_machine", watts=500, minutes=75, we_freq="usually_one_day",
                   weekend=[(9, 13, 10, "always")])],
        grid_prepaid(2500, kwh=95, kes=2400), site(10, 8, roof="concrete", tilt=0, orientation="flat")),

    household(
        "S103", "2026-11-19", "Embakasi East", "flat", 5,
        [occupancy("adults_working", 2, (7, 18), None),
         occupancy("household_helpers", 1, None, (13, 18)),
         occupancy("school_children", 2, (7, 16), None)],
        [room("living_room", 3, btype="CFL", weekday=[(16, 22)], weekend=[(9, 22)]),
         room("dining_area", 1, btype="CFL", weekday=[(19, 21)]),
         room("master_bedroom", 1, watts=9, weekday=[(6, 7), (22, 23)]),
         room("children_bedroom", 1, watts=9, weekday=[(6, 7), (20, 21)], weekend=[(8, 9), (20, 22)]),
         room("bedroom", 1, watts=9, weekday=[(5, 6), (21, 22)]),
         room("kitchen", 1, btype="fluorescent", weekday=[(5, 7), (17, 21)], weekend=[(7, 9), (12, 14), (17, 21)]),
         room("bathroom", 2, watts=5, weekday=[(5, 7), (20, 22)]),
         room("staircase_corridor", 1, watts=5, pattern=False),
         room("outside_security", 2, watts=10, security=("dusk_to_dawn",))],
        [appliance("refrigerator", watts=150),
         appliance("wifi_router"),
         appliance("television", watts=100, minutes=240, wd_freq="every_weekday",
                   weekday=[(16, 22, 20, "always")], we_freq="both_days",
                   weekend=[(9, 22, 20, "always")]),
         appliance("dstv_decoder", minutes=240, wd_freq="every_weekday",
                   weekday=[(16, 22, 20, "always")], we_freq="both_days",
                   weekend=[(9, 22, 20, "always")]),
         appliance("electric_kettle", minutes=4, wd_freq="every_weekday",
                   weekday=[(5, 7, 6, "always"), (17, 20, "spread", "usually")], wd_sessions=2,
                   we_freq="both_days", weekend="same", we_sessions=3),
         appliance("iron_box", watts=1200, minutes=30, wd_freq="few_times_a_week",
                   weekday=[(5, 7, 6, "always")], we_freq="usually_one_day",
                   weekend=[(14, 17, "spread", "always")]),
         appliance("immersion_water_heater", minutes=10, wd_freq="every_weekday",
                   weekday=[(5, 7, 6, "always"), (20, 22, 21, "sometimes")], wd_sessions=3,
                   we_freq="both_days", weekend=[(7, 10, 8, "always")], we_sessions=3),
         appliance("blender", minutes=3, wd_freq="few_times_a_week",
                   weekday=[(6, 7, "spread", "always")], we_freq="occasionally",
                   weekend=[(8, 11, "spread", "always")]),
         appliance("smartphone_charger", count=3, minutes=120, wd_freq="every_weekday",
                   weekday=[(19, 24, "spread", "always")], we_freq="both_days"),
         appliance("washing_machine", minutes=90, we_freq="both_days",
                   weekend=[(8, 12, 9, "always")])],
        grid_prepaid(4500, kwh=170, kes=4300), site(12, 9, orientation="east_facing")),

    household(
        "S104", "2026-11-19", "Lang'ata", "maisonette", 5,
        [occupancy("adults_working", 1, (7, 19), None),
         occupancy("adults_non_working", 1, (10, 12), None),
         occupancy("household_helpers", 1, None, (14, 18)),
         occupancy("young_children", 1, None),
         occupancy("elderly", 1, None)],
        [room("living_room", 4, watts=9, weekday=[(9, 13), (15, 22)], weekend=[(9, 22)]),
         room("dining_area", 2, watts=9, weekday=[(7, 8), (13, 14), (19, 21)]),
         room("master_bedroom", 2, watts=9, weekday=[(6, 7), (22, 23)]),
         room("bedroom", 1, watts=9, weekday=[(6, 7), (21, 22)]),
         room("bedroom", 1, watts=9, weekday=[(5, 6), (20, 21)]),
         room("kitchen", 2, watts=18, btype="fluorescent", weekday=[(6, 9), (12, 14), (17, 21)]),
         room("bathroom", 3, watts=5, weekday=[(6, 8), (20, 22)]),
         room("store_room", 1, watts=9, pattern=False),
         room("outside_security", 4, watts=20, security=("motion",))],
        [appliance("refrigerator", watts=180),
         appliance("chest_freezer", watts=120),
         appliance("wifi_router"),
         appliance("cctv_system", watts=35),
         appliance("electric_fence_energiser"),
         appliance("water_pump", watts=750, minutes=20, wd_freq="every_weekday",
                   weekday=[(6, 8, 6, "always"), (18, 20, "spread", "usually")], wd_sessions=2,
                   we_freq="both_days", we_sessions=2),
         appliance("electric_oven", watts=2200, minutes=45, wd_freq="once_a_week",
                   weekday=[(17, 19, 18, "always")], we_freq="usually_one_day",
                   weekend=[(12, 14, 13, "always")]),
         appliance("induction_cooker", watts=2000, minutes=40, wd_freq="every_weekday",
                   weekday=[(12, 14, 13, "always"), (18, 20, 19, "always")], wd_sessions=2,
                   we_freq="both_days", we_sessions=2),
         appliance("microwave", minutes=4, wd_freq="every_weekday",
                   weekday=[(7, 9, "spread", "usually"), (13, 14, "spread", "always")], wd_sessions=2,
                   we_freq="both_days", we_sessions=2),
         appliance("television", count=2, volts=230, amps=0.5, minutes=300, wd_freq="every_weekday",
                   weekday=[(9, 22, 20, "always")], we_freq="both_days"),
         appliance("washing_machine", watts=2000, minutes=100, wd_freq="few_times_a_week",
                   weekday=[(9, 12, 10, "always")], we_freq="never"),
         appliance("gate_motor", minutes=1, wd_freq="every_weekday",
                   weekday=[(6, 8, 7, "always"), (18, 20, 19, "always")], wd_sessions=4,
                   we_freq="both_days", we_sessions=4)],
        {"grid_connected": "yes", "phase": "three", "metering": "postpaid", "tariff": "domestic",
         "monthly_spend": 9000, "blackout_hours": 4, "backup": "generator", "backup_kilowatts": 5.5,
         "backup_loads": "fridge, freezer, lights, gate"},
        site(15, 10, roof="clay_tile", tilt=25, shading="moderate", cable=20),
        notes="Elderly parent and toddler home all day."),

    household(
        "S105", "2026-11-19", "Kibra", "bungalow", 2,
        [occupancy("elderly", 2, None)],
        [room("living_room", 1, btype="incandescent", weekday=[(8, 12), (16, 21)]),
         room("master_bedroom", 1, btype="incandescent", weekday=[(6, 7), (21, 22)]),
         room("kitchen", 1, watts=9, weekday=[(7, 8), (12, 13), (17, 19)]),
         room("bathroom", 1, watts=9, weekday=[(6, 7), (20, 21)]),
         room("outside_security", 1, btype="CFL", security=("timer", 19, 23))],
        [appliance("refrigerator"),
         appliance("electric_kettle", watts=1500, minutes=5, wd_freq="every_weekday",
                   weekday=[(6, 8, 7, "always"), (16, 17, "spread", "always")], wd_sessions=2,
                   we_freq="both_days", we_sessions=2),
         appliance("television", watts=50, minutes=240, wd_freq="every_weekday",
                   weekday=[(17, 22, 19, "always"), (10, 12, "spread", "rarely")],
                   we_freq="both_days"),
         appliance("bluetooth_speaker", minutes=120, wd_freq="every_weekday",
                   weekday=[(6, 9, 7, "always")], we_freq="both_days")],
        grid_prepaid(1000, kwh=55, kes=1000, tariff="lifeline"), site(8, 6)),

    household(
        "S106", "2026-11-19", "Ruaraka", "bungalow", 3,
        [occupancy("adults_working", 1, (7, 18), None),
         # night-shift worker: home in the day, so "usually at home during the day", away 18-07
         occupancy("adults_non_working", 1, (18, 7), None),
         occupancy("school_children", 1, (7, 16), None)],
        [room("living_room", 2, watts=9, weekday=[(16, 23)], weekend=[(9, 23)]),
         room("master_bedroom", 1, watts=9, weekday=[(6, 7), (22, 24)]),
         room("children_bedroom", 1, watts=9, weekday=[(6, 7), (20, 21)]),
         room("kitchen", 1, watts=9, weekday=[(6, 7), (17, 20)]),
         room("bathroom", 1, watts=5, weekday=[(6, 7), (17, 18), (21, 22)]),
         room("outside_security", 2, watts=10, security=("manual", 18, 6))],
        [appliance("refrigerator", watts=90),
         appliance("wifi_router"),
         appliance("television", watts=70, minutes=180, wd_freq="every_weekday",
                   weekday=[(20, 1, 22, "always")], we_freq="both_days"),
         appliance("electric_pressure_cooker", watts=1000, minutes=40, wd_freq="most_weekdays",
                   weekday=[(17, 20, 18, "always")], we_freq="both_days"),
         appliance("laptop", watts=45, minutes=240, wd_freq="every_weekday",
                   weekday=[(8, 17, "spread", "always")], we_freq="occasionally",
                   weekend=[(10, 16, "spread", "always")]),
         appliance("other", watts=15, other="Aquarium pump", other_always_on="yes"),
         appliance("other", minutes=60, other="Hair clipper", wd_freq="rarely",
                   weekday=[(19, 21, "spread", "always")], we_freq="occasionally",
                   weekend=[(10, 12, "spread", "always")])],
        grid_prepaid(1500, kwh=60, kes=1450, backup="solar_battery", solar_panel_count=4,
                     solar_panel_watts=330, battery_volts=12, battery_amp_hours=200, battery_units=2,
                     backup_kilowatts=3, backup_loads="lights, TV, router, fridge"),
        site(10, 7, orientation="north_facing", tilt=10, cable=12),
        notes="One adult works nights (19:00-07:00)."),

    household(
        "S107", "2026-11-19", "Kajiado North", "bungalow", 3,
        [occupancy("adults_non_working", 2, (9, 13), None),
         occupancy("school_children", 1, (7, 16), None)],
        [room("living_room", 2, watts=5, weekday=[(18, 22)]),
         room("master_bedroom", 1, watts=5, weekday=[(21, 22)]),
         room("kitchen", 1, watts=5, weekday=[(6, 7), (18, 20)])],
        [appliance("smartphone_charger", count=3, minutes=120, wd_freq="every_weekday",
                   weekday=[(10, 16, "spread", "always")], we_freq="both_days"),
         appliance("television", watts=30, minutes=120, wd_freq="every_weekday",
                   weekday=[(19, 22, 20, "always")], we_freq="both_days"),
         appliance("bluetooth_speaker", minutes=180, wd_freq="every_weekday",
                   weekday=[(14, 18, "spread", "always")], we_freq="both_days")],
        {"grid_connected": "no", "backup": "solar_only", "solar_panel_count": 2,
         "solar_panel_watts": 150},
        site(7, 5, cable=6), county="Kajiado", gps="-1.3900 36.7700 1720 8",
        notes="Off grid, solar only."),
]

if __name__ == "__main__":
    out = HERE / "sample_households.json"
    out.write_text(json.dumps(SAMPLES, indent=1))
    print(f"wrote {out} ({len(SAMPLES)} made-up households)")
