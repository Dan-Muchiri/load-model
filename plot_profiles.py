"""
plot_profiles.py
================
Dependency-free SVG chart of a household's simulated load: one panel per day
type (weekday, weekend) on a shared power scale, each with the ensemble mean
power (line) and the 10th-90th percentile band across runs (shaded), in
10-minute bins. Hovering a point shows its values (SVG <title>). Light and
dark mode follow the viewer's system setting.

Used by `simulate.py ... --out <folder>`, or directly:
    from plot_profiles import write_profiles_svg
    write_profiles_svg("H902_profiles.svg", [weekday_ensemble, weekend_ensemble])
"""
from html import escape

import numpy as np

BIN_MIN = 10
W, H = 920, 384
PAD_L, PAD_R, PAD_T, PAD_B = 64, 24, 82, 46
GAP = 36


def _binned(power_w):
    """(n_runs, 1440) W -> per-bin mean, p10, p90 in kW (n_bins,)."""
    b = power_w.reshape(power_w.shape[0], -1, BIN_MIN).mean(axis=2) / 1000.0
    return b.mean(axis=0), np.percentile(b, 10, axis=0), np.percentile(b, 90, axis=0)


def _nice_max(v):
    if v <= 0:
        return 1.0
    mag = 10 ** np.floor(np.log10(v))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * mag >= v:
            return float(m * mag)
    return float(10 * mag)


def write_profiles_svg(path, ensembles, title="Simulated load"):
    series = [(e.day_type, e.n_runs, *_binned(e.power_w), e.summary()) for e in ensembles]
    ymax = _nice_max(max(s[4].max() for s in series) * 1.05)
    n = len(series)
    pw = (W - PAD_L - PAD_R - GAP * (n - 1)) / n
    ph = H - PAD_T - PAD_B
    nb = MINUTES = 1440 // BIN_MIN

    def x(i, px0):
        return px0 + pw * (i + 0.5) / nb

    def y(v):
        return PAD_T + ph * (1 - v / ymax)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'role="img" aria-label="{escape(title)}: mean power and 10th-90th percentile band">',
           """<style>
  svg { --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --grid:#e4e3df; --series:#2a78d6; }
  @media (prefers-color-scheme: dark) {
    svg { --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --grid:#3a3936; --series:#3987e5; }
  }
  .bg { fill: var(--surface); }
  text { font: 12px system-ui, -apple-system, 'Segoe UI', sans-serif; fill: var(--ink2); }
  .title { font-size: 15px; font-weight: 600; fill: var(--ink); }
  .ptitle { font-size: 13px; font-weight: 600; fill: var(--ink); }
  .grid { stroke: var(--grid); stroke-width: 1; }
  .band { fill: var(--series); fill-opacity: 0.18; }
  .mean { fill: none; stroke: var(--series); stroke-width: 2; stroke-linejoin: round; }
  .hit { fill: transparent; }
  .hit:hover { fill: var(--series); fill-opacity: 0.08; }
</style>""",
           f'<rect class="bg" x="0" y="0" width="{W}" height="{H}" rx="8"/>',
           f'<text class="title" x="{PAD_L}" y="24">{escape(title)}</text>',
           f'<text x="{PAD_L}" y="42">Mean power (line) and 10th-90th percentile across runs (band), '
           f'{BIN_MIN}-minute bins</text>']

    for k, (day, runs, mean, p10, p90, summ) in enumerate(series):
        x0 = PAD_L + k * (pw + GAP)
        # grid + y labels (shared scale; labels on the first panel only)
        for t in np.linspace(0, ymax, 5):
            out.append(f'<line class="grid" x1="{x0:.1f}" x2="{x0 + pw:.1f}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
            if k == 0:
                out.append(f'<text x="{x0 - 8:.1f}" y="{y(t) + 4:.1f}" text-anchor="end">{t:g}</text>')
        for hr in range(0, 25, 6):
            xx = x0 + pw * hr / 24
            out.append(f'<text x="{xx:.1f}" y="{PAD_T + ph + 18:.1f}" text-anchor="middle">{hr:02d}:00</text>')
        band = [f"{x(i, x0):.1f},{y(v):.1f}" for i, v in enumerate(p90)] + \
               [f"{x(i, x0):.1f},{y(v):.1f}" for i, v in reversed(list(enumerate(p10)))]
        out.append(f'<polygon class="band" points="{" ".join(band)}"/>')
        out.append('<polyline class="mean" points="'
                   + " ".join(f"{x(i, x0):.1f},{y(v):.1f}" for i, v in enumerate(mean)) + '"/>')
        # hover targets: one column per bin
        cw = pw / nb
        for i in range(nb):
            hh, mm = divmod(i * BIN_MIN, 60)
            out.append(f'<rect class="hit" x="{x0 + i * cw:.1f}" y="{PAD_T}" width="{cw:.2f}" height="{ph}">'
                       f'<title>{day} {hh:02d}:{mm:02d}: mean {mean[i]:.2f} kW, '
                       f'10th-90th {p10[i]:.2f}-{p90[i]:.2f} kW</title></rect>')
        out.append(f'<text class="ptitle" x="{x0:.1f}" y="{PAD_T - 6:.1f}">{day.capitalize()} '
                   f'<tspan class="" style="font-weight:400">· median {summ["energy_kwh_p50"]:.2f} kWh/day, '
                   f'peak p50 {summ["peak_w_p50"] / 1000:.2f} kW, {runs} runs</tspan></text>')
    out.append(f'<text x="18" y="{PAD_T + ph / 2:.1f}" transform="rotate(-90 18 {PAD_T + ph / 2:.1f})" '
               f'text-anchor="middle">Power (kW)</text>')
    out.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(out))
    return path
