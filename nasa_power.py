"""
nasa_power.py
=============
Hourly solar irradiance for the lighting sub-model, from NASA POWER.

The lighting model (simulate.py) decides minute by minute whether artificial
light is needed from the outdoor global horizontal irradiance (GHI): a light
is needed when GHI falls below the household's threshold (CREST). Each
simulated day uses one REAL historical day from the same calendar month, so
seasonal variation and the timing of cloud within the day are both kept.

Source: NASA POWER hourly API, community RE,
    ALLSKY_SFC_SW_DWN  all-sky GHI, W/m2 (hourly mean)
    CLRSKY_SFC_SW_DWN  clear-sky GHI, W/m2 (same product)
Clear-sky index CSI = ALLSKY / CLRSKY (daytime hours only).

Location: the thesis point, Nairobi 1.2921 S, 36.8219 E. NASA POWER's solar
data are on a ~1 degree grid, so every Nairobi household falls in the same
cell; household GPS is never sent anywhere.

Times: requested in UTC and shifted to East Africa Time (UTC+3), so the hours
line up with the interview hours. A value stamped hour H is the mean over
H:00-H+1:00.

Usage:
    python nasa_power.py                  # download 2001-2025 into data/ (cached)
    from nasa_power import load_irradiance_days
    days = load_irradiance_days()         # dict month -> array (n_days, 24) of GHI, W/m2
"""
import argparse
import csv
import datetime as dt
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

LAT, LON = -1.2921, 36.8219            # thesis site (Nairobi)
UTC_OFFSET_H = 3                       # East Africa Time
FIRST_YEAR, LAST_YEAR = 2001, 2025     # NASA POWER hourly starts in 2001
DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE = DATA_DIR / "nasa_power_nairobi_hourly.csv"
API = ("https://power.larc.nasa.gov/api/temporal/hourly/point"
       "?parameters=ALLSKY_SFC_SW_DWN,CLRSKY_SFC_SW_DWN&community=RE"
       "&longitude={lon}&latitude={lat}&start={start}&end={end}&format=JSON&time-standard=UTC")
FILL = -999.0


def _fetch_year(year, lat=LAT, lon=LON, retries=4):
    # One extra day either side so the UTC->EAT shift fills every local day.
    start = dt.date(year, 1, 1) - dt.timedelta(days=1)
    end = dt.date(year, 12, 31) + dt.timedelta(days=1)
    start = max(start, dt.date(FIRST_YEAR, 1, 1))          # hourly data begin 2001-01-01
    end = min(end, dt.date.today() - dt.timedelta(days=7))  # recent days are not yet available
    url = API.format(lat=lat, lon=lon, start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                return json.load(r)["properties"]["parameter"]
        except Exception as e:          # network hiccup: back off and retry
            if attempt == retries - 1:
                raise
            print(f"  {year}: {type(e).__name__}, retrying", file=sys.stderr)
            time.sleep(5 * 2 ** attempt)


def download(first=FIRST_YEAR, last=LAST_YEAR, path=CACHE):
    """Download hourly all-sky and clear-sky GHI and write them in EAT to `path`."""
    rows = {}
    for year in range(first, last + 1):
        p = _fetch_year(year)
        for stamp, ghi in p["ALLSKY_SFC_SW_DWN"].items():
            t = dt.datetime.strptime(stamp, "%Y%m%d%H") + dt.timedelta(hours=UTC_OFFSET_H)
            if t.year != year:
                continue
            rows[t] = (ghi, p["CLRSKY_SFC_SW_DWN"].get(stamp, FILL))
        print(f"  {year}: {sum(1 for t in rows if t.year == year)} hours")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_eat", "ghi_allsky_wm2", "ghi_clearsky_wm2"])
        for t in sorted(rows):
            w.writerow([t.strftime("%Y-%m-%d %H:00"), *rows[t]])
    return path


def load_hourly(path=CACHE):
    """(times, all-sky GHI, clear-sky GHI) from the cache; fill values -> NaN."""
    if not Path(path).exists():
        raise FileNotFoundError(f"{path} not found. Run `python nasa_power.py` once to download it.")
    times, ghi, clr = [], [], []
    with open(path) as f:
        for r in csv.DictReader(f):
            times.append(dt.datetime.strptime(r["time_eat"], "%Y-%m-%d %H:%M"))
            g, c = float(r["ghi_allsky_wm2"]), float(r["ghi_clearsky_wm2"])
            ghi.append(np.nan if g <= FILL else g)
            clr.append(np.nan if c <= FILL else c)
    return times, np.array(ghi), np.array(clr)


def load_irradiance_days(path=CACHE):
    """Complete local days grouped by calendar month.

    Returns dict month (1-12) -> np.ndarray (n_days, 24) of all-sky GHI in
    W/m2, hour h = mean over h:00-h+1:00 East Africa Time. Days with any
    missing hour are dropped.
    """
    times, ghi, _ = load_hourly(path)
    days = {}
    for t, g in zip(times, ghi):
        days.setdefault(t.date(), [np.nan] * 24)[t.hour] = g
    out = {m: [] for m in range(1, 13)}
    for d, vals in days.items():
        if not np.isnan(vals).any():
            out[d.month].append(vals)
    return {m: np.array(v) for m, v in out.items() if v}


def daily_csi(path=CACHE):
    """Daily clear-sky index (sum of all-sky / sum of clear-sky GHI) per date."""
    times, ghi, clr = load_hourly(path)
    tot = {}
    for t, g, c in zip(times, ghi, clr):
        a = tot.setdefault(t.date(), [0.0, 0.0, False])
        if np.isnan(g) or np.isnan(c):
            a[2] = True
        else:
            a[0] += g
            a[1] += c
    return {d: a[0] / a[1] for d, a in tot.items() if not a[2] and a[1] > 0}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--first", type=int, default=FIRST_YEAR)
    ap.add_argument("--last", type=int, default=LAST_YEAR)
    ap.add_argument("--force", action="store_true", help="download even if the cache exists")
    a = ap.parse_args()
    if CACHE.exists() and not a.force:
        print(f"{CACHE} already exists (use --force to download again).")
    else:
        print(f"Downloading NASA POWER hourly GHI {a.first}-{a.last} for {LAT}, {LON} ...")
        print(f"wrote {download(a.first, a.last)}")
    days = load_irradiance_days()
    print("complete days per month:", {m: len(v) for m, v in days.items()})
