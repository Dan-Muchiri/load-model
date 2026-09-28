# load-model

Stochastic bottom-up residential load model (CREST-style, one household at a time)
for the MSc thesis on multi-objective sizing of hybrid solar systems.

| File | What it does |
|------|--------------|
| `schema.py` | Household questionnaire schema, validator and the reference household `H001` |
| `calibrate_tou.py` | Turns interview usage windows into calibrated `tou_weekday` / `tou_weekend` arrays |
| `simulate.py` | Monte Carlo load simulator: 1-minute daily profiles for weekdays and weekends |

## Setup

```bash
pip install -r requirements.txt
```

## Simulating a household

From the command line (uses the reference household in `schema.py`):

```bash
python simulate.py --runs 1000 --seed 1 --csv profiles.csv
```

This prints daily energy and peak percentiles, the mean hourly profile, the largest
loads and the consumption tier, and writes one row per minute with the mean,
10th/50th/90th percentile-day, maximum and minimum profiles for each day type.

From Python:

```python
from schema import REFERENCE_HOUSEHOLD
from simulate import simulate_day, simulate_ensemble, assign_tier

profile_w = simulate_day(REFERENCE_HOUSEHOLD, "weekday", seed=1)   # 1440 values, W

wd = simulate_ensemble(REFERENCE_HOUSEHOLD, "weekday", n_runs=1000, seed=1)
we = simulate_ensemble(REFERENCE_HOUSEHOLD, "weekend", n_runs=1000, seed=2)
wd.summary()                 # p10/p50/p90 daily kWh and peak W
wd.percentile_profile(90)    # representative high-load day for the sizing MILP
wd.load_breakdown()          # mean kWh/day per appliance and lighting zone
assign_tier(wd, we)          # ("medium", 10.7)
```

For a surveyed household, pass its schema dict instead of `REFERENCE_HOUSEHOLD`.
When `model_parameters.csi_source` is `"nasa_power"`, pass the site's historical
daily clear-sky index values as `csi_samples=...`; without them the simulator warns
and uses `csi_fixed_value`.

To check a model profile against power-logger data (both 1-minute, same length):

```python
from simulate import compare_to_measured
compare_to_measured(wd.mean_profile(), measured_weekday_w)  # MAE, variability ratio, PAR
```

## How it works

- **Occupancy**: first-order Markov chain over the number of active people, with one
  transition matrix per hour whose stationary distribution has the interview value as
  its mean. Occupancy moves by one person per minute.
- **Appliances**: each unit switches on with probability `tou[h] / 60` per minute
  (scaled by `appliance_occupancy_scale_zero` when nobody is home and the appliance
  needs occupancy), runs for a Normal(`mean_duration_min`, `std_duration_min`)
  duration, then waits `restart_delay_min`. This is the same mechanism
  `calibrate_tou.verify_calibration()` uses, so calibrated TOU arrays hit their targets.
  `standby_power_w` is drawn while a unit is off.
- **Lighting**: the same mechanism per room, allowed to switch on only when it is dark
  (half-sine daylight between sunrise and sunset times the day's CSI is below
  `daylight_threshold_csi`) and, for interior rooms, when someone is home and the room
  is occupied that hour in `room_occupancy`.

## Tests

```bash
python -m pytest
```
