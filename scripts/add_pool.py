#!/usr/bin/env python3
"""Interactive CLI for registering a new US wave pool in pools_config.json."""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "pools_config.json"

COMPASS_HELP = (
    "0/360=N  45=NE  90=E  135=SE  180=S  225=SW  270=W  315=NW"
)

SOIL_HELP = (
    "Typical values: dry desert sand=0.35  coastal sand=0.60  "
    "moist sandy-loam=0.80  irrigated clay/silt=1.20  moist clay=1.50"
)


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def _ask(label: str, default=None, blank_ok: bool = False) -> str:
    hint = f" [{default}]" if default is not None else (" [optional, press Enter to skip]" if blank_ok else "")
    while True:
        raw = input(f"  {label}{hint}: ").strip()
        if raw:
            return raw
        if default is not None:
            return str(default)
        if blank_ok:
            return ""
        print("    ! This field is required.")


def _ask_float(label: str, default=None, lo: float = None, hi: float = None, blank_ok: bool = False) -> float | None:
    while True:
        raw = _ask(label, default=default, blank_ok=blank_ok)
        if raw == "":
            return None
        try:
            val = float(raw)
        except ValueError:
            print("    ! Please enter a number.")
            continue
        if lo is not None and val < lo:
            print(f"    ! Must be >= {lo}.")
            continue
        if hi is not None and val > hi:
            print(f"    ! Must be <= {hi}.")
            continue
        return val


def _ask_int(label: str, default: int = None, lo: int = None, hi: int = None) -> int:
    while True:
        raw = _ask(label, default=default)
        try:
            val = int(raw)
        except ValueError:
            print("    ! Please enter a whole number.")
            continue
        if lo is not None and val < lo:
            print(f"    ! Must be >= {lo}.")
            continue
        if hi is not None and val > hi:
            print(f"    ! Must be <= {hi}.")
            continue
        return val


def _ask_state() -> str:
    valid = {
        "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
        "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
        "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
        "TX","UT","VT","VA","WA","WV","WI","WY","DC",
    }
    while True:
        raw = _ask("State abbreviation (e.g. TX, CA)").upper()
        if raw in valid:
            return raw
        print(f"    ! '{raw}' is not a recognized US state abbreviation.")


# ---------------------------------------------------------------------------
# NWS validation
# ---------------------------------------------------------------------------

def _probe_nws(lat: float, lon: float) -> bool:
    """Return True if NWS /points covers this lat/lon."""
    url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
    req = urllib.request.Request(url, headers={"User-Agent": "WavePoolWeather/add_pool.py"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        print(f"    ! NWS returned HTTP {exc.code} — check manually if needed.")
        return True  # don't block on transient NWS errors
    except Exception:
        print("    ! Could not reach NWS API — skipping coverage check.")
        return True


# ---------------------------------------------------------------------------
# Main prompt flow
# ---------------------------------------------------------------------------

def collect_pool() -> tuple[str, dict]:
    print()
    print("=== Register New Wave Pool ===")
    print("All fields marked [optional] can be left blank.")
    print()

    # --- Identity ---
    print("-- Pool Identity --")
    key = _ask("Internal key (used for filenames, e.g. 'Waco', 'Palm Springs')")
    display_name = _ask("Display name (shown on site)", default=key)
    city = _ask("City")
    state = _ask_state()
    description = _ask("Short description (1-2 sentences, shown on site)")

    # --- Location ---
    print()
    print("-- Location --")
    lat = _ask_float("Latitude  (decimal degrees, positive=N)", lo=-90.0, hi=90.0)
    lon = _ask_float("Longitude (decimal degrees, negative=W for continental US)", lo=-180.0, hi=180.0)

    print("  Checking NWS API coverage...", end=" ", flush=True)
    if _probe_nws(lat, lon):
        print("OK")
    else:
        print()
        print("  WARNING: NWS API does not cover this location.")
        print("  The pipeline uses the National Weather Service (NWS) for forecast data.")
        print("  Only continental US, Alaska, Hawaii, and some territories are supported.")
        cont = _ask("Continue anyway? (yes/no)", default="no")
        if cont.lower() not in ("yes", "y"):
            print("Aborted.")
            sys.exit(0)

    # --- Thermal model ---
    print()
    print("-- Thermal Model Parameters --")
    print(f"  {SOIL_HELP}")
    depth_m = _ask_float("Pool mean water depth (m)", default=2.0, lo=0.1, hi=10.0)
    ground_temp_C = _ask_float("Annual mean deep-soil temperature (°C)", default=18.0, lo=-5.0, hi=40.0)
    k_soil = _ask_float("Soil thermal conductivity W/(m·K)", default=1.0, lo=0.1, hi=3.0)
    soil_depth_m = _ask_float("Effective soil column depth to undisturbed ground (m)", default=2.0, lo=0.1, hi=10.0)

    # --- Physical dimensions (optional) ---
    print()
    print("-- Physical Info (optional) --")
    pool_length_m = _ask_float("Pool length (m)", blank_ok=True)
    pool_width_m = _ask_float("Pool width (m)", blank_ok=True)
    max_wave_height_ft = _ask_float("Max wave height (ft)", blank_ok=True)
    wave_technology = _ask("Wave technology (e.g. Wavegarden Cove, Kelly Slater Wave Co., UNIT Surf)", default="", blank_ok=True) or None
    booking_url = _ask("Booking / website URL", default="", blank_ok=True) or None

    # --- Wave breaks ---
    print()
    print("-- Wave Breaks --")
    print(f"  Compass: {COMPASS_HELP}")
    num_breaks = _ask_int("Number of named break directions (e.g. 2 for Rights + Lefts)", default=2, lo=1, hi=8)

    breaks: dict = {}

    use_defaults = _ask(
        "Use standard barrel/air rating defaults? (yes/no)", default="yes"
    )
    if use_defaults.lower() in ("yes", "y"):
        breaks["_defaults"] = {
            "barrel_offset_degrees": 180,
            "barrel_tolerance_degrees": 30,
            "air_offset_degrees": 45,
            "air_excellent_half_width_degrees": 30,
        }
    else:
        barrel_offset = _ask_float("  Barrel offset degrees (wind-to-wave angle ideal for barrels)", default=180, lo=0, hi=360)
        barrel_tol = _ask_float("  Barrel tolerance half-width degrees", default=30, lo=5, hi=90)
        air_offset = _ask_float("  Air offset degrees (ideal angle for air sections)", default=45, lo=0, hi=360)
        air_hw = _ask_float("  Air excellent half-width degrees", default=30, lo=5, hi=90)
        breaks["_defaults"] = {
            "barrel_offset_degrees": barrel_offset,
            "barrel_tolerance_degrees": barrel_tol,
            "air_offset_degrees": air_offset,
            "air_excellent_half_width_degrees": air_hw,
        }

    for i in range(num_breaks):
        print(f"  Break {i + 1}:")
        break_name = _ask("    Break name (e.g. Rights, Lefts, Main Break)")
        wave_vector = _ask_float(
            f"    Wave vector direction (0-360°)", lo=0.0, hi=360.0
        )
        breaks[break_name] = {"wave_vector": wave_vector}

    entry = {
        "display_name": display_name,
        "city": city,
        "state": state,
        "description": description,
        "lat": lat,
        "lon": lon,
        "depth_m": depth_m,
        "ground_temp_C": ground_temp_C,
        "k_soil_Wm1K": k_soil,
        "soil_depth_m": soil_depth_m,
        "pool_length_m": pool_length_m,
        "pool_width_m": pool_width_m,
        "max_wave_height_ft": max_wave_height_ft,
        "wave_technology": wave_technology,
        "booking_url": booking_url,
        "breaks": breaks,
    }

    return key, entry


def main() -> None:
    key, entry = collect_pool()

    config: dict = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text())

    if key in config:
        print(f"\nWARNING: A pool with key '{key}' already exists.")
        overwrite = _ask("Overwrite it? (yes/no)", default="no")
        if overwrite.lower() not in ("yes", "y"):
            print("Aborted.")
            sys.exit(0)

    config[key] = entry
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")

    print(f"\nPool '{key}' saved to {CONFIG_PATH}")
    print("Next step: run  python scripts/pull_api_data.py  to fetch forecasts.")


if __name__ == "__main__":
    main()
