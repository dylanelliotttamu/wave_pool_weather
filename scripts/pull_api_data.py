#!/usr/bin/env python3
# Import necessary libraries
import urllib.request
import json
import math
import random
import re
from datetime import datetime, date, timedelta
import time
import sqlite3
import os

print("Imported libraries successfully.")

todays_date = date.today()
todays_time = datetime.now().time()

print("Starting data pull from NWS API...")
print(f"Today's date: {todays_date}")
print(f"Current time: {todays_time}")

# ---------------------------------------------------------------------------
# Locations dictionary
#   depth_m: effective thermal-mass depth used in the pool energy balance.
#   For a well-mixed pool this is the mean water depth (~1.5–2.5 m).
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Bottom heat-loss calibration notes
# ---------------------------------------------------------------------------
# U_bottom is NOT set directly — it is derived from the thermal resistance
# chain in the main loop:
#
#   R_concrete = L_CONCRETE_M / K_CONCRETE   (1 ft reinforced concrete slab)
#   R_soil     = soil_depth_m / k_soil_Wm1K  (soil column to undisturbed earth)
#   U_bottom   = 1 / (R_concrete + R_soil)    [W m⁻² K⁻¹]
#
# Soil conductivity typical values:
#   Dry desert sand  : 0.30–0.40 W m⁻¹ K⁻¹
#   Moist sandy loam : 0.60–0.90 W m⁻¹ K⁻¹
#   Moist clay-loam  : 1.20–1.60 W m⁻¹ K⁻¹
#
# soil_depth_m = effective column depth to undisturbed ground temperature.
# ground_temp_C = approximate annual mean deep-soil temperature.
# ---------------------------------------------------------------------------
locations = {
    'Waco': {
        'lat': 31.6212, 'lon': -97.0037,
        'depth_m': 2.0,           # BSR Waco — roughly 2 m mean depth
        'ground_temp_C': 20.0,    # Annual-mean deep-soil temp, central TX (~68 °F)
        'k_soil_Wm1K': 1.5,       # Moist Texas clay-loam
        'soil_depth_m': 2.0,      # Effective column to undisturbed ground
    },
    'Palm Springs': {
        'lat': 33.8303, 'lon': -116.5453,
        'depth_m': 1.8,
        'ground_temp_C': 23.0,    # Coachella Valley — warm desert (~73 °F)
        'k_soil_Wm1K': 0.35,      # Dry desert sand/gravel (low conductivity)
        'soil_depth_m': 2.0,
    },
    'Lemoore': {
        'lat': 36.3008, 'lon': -119.7829,
        'depth_m': 2.0,
        'ground_temp_C': 18.0,    # San Joaquin Valley (~64 °F)
        'k_soil_Wm1K': 1.2,       # Irrigated valley clay/silt-loam
        'soil_depth_m': 2.0,
    },
    'Atlantic Park Virginia Beach': {
        'lat': 36.8529, 'lon': -75.9779,
        'depth_m': 1.8,
        'ground_temp_C': 16.0,    # Coastal Virginia (~61 °F)
        'k_soil_Wm1K': 0.8,       # Moist coastal sand
        'soil_depth_m': 2.0,
    },
    'Oceanside': {
        'lat': 33.1959, 'lon': -117.3795,
        'depth_m': 1.5,
        'ground_temp_C': 18.0,    # Southern CA coast (~64 °F)
        'k_soil_Wm1K': 0.6,       # Dry/moist coastal sand
        'soil_depth_m': 2.0,
    },
}

# ---------------------------------------------------------------------------
# Physical constants used by the thermal balance model
# ---------------------------------------------------------------------------
SIGMA_SB      = 5.67e-8   # Stefan-Boltzmann constant  (W m⁻² K⁻⁴)
EPSILON_WATER = 0.97      # Long-wave emissivity of water surface  (–)
ALBEDO_WATER  = 0.06      # Short-wave albedo of open water  (–)
RHO_WATER     = 1000.0    # Density of water  (kg m⁻³)
CP_WATER      = 4186.0    # Specific heat of water  (J kg⁻¹ K⁻¹)
L_VAP         = 2.45e6    # Latent heat of vaporization ≈ 25 °C  (J kg⁻¹)
K_CONCRETE    = 1.4       # Thermal conductivity of concrete  (W m⁻¹ K⁻¹)
L_CONCRETE_M  = 0.3048    # Pool slab thickness — 1 ft  (m)
MIN_POOL_TEMP_C = 4.0     # Reject obviously bad stored seeds below ~39 F
MAX_POOL_TEMP_C = 40.0    # Reject obviously bad stored seeds above 104 F

# ---------------------------------------------------------------------------
# Unit-conversion helpers
# ---------------------------------------------------------------------------
def fahrenheit_to_celsius(T_F):
    return (T_F - 32.0) * 5.0 / 9.0

def celsius_to_fahrenheit(T_C):
    return T_C * 9.0 / 5.0 + 32.0

def mph_to_ms(speed_mph):
    return speed_mph * 0.44704

def kmh_to_ms(speed_kmh):
    return speed_kmh * 0.27778

def is_plausible_pool_temp_C(temp_C):
    return math.isfinite(temp_C) and MIN_POOL_TEMP_C <= temp_C <= MAX_POOL_TEMP_C

def clamp(value, low, high):
    return max(low, min(high, value))

def percentile(values, pct):
    """Linear-interpolated percentile for a non-empty numeric list."""
    if not values:
        raise ValueError('percentile() requires non-empty values')
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return sorted_vals[lo]
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac

def mean_and_std(values):
    if not values:
        return 0.0, 0.0
    mu = sum(values) / len(values)
    if len(values) == 1:
        return mu, 0.0
    var = sum((v - mu) ** 2 for v in values) / len(values)
    return mu, math.sqrt(var)

def mc_sigma_schedule(day_index):
    """
    Day-dependent uncertainty assumptions (1-sigma).
    day_index: 0-based forecast horizon index.
    """
    return {
        # ~1.5 F at day 1, +0.5 F per extra day (converted to C)
        'temp_C': fahrenheit_to_celsius(1.5 + 0.5 * day_index),
        # ~2 mph at day 1, +0.5 mph per extra day (converted to m/s)
        'wind_ms': mph_to_ms(2.0 + 0.5 * day_index),
        # RH uncertainty widens with horizon
        'rh_pct': 8.0 + 2.0 * day_index,
        # solar uncertainty as fraction of daily value
        'solar_frac': min(0.35, 0.10 + 0.02 * day_index),
        # modest structural uncertainty for ground temperature
        'ground_temp_C': 0.5,
    }

# ---------------------------------------------------------------------------
# Atmospheric / thermodynamic helpers
# ---------------------------------------------------------------------------
def saturation_vapor_pressure_kPa(T_C):
    """Saturation vapour pressure (kPa) via the Tetens formula."""
    return 0.6108 * math.exp(17.27 * T_C / (T_C + 237.3))

def sky_emissivity(T_air_C, RH_pct):
    """
    Effective sky emissivity after Brutsaert (1975).
    Uses near-surface vapour pressure to approximate atmospheric
    longwave emission from a clear-ish sky.

    Note: the Brutsaert formula requires e_a in hPa (millibars) and T in K.
    """
    T_air_K = T_air_C + 273.15
    RH = max(0.0, min(1.0, RH_pct / 100.0))
    e_a_hPa = saturation_vapor_pressure_kPa(T_air_C) * RH * 10.0  # 1 kPa = 10 hPa
    # Brutsaert: ε_sky = 1.24 * (e_a [hPa] / T [K])^(1/7)
    return min(1.0, 1.24 * (e_a_hPa / T_air_K) ** (1.0 / 7.0))

# ---------------------------------------------------------------------------
# One-state (well-mixed) pool thermal balance model
# ---------------------------------------------------------------------------
def pool_thermal_balance_step(
        T_pool_C,
        T_air_C,
        RH_pct,
        wind_speed_ms,
        solar_MJm2_day,
        depth_m=2.0,
        ground_temp_C=18.0,
        bottom_u_Wm2K=1.0,
        include_ground=True,
        dt_days=1.0):
    """
    Advance pool temperature by one time step using a single-layer (one-state)
    energy balance.  All fluxes are in W m⁻²; positive values heat the pool.

    Energy components
    -----------------
    Q_solar   : absorbed shortwave solar radiation
                  = (1 − α) × G_s
    Q_lw_net  : net longwave exchange (sky emission minus pool emission)
                  = ε_sky σ T_air⁴ − ε_water σ T_pool⁴
    Q_conv    : sensible (convective) heat from air
                  = h_c (T_air − T_pool),   h_c = 5.7 + 3.8 U  [W m⁻² K⁻¹]
                  (McAdams 1954 bulk-transfer correlation)
    Q_evap    : latent heat loss due to evaporation (always ≤ 0 when
                  pool is warmer than dew point)
                  = −L × f(U) × (e_s(T_pool) − e_air)
                  open-water mass-transfer: f(U) ≈ 2.7e-3 + 1.35e-3 U
                  [kg m⁻² s⁻¹ kPa⁻¹]  (after Penman 1948 / Monteith)
    Q_ground  : conductive heat exchange through pool bottom to ground
                  = −U_bottom × (T_pool − T_ground)

    Forward Euler integration:
      ΔT = Q_total × Δt / (ρ d c_p)

    Parameters
    ----------
    T_pool_C      : current pool temperature (°C)
    T_air_C       : daily-mean air temperature (°C)
    RH_pct        : relative humidity (%)
    wind_speed_ms : wind speed (m s⁻¹)
    solar_MJm2_day: total daily solar irradiation (MJ m⁻² day⁻¹)
    depth_m       : effective depth / thermal-mass depth of the pool (m)
    ground_temp_C : effective ground temperature beneath pool (°C)
    bottom_u_Wm2K : effective bottom U-value (W m⁻² K⁻¹)
    dt_days       : integration time step (days)

    Returns
    -------
    T_pool_new_C  : updated pool temperature (°C)
    heat_fluxes   : dict with individual flux components (W m⁻²) and dT (°C)
    """
    T_pool_K = T_pool_C + 273.15
    T_air_K  = T_air_C  + 273.15
    RH       = max(0.0, min(1.0, RH_pct / 100.0))

    # Convert daily solar sum to mean irradiance over the day
    solar_Wm2 = solar_MJm2_day * 1.0e6 / 86400.0  # W m⁻²

    # 1. Absorbed solar radiation
    Q_solar = (1.0 - ALBEDO_WATER) * solar_Wm2

    # 2. Net longwave radiation
    eps_sky  = sky_emissivity(T_air_C, RH_pct)
    Q_lw_in  = eps_sky * SIGMA_SB * T_air_K ** 4
    Q_lw_out = EPSILON_WATER * SIGMA_SB * T_pool_K ** 4
    Q_lw_net = Q_lw_in - Q_lw_out

    # 3. Sensible (convective) heat flux
    h_c    = 5.7 + 3.8 * wind_speed_ms          # W m⁻² K⁻¹
    Q_conv = h_c * (T_air_C - T_pool_C)

    # 4. Evaporative heat flux (bulk atmospheric transfer)
    #    E [kg/m²/s] = ρ_a × C_E × U × (0.622/P_atm) × (e_s_pool − e_a)
    #    Q_evap [W/m²] = −L_VAP × E
    #    EVAP_COEFF = ρ_a × C_E × L_VAP × 0.622 / P_atm
    #               = 1.2 × 1.3e-3 × 2.45e6 × 0.622 / 101.325 ≈ 23.5
    EVAP_COEFF = 1.2 * 1.3e-3 * L_VAP * 0.622 / 101.325  # W m⁻² (m s⁻¹)⁻¹ kPa⁻¹
    e_s_pool = saturation_vapor_pressure_kPa(T_pool_C)      # kPa
    e_a      = saturation_vapor_pressure_kPa(T_air_C) * RH  # kPa
    Q_evap   = -EVAP_COEFF * wind_speed_ms * (e_s_pool - e_a)  # W m⁻²

    # 5. Conductive exchange at pool bottom (positive warms pool)
    Q_ground = -bottom_u_Wm2K * (T_pool_C - ground_temp_C) if include_ground else 0.0

    # 6. Total flux and temperature change (forward Euler)
    Q_total      = Q_solar + Q_lw_net + Q_conv + Q_evap + Q_ground
    dt_seconds   = dt_days * 86400.0
    thermal_mass = RHO_WATER * depth_m * CP_WATER            # J m⁻² K⁻¹
    dT           = Q_total * dt_seconds / thermal_mass       # °C

    T_pool_new_C = T_pool_C + dT

    heat_fluxes = {
        'Q_solar_Wm2'  : round(Q_solar,   2),
        'Q_lw_net_Wm2' : round(Q_lw_net,  2),
        'Q_conv_Wm2'   : round(Q_conv,    2),
        'Q_evap_Wm2'   : round(Q_evap,    2),
        'Q_ground_Wm2' : round(Q_ground,  2),
        'Q_total_Wm2'  : round(Q_total,   2),
        'dT_C'         : round(dT,         4),
    }
    return T_pool_new_C, heat_fluxes

# ---------------------------------------------------------------------------
# Wind-direction text → degrees lookup
# ---------------------------------------------------------------------------
direction_map = {
    'N': 0, 'NNE': 22.5, 'NE': 45, 'ENE': 67.5,
    'E': 90, 'ESE': 112.5, 'SE': 135, 'SSE': 157.5,
    'S': 180, 'SSW': 202.5, 'SW': 225, 'WSW': 247.5,
    'W': 270, 'WNW': 292.5, 'NW': 315, 'NNW': 337.5
}

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------
# Output mode switch:
#   Live mode (default): writes under /var/www/html
#   Test mode: set WAVE_POOL_TEST_MODE=1 for terminal-only execution
#              (no file writes, no on-disk DB writes)
TEST_MODE = os.getenv('WAVE_POOL_TEST_MODE', '0').strip().lower() in ('1', 'true', 'yes', 'on')
WRITE_FILES = not TEST_MODE

if WRITE_FILES:
    BASE_OUTPUT_DIR = '/var/www/html'
    DATA_DIR      = os.path.join(BASE_OUTPUT_DIR, 'data')
    FORECASTS_DIR = os.path.join(DATA_DIR, 'forecasts')
    SITE_ROOT     = BASE_OUTPUT_DIR
    DB_PATH       = os.path.join(DATA_DIR, 'wave_pool_weather.db')

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(FORECASTS_DIR, exist_ok=True)
else:
    BASE_OUTPUT_DIR = os.getcwd()
    DATA_DIR      = '(disabled in test mode)'
    FORECASTS_DIR = '(disabled in test mode)'
    SITE_ROOT     = '(disabled in test mode)'
    DB_PATH       = ':memory:'

print(
    f"Output mode: {'TEST' if TEST_MODE else 'LIVE'} | "
    f"base={BASE_OUTPUT_DIR} | db={DB_PATH} | write_files={WRITE_FILES}"
)

conn   = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS locations (
    id   INTEGER PRIMARY KEY,
    name TEXT UNIQUE,
    lat  REAL,
    lon  REAL
)''')

# air_temps: temperatures stored in °C, wind speed in m s⁻¹,
#            solar_radiation in MJ m⁻² day⁻¹
cursor.execute('''CREATE TABLE IF NOT EXISTS air_temps (
    location_id      INTEGER,
    date             TEXT,
    temp             REAL,
    humidity         REAL,
    wind_speed       REAL,
    wind_direction   REAL,
    solar_radiation  REAL,
    FOREIGN KEY(location_id) REFERENCES locations(id),
    UNIQUE(location_id, date)
)''')

# pool_temps: temperature stored in °C; heat_fluxes is a JSON string
cursor.execute('''CREATE TABLE IF NOT EXISTS pool_temps (
    location_id  INTEGER,
    date         TEXT,
    temp         REAL,
    heat_fluxes  TEXT,
    FOREIGN KEY(location_id) REFERENCES locations(id),
    UNIQUE(location_id, date)
)''')

# pool_temp_ci: 95% interval and distribution stats for pool temp (°C)
cursor.execute('''CREATE TABLE IF NOT EXISTS pool_temp_ci (
    location_id  INTEGER,
    date         TEXT,
    p025_C       REAL,
    p500_C       REAL,
    p975_C       REAL,
    mean_C       REAL,
    std_C        REAL,
    n_samples    INTEGER,
    FOREIGN KEY(location_id) REFERENCES locations(id),
    UNIQUE(location_id, date)
)''')

# Migrate existing databases: add new columns if they are absent
for col, table, col_type in [
    ('solar_radiation', 'air_temps', 'REAL'),
    ('heat_fluxes',     'pool_temps', 'TEXT'),
]:
    try:
        cursor.execute(f'ALTER TABLE {table} ADD COLUMN {col} {col_type}')
    except Exception:
        pass  # column already exists

# Insert/update locations (now including depth_m)
for name, coords in locations.items():
    cursor.execute(
        'INSERT OR IGNORE INTO locations (name, lat, lon) VALUES (?, ?, ?)',
        (name, coords['lat'], coords['lon'])
    )

conn.commit()

def fetch_json_from_url(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = json.loads(response.read().decode())
        return data
    
def get_hourly_forecast_url(input_data):
    try:
        hourly_forecast_url = input_data["properties"]["forecastHourly"]
        return hourly_forecast_url
    except KeyError as e:
        print(f"KeyError: {e}")
        return None

# Given lat, lon of a wave pool (US), retrieve the hourly forecast link
def retrieve_the_hourly_url_given_only_lat_and_lon(input_lat, input_lon):
    data_1 = fetch_json_from_url(f'https://api.weather.gov/points/{input_lat},{input_lon}')
    hourly_forecast_url = get_hourly_forecast_url(data_1)
    print('hourly_forecast_url = ', hourly_forecast_url)    
    return hourly_forecast_url

# Use url and return data (returns 7-days of hourly data)
def request_data(url, retries=3, delay=2):
    hourly_weather_json_data = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                hourly_weather_json_data = json.loads(response.read().decode())
                return hourly_weather_json_data
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                print(f"Failed to retrieve data after {retries} attempts.")
                return None

# cron tab calls this script to be ran -> frequency is determiend there (once per day) now.

# ---------------------------------------------------------------------------
# Open-Meteo solar radiation retrieval  (no API key required)
# ---------------------------------------------------------------------------
def fetch_solar_radiation_open_meteo(lat, lon, date_list):
    """
    Retrieve daily total shortwave solar radiation from the Open-Meteo API.

    Returns a dict mapping 'YYYY-MM-DD' strings to solar irradiation values
    in MJ m⁻² day⁻¹.  Returns an empty dict on any failure so the caller can
    fall back gracefully.
    """
    if not date_list:
        return {}
    start_date = str(min(date_list))
    end_date   = str(max(date_list))
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=shortwave_radiation_sum"
        f"&timezone=auto"
        f"&start_date={start_date}&end_date={end_date}"
    )
    try:
        response_data = fetch_json_from_url(url)
        times  = response_data['daily']['time']                      # list of 'YYYY-MM-DD'
        solar  = response_data['daily']['shortwave_radiation_sum']   # MJ m⁻² day⁻¹
        result = {t: s for t, s in zip(times, solar) if s is not None}
        print(f"  Fetched solar radiation for {len(result)} days from Open-Meteo")
        return result
    except Exception as exc:
        print(f"  Warning: could not fetch solar radiation from Open-Meteo: {exc}")
        return {}

# Main function to pull data from NWS api
def pull_api_temp_data_main(lat, lon, timestep_in_hours):
    try:
        # pull data from NWS api
        if timestep_in_hours == 1:
            hourly_forecast_url = retrieve_the_hourly_url_given_only_lat_and_lon(lat, lon)
            if hourly_forecast_url:
                hourly_weather_json_data = request_data(hourly_forecast_url)
                if hourly_weather_json_data:
                    return parse_weather_data(hourly_weather_json_data)
        else:
            print('Invalid timestep')
    except Exception as e:
        print(f"Error pulling data from NWS api: {e}")
    return None

def parse_weather_data(inputhourlyjsonweather_data):
    """
    Parse the NWS hourly forecast JSON.

    All temperatures are normalised to **°C** and wind speeds to **m s⁻¹**
    before aggregation, regardless of the unit format returned by the API.
    """
    print(f"Data keys: {inputhourlyjsonweather_data.keys()}")
    try:
        # Access periods
        periods = inputhourlyjsonweather_data["properties"]["periods"]
        print(f"Periods type: {type(periods)}, len: {len(periods) if hasattr(periods, '__len__') else 'N/A'}")

        if not isinstance(periods, list):
            print(f"Unexpected periods type: {type(periods)}, content: {periods}")
            return None

        daily_data = {}

        for period in periods:
            try:
                start_time = period["startTime"]
                date_obj   = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S%z").date()

                # ---- Temperature --------------------------------------------
                # NWS can return either:
                #   dict  {"value": 22.2, "unitCode": "wmoUnit:degC"}  → Celsius
                #   int/float  72                                        → Fahrenheit
                if isinstance(period["temperature"], dict):
                    temp_value = period["temperature"].get("value")
                    if temp_value is None:
                        raise ValueError("missing temperature value")
                    temp_raw      = float(temp_value)
                    unit_code_raw = period["temperature"].get("unitCode", "")
                    # Treat as Celsius if unit says degC, otherwise Fahrenheit
                    if "degC" in unit_code_raw or "cel" in unit_code_raw.lower():
                        temperature_C = temp_raw
                    else:
                        temperature_C = fahrenheit_to_celsius(temp_raw)
                else:
                    # Plain value → Fahrenheit (NWS text forecast default)
                    temperature_C = fahrenheit_to_celsius(float(period["temperature"]))

                # ---- Humidity -----------------------------------------------
                humidity_value = (
                    period["relativeHumidity"]["value"]
                    if isinstance(period["relativeHumidity"], dict)
                    else period["relativeHumidity"]
                )
                humidity = float(humidity_value) if humidity_value is not None else 0.0

                # ---- Wind speed → m s⁻¹ ------------------------------------
                wind_speed = period["windSpeed"]
                if isinstance(wind_speed, dict):
                    ws_value = wind_speed.get("value")
                    ws_raw = float(ws_value) if ws_value is not None else 0.0
                    ws_unit = wind_speed.get("unitCode", "")
                    if "mph" in ws_unit or "mi_i-h" in ws_unit:
                        wind_speed_ms = mph_to_ms(ws_raw)
                    elif "m_s" in ws_unit:
                        wind_speed_ms = ws_raw
                    else:
                        # Assume km h⁻¹ (NWS SI default for windSpeed dict)
                        wind_speed_ms = kmh_to_ms(ws_raw)
                else:
                    # Strings may be "8 mph", "5 to 10 mph", or "Calm".
                    wind_speed_text = str(wind_speed).strip().lower()
                    if wind_speed_text == "calm":
                        wind_speed_ms = 0.0
                    else:
                        samples = [float(v) for v in re.findall(r"\d+(?:\.\d+)?", wind_speed_text)]
                        wind_mag = sum(samples) / len(samples) if samples else 0.0
                        wind_speed_ms = kmh_to_ms(wind_mag) if "km" in wind_speed_text else mph_to_ms(wind_mag)

                # ---- Wind direction → degrees ------------------------------
                wind_direction_str = (
                    period["windDirection"]["value"]
                    if isinstance(period["windDirection"], dict)
                    else period["windDirection"]
                )
                wind_direction = direction_map.get(str(wind_direction_str), 0)

                if date_obj not in daily_data:
                    daily_data[date_obj] = {
                        "temperatures":    [],
                        "humidities":      [],
                        "wind_speeds":     [],
                        "wind_directions": [],
                    }

                daily_data[date_obj]["temperatures"].append(temperature_C)
                daily_data[date_obj]["humidities"].append(humidity)
                daily_data[date_obj]["wind_speeds"].append(wind_speed_ms)
                daily_data[date_obj]["wind_directions"].append(wind_direction)
            except Exception as period_exc:
                print(
                    f"Warning: skipping malformed period at "
                    f"{period.get('startTime', 'unknown')}: {period_exc}"
                )
                continue

        date_list                = []
        average_temp_list        = []   # °C
        average_humidity_list    = []   # %
        average_wind_list        = []   # m s⁻¹
        average_wind_direction_list = []

        for date_obj in sorted(daily_data.keys()):
            day = daily_data[date_obj]
            # Daily mean temperature: average of hourly max and min
            avg_temperature = (max(day["temperatures"]) + min(day["temperatures"])) / 2.0
            avg_humidity    = sum(day["humidities"])      / len(day["humidities"])
            avg_wind        = sum(day["wind_speeds"])     / len(day["wind_speeds"])
            avg_wind_dir    = sum(day["wind_directions"]) / len(day["wind_directions"])

            date_list.append(date_obj)
            average_temp_list.append(avg_temperature)
            average_humidity_list.append(avg_humidity)
            average_wind_list.append(avg_wind)
            average_wind_direction_list.append(avg_wind_dir)

        return {
            'date_list':                  date_list,
            'average_temp_list':          average_temp_list,          # °C
            'average_humidity_list':      average_humidity_list,      # %
            'average_wind_list':          average_wind_list,          # m s⁻¹
            'average_wind_direction_list': average_wind_direction_list,
        }
    except Exception as exc:
        print(f"Error parsing data: {exc}")
        return None


'''

Begin main part of script and call functions

'''

# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
current_date = date.today()
all_comparison_rows = {}

for name, coords in locations.items():
    lat            = coords['lat']
    lon            = coords['lon']
    depth_m        = coords.get('depth_m', 2.0)
    ground_temp_C  = coords.get('ground_temp_C', 18.0)
    k_soil         = coords.get('k_soil_Wm1K', 1.0)
    soil_depth     = coords.get('soil_depth_m', 2.0)
    # Resistance-network derivation: concrete slab + soil column in series
    R_concrete    = L_CONCRETE_M / K_CONCRETE             # m²K/W
    R_soil        = soil_depth / k_soil                   # m²K/W
    bottom_u_Wm2K = 1.0 / (R_concrete + R_soil)          # W m⁻² K⁻¹
    print(
        f"\nProcessing {name}  (lat={lat}, lon={lon}, depth={depth_m} m, "
        f"T_ground={ground_temp_C} °C, "
        f"R_slab={R_concrete:.3f} + R_soil={R_soil:.3f} = {R_concrete+R_soil:.3f} m²K/W, "
        f"U_bottom={bottom_u_Wm2K:.3f} W/m²/K)..."
    )

    cursor.execute('SELECT id FROM locations WHERE name=?', (name,))
    location_id = cursor.fetchone()[0]

    data = pull_api_temp_data_main(lat, lon, 1)
    if not data:
        print(f"  Failed to get NWS data for {name}")
        continue

    # ------------------------------------------------------------------ #
    # Fetch solar radiation from Open-Meteo                               #
    # ------------------------------------------------------------------ #
    solar_map = fetch_solar_radiation_open_meteo(lat, lon, data['date_list'])

    # ------------------------------------------------------------------ #
    # Store forecasted air temperatures (°C), wind (m s⁻¹), solar        #
    # ------------------------------------------------------------------ #
    for i, d in enumerate(data['date_list']):
        solar_val = solar_map.get(str(d), None)
        cursor.execute(
            'INSERT OR REPLACE INTO air_temps '
            '(location_id, date, temp, humidity, wind_speed, wind_direction, solar_radiation) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (
                location_id,
                str(d),
                data['average_temp_list'][i],
                data['average_humidity_list'][i],
                data['average_wind_list'][i],
                data['average_wind_direction_list'][i],
                solar_val,
            )
        )

    # ------------------------------------------------------------------ #
    # Thermal balance: find the most recent stored pool temperature as    #
    # the initial condition, then step forward through the forecast.      #
    # ------------------------------------------------------------------ #
    # Look for the latest pool temp already in the DB (up to 7 days ago)
    T_pool_init_C = None
    for days_back in range(1, 8):
        check_date = current_date - timedelta(days=days_back)
        cursor.execute(
            'SELECT temp FROM pool_temps WHERE location_id=? AND date=?',
            (location_id, str(check_date))
        )
        row = cursor.fetchone()
        if row is not None:
            candidate_pool_temp_C = float(row[0])
            if is_plausible_pool_temp_C(candidate_pool_temp_C):
                T_pool_init_C = candidate_pool_temp_C
                print(f"  Using stored pool temp from {check_date}: {T_pool_init_C:.2f} °C "
                      f"({celsius_to_fahrenheit(T_pool_init_C):.1f} °F)")
                break
            print(
                f"  WARNING: ignoring implausible stored pool temp from {check_date}: "
                f"{candidate_pool_temp_C:.2f} °C "
                f"({celsius_to_fahrenheit(candidate_pool_temp_C):.1f} °F)"
            )

    if T_pool_init_C is None:
        # No plausible pool history: initialise from today's air temp.
        # If today's row is unexpectedly missing, use the most recent air temp.
        cursor.execute(
            'SELECT temp FROM air_temps WHERE location_id=? AND date=?',
            (location_id, str(current_date))
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                'SELECT temp FROM air_temps WHERE location_id=? ORDER BY date DESC LIMIT 1',
                (location_id,)
            )
            row = cursor.fetchone()

        T_pool_init_C = float(row[0]) if row else 20.0
        print(f"  No plausible pool history found; initialising to today's air temp: "
              f"{T_pool_init_C:.2f} °C ({celsius_to_fahrenheit(T_pool_init_C):.1f} °F)")

    print(
        f"  DEBUG init pool temp type={type(T_pool_init_C).__name__}, "
        f"value={T_pool_init_C!r}"
    )

    # Default solar irradiation fallback (clear-sky midlatitude estimate)
    SOLAR_FALLBACK_MJM2 = 15.0

    T_pool_air_C = T_pool_init_C   # tracks simple air-only baseline
    T_pool_old_C = T_pool_init_C   # tracks original model (no ground flux)
    T_pool_new_C = T_pool_init_C   # tracks new physics model (with ground flux)
    mc_prev_samples = []
    MC_SAMPLES = max(50, int(os.getenv('WAVE_POOL_MC_SAMPLES', '200')))
    for _ in range(MC_SAMPLES):
        # Start near initial condition with a small spread.
        init_sample = random.gauss(T_pool_init_C, fahrenheit_to_celsius(0.5))
        mc_prev_samples.append(clamp(init_sample, MIN_POOL_TEMP_C, MAX_POOL_TEMP_C))
    comparison_rows = []           # for the per-location comparison file

    for i, d in enumerate(data['date_list']):
        T_air_C      = data['average_temp_list'][i]
        RH_pct       = data['average_humidity_list'][i]
        wind_ms      = data['average_wind_list'][i]
        solar_MJm2   = solar_map.get(str(d), SOLAR_FALLBACK_MJM2)
        if solar_MJm2 is None:
            solar_MJm2 = SOLAR_FALLBACK_MJM2

        # --- simple baseline (pool temp equals daily mean air temp) ---
        T_pool_air_C = T_air_C

        # --- original model (no bottom conduction) ---
        T_pool_old_C, _ = pool_thermal_balance_step(
            T_pool_old_C, T_air_C, RH_pct, wind_ms, solar_MJm2,
            depth_m=depth_m,
            ground_temp_C=ground_temp_C,
            bottom_u_Wm2K=bottom_u_Wm2K,
            include_ground=False,
            dt_days=1.0
        )

        # --- new physics model (with bottom conduction) ---
        T_pool_new_C, fluxes = pool_thermal_balance_step(
            T_pool_new_C, T_air_C, RH_pct, wind_ms, solar_MJm2,
            depth_m=depth_m,
            ground_temp_C=ground_temp_C,
            bottom_u_Wm2K=bottom_u_Wm2K,
            include_ground=True,
            dt_days=1.0
        )

        # --- Monte Carlo uncertainty propagation for 95% interval ---
        sigmas = mc_sigma_schedule(i)
        mc_day_samples = []
        for prev_sample in mc_prev_samples:
            T_air_s = random.gauss(T_air_C, sigmas['temp_C'])
            RH_s = clamp(random.gauss(RH_pct, sigmas['rh_pct']), 0.0, 100.0)
            wind_s = max(0.0, random.gauss(wind_ms, sigmas['wind_ms']))
            solar_sigma = abs(solar_MJm2) * sigmas['solar_frac']
            solar_s = max(0.0, random.gauss(solar_MJm2, solar_sigma))
            ground_temp_s = random.gauss(ground_temp_C, sigmas['ground_temp_C'])

            mc_temp_C, _ = pool_thermal_balance_step(
                prev_sample, T_air_s, RH_s, wind_s, solar_s,
                depth_m=depth_m,
                ground_temp_C=ground_temp_s,
                bottom_u_Wm2K=bottom_u_Wm2K,
                include_ground=True,
                dt_days=1.0
            )
            mc_day_samples.append(clamp(mc_temp_C, MIN_POOL_TEMP_C, MAX_POOL_TEMP_C))

        mc_prev_samples = mc_day_samples
        p025_C = percentile(mc_day_samples, 2.5)
        p500_C = percentile(mc_day_samples, 50.0)
        p975_C = percentile(mc_day_samples, 97.5)
        mc_mean_C, mc_std_C = mean_and_std(mc_day_samples)

        # DB and exported files continue to use the new physics model
        cursor.execute(
            'INSERT OR REPLACE INTO pool_temps (location_id, date, temp, heat_fluxes) '
            'VALUES (?, ?, ?, ?)',
            (location_id, str(d), T_pool_new_C, json.dumps(fluxes))
        )
        cursor.execute(
            'INSERT OR REPLACE INTO pool_temp_ci '
            '(location_id, date, p025_C, p500_C, p975_C, mean_C, std_C, n_samples) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (location_id, str(d), p025_C, p500_C, p975_C, mc_mean_C, mc_std_C, MC_SAMPLES)
        )

        air_F         = celsius_to_fahrenheit(T_pool_air_C)
        old_F         = celsius_to_fahrenheit(T_pool_old_C)
        new_F         = celsius_to_fahrenheit(T_pool_new_C)
        delta_new_old = new_F - old_F
        delta_old_air = old_F - air_F
        delta_new_air = new_F - air_F
        comparison_rows.append((
            str(d),
            round(air_F, 2),
            round(old_F, 2),
            round(new_F, 2),
            round(delta_new_old, 2),
            round(delta_old_air, 2),
            round(delta_new_air, 2),
            fluxes['Q_ground_Wm2']
        ))

        print(f"  {d}: air={air_F:.1f}°F old={old_F:.1f}°F new={new_F:.1f}°F | "
              f"Δ(new-old)={delta_new_old:+.2f}°F "
              f"Δ(old-air)={delta_old_air:+.2f}°F "
              f"Δ(new-air)={delta_new_air:+.2f}°F "
              f"CI95=[{celsius_to_fahrenheit(p025_C):.1f}, {celsius_to_fahrenheit(p975_C):.1f}]°F | "
              f"Q_solar={fluxes['Q_solar_Wm2']:.0f} "
              f"Q_lw={fluxes['Q_lw_net_Wm2']:.0f} "
              f"Q_conv={fluxes['Q_conv_Wm2']:.0f} "
              f"Q_evap={fluxes['Q_evap_Wm2']:.0f} "
              f"Q_ground={fluxes['Q_ground_Wm2']:.0f} W/m²")

    if WRITE_FILES:
        # Write per-location comparison file
        cmp_filename = os.path.join(
            FORECASTS_DIR,
            f'model_comparison_{name.replace(" ", "_")}.txt'
        )
        with open(cmp_filename, 'w') as f:
            f.write('date,T_air_only_F,T_old_F,T_new_F,delta_new_minus_old_F,delta_old_minus_air_F,delta_new_minus_air_F,Q_ground_Wm2\n')
            for row in comparison_rows:
                f.write(
                    f"{row[0]},{row[1]},{row[2]},{row[3]},{row[4]},{row[5]},{row[6]},{row[7]}\n"
                )
        print(f"  Comparison written → {cmp_filename}")
    else:
        print(f"  TEST MODE summary for {name} (first 3 rows):")
        for row in comparison_rows[:3]:
            print(
                f"    {row[0]} air={row[1]} old={row[2]} new={row[3]} "
                f"d_new_old={row[4]} d_old_air={row[5]} d_new_air={row[6]} Qg={row[7]}"
            )

    # Accumulate for the cross-location summary (keyed by location name)
    all_comparison_rows[name] = comparison_rows

conn.commit()

# ---------------------------------------------------------------------------
# Write cross-location model comparison summary
# ---------------------------------------------------------------------------
if WRITE_FILES:
    summary_path = os.path.join(FORECASTS_DIR, 'model_comparison_summary.txt')
    with open(summary_path, 'w') as f:
        f.write('location,date,T_air_only_F,T_old_F,T_new_F,delta_new_minus_old_F,delta_old_minus_air_F,delta_new_minus_air_F,Q_ground_Wm2\n')
        for loc_name, rows in all_comparison_rows.items():
            for row in rows:
                f.write(
                    f"{loc_name},{row[0]},{row[1]},{row[2]},{row[3]},{row[4]},{row[5]},{row[6]},{row[7]}\n"
                )
    print(f"\nCross-location summary written → {summary_path}")

    # -----------------------------------------------------------------------
    # Export forecasted pool temps to text files (°F for website compatibility)
    # -----------------------------------------------------------------------
    for name in locations.keys():
        cursor.execute(
            'SELECT date, temp FROM pool_temps '
            'WHERE location_id = (SELECT id FROM locations WHERE name = ?) AND date >= ? '
            'ORDER BY date',
            (name, str(current_date))
        )
        rows = cursor.fetchall()
        filename = os.path.join(
            FORECASTS_DIR, f'forecasted_pool_temps_{name.replace(" ", "_")}.txt'
        )
        with open(filename, 'w') as f:
            for row in rows:
                temp_F = celsius_to_fahrenheit(row[1])
                f.write(f"{row[0]},{temp_F:.2f}\n")

        # Write extended weather forecast file (pool temp + air met data)
        weather_name = name.replace(" ", "_")
        cursor.execute(
            'SELECT pt.date, pt.temp, ci.p025_C, ci.p975_C, '
            'at.temp, at.wind_speed, at.wind_direction, at.humidity '
            'FROM pool_temps pt '
            'JOIN air_temps at ON pt.location_id = at.location_id AND pt.date = at.date '
            'LEFT JOIN pool_temp_ci ci ON pt.location_id = ci.location_id AND pt.date = ci.date '
            'WHERE pt.location_id = (SELECT id FROM locations WHERE name = ?) AND pt.date >= ? '
            'ORDER BY pt.date',
            (name, str(current_date))
        )
        weather_rows = cursor.fetchall()
        weather_filename = os.path.join(
            FORECASTS_DIR, f'forecasted_weather_{weather_name}.txt'
        )
        with open(weather_filename, 'w') as wf:
            for wrow in weather_rows:
                pool_temp_F = celsius_to_fahrenheit(wrow[1])
                ci_low_F    = celsius_to_fahrenheit(wrow[2]) if wrow[2] is not None else pool_temp_F
                ci_high_F   = celsius_to_fahrenheit(wrow[3]) if wrow[3] is not None else pool_temp_F
                air_temp_F  = celsius_to_fahrenheit(wrow[4]) if wrow[4] is not None else 0.0
                wind_mph    = (wrow[5] / 0.44704) if wrow[5] is not None else 0.0
                wind_dir    = wrow[6] if wrow[6] is not None else 0.0
                humidity    = wrow[7] if wrow[7] is not None else 0.0
                wf.write(
                    f"{wrow[0]},{pool_temp_F:.2f},{ci_low_F:.2f},{ci_high_F:.2f},{air_temp_F:.2f},"
                    f"{wind_mph:.1f},{wind_dir:.1f},{humidity:.1f}\n"
                )

    # Keep the legacy Waco file for backward compatibility
    cursor.execute(
        'SELECT date, temp FROM pool_temps '
        'WHERE location_id = (SELECT id FROM locations WHERE name = "Waco") AND date >= ? '
        'ORDER BY date',
        (str(current_date),)
    )
    with open(os.path.join(FORECASTS_DIR, 'forecasted_pool_temps.txt'), 'w') as f:
        for row in cursor.fetchall():
            temp_F = celsius_to_fahrenheit(row[1])
            f.write(f"{row[0]},{temp_F:.2f}\n")
else:
    print("\nTEST MODE: skipped all file exports (comparison txt + forecast txt).")

# ---------------------------------------------------------------------------
# Generate dashboard.html with real data
# ---------------------------------------------------------------------------
dashboard_location = 'Waco'
dashboard_history_days = max(0, int(os.getenv('WAVE_POOL_DASHBOARD_HISTORY_DAYS', '3')))
dashboard_default_min_date = str(current_date - timedelta(days=dashboard_history_days))
dashboard_min_date = os.getenv('WAVE_POOL_DASHBOARD_MIN_DATE', dashboard_default_min_date).strip() or dashboard_default_min_date

cursor.execute(
    'SELECT date, temp, humidity, wind_speed, solar_radiation '
    'FROM air_temps '
    'WHERE location_id = (SELECT id FROM locations WHERE name = ?) AND date >= ? '
    'ORDER BY date',
    (dashboard_location, dashboard_min_date)
)
air_rows   = cursor.fetchall()
dates      = [row[0] for row in air_rows]
# Convert stored °C back to °F for the dashboard chart
air_temps_F  = [round(celsius_to_fahrenheit(row[1]), 1) for row in air_rows]
humidities   = [row[2] for row in air_rows]
wind_speeds  = [row[3] for row in air_rows]          # m s⁻¹
solar_vals   = []  # MJ/m²/day
for i, row in enumerate(air_rows):
    raw_solar = row[4]
    try:
        solar_val = float(raw_solar) if raw_solar is not None else 0.0
    except (TypeError, ValueError):
        print(
            f"WARNING: invalid solar_radiation at index {i}: "
            f"raw={raw_solar!r}, type={type(raw_solar).__name__}; using 0.0"
        )
        solar_val = 0.0
    solar_vals.append(solar_val)

    # Print a small sample for debugging values/types pulled from DB.
    if i < 10:
        print(
            f"DEBUG solar_vals[{i}] raw={raw_solar!r} "
            f"type={type(raw_solar).__name__} -> parsed={solar_val:.3f}"
        )

cursor.execute(
    'SELECT date, temp FROM pool_temps '
    'WHERE location_id = (SELECT id FROM locations WHERE name = ?) AND date >= ? '
    'ORDER BY date',
    (dashboard_location, dashboard_min_date)
)
pool_temps_F = [round(celsius_to_fahrenheit(row[1]), 1) for row in cursor.fetchall()]

# Wind-direction frequency bins for wind rose
cursor.execute(
    'SELECT wind_direction FROM air_temps '
    'WHERE location_id = (SELECT id FROM locations WHERE name = ?) AND date >= ?',
    (dashboard_location, dashboard_min_date)
)
wind_dirs = [row[0] for row in cursor.fetchall() if row[0] is not None]
bin_labels = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
bin_edges  = [337.5, 22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5]
bins = [0] * 8
for wd in wind_dirs:
    wd = wd % 360
    if wd >= 337.5 or wd < 22.5:
        bins[0] += 1
    elif wd < 67.5:
        bins[1] += 1
    elif wd < 112.5:
        bins[2] += 1
    elif wd < 157.5:
        bins[3] += 1
    elif wd < 202.5:
        bins[4] += 1
    elif wd < 247.5:
        bins[5] += 1
    elif wd < 292.5:
        bins[6] += 1
    else:
        bins[7] += 1

real_data_js = (
    f'const chartLocation = {json.dumps(dashboard_location)};\n'
    f'        const realData = {{\n'
    f'            dates:      {json.dumps(dates)},\n'
    f'            poolTemps:  {json.dumps(pool_temps_F)},\n'
    f'            airTemps:   {json.dumps(air_temps_F)},\n'
    f'            humidities: {json.dumps(humidities)},\n'
    f'            windSpeeds: {json.dumps([round(w, 2) for w in wind_speeds])},\n'
    f'            solarRad:   {json.dumps([round(s, 2) for s in solar_vals])},\n'
    f'            windBins:   {json.dumps(bins)}\n'
    f'        }};'
)

if WRITE_FILES:
    template_path = os.path.join(SITE_ROOT, 'dashboard.template.html')
    with open(template_path) as _tmpl:
        _raw = _tmpl.read()
    dashboard_html = (
        _raw
        .replace('__LOCATION__', dashboard_location)
        .replace('// __REAL_DATA__', real_data_js)
    )
else:
    dashboard_html = ''

if False:  # dead branch — keeps the old giant f-string out of scope
    dashboard_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Wave Pool Weather Dashboard</title>
    <link rel="icon" href="favicon.ico">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{
            font-family: 'Lato', sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f4f4f4;
        }}
        .navbar {{
            background-color: #222;
            overflow: hidden;
            width: 100%;
            display: flex;
            justify-content: center;
            position: sticky;
            top: 0;
            z-index: 1000;
        }}
        .navbar a {{
            color: #ccc;
            text-align: center;
            padding: 14px 20px;
            text-decoration: none;
            font-size: 17px;
            transition: color 0.3s ease;
        }}
        .navbar a:hover {{
            color: #8db600;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1 {{
            text-align: center;
            color: #333;
        }}
        .chart-container {{
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            margin: 20px 0;
            padding: 20px;
        }}
        .chart {{
            width: 100%;
            height: 400px;
        }}
    </style>
</head>
<body>
    <!-- Navigation Bar -->
    <div class="navbar">
        <a href="index.html">Home</a>
        <a href="pool_temp_forecasts.html">Pool Temperature Forecasts</a>
        <a href="news.html">Wave Pool News</a>
        <a href="about_contact.html">About/Contact</a>
        <a href="dashboard.html">Dashboard</a>
    </div>

    <div class="container">
        <h1>Wave Pool Weather Dashboard - {dashboard_location}</h1>

        <div class="chart-container">
            <h2>{dashboard_location} Pool Temperature Forecast</h2>
            <canvas id="poolTempChart" class="chart"></canvas>
        </div>

        <div class="chart-container">
            <h2>{dashboard_location} Air Temperature Forecast</h2>
            <canvas id="airTempChart" class="chart"></canvas>
        </div>

        <div class="chart-container">
            <h2>{dashboard_location} Humidity Forecast</h2>
            <canvas id="humidityChart" class="chart"></canvas>
        </div>

        <div class="chart-container">
            <h2>{dashboard_location} Wind Speed Forecast</h2>
            <canvas id="windChart" class="chart"></canvas>
        </div>

        <div class="chart-container">
            <h2>{dashboard_location} Solar Radiation Forecast</h2>
            <canvas id="solarChart" class="chart"></canvas>
        </div>

        <div class="chart-container">
            <h2>{dashboard_location} Wind Rose</h2>
            <canvas id="windRoseChart" class="chart"></canvas>
        </div>
    </div>

    <script>
        const chartLocation = {json.dumps(dashboard_location)};
        const realData = {{
            dates:      {json.dumps(dates)},
            poolTemps:  {json.dumps(pool_temps_F)},
            airTemps:   {json.dumps(air_temps_F)},
            humidities: {json.dumps(humidities)},
            windSpeeds: {json.dumps([round(w, 2) for w in wind_speeds])},
            solarRad:   {json.dumps([round(s, 2) for s in solar_vals])},
            windBins:   {json.dumps(bins)}
        }};

        // Pool Temp Chart
        new Chart(document.getElementById('poolTempChart'), {{
            type: 'line',
            data: {{
                labels: realData.dates,
                datasets: [{{
                    label: 'Pool Temperature (°F)',
                    data: realData.poolTemps,
                    borderColor: 'blue',
                    backgroundColor: 'rgba(0,0,255,0.05)',
                    fill: true
                }}]
            }},
            options: {{
                plugins: {{ title: {{ display: true, text: `${{chartLocation}} Pool Temperature Forecast (°F)` }} }},
                scales: {{
                    x: {{ title: {{ display: true, text: 'Date' }} }},
                    y: {{ title: {{ display: true, text: 'Pool Temperature (°F)' }} }}
                }}
            }}
        }});

        // Air Temp Chart
        new Chart(document.getElementById('airTempChart'), {{
            type: 'line',
            data: {{
                labels: realData.dates,
                datasets: [{{
                    label: 'Air Temperature (°F)',
                    data: realData.airTemps,
                    borderColor: 'red',
                    backgroundColor: 'rgba(255,0,0,0.05)',
                    fill: true
                }}]
            }},
            options: {{
                plugins: {{ title: {{ display: true, text: `${{chartLocation}} Air Temperature Forecast (°F)` }} }},
                scales: {{
                    x: {{ title: {{ display: true, text: 'Date' }} }},
                    y: {{ title: {{ display: true, text: 'Air Temperature (°F)' }} }}
                }}
            }}
        }});

        // Humidity Chart
        new Chart(document.getElementById('humidityChart'), {{
            type: 'bar',
            data: {{
                labels: realData.dates,
                datasets: [{{
                    label: 'Relative Humidity (%)',
                    data: realData.humidities,
                    backgroundColor: 'rgba(0,128,0,0.6)'
                }}]
            }},
            options: {{
                plugins: {{ title: {{ display: true, text: `${{chartLocation}} Humidity Forecast (%)` }} }},
                scales: {{
                    x: {{ title: {{ display: true, text: 'Date' }} }},
                    y: {{ title: {{ display: true, text: 'Humidity (%)' }}, min: 0, max: 100 }}
                }}
            }}
        }});

        // Wind Speed Chart
        new Chart(document.getElementById('windChart'), {{
            type: 'line',
            data: {{
                labels: realData.dates,
                datasets: [{{
                    label: 'Wind Speed (m/s)',
                    data: realData.windSpeeds,
                    borderColor: 'orange',
                    backgroundColor: 'rgba(255,165,0,0.05)',
                    fill: true
                }}]
            }},
            options: {{
                plugins: {{ title: {{ display: true, text: `${{chartLocation}} Wind Speed Forecast (m/s)` }} }},
                scales: {{
                    x: {{ title: {{ display: true, text: 'Date' }} }},
                    y: {{ title: {{ display: true, text: 'Wind Speed (m s⁻¹)' }} }}
                }}
            }}
        }});

        // Solar Radiation Chart
        new Chart(document.getElementById('solarChart'), {{
            type: 'bar',
            data: {{
                labels: realData.dates,
                datasets: [{{
                    label: 'Solar Radiation (MJ/m²/day)',
                    data: realData.solarRad,
                    backgroundColor: 'rgba(255,215,0,0.7)'
                }}]
            }},
            options: {{
                plugins: {{ title: {{ display: true, text: `${{chartLocation}} Solar Radiation Forecast (MJ m⁻² day⁻¹)` }} }},
                scales: {{
                    x: {{ title: {{ display: true, text: 'Date' }} }},
                    y: {{ title: {{ display: true, text: 'Solar Radiation (MJ m⁻² day⁻¹)' }} }}
                }}
            }}
        }});

        // Wind Rose
        new Chart(document.getElementById('windRoseChart'), {{
            type: 'polarArea',
            data: {{
                labels: ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'],
                datasets: [{{
                    label: 'Wind Direction Frequency',
                    data: realData.windBins,
                    backgroundColor: [
                        'rgba(255, 99, 132, 0.5)',
                        'rgba(54, 162, 235, 0.5)',
                        'rgba(255, 205, 86, 0.5)',
                        'rgba(75, 192, 192, 0.5)',
                        'rgba(153, 102, 255, 0.5)',
                        'rgba(255, 159, 64, 0.5)',
                        'rgba(199, 199, 199, 0.5)',
                        'rgba(83, 102, 255, 0.5)'
                    ]
                }}]
            }},
            options: {{
                plugins: {{
                    title: {{ display: true, text: `${{chartLocation}} Wind Direction Frequency` }}
                }},
                scales: {{
                    r: {{ title: {{ display: true, text: 'Frequency (count)' }} }}
                }}
            }}
        }});
    </script>
</body>
</html>'''

if WRITE_FILES:
    with open(os.path.join(SITE_ROOT, 'dashboard.html'), 'w') as f:
        f.write(dashboard_html)
else:
    print("TEST MODE: skipped dashboard.html write.")
conn.commit()
conn.close()

print('Script completed successfully')
