#!/usr/bin/env python3
"""
Build per-pool climatology data for the website's Climate tab.

For each pool in pools_config.json this script:
  1. Downloads a multi-decade daily weather history (air temperature, wind,
     solar radiation, sunshine, cloud cover, humidity) from Open-Meteo's
     free historical archive (ERA5 reanalysis, api docs:
     https://open-meteo.com/en/docs/historical-weather-api).
  2. Downloads hourly CAPE (convective available potential energy) for the
     same period and reduces it to a daily maximum, used as a proxy
     "lightning potential" index (there is no free historical lightning
     strike API, but NWS/SPC and academic literature commonly use CAPE as
     the standard thunderstorm-potential indicator).
  3. Re-runs the site's existing WAVE-FEST one-state thermal balance model
     (scripts/pool_physics.py) day-by-day over the whole historical record
     to reconstruct a modeled pool-water-temperature history, since no
     historical pool-temperature dataset exists.
  4. Aggregates every variable by day-of-year (± a smoothing window) across
     all baseline years into percentile bands (p10 / p50 / p90), and writes
     one compact JSON file per pool to data/climatology_<Pool_Name>.json for
     the website to render without ever calling a live API.

Usage:
    python scripts/build_climatology.py
    WAVE_POOL_CLIMATOLOGY_START=1994 WAVE_POOL_CLIMATOLOGY_END=2023 \\
        python scripts/build_climatology.py

Intended to run on a low-frequency cron (e.g. monthly) since 30-year
climate normals barely shift day to day — decoupled from the daily
forecast cron that runs pull_api_data.py.
"""
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from pool_physics import (  # noqa: E402
    celsius_to_fahrenheit,
    kmh_to_ms,
    bottom_u_value_Wm2K,
    pool_thermal_balance_step,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASELINE_START_YEAR = int(os.getenv('WAVE_POOL_CLIMATOLOGY_START', '1994'))
BASELINE_END_YEAR   = int(os.getenv('WAVE_POOL_CLIMATOLOGY_END', '2023'))
SMOOTHING_WINDOW_DAYS = int(os.getenv('WAVE_POOL_CLIMATOLOGY_WINDOW_DAYS', '7'))

# CAPE (J/kg) is heavily zero-inflated, so a plain percentile band is not a
# very intuitive "lightning potential" signal. We additionally report the
# fraction of days that exceed a moderate-instability threshold, which reads
# like the forecast side's existing thunder-probability percentage.
LIGHTNING_CAPE_THRESHOLD_JKG = float(os.getenv('WAVE_POOL_LIGHTNING_CAPE_THRESHOLD', '1000'))

HTTP_TIMEOUT_SECONDS = 60
HTTP_RETRIES = 5
HTTP_RETRY_DELAY_SECONDS = 10.0
INTER_POOL_DELAY_SECONDS = 5.0
DAILY_ARCHIVE_URL = 'https://archive-api.open-meteo.com/v1/archive'
# CAPE is not populated on the ERA5-based archive-api endpoint (it silently
# returns nulls for the whole record). It IS available, but only from
# 2016-01-01 onward, via the higher-resolution historical-forecast-api
# (ECMWF IFS reanalysis-adjacent archive), so the lightning-potential proxy
# uses a shorter recent window than the 30-year temp/wind/solar baseline.
CAPE_ARCHIVE_URL = 'https://historical-forecast-api.open-meteo.com/v1/forecast'
CAPE_EARLIEST_YEAR = 2016
DAILY_VARS = (
    'temperature_2m_max,temperature_2m_min,temperature_2m_mean,'
    'relative_humidity_2m_mean,wind_speed_10m_mean,wind_gusts_10m_max,'
    'wind_direction_10m_dominant,shortwave_radiation_sum,sunshine_duration,'
    'cloud_cover_mean'
)

BASE_OUTPUT_DIR = os.getenv('WAVE_POOL_BASE_OUTPUT_DIR', str(SCRIPTS_DIR.parent)).strip()
DATA_DIR = Path(BASE_OUTPUT_DIR) / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = SCRIPTS_DIR / 'pools_config.json'
with open(CONFIG_PATH) as f:
    POOL_REGISTRY: dict = json.load(f)

# Ordered (month, day) calendar for a leap year, used as the day-of-year axis
# so Feb 29 has its own slot and the ± window can wrap around Dec 31 -> Jan 1.
_REF_LEAP_YEAR = 2000
_CALENDAR_DAYS = []
_d = date(_REF_LEAP_YEAR, 1, 1)
while _d.year == _REF_LEAP_YEAR:
    _CALENDAR_DAYS.append((_d.month, _d.day))
    _d += timedelta(days=1)
DOY_COUNT = len(_CALENDAR_DAYS)  # 366
DOY_INDEX = {md: i for i, md in enumerate(_CALENDAR_DAYS)}


def fetch_json(url):
    request = urllib.request.Request(
        url,
        headers={'User-Agent': 'WavePoolWeather-Climatology/1.0 (+https://wavepoolweather.com)'},
    )
    last_error = None
    for attempt in range(HTTP_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            last_error = exc
            # 429 = rate limited by Open-Meteo's free tier; back off and retry.
            wait = HTTP_RETRY_DELAY_SECONDS * (attempt + 1) if exc.code == 429 else HTTP_RETRY_DELAY_SECONDS
            print(f'  HTTP {exc.code} from Open-Meteo (attempt {attempt + 1}/{HTTP_RETRIES}); '
                  f'retrying in {wait:.0f}s...')
            time.sleep(wait)
        except Exception as exc:
            last_error = exc
            print(f'  Request failed (attempt {attempt + 1}/{HTTP_RETRIES}): {exc}')
            time.sleep(HTTP_RETRY_DELAY_SECONDS)
    raise last_error


def fetch_daily_history(lat, lon, start_date, end_date):
    """Daily air/wind/solar/sunshine history from Open-Meteo's ERA5 archive."""
    url = (
        f'{DAILY_ARCHIVE_URL}?latitude={lat}&longitude={lon}'
        f'&start_date={start_date}&end_date={end_date}'
        f'&daily={DAILY_VARS}&timezone=UTC'
    )
    data = fetch_json(url)
    return data['daily']


def fetch_daily_max_cape(lat, lon, start_date, end_date):
    """Hourly CAPE reduced to a daily maximum — our lightning-potential proxy.

    Only available from CAPE_EARLIEST_YEAR onward on Open-Meteo's
    historical-forecast-api; caller is expected to clamp the requested range.
    """
    url = (
        f'{CAPE_ARCHIVE_URL}?latitude={lat}&longitude={lon}'
        f'&start_date={start_date}&end_date={end_date}'
        f'&hourly=cape&timezone=UTC'
    )
    data = fetch_json(url)
    hourly = data['hourly']
    daily_max = {}
    for t, cape in zip(hourly['time'], hourly['cape']):
        if cape is None:
            continue
        day = t.split('T')[0]
        daily_max[day] = max(daily_max.get(day, 0.0), float(cape))
    return daily_max


def percentile(sorted_values, pct):
    """Linear-interpolation percentile (pct in [0, 100]) of a pre-sorted list."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if lo == hi:
        return sorted_values[lo]
    frac = k - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def band(values):
    """p10/p50(median)/p90/mean summary used for every climatology variable."""
    finite = sorted(v for v in values if v is not None)
    if not finite:
        return None
    return {
        'p10': round(percentile(finite, 10), 2),
        'p50': round(percentile(finite, 50), 2),
        'p90': round(percentile(finite, 90), 2),
        'mean': round(statistics.fmean(finite), 2),
        'n': len(finite),
    }


def dominant_direction(direction_samples):
    """Most common 45°-bin wind direction across samples (for a wind rose)."""
    if not direction_samples:
        return None
    bin_labels = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    counts = [0] * 8
    for deg in direction_samples:
        idx = int(((deg % 360) + 22.5) // 45) % 8
        counts[idx] += 1
    return bin_labels[counts.index(max(counts))]


def build_pool_climatology(pool_name, pool_cfg):
    lat, lon = pool_cfg['lat'], pool_cfg['lon']
    depth_m = pool_cfg.get('depth_m', 2.0)
    ground_temp_C = pool_cfg.get('ground_temp_C', 18.0)
    k_soil = pool_cfg.get('k_soil_Wm1K', 1.0)
    soil_depth = pool_cfg.get('soil_depth_m', 2.0)
    bottom_u = bottom_u_value_Wm2K(k_soil, soil_depth)

    start_date = f'{BASELINE_START_YEAR}-01-01'
    end_date = f'{BASELINE_END_YEAR}-12-31'
    print(f'[{pool_name}] fetching {start_date}..{end_date} daily history from Open-Meteo...')
    daily = fetch_daily_history(lat, lon, start_date, end_date)

    cape_start_year = max(BASELINE_START_YEAR, CAPE_EARLIEST_YEAR)
    cape_start_date = f'{cape_start_year}-01-01'
    print(f'[{pool_name}] fetching hourly CAPE ({cape_start_date}..{end_date}) '
          f'for lightning-potential proxy...')
    cape_by_date = fetch_daily_max_cape(lat, lon, cape_start_date, end_date)

    dates = daily['time']
    # Seed the thermal model with the first day's mean air temperature and
    # run WAVE-FEST forward day-by-day across the whole historical record.
    T_pool_C = daily['temperature_2m_mean'][0]
    samples = {key: [[] for _ in range(DOY_COUNT)] for key in (
        'pool_temp_F', 'air_temp_F', 'air_temp_max_F', 'air_temp_min_F',
        'wind_speed_mph', 'wind_gust_mph', 'sunshine_hours',
        'solar_MJm2', 'cloud_cover_pct',
    )}
    cape_samples = [[] for _ in range(DOY_COUNT)]  # only populated where CAPE data exists
    direction_samples = [[] for _ in range(DOY_COUNT)]

    for i, date_str in enumerate(dates):
        y, m, d = (int(p) for p in date_str.split('-'))
        doy_idx = DOY_INDEX[(m, d)]

        T_air_C = daily['temperature_2m_mean'][i]
        RH_pct = daily['relative_humidity_2m_mean'][i]
        wind_speed_ms = kmh_to_ms(daily['wind_speed_10m_mean'][i] or 0.0)
        solar_MJm2 = daily['shortwave_radiation_sum'][i] or 0.0

        T_pool_C, _ = pool_thermal_balance_step(
            T_pool_C, T_air_C, RH_pct, wind_speed_ms, solar_MJm2,
            depth_m=depth_m, ground_temp_C=ground_temp_C,
            bottom_u_Wm2K=bottom_u,
        )

        samples['pool_temp_F'][doy_idx].append(celsius_to_fahrenheit(T_pool_C))
        samples['air_temp_F'][doy_idx].append(celsius_to_fahrenheit(T_air_C))
        samples['air_temp_max_F'][doy_idx].append(
            celsius_to_fahrenheit(daily['temperature_2m_max'][i]))
        samples['air_temp_min_F'][doy_idx].append(
            celsius_to_fahrenheit(daily['temperature_2m_min'][i]))
        samples['wind_speed_mph'][doy_idx].append(daily['wind_speed_10m_mean'][i] * 0.621371)
        samples['wind_gust_mph'][doy_idx].append(
            (daily['wind_gusts_10m_max'][i] or 0.0) * 0.621371)
        samples['sunshine_hours'][doy_idx].append((daily['sunshine_duration'][i] or 0.0) / 3600.0)
        samples['solar_MJm2'][doy_idx].append(solar_MJm2)
        samples['cloud_cover_pct'][doy_idx].append(daily['cloud_cover_mean'][i])
        if date_str in cape_by_date:
            cape_samples[doy_idx].append(cape_by_date[date_str])

        wind_dir = daily['wind_direction_10m_dominant'][i]
        if wind_dir is not None:
            direction_samples[doy_idx].append(wind_dir)

    # Aggregate with a ± SMOOTHING_WINDOW_DAYS circular window per day-of-year
    # so each day's climatology is backed by (years × (2·window+1)) samples
    # instead of just one sample per year.
    doy_records = []
    for idx, (month, day) in enumerate(_CALENDAR_DAYS):
        window_idxs = [
            (idx + offset) % DOY_COUNT
            for offset in range(-SMOOTHING_WINDOW_DAYS, SMOOTHING_WINDOW_DAYS + 1)
        ]
        record = {
            'month': month,
            'day': day,
            'label': f'{_REF_LEAP_YEAR}-{month:02d}-{day:02d}',
        }
        for key, per_day_lists in samples.items():
            pooled = [v for w in window_idxs for v in per_day_lists[w]]
            record[key] = band(pooled)
        pooled_dirs = [v for w in window_idxs for v in direction_samples[w]]
        record['wind_dir_dominant'] = dominant_direction(pooled_dirs)

        pooled_cape = [v for w in window_idxs for v in cape_samples[w]]
        record['cape_Jkg'] = band(pooled_cape)
        record['lightning_potential_pct'] = (
            round(100.0 * sum(1 for v in pooled_cape if v >= LIGHTNING_CAPE_THRESHOLD_JKG)
                  / len(pooled_cape), 1)
            if pooled_cape else None
        )
        doy_records.append(record)

    return {
        'pool': pool_name,
        'display_name': pool_cfg.get('display_name', pool_name),
        'lat': lat,
        'lon': lon,
        'baseline_start_year': BASELINE_START_YEAR,
        'baseline_end_year': BASELINE_END_YEAR,
        'lightning_baseline_start_year': cape_start_year,
        'smoothing_window_days': SMOOTHING_WINDOW_DAYS,
        'source': 'Open-Meteo Historical Weather API (ERA5 reanalysis); '
                   'pool temperature reconstructed with the WAVE-FEST thermal model; '
                   'lightning potential proxied by daily-max CAPE '
                   f'(>= {LIGHTNING_CAPE_THRESHOLD_JKG:.0f} J/kg threshold).',
        'generated_at_utc': f'{date.today().isoformat()}',
        'days': doy_records,
    }


def main():
    pool_names = list(POOL_REGISTRY.items())
    for i, (pool_name, pool_cfg) in enumerate(pool_names):
        if i > 0:
            time.sleep(INTER_POOL_DELAY_SECONDS)  # be polite to Open-Meteo's free tier
        try:
            climatology = build_pool_climatology(pool_name, pool_cfg)
        except Exception as exc:
            print(f'[{pool_name}] FAILED to build climatology: {exc}')
            continue

        safe_name = pool_name.replace(' ', '_')
        out_path = DATA_DIR / f'climatology_{safe_name}.json'
        with open(out_path, 'w') as f:
            json.dump(climatology, f, separators=(',', ':'))
        print(f'[{pool_name}] wrote {out_path}')


if __name__ == '__main__':
    main()
