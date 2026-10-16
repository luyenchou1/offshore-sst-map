"""Build the magnetic-variation (declination) grid the SST measure tool uses.

Boat compasses read magnetic, so the measure tool shows each course true and
magnetic. Variation across the SST area runs from about 12 to 16 degrees west
and drifts a little every year, so instead of one fixed number we look it up
in a small grid computed from the World Magnetic Model (WMM-2025, NOAA NCEI
and the British Geological Survey; public domain).

Usage (the coefficient file is WMM.COF from
https://www.ncei.noaa.gov/products/world-magnetic-model, any WMM-2025 copy):
    .venv/bin/python tools/build_declination.py WMM.COF > map/declination.py
    .venv/bin/python tools/build_declination.py WMM.COF --check

The grid covers the SST area (38.5 to 44 N, 75.5 to 68.5 W) every half degree
at EPOCH, with each point's change per year, so map/measure.py stays within
a tenth of a degree of the full model until the WMM is replaced (2030).
--check prints the model at a few spots next to the values NOAA's online
calculator gave (WMM-2025, Oct 2026) and the grid's worst interpolation error.
"""

import math
import sys

A_WGS84 = 6378.137                  # km
F_WGS84 = 1 / 298.257223563
E2 = F_WGS84 * (2 - F_WGS84)
A_REF = 6371.2                      # WMM reference radius, km

EPOCH = 2026.5                      # grid epoch (decimal year)
LAT0, LAT1, LON0, LON1, STEP = 38.5, 44.0, -75.5, -68.5, 0.5

# NOAA magnetic field calculator (WMM-2025), Oct 2026, quoted in docs/sst-review.md
NOAA_CHECKS = [
    ("Hudson Canyon", 39.54, -72.05, -13.1),
    ("Stellwagen Bank", 42.35, -70.35, -14.2),
]


def read_cof(path):
    with open(path) as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    epoch = float(lines[0].split()[0])
    model = lines[0].split()[1]
    coef = {}
    for ln in lines[1:]:
        if ln.startswith("9999"):
            break
        n, m, g, h, gd, hd = ln.split()
        coef[(int(n), int(m))] = (float(g), float(h), float(gd), float(hd))
    return model, epoch, coef


def declination(coef, epoch0, lat, lon, year, alt_km=0.0):
    """Declination in degrees (east positive) from WMM coefficients."""
    nmax = max(n for n, _ in coef)
    phi = math.radians(lat)
    lam = math.radians(lon)
    # Geodetic -> geocentric spherical
    rc = A_WGS84 / math.sqrt(1 - E2 * math.sin(phi) ** 2)
    p = (rc + alt_km) * math.cos(phi)
    z = (rc * (1 - E2) + alt_km) * math.sin(phi)
    r = math.hypot(p, z)
    phic = math.asin(z / r)
    theta = math.pi / 2 - phic
    x, s = math.cos(theta), math.sin(theta)

    # Schmidt semi-normalized associated Legendre functions P[n][m](cos theta)
    P = [[0.0] * (nmax + 2) for _ in range(nmax + 2)]
    P[0][0] = 1.0
    for n in range(1, nmax + 1):
        P[n][n] = s * P[n - 1][n - 1] * (1.0 if n == 1 else math.sqrt((2 * n - 1) / (2 * n)))
        P[n][n - 1] = math.sqrt(2 * n - 1) * x * P[n - 1][n - 1]
        for m in range(0, n - 1):
            P[n][m] = ((2 * n - 1) * x * P[n - 1][m]
                       - math.sqrt((n - 1) ** 2 - m * m) * P[n - 2][m]) / math.sqrt(n * n - m * m)

    dt = year - epoch0
    bx = by = bz = 0.0                 # geocentric north, east, down
    for n in range(1, nmax + 1):
        ar = (A_REF / r) ** (n + 2)
        for m in range(0, n + 1):
            g, h, gd, hd = coef[(n, m)]
            g += gd * dt
            h += hd * dt
            cm, sm = math.cos(m * lam), math.sin(m * lam)
            dP = (n * x * P[n][m] - math.sqrt(n * n - m * m) * P[n - 1][m]) / s   # dP/dtheta
            bx += ar * (g * cm + h * sm) * dP
            by += ar * m * (g * sm - h * cm) * P[n][m] / s
            bz -= ar * (n + 1) * (g * cm + h * sm) * P[n][m]
    psi = phic - phi                   # rotate to geodetic
    north = bx * math.cos(psi) - bz * math.sin(psi)
    return math.degrees(math.atan2(by, north))


def grid(coef, epoch0):
    lats = [round(LAT0 + i * STEP, 2) for i in range(int(round((LAT1 - LAT0) / STEP)) + 1)]
    lons = [round(LON0 + j * STEP, 2) for j in range(int(round((LON1 - LON0) / STEP)) + 1)]
    d0 = [[declination(coef, epoch0, la, lo, EPOCH) for lo in lons] for la in lats]
    d1 = [[declination(coef, epoch0, la, lo, EPOCH + 1) for lo in lons] for la in lats]
    rate = [[d1[i][j] - d0[i][j] for j in range(len(lons))] for i in range(len(lats))]
    return lats, lons, d0, rate


def bilinear(lats, lons, vals, lat, lon):
    lat = min(max(lat, lats[0]), lats[-1])
    lon = min(max(lon, lons[0]), lons[-1])
    i = min(int((lat - lats[0]) / STEP), len(lats) - 2)
    j = min(int((lon - lons[0]) / STEP), len(lons) - 2)
    ty = (lat - lats[i]) / STEP
    tx = (lon - lons[j]) / STEP
    return ((1 - ty) * ((1 - tx) * vals[i][j] + tx * vals[i][j + 1])
            + ty * ((1 - tx) * vals[i + 1][j] + tx * vals[i + 1][j + 1]))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    model, epoch0, coef = read_cof(sys.argv[1])
    lats, lons, d0, rate = grid(coef, epoch0)
    if "--check" in sys.argv:
        year = 2026.79
        for name, la, lo, noaa in NOAA_CHECKS:
            print(f"{name:16s} model {declination(coef, epoch0, la, lo, year):6.2f}  NOAA {noaa:6.1f}")
        worst = 0.0
        for k in range(2000):
            la = LAT0 + (LAT1 - LAT0) * ((k * 0.6180339) % 1)
            lo = LON0 + (LON1 - LON0) * ((k * 0.7548777) % 1)
            g = bilinear(lats, lons, d0, la, lo)
            worst = max(worst, abs(g - declination(coef, epoch0, la, lo, EPOCH)))
        flat = [v for row in d0 for v in row]
        print(f"grid {min(flat):.2f} .. {max(flat):.2f} deg; worst interpolation error {worst:.3f} deg")
        print(f"change per year {min(v for r in rate for v in r):+.3f} .. {max(v for r in rate for v in r):+.3f}")
        return
    q = lambda v: f"{v:.2f}"
    print('"""Magnetic variation (declination) over the SST area, for the measure tool.')
    print("")
    print(f"Generated by tools/build_declination.py from {model} (NOAA NCEI / BGS, public domain).")
    print(f"DECL[i][j] is the declination in degrees (east positive, so west is negative) at")
    print(f"LATS[i], LONS[j] on {EPOCH}; RATE[i][j] is its change per year. Don't edit by hand.")
    print('"""')
    print("")
    print(f'MODEL = "{model}"')
    print(f"EPOCH = {EPOCH}")
    print(f"STEP = {STEP}")
    print(f"LATS = {lats}")
    print(f"LONS = {lons}")
    print("DECL = [")
    for row in d0:
        print("    [" + ", ".join(q(v) for v in row) + "],")
    print("]")
    print("RATE = [")
    for row in rate:
        print("    [" + ", ".join(f"{v:.3f}" for v in row) + "],")
    print("]")


if __name__ == "__main__":
    main()
