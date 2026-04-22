#!/usr/bin/env python3
# Import necessary libraries
import urllib.request
import json
import math
from datetime import datetime, date, timedelta
import time
import sqlite3
import os

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
locations = {
    'Waco': {
        'lat': 31.6212, 'lon': -97.0037,
        'depth_m': 2.0,          # BSR Waco — roughly 2 m mean depth
    },
    'Palm Springs': {
        'lat': 33.8303, 'lon': -116.5453,
        'depth_m': 1.8,
    },
    'Lemoore': {
        'lat': 36.3008, 'lon': -119.7829,
        'depth_m': 2.0,
    },
    'Atlantic Park Virginia Beach': {
        'lat': 36.8529, 'lon': -75.9779,
        'depth_m': 1.8,
    },
    'Oceanside': {
        'lat': 33.1959, 'lon': -117.3795,
        'depth_m': 1.5,
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
L_VAP         = 2.45e6    # Latent heat of vaporisation ≈ 25 °C  (J kg⁻¹)

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

    # 5. Total flux and temperature change (forward Euler)
    Q_total      = Q_solar + Q_lw_net + Q_conv + Q_evap
    dt_seconds   = dt_days * 86400.0
    thermal_mass = RHO_WATER * depth_m * CP_WATER            # J m⁻² K⁻¹
    dT           = Q_total * dt_seconds / thermal_mass       # °C

    T_pool_new_C = T_pool_C + dT

    heat_fluxes = {
        'Q_solar_Wm2'  : round(Q_solar,   2),
        'Q_lw_net_Wm2' : round(Q_lw_net,  2),
        'Q_conv_Wm2'   : round(Q_conv,    2),
        'Q_evap_Wm2'   : round(Q_evap,    2),
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
DATA_DIR      = '/var/www/html/data'
FORECASTS_DIR = '/var/www/html/data/forecasts'
SITE_ROOT     = '/var/www/html'

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(FORECASTS_DIR, exist_ok=True)
conn   = sqlite3.connect(os.path.join(DATA_DIR, 'wave_pool_weather.db'))
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

# Migrate existing databases: add new columns if they are absent
for col, table in [
    ('solar_radiation', 'air_temps'),
    ('heat_fluxes',     'pool_temps'),
]:
    try:
        cursor.execute(f'ALTER TABLE {table} ADD COLUMN {col} TEXT')
    except Exception:
        pass  # column already exists

# Insert/update locations (now including depth_m)
for name, coords in locations.items():
    cursor.execute(
        'INSERT OR IGNORE INTO locations (name, lat, lon) VALUES (?, ?, ?)',
        (name, coords['lat'], coords['lon'])
    )

conn.commit()

def fetch_json_from_url(url):
    with urllib.request.urlopen(url) as response:
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
            with urllib.request.urlopen(url) as response:
                hourly_weather_json_data = json.loads(response.read().decode())
                return hourly_weather_json_data
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                print(f"Failed to retrieve data after {retries} attempts.")
                return None

# frequency this script is ran will be determined by another script that will call this one

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
            start_time = period["startTime"]
            date_obj   = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S%z").date()

            # ---- Temperature ------------------------------------------------
            # NWS can return either:
            #   dict  {"value": 22.2, "unitCode": "wmoUnit:degC"}  → Celsius
            #   int/float  72                                        → Fahrenheit
            if isinstance(period["temperature"], dict):
                temp_raw      = float(period["temperature"]["value"])
                unit_code_raw = period["temperature"].get("unitCode", "")
                # Treat as Celsius if unit says degC, otherwise Fahrenheit
                if "degC" in unit_code_raw or "cel" in unit_code_raw.lower():
                    temperature_C = temp_raw
                else:
                    temperature_C = fahrenheit_to_celsius(temp_raw)
            else:
                # Plain value → Fahrenheit (NWS text forecast default)
                temperature_C = fahrenheit_to_celsius(float(period["temperature"]))

            # ---- Humidity ---------------------------------------------------
            humidity = float(
                period["relativeHumidity"]["value"]
                if isinstance(period["relativeHumidity"], dict)
                else period["relativeHumidity"]
            )

            # ---- Wind speed → m s⁻¹ ----------------------------------------
            if isinstance(period["windSpeed"], dict):
                ws_raw = float(period["windSpeed"]["value"])
                ws_unit = period["windSpeed"].get("unitCode", "")
                if "mph" in ws_unit or "mi_i-h" in ws_unit:
                    wind_speed_ms = mph_to_ms(ws_raw)
                else:
                    # Assume km h⁻¹ (NWS SI default for windSpeed dict)
                    wind_speed_ms = kmh_to_ms(ws_raw)
            else:
                # String like "8 mph"
                wind_mph      = float(period["windSpeed"].split()[0])
                wind_speed_ms = mph_to_ms(wind_mph)

            # ---- Wind direction → degrees ------------------------------------
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

for name, coords in locations.items():
    lat       = coords['lat']
    lon       = coords['lon']
    depth_m   = coords.get('depth_m', 2.0)
    print(f"\nProcessing {name}  (lat={lat}, lon={lon}, depth={depth_m} m)...")

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
            T_pool_init_C = row[0]
            print(f"  Using stored pool temp from {check_date}: {T_pool_init_C:.2f} °C "
                  f"({celsius_to_fahrenheit(T_pool_init_C):.1f} °F)")
            break

    if T_pool_init_C is None:
        # No history: initialise to today's air temperature
        cursor.execute(
            'SELECT temp FROM air_temps WHERE location_id=? ORDER BY date DESC LIMIT 1',
            (location_id,)
        )
        row = cursor.fetchone()
        T_pool_init_C = row[0] if row else 20.0
        print(f"  No pool history found; initialising to air temp: "
              f"{T_pool_init_C:.2f} °C ({celsius_to_fahrenheit(T_pool_init_C):.1f} °F)")

    # Default solar irradiation fallback (clear-sky midlatitude estimate)
    SOLAR_FALLBACK_MJM2 = 15.0

    T_pool_C = T_pool_init_C
    for i, d in enumerate(data['date_list']):
        T_air_C      = data['average_temp_list'][i]
        RH_pct       = data['average_humidity_list'][i]
        wind_ms      = data['average_wind_list'][i]
        solar_MJm2   = solar_map.get(str(d), SOLAR_FALLBACK_MJM2)
        if solar_MJm2 is None:
            solar_MJm2 = SOLAR_FALLBACK_MJM2

        T_pool_C, fluxes = pool_thermal_balance_step(
            T_pool_C, T_air_C, RH_pct, wind_ms, solar_MJm2,
            depth_m=depth_m, dt_days=1.0
        )

        cursor.execute(
            'INSERT OR REPLACE INTO pool_temps (location_id, date, temp, heat_fluxes) '
            'VALUES (?, ?, ?, ?)',
            (location_id, str(d), T_pool_C, json.dumps(fluxes))
        )
        print(f"  {d}: T_pool = {celsius_to_fahrenheit(T_pool_C):.1f} °F | "
              f"Q_solar={fluxes['Q_solar_Wm2']:.0f} "
              f"Q_lw={fluxes['Q_lw_net_Wm2']:.0f} "
              f"Q_conv={fluxes['Q_conv_Wm2']:.0f} "
              f"Q_evap={fluxes['Q_evap_Wm2']:.0f} W/m²")

conn.commit()

# ---------------------------------------------------------------------------
# Export forecasted pool temps to text files (°F for website compatibility)
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Generate dashboard.html with real data
# ---------------------------------------------------------------------------
dashboard_location = 'Waco'

cursor.execute(
    'SELECT date, temp, humidity, wind_speed, solar_radiation '
    'FROM air_temps '
    'WHERE location_id = (SELECT id FROM locations WHERE name = ?) '
    'ORDER BY date',
    (dashboard_location,)
)
air_rows   = cursor.fetchall()
dates      = [row[0] for row in air_rows]
# Convert stored °C back to °F for the dashboard chart
air_temps_F  = [round(celsius_to_fahrenheit(row[1]), 1) for row in air_rows]
humidities   = [row[2] for row in air_rows]
wind_speeds  = [row[3] for row in air_rows]          # m s⁻¹
solar_vals   = [row[4] if row[4] is not None else 0 for row in air_rows]  # MJ/m²/day

cursor.execute(
    'SELECT date, temp FROM pool_temps '
    'WHERE location_id = (SELECT id FROM locations WHERE name = ?) '
    'ORDER BY date',
    (dashboard_location,)
)
pool_temps_F = [round(celsius_to_fahrenheit(row[1]), 1) for row in cursor.fetchall()]

# Wind-direction frequency bins for wind rose
cursor.execute(
    'SELECT wind_direction FROM air_temps '
    'WHERE location_id = (SELECT id FROM locations WHERE name = ?)',
    (dashboard_location,)
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

with open(os.path.join(SITE_ROOT, 'dashboard.html'), 'w') as f:
    f.write(dashboard_html)
conn.commit()
conn.close()

print('Script completed successfully')
