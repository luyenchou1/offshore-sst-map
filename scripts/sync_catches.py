#!/usr/bin/env python3
"""Publish a clean, anonymized GotOne catch CSV for the catch map.

Two input modes, one cleaning path:
  * Firestore (default): pull straight from the live DB with a service account.
  * Local CSV (--from-csv): clean a raw export you already pulled (the same
    `catches-*.csv` / `species-*.csv` files from the Firebase console).

Either way it writes the exact schema data/catches.py reads:
    catch_time,lat,lon,species_id,fish_length,water_temperature,moon_fraction
and can POST the result to the live app's /api/catches/upload (hot-reload).

The cleaning is identical in both modes (one `_clean_catch`): drop skunks /
missing-coord / out-of-range rows, strip PII (user_id, caught_by, details,
weight, wind, state), round lat/lon to ~1 km. It is **scope-agnostic** — every
valid-coordinate catch is kept (nationwide), so geography stays a runtime filter.

Usage
-----
    # Clean a local raw export (no credentials needed):
    python scripts/sync_catches.py --from-csv "/path/catches-XXXX.csv" \
        --species-csv "/path/species-XXXX.csv" --out data

    # Or pull live from Firestore:
    pip install google-cloud-firestore requests
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json
    python scripts/sync_catches.py --out data [--upload]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone

# Mirror the loader's privacy/quality rules so the published file is already clean.
SKUNK_SPECIES_ID = "1ZIZ6YritSABYbsTg1EJ"
COORD_DECIMALS = 2  # ~1 km privacy floor for the PUBLIC view
# Internal/research view keeps exact lat/lon. Toggled by --full-precision.
FULL_PRECISION = False
CATCHES_HEADER = [
    "catch_time", "lat", "lon", "species_id",
    "fish_length", "water_temperature", "moon_fraction",
]


def _iso(ts) -> str | None:
    """Normalize a Firestore timestamp / string to ISO-8601 with a trailing Z."""
    if ts is None or ts == "":
        return None
    if isinstance(ts, str):
        return ts
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return str(ts)  # DatetimeWithNanoseconds quacks like datetime


def _num(v):
    """Coerce to float, or '' (blank). Firestore/CSV may store numbers as str."""
    if v is None or v == "":
        return ""
    try:
        return float(v)
    except (TypeError, ValueError):
        return ""


def _clean_catch(d: dict):
    """Raw catch dict -> cleaned/anonymized row, or None to drop it.

    Works for both Firestore docs and csv.DictReader rows.
    """
    sid = d.get("species_id")
    if not sid or sid == SKUNK_SPECIES_ID:
        return None
    ct = _iso(d.get("catch_time"))
    if not ct:
        return None
    try:
        lat = float(d.get("latitude"))
        lon = float(d.get("longitude"))
    except (TypeError, ValueError):
        return None
    # Drop clearly invalid coordinates (test rows, 0/0, out of range).
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return [
        ct,
        lat if FULL_PRECISION else round(lat, COORD_DECIMALS),  # exact, or ~1 km privacy snap
        lon if FULL_PRECISION else round(lon, COORD_DECIMALS),
        sid,
        _num(d.get("fish_length")),
        _num(d.get("water_temperature")),
        _num(d.get("moon_fraction")),
        # NOTE: user_id, caught_by, details, weight, wind, state are
        # intentionally NOT written — PII / identifying fields.
    ]


def _write_species(rows, out_dir: str) -> int:
    path = os.path.join(out_dir, "gotone_species.csv")
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["__id__", "name", "phonetic_names"])
        for sid, name, phon in rows:
            w.writerow([sid, name, phon])
            n += 1
    print(f"[sync] wrote {n} species -> {path}")
    return n


def _write_catches(records, out_dir: str) -> int:
    path = os.path.join(out_dir, "gotone_catches.csv")
    kept = skipped = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CATCHES_HEADER)
        for d in records:
            row = _clean_catch(d)
            if row is None:
                skipped += 1
                continue
            w.writerow(row)
            kept += 1
    print(f"[sync] wrote {kept} catches ({skipped} skipped) -> {path}")
    return kept


# ---- Firestore source -----------------------------------------------------

def _firestore_client(service_account: str | None):
    try:
        from google.cloud import firestore
    except ImportError:
        sys.exit("Missing dependency: pip install google-cloud-firestore")
    if service_account:
        return firestore.Client.from_service_account_json(service_account)
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        sys.exit("Set GOOGLE_APPLICATION_CREDENTIALS or pass --service-account")
    return firestore.Client()


def from_firestore(db, out_dir: str, catches_coll="catches", species_coll="species") -> int:
    def _species_rows():
        for doc in db.collection(species_coll).stream():
            d = doc.to_dict() or {}
            yield doc.id, d.get("name", ""), d.get("phonetic_names", "")
    _write_species(_species_rows(), out_dir)
    records = (doc.to_dict() or {} for doc in db.collection(catches_coll).stream())
    return _write_catches(records, out_dir)


# ---- Local CSV source -----------------------------------------------------

def from_csv(catches_csv: str, species_csv: str | None, out_dir: str) -> int:
    if species_csv:
        with open(species_csv, newline="") as f:
            rows = [(r["__id__"], r.get("name", ""), r.get("phonetic_names", ""))
                    for r in csv.DictReader(f)]
        _write_species(rows, out_dir)
    with open(catches_csv, newline="") as f:
        return _write_catches(list(csv.DictReader(f)), out_dir)


# ---- Upload to the live app ----------------------------------------------

def upload(out_dir: str):
    import requests

    base = os.environ.get("SST_UPLOAD_URL", "").rstrip("/")
    token = os.environ.get("CATCHES_UPLOAD_TOKEN", "")
    if not base or not token:
        sys.exit("Set SST_UPLOAD_URL and CATCHES_UPLOAD_TOKEN to --upload")
    for fname in ("gotone_species.csv", "gotone_catches.csv"):
        with open(os.path.join(out_dir, fname), "rb") as f:
            body = f.read()
        r = requests.post(
            f"{base}/api/catches/upload",
            data=body,
            headers={"X-Upload-Token": token, "X-Catches-Filename": fname},
            timeout=60,
        )
        print(f"[sync] upload {fname}: {r.status_code} {r.text[:200]}")
        r.raise_for_status()


def main():
    ap = argparse.ArgumentParser(description="Sync/clean GotOne catches.")
    ap.add_argument("--out", default="data", help="output dir for the CSVs")
    ap.add_argument("--from-csv", help="clean a local raw catches CSV instead of Firestore")
    ap.add_argument("--species-csv", help="raw species CSV (with --from-csv)")
    ap.add_argument("--service-account", help="path to a service-account JSON")
    ap.add_argument("--catches-collection", default="catches",
                    help="Firestore collection name for catches (default: catches)")
    ap.add_argument("--species-collection", default="species",
                    help="Firestore collection name for species (default: species)")
    ap.add_argument("--upload", action="store_true", help="POST to /api/catches/upload")
    ap.add_argument("--full-precision", action="store_true",
                    help="keep exact lat/lon (internal view); default rounds to ~1 km for privacy")
    ap.add_argument("--min-catches", type=int, default=1000,
                    help="refuse to upload if fewer than this many catches were produced "
                         "(guards against a bad/empty Firestore pull wiping the live map)")
    args = ap.parse_args()

    global FULL_PRECISION
    FULL_PRECISION = args.full_precision
    os.makedirs(args.out, exist_ok=True)
    if args.from_csv:
        kept = from_csv(args.from_csv, args.species_csv, args.out)
    else:
        kept = from_firestore(_firestore_client(args.service_account), args.out,
                              args.catches_collection, args.species_collection)
    if args.upload:
        if kept < args.min_catches:
            sys.exit(f"[sync] ABORT: only {kept} catches produced (< --min-catches "
                     f"{args.min_catches}); NOT uploading — keeping the live data intact.")
        upload(args.out)


if __name__ == "__main__":
    main()
