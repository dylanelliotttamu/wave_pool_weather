#!/usr/bin/env python3
# Import necessary libraries
import urllib.request
import json
import math
import random
from datetime import datetime, date, timedelta, timezone
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
# Bias correction configuration
# ---------------------------------------------------------------------------
# The model has a warm bias: mean Q_evap/Q_solar ≈ 0.56 (needs ~1.0 for
# equilibrium). On hot days (pool > 91°F) the ratio drops to ~0.46.
# Root cause: C_E = 1.3e-3 is an ocean-derived bulk transfer coefficient;
# enclosed wave pools likely have different fetch/turbulence characteristics.
#
# Three correction approaches run in parallel on every forecast and are
# written to the comparison file so you can review them before choosing
# one to promote to live output (ACTIVE_BIAS_CORRECTION).
#
# METHOD options:
#
#   'none'
#       Original model. No correction. Baseline only.
#
#   'evap_multiplier'  ← data points here (~1.25x needed for Waco summer)
#       Scale C_E by CE_MULTIPLIER. Physical justification: C_E = 1.3e-3 is
#       ocean-derived. Published values for enclosed ponds/lakes range from
#       0.9e-3 to 2.0e-3. 1.25x brings Waco steady-state to ~91°F at 105°F air.
#       Tune: CE_MULTIPLIER (default 1.25)
#
#   'wind_floor'
#       Enforce a minimum effective wind speed for evaporation. Physical
#       justification: natural convection drives evaporation even at zero
#       measured wind. Less impactful for Waco (hot-day wind already 2.3 m/s)
#       but may matter more for Palm Springs / Lemoore calm nights.
#       Tune: EVAP_WIND_FLOOR_MS (default 1.5 m/s)
#
#   'penman'
#       Replace bulk-transfer Q_evap with the Penman (1948) open-water
#       combination equation: adds a radiation-driven term (Δ·Rn) alongside
#       the aerodynamic term (γ·Ea). More physically complete, no extra
#       tuning parameters. Produces higher evaporation on sunny calm days
#       (exactly the problem scenario) and lower on cloudy windy days.
#
# ACTIVE_BIAS_CORRECTION is what gets written to the DB and exported.
# ---------------------------------------------------------------------------
ACTIVE_BIAS_CORRECTION = os.getenv('WAVE_POOL_BIAS_CORRECTION', 'evap_multiplier')
CE_MULTIPLIER          = float(os.getenv('WAVE_POOL_CE_MULTIPLIER', '1.25'))
EVAP_WIND_FLOOR_MS     = float(os.getenv('WAVE_POOL_EVAP_WIND_FLOOR', '1.5'))

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

def coerce_float(value, fallback=0.0, field_name='value'):
    """Best-effort conversion of API/DB values to finite float."""
    raw_value = value

    # Some legacy rows may store a single-item sequence; unwrap it.
    if isinstance(value, (list, tuple)):
        if not value:
            return fallback
        value = value[0]

    # Fast path for numeric values.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value_f = float(value)
        return value_f if math.isfinite(value_f) else fallback

    # String inputs may be numeric text or JSON-encoded payloads.
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == '':
            return fallback
        try:
            value_f = float(stripped)
            return value_f if math.isfinite(value_f) else fallback
        except ValueError:
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if parsed is not None:
                return coerce_float(parsed, fallback=fallback, field_name=field_name)

    print(
        f"  WARNING: could not parse {field_name}: raw={raw_value!r} "
        f"type={type(raw_value).__name__}; using {fallback}"
    )
    return fallback

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
# Wind rating helpers for air wind and barrel wind conditions
# ---------------------------------------------------------------------------

def calculate_angular_distance(angle1, angle2):
    """Calculate shortest distance between two angles on a 360° circle."""
    diff = abs(angle1 - angle2)
    return min(diff, 360 - diff)

def normalize_wind_direction(angle):
    """Normalize any angle to [0, 360) so 360° and 0° are treated identically."""
    return angle % 360

def is_direction_in_range(angle, range_min, range_max):
    """Return True when angle lies in an inclusive circular range."""
    angle = normalize_wind_direction(angle)
    range_min = normalize_wind_direction(range_min)
    range_max = normalize_wind_direction(range_max)

    if range_min <= range_max:
        return range_min <= angle <= range_max
    # Wrapped range, e.g. [315, 45]
    return angle >= range_min or angle <= range_max

def angular_distance_to_range(angle, range_min, range_max):
    """Shortest circular distance from an angle to an inclusive circular range."""
    if is_direction_in_range(angle, range_min, range_max):
        return 0.0
    return min(
        calculate_angular_distance(angle, range_min),
        calculate_angular_distance(angle, range_max),
    )

def build_angular_range(center_angle, half_width_degrees):
    """Build an inclusive circular range around a center angle."""
    center_angle = normalize_wind_direction(center_angle)
    return [
        normalize_wind_direction(center_angle - half_width_degrees),
        normalize_wind_direction(center_angle + half_width_degrees),
    ]

def infer_break_side(break_name, break_config):
    """Infer whether a break is left or right for derived-angle rules."""
    explicit_side = (break_config.get('break_side') or break_config.get('side') or '').strip().lower()
    if explicit_side in ('left', 'right'):
        return explicit_side

    break_name_lower = break_name.strip().lower()
    if 'left' in break_name_lower:
        return 'left'
    if 'right' in break_name_lower:
        return 'right'

    raise ValueError(f"Cannot infer break side for {break_name!r}")

def derive_break_config(break_name, break_config, pool_defaults=None):
    """Derive scoring angles from a compact break config, with explicit overrides."""
    pool_defaults = pool_defaults or {}

    wave_vector = break_config.get('wave_vector')
    if wave_vector is not None:
        wave_vector = normalize_wind_direction(float(wave_vector))

    side = infer_break_side(break_name, break_config)
    barrel_offset = float(break_config.get(
        'barrel_offset_degrees',
        pool_defaults.get('barrel_offset_degrees', 180),
    ))
    air_offset = float(break_config.get(
        'air_offset_degrees',
        pool_defaults.get('air_offset_degrees', 135),
    ))
    air_half_width = float(break_config.get(
        'air_excellent_half_width_degrees',
        pool_defaults.get('air_excellent_half_width_degrees', 30),
    ))
    barrel_tolerance = float(break_config.get(
        'barrel_tolerance_degrees',
        pool_defaults.get('barrel_tolerance_degrees', 30),
    ))

    derived_barrel_ideal = None
    derived_air_peak = None
    derived_air_range = None
    if wave_vector is not None:
        side_sign = 1 if side == 'left' else -1
        derived_barrel_ideal = normalize_wind_direction(wave_vector + barrel_offset)
        derived_air_peak = normalize_wind_direction(wave_vector + side_sign * air_offset)
        derived_air_range = build_angular_range(derived_air_peak, air_half_width)

    barrel_ideal = break_config.get('barrel_ideal_angle', derived_barrel_ideal)
    air_peak = break_config.get(
        'air_peak_angle',
        break_config.get('air_ideal_angle', derived_air_peak),
    )
    air_range = break_config.get('air_excellent_range', derived_air_range)

    if barrel_ideal is None or air_peak is None or air_range is None:
        raise ValueError(
            f"Break {break_name!r} is missing required rating geometry; "
            "provide wave_vector or explicit override angles"
        )

    air_range_min, air_range_max = air_range
    return {
        'break_side': side,
        'wave_vector': wave_vector,
        'barrel_ideal_angle': normalize_wind_direction(float(barrel_ideal)),
        'barrel_tolerance_degrees': barrel_tolerance,
        'air_peak_angle': normalize_wind_direction(float(air_peak)),
        'air_excellent_range': [
            normalize_wind_direction(float(air_range_min)),
            normalize_wind_direction(float(air_range_max)),
        ],
    }

def load_breaks_config(location="Waco"):
    """Load and derive break configuration from JSON file."""
    config_path = os.path.join(os.path.dirname(__file__), 'breaks_config.json')
    try:
        with open(config_path, 'r') as f:
            data = json.load(f)
        location_config = data.get(location, {})
        pool_defaults = location_config.get('_defaults', {})
        derived_config = {}
        for break_name, break_config in location_config.items():
            if break_name.startswith('_'):
                continue
            derived_config[break_name] = derive_break_config(
                break_name,
                break_config,
                pool_defaults,
            )
        return derived_config
    except Exception as e:
        print(f"Warning: Could not load breaks_config.json: {e}")
        return {}

def calculate_wind_ratings(wind_direction, break_name, location="Waco"):
    """
    Calculate barrel and air wind ratings for a break.

    Args:
        wind_direction: int, degrees 0-360
        break_name: str, "Rights" or "Lefts"
        location: str, default "Waco"

    Returns:
        dict {
            'barrel_score': 0-100,
            'barrel_rating': 'Epic' | 'Good' or None (not displayed if Poor),
            'air_score': 0-100,
            'air_rating': 'Epic' | 'Good' or None (not displayed if Poor),
        }
    """
    config = load_breaks_config(location)
    wind_direction = normalize_wind_direction(wind_direction)

    if break_name not in config:
        return {
            'barrel_score': 0,
            'barrel_rating': None,
            'air_score': 0,
            'air_rating': None,
        }

    break_config = config[break_name]

    # BARREL WIND SCORING
    barrel_ideal = break_config['barrel_ideal_angle']
    barrel_epic_tol = 15
    barrel_good_tol = 60
    angle_diff = calculate_angular_distance(wind_direction, barrel_ideal)

    if angle_diff <= barrel_epic_tol:
        barrel_score = 100 - (angle_diff / barrel_epic_tol * 20)  # 100 to 80
        barrel_rating = 'Epic'
    elif angle_diff <= barrel_good_tol:
        barrel_score = 80 - ((angle_diff - barrel_epic_tol) / (barrel_good_tol - barrel_epic_tol) * 40)  # 80 to 40
        barrel_rating = 'Good'
    else:
        barrel_score = max(0, 40 - ((angle_diff - barrel_good_tol) / 135 * 40))
        barrel_rating = None  # Don't display Poor

    # AIR WIND SCORING (can be disabled for testing)
    if DISABLE_AIR_WIND_VALIDATION:
        air_score = 0
        air_rating = None
    else:
        air_range_min, air_range_max = break_config['air_excellent_range']
        air_peak = break_config['air_peak_angle']

        # Check if in excellent range (supports wrapped ranges and 360° == 0°)
        if is_direction_in_range(wind_direction, air_range_min, air_range_max):
            distance_to_peak = abs(wind_direction - air_peak)
            air_score = 100 - (distance_to_peak / 90 * 30)  # 100 to 70
            air_rating = 'Epic'
        else:
            # Calculate circular distance to nearest boundary of excellent range
            distance_to_range = angular_distance_to_range(
                wind_direction, air_range_min, air_range_max
            )

            if distance_to_range <= 45:  # Fair range (±45° beyond excellent)
                air_score = 70 - (distance_to_range / 45 * 30)  # 70 to 40
                air_rating = 'Good'
            else:  # Poor (hidden in UI)
                air_score = max(0, 40 - ((distance_to_range - 45) / 135 * 40))
                air_rating = None  # Don't display Poor

    return {
        'barrel_score': round(barrel_score),
        'barrel_rating': barrel_rating,
        'air_score': round(air_score),
        'air_rating': air_rating,
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

def penman_evap_Wm2(T_pool_C, T_air_C, RH_pct, wind_ms, solar_Wm2):
    """
    Penman (1948) open-water evaporation expressed as a heat flux (W m⁻²,
    negative = cooling).

    Formula:
        E_mm_day = (Δ·Rn/λ + γ·Ea) / (Δ + γ)

    where:
        Δ  = slope of saturation vapor pressure curve at T_air  (kPa K⁻¹)
        γ  = psychrometric constant ≈ 0.067 kPa K⁻¹
        Rn = net radiation estimated from solar input and LW terms (MJ m⁻² day⁻¹)
        λ  = latent heat of vaporization = 2.45 MJ kg⁻¹
        Ea = aerodynamic evaporation = f(u) × (e_s(T_pool) − e_a)
             f(u) = 6.43 × (1 + 0.536·U)  [Monteith & Unsworth wind function,
                    mm day⁻¹ kPa⁻¹]

    Differences from bulk-transfer baseline:
      - The Δ/(Δ+γ) weight on the radiation term means sunny calm days produce
        MORE evaporation than the linear-wind bulk formula would predict —
        exactly the scenario where the model currently under-cools.
      - At T_air = 35°C: Δ ≈ 0.245, γ = 0.067, Δ/(Δ+γ) ≈ 0.78 so ~78% of
        evaporation is radiation-driven, only 22% wind-driven.
      - No additional tuning constants — all parameters are standard physics.
    """
    RH = max(0.0, min(1.0, RH_pct / 100.0))

    # Slope of saturation vapor pressure curve at air temp (kPa K⁻¹)
    e_s_air = saturation_vapor_pressure_kPa(T_air_C)
    delta = 4098.0 * e_s_air / (T_air_C + 237.3) ** 2

    gamma = 0.067  # psychrometric constant (kPa K⁻¹) at sea level

    # Net radiation: use incoming solar as proxy (W m⁻² → MJ m⁻² day⁻¹)
    # LW components cancel approximately for open water near air temp; using
    # solar only keeps this self-contained without double-counting LW.
    Rn_MJm2day = solar_Wm2 * 86400.0 / 1.0e6

    lam = 2.45  # latent heat (MJ kg⁻¹)

    # Aerodynamic term: Monteith wind function (mm day⁻¹ kPa⁻¹)
    f_u = 6.43 * (1.0 + 0.536 * wind_ms)
    e_s_pool = saturation_vapor_pressure_kPa(T_pool_C)
    e_a      = e_s_air * RH
    Ea_mm_day = f_u * (e_s_pool - e_a)  # mm day⁻¹

    # Penman combination
    E_mm_day = (delta * (Rn_MJm2day / lam) + gamma * Ea_mm_day) / (delta + gamma)

    # Convert mm day⁻¹ → W m⁻²  (1 mm water = 1 kg m⁻²; ×L_VAP / 86400)
    E_kgm2s = max(0.0, E_mm_day) / 1000.0 / 86400.0  # kg m⁻² s⁻¹
    return -(E_kgm2s * L_VAP)  # negative = cooling

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
        dt_days=1.0,
        bias_correction=None,
        ce_multiplier=None,
        evap_wind_floor_ms=None):
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
    T_pool_C = coerce_float(T_pool_C, field_name='T_pool_C')
    T_air_C = coerce_float(T_air_C, field_name='T_air_C')
    RH_pct = coerce_float(RH_pct, fallback=50.0, field_name='RH_pct')
    wind_speed_ms = max(0.0, coerce_float(wind_speed_ms, field_name='wind_speed_ms'))
    solar_MJm2_day = max(0.0, coerce_float(solar_MJm2_day, field_name='solar_MJm2_day'))

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

    # 4. Evaporative heat flux — method selected by bias_correction param
    #    Falls back to module-level ACTIVE_BIAS_CORRECTION when not specified.
    method    = bias_correction   if bias_correction   is not None else ACTIVE_BIAS_CORRECTION
    ce_mult   = ce_multiplier     if ce_multiplier     is not None else CE_MULTIPLIER
    wind_floor = evap_wind_floor_ms if evap_wind_floor_ms is not None else EVAP_WIND_FLOOR_MS

    EVAP_BASE = 1.2 * 1.3e-3 * L_VAP * 0.622 / 101.325  # ≈ 23.5 W m⁻² (m s⁻¹)⁻¹ kPa⁻¹
    e_s_pool  = saturation_vapor_pressure_kPa(T_pool_C)
    e_a       = saturation_vapor_pressure_kPa(T_air_C) * RH

    if method == 'evap_multiplier':
        # Scale C_E by ce_mult. Data suggests ~1.25x for Waco summer.
        Q_evap = -(EVAP_BASE * ce_mult) * wind_speed_ms * (e_s_pool - e_a)

    elif method == 'wind_floor':
        # Minimum effective wind for evaporation (natural convection floor).
        effective_wind = max(wind_speed_ms, wind_floor)
        Q_evap = -EVAP_BASE * effective_wind * (e_s_pool - e_a)

    elif method == 'penman':
        # Penman (1948) open-water combination equation.
        Q_evap = penman_evap_Wm2(T_pool_C, T_air_C, RH_pct, wind_speed_ms, solar_Wm2)

    else:  # 'none' — original model, no correction
        Q_evap = -EVAP_BASE * wind_speed_ms * (e_s_pool - e_a)

    # 5. Conductive exchange at pool bottom (positive warms pool)
    Q_ground = -bottom_u_Wm2K * (T_pool_C - ground_temp_C) if include_ground else 0.0

    # 6. Total flux and temperature change (forward Euler)
    Q_total      = Q_solar + Q_lw_net + Q_conv + Q_evap + Q_ground
    dt_seconds   = dt_days * 86400.0
    thermal_mass = RHO_WATER * depth_m * CP_WATER            # J m⁻² K⁻¹
    dT           = Q_total * dt_seconds / thermal_mass       # °C

    T_pool_new_C = T_pool_C + dT

    heat_fluxes = {
        'Q_solar_Wm2'      : round(Q_solar,   2),
        'Q_lw_net_Wm2'     : round(Q_lw_net,  2),
        'Q_conv_Wm2'       : round(Q_conv,    2),
        'Q_evap_Wm2'       : round(Q_evap,    2),
        'Q_ground_Wm2'     : round(Q_ground,  2),
        'Q_total_Wm2'      : round(Q_total,   2),
        'dT_C'             : round(dT,         4),
        'bias_correction'  : method,
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
# Set WAVE_POOL_FORCE_REFRESH=1 to bypass the same-day API-call cache and
# re-fetch all external data even when today's rows already exist in the DB.
FORCE_REFRESH = os.getenv('WAVE_POOL_FORCE_REFRESH', '0').strip().lower() in ('1', 'true', 'yes', 'on')
# Minimum number of forecast days (including today) that must exist in cache
# before we skip API refresh.
MIN_FORECAST_DAYS = max(1, int(os.getenv('WAVE_POOL_MIN_FORECAST_DAYS', '7')))
# Developer toggle to disable air wind validation (for testing)
# Set WAVE_POOL_DISABLE_AIR_WIND_VALIDATION=1 to hide air wind ratings from users
DISABLE_AIR_WIND_VALIDATION = os.getenv('WAVE_POOL_DISABLE_AIR_WIND_VALIDATION', '0').strip().lower() in ('1', 'true', 'yes', 'on')

if WRITE_FILES:
    BASE_OUTPUT_DIR = os.getenv('WAVE_POOL_BASE_OUTPUT_DIR', '/var/www/html').strip() or '/var/www/html'
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
#            solar_radiation in MJ m⁻² day⁻¹,
#            thunder_prob in % (0-100)
cursor.execute('''CREATE TABLE IF NOT EXISTS air_temps (
    location_id      INTEGER,
    date             TEXT,
    temp             REAL,
    humidity         REAL,
    wind_speed       REAL,
    wind_direction   REAL,
    solar_radiation  REAL,
    thunder_prob     REAL,
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
    ('solar_radiation',  'air_temps',  'REAL'),
    ('heat_fluxes',      'pool_temps', 'TEXT'),
    # Morning (midnight–9 AM) and afternoon (9 AM–3 PM) air conditions
    ('morning_temp',     'air_temps',  'REAL'),
    ('morning_humidity', 'air_temps',  'REAL'),
    ('morning_wind',     'air_temps',  'REAL'),
    ('afternoon_temp',   'air_temps',  'REAL'),
    ('afternoon_humidity','air_temps', 'REAL'),
    ('afternoon_wind',   'air_temps',  'REAL'),
    # Sub-daily pool temperature estimates
    ('morning_temp_C',   'pool_temps', 'REAL'),
    ('afternoon_temp_C', 'pool_temps', 'REAL'),
    # Thunder probability
    ('thunder_prob',     'air_temps',  'REAL'),
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

def fetch_json_from_url(url, timeout=15):
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
def request_data(url, retries=3, delay=1):
    hourly_weather_json_data = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
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

# ---------------------------------------------------------------------------
# NWS thunder probability retrieval  (no API key required)
# ---------------------------------------------------------------------------
def fetch_thunder_probability_nws(lat, lon, date_list):
    """
    Retrieve daily maximum thunder probability from the NWS API gridData endpoint.

    Returns a dict mapping date objects to daily max thunder probability (%).
    Returns an empty dict on any failure so the caller can fall back gracefully.
    """
    if not date_list:
        return {}
    try:
        # Get grid URL from points endpoint
        points_url = f'https://api.weather.gov/points/{lat},{lon}'
        points_data = fetch_json_from_url(points_url, timeout=15)
        if not points_data or 'properties' not in points_data:
            print(f"  Warning: could not fetch NWS points data for thunder probability")
            return {}

        grid_url = points_data['properties'].get('forecastGridData')
        if not grid_url:
            print(f"  Warning: no forecastGridData URL in NWS points response")
            return {}

        # Fetch grid data
        grid_data = fetch_json_from_url(grid_url, timeout=15)
        if not grid_data or 'properties' not in grid_data:
            print(f"  Warning: could not fetch NWS grid data for thunder probability")
            return {}

        thunder_data = grid_data['properties'].get('probabilityOfThunder')
        if not thunder_data or 'values' not in thunder_data:
            print(f"  Info: no probabilityOfThunder data available in NWS grid response")
            return {}

        thunder_values = thunder_data['values']

        # Parse into daily max values
        daily_thunder = {}
        for entry in thunder_values:
            if not entry or 'validTime' not in entry:
                continue

            # Parse ISO 8601 validTime (format: "2026-05-20T05:00:00+00:00/PT1H")
            time_str = entry['validTime'].split('/')[0]
            try:
                date_obj = datetime.strptime(time_str, '%Y-%m-%dT%H:%M:%S%z').date()
            except ValueError:
                # Try without timezone if the format is different
                try:
                    date_obj = datetime.strptime(time_str[:10], '%Y-%m-%d').date()
                except ValueError:
                    continue

            value = entry.get('value')
            if value is None:
                continue

            # Convert to float and take max probability for the day
            try:
                value_f = float(value)
                if date_obj not in daily_thunder:
                    daily_thunder[date_obj] = value_f
                else:
                    daily_thunder[date_obj] = max(daily_thunder[date_obj], value_f)
            except (TypeError, ValueError):
                continue

        print(f"  Fetched thunder probability for {len(daily_thunder)} days from NWS")
        return daily_thunder

    except Exception as exc:
        print(f"  Warning: could not fetch thunder probability from NWS: {exc}")
        return {}

# Main function to pull data from NWS api
def pull_api_temp_data_main(lat, lon, timestep_in_hours, location_name="Waco"):
    try:
        # pull data from NWS api
        if timestep_in_hours == 1:
            hourly_forecast_url = retrieve_the_hourly_url_given_only_lat_and_lon(lat, lon)
            if hourly_forecast_url:
                hourly_weather_json_data = request_data(hourly_forecast_url)
                if hourly_weather_json_data:
                    return parse_weather_data(hourly_weather_json_data, location_name)
        else:
            print('Invalid timestep')
    except Exception as e:
        print(f"Error pulling data from NWS api: {e}")
    return None

def parse_weather_data(inputhourlyjsonweather_data, location_name="Waco"):
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
        daytime_wind_start_hour = 7
        daytime_wind_end_hour = 19

        for period in periods:
            start_time = period["startTime"]
            period_dt  = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S%z")
            date_obj   = period_dt.date()
            hour       = period_dt.hour

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
                    "temperatures":         [],
                    "humidities":           [],
                    "wind_speeds":          [],
                    "wind_directions":      [],
                    # daytime window used for daily wind UI + wind rating inputs
                    # includes hours 7:00 through 19:00 local forecast time
                    "daytime_wind_speeds":     [],
                    "daytime_wind_directions": [],
                    # morning window: midnight–9 AM (hours 0–8 inclusive)
                    "morning_temperatures": [],
                    "morning_humidities":   [],
                    "morning_wind_speeds":  [],
                    # afternoon window: 9 AM–3 PM (hours 9–14 inclusive)
                    "afternoon_temperatures": [],
                    "afternoon_humidities":   [],
                    "afternoon_wind_speeds":  [],
                }

            daily_data[date_obj]["temperatures"].append(temperature_C)
            daily_data[date_obj]["humidities"].append(humidity)
            daily_data[date_obj]["wind_speeds"].append(wind_speed_ms)
            daily_data[date_obj]["wind_directions"].append(wind_direction)

            if daytime_wind_start_hour <= hour <= daytime_wind_end_hour:
                daily_data[date_obj]["daytime_wind_speeds"].append(wind_speed_ms)
                daily_data[date_obj]["daytime_wind_directions"].append(wind_direction)

            if hour <= 8:
                daily_data[date_obj]["morning_temperatures"].append(temperature_C)
                daily_data[date_obj]["morning_humidities"].append(humidity)
                daily_data[date_obj]["morning_wind_speeds"].append(wind_speed_ms)
            elif hour <= 14:
                daily_data[date_obj]["afternoon_temperatures"].append(temperature_C)
                daily_data[date_obj]["afternoon_humidities"].append(humidity)
                daily_data[date_obj]["afternoon_wind_speeds"].append(wind_speed_ms)

        date_list                   = []
        average_temp_list           = []   # °C
        average_humidity_list       = []   # %
        average_wind_list           = []   # m s⁻¹
        average_wind_direction_list = []
        morning_temp_list           = []   # °C, avg over midnight–8 AM
        morning_humidity_list       = []
        morning_wind_list           = []
        afternoon_temp_list         = []   # °C, avg over 9 AM–2 PM
        afternoon_humidity_list     = []
        afternoon_wind_list         = []
        rights_barrel_rating_list   = []
        rights_air_rating_list      = []
        lefts_barrel_rating_list    = []
        lefts_air_rating_list       = []

        for date_obj in sorted(daily_data.keys()):
            day = daily_data[date_obj]
            # Daily mean temperature: average of hourly max and min
            avg_temperature = (max(day["temperatures"]) + min(day["temperatures"])) / 2.0
            avg_humidity    = sum(day["humidities"])      / len(day["humidities"])

            def _mean(lst, fallback):
                return sum(lst) / len(lst) if lst else fallback

            avg_wind = _mean(
                day["daytime_wind_speeds"],
                _mean(day["wind_speeds"], 0.0)
            )
            avg_wind_dir = _mean(
                day["daytime_wind_directions"],
                _mean(day["wind_directions"], 0.0)
            )

            # Morning / afternoon means (fall back to daily mean when no data)
            m_temp  = _mean(day["morning_temperatures"], avg_temperature)
            m_hum   = _mean(day["morning_humidities"],   avg_humidity)
            m_wind  = _mean(day["morning_wind_speeds"],  avg_wind)
            a_temp  = _mean(day["afternoon_temperatures"], avg_temperature)
            a_hum   = _mean(day["afternoon_humidities"],   avg_humidity)
            a_wind  = _mean(day["afternoon_wind_speeds"],  avg_wind)

            # Calculate wind ratings for both breaks
            rights_ratings = calculate_wind_ratings(avg_wind_dir, 'Rights', location_name)
            lefts_ratings = calculate_wind_ratings(avg_wind_dir, 'Lefts', location_name)

            date_list.append(date_obj)
            average_temp_list.append(avg_temperature)
            average_humidity_list.append(avg_humidity)
            average_wind_list.append(avg_wind)
            average_wind_direction_list.append(avg_wind_dir)
            morning_temp_list.append(m_temp)
            morning_humidity_list.append(m_hum)
            morning_wind_list.append(m_wind)
            afternoon_temp_list.append(a_temp)
            afternoon_humidity_list.append(a_hum)
            afternoon_wind_list.append(a_wind)
            rights_barrel_rating_list.append(rights_ratings['barrel_rating'])
            rights_air_rating_list.append(rights_ratings['air_rating'])
            lefts_barrel_rating_list.append(lefts_ratings['barrel_rating'])
            lefts_air_rating_list.append(lefts_ratings['air_rating'])

        return {
            'date_list':                   date_list,
            'average_temp_list':           average_temp_list,           # °C
            'average_humidity_list':       average_humidity_list,       # %
            'average_wind_list':           average_wind_list,           # m s⁻¹
            'average_wind_direction_list': average_wind_direction_list,
            'morning_temp_list':           morning_temp_list,           # °C
            'morning_humidity_list':       morning_humidity_list,       # %
            'morning_wind_list':           morning_wind_list,           # m s⁻¹
            'afternoon_temp_list':         afternoon_temp_list,         # °C
            'afternoon_humidity_list':     afternoon_humidity_list,     # %
            'afternoon_wind_list':         afternoon_wind_list,         # m s⁻¹
            'rights_barrel_rating_list':   rights_barrel_rating_list,
            'rights_air_rating_list':      rights_air_rating_list,
            'lefts_barrel_rating_list':    lefts_barrel_rating_list,
            'lefts_air_rating_list':       lefts_air_rating_list,
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

    # ------------------------------------------------------------------ #
    # Decide whether to re-fetch from external APIs.                      #
    # Skip the network round-trip only when today's row exists AND the     #
    # cached forecast still covers at least MIN_FORECAST_DAYS ahead.       #
    # This avoids forecast-horizon decay where each day removes one day    #
    # from cached coverage if we never refresh.                            #
    # ------------------------------------------------------------------ #
    cursor.execute(
        'SELECT COUNT(*) FROM air_temps WHERE location_id=? AND date=?',
        (location_id, str(current_date))
    )
    todays_air_data_exists = cursor.fetchone()[0] > 0

    cursor.execute(
        'SELECT COUNT(*), MAX(date) FROM air_temps WHERE location_id=? AND date >= ?',
        (location_id, str(current_date))
    )
    cached_count, cached_max_date = cursor.fetchone()
    target_cache_max_date = str(current_date + timedelta(days=MIN_FORECAST_DAYS - 1))
    cache_has_required_horizon = (
        cached_count >= MIN_FORECAST_DAYS
        and cached_max_date is not None
        and cached_max_date >= target_cache_max_date
    )

    if todays_air_data_exists and cache_has_required_horizon and not FORCE_REFRESH:
        print(
            "  Today's air-temp data already in DB with sufficient horizon; "
            "loading from cache (skipping API)."
        )
        cursor.execute(
            'SELECT date, temp, humidity, wind_speed, wind_direction, solar_radiation, '
            'morning_temp, morning_humidity, morning_wind, '
            'afternoon_temp, afternoon_humidity, afternoon_wind '
            'FROM air_temps WHERE location_id=? AND date >= ? ORDER BY date',
            (location_id, str(current_date))
        )
        db_rows = cursor.fetchall()
        if not db_rows:
            print(f"  No cached forecast rows found for {name}; skipping.")
            continue
        avg_t_list = [coerce_float(r[1], field_name='air_temp') for r in db_rows]
        avg_h_list = [coerce_float(r[2], fallback=50.0, field_name='humidity') for r in db_rows]
        avg_w_list = [coerce_float(r[3], field_name='wind_speed') for r in db_rows]
        data = {
            'date_list':                   [datetime.strptime(r[0], '%Y-%m-%d').date() for r in db_rows],
            'average_temp_list':           avg_t_list,
            'average_humidity_list':       avg_h_list,
            'average_wind_list':           avg_w_list,
            'average_wind_direction_list': [r[4] if r[4] is not None else 0.0  for r in db_rows],
            # Fall back to daily mean when morning/afternoon columns are NULL
            'morning_temp_list':     [coerce_float(r[6],  fallback=avg_t_list[i], field_name='morning_temp')   for i, r in enumerate(db_rows)],
            'morning_humidity_list': [coerce_float(r[7],  fallback=avg_h_list[i], field_name='morning_hum')    for i, r in enumerate(db_rows)],
            'morning_wind_list':     [coerce_float(r[8],  fallback=avg_w_list[i], field_name='morning_wind')   for i, r in enumerate(db_rows)],
            'afternoon_temp_list':   [coerce_float(r[9],  fallback=avg_t_list[i], field_name='afternoon_temp') for i, r in enumerate(db_rows)],
            'afternoon_humidity_list':[coerce_float(r[10], fallback=avg_h_list[i], field_name='afternoon_hum') for i, r in enumerate(db_rows)],
            'afternoon_wind_list':   [coerce_float(r[11], fallback=avg_w_list[i], field_name='afternoon_wind') for i, r in enumerate(db_rows)],
        }
        solar_map = {
            r[0]: coerce_float(r[5], fallback=0.0, field_name='solar_radiation')
            for r in db_rows if r[5] is not None
        }
    else:
        if FORCE_REFRESH:
            print("  Force refresh enabled; fetching fresh API data.")
        elif not todays_air_data_exists:
            print("  No cached row for today; fetching fresh API data.")
        else:
            print(
                f"  Cached horizon is short ({cached_count} day(s), max={cached_max_date}); "
                f"need >= {MIN_FORECAST_DAYS} day(s) through {target_cache_max_date}. "
                "Fetching fresh API data."
            )
        data = pull_api_temp_data_main(lat, lon, 1, name)
        if not data:
            print(f"  Failed to get NWS data for {name}")
            continue

        # -------------------------------------------------------------- #
        # Fetch solar radiation from Open-Meteo                           #
        # -------------------------------------------------------------- #
        solar_map = fetch_solar_radiation_open_meteo(lat, lon, data['date_list'])

        # -------------------------------------------------------------- #
        # Fetch thunder probability from NWS                              #
        # -------------------------------------------------------------- #
        thunder_map = fetch_thunder_probability_nws(lat, lon, data['date_list'])

        # -------------------------------------------------------------- #
        # Store forecasted air temperatures (°C), wind (m s⁻¹), solar,   #
        # thunder probability                                             #
        # -------------------------------------------------------------- #
        cursor.executemany(
            'INSERT OR REPLACE INTO air_temps '
            '(location_id, date, temp, humidity, wind_speed, wind_direction, solar_radiation, '
            'morning_temp, morning_humidity, morning_wind, '
            'afternoon_temp, afternoon_humidity, afternoon_wind, thunder_prob) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                (
                    location_id,
                    str(d),
                    data['average_temp_list'][i],
                    data['average_humidity_list'][i],
                    data['average_wind_list'][i],
                    data['average_wind_direction_list'][i],
                    solar_map.get(str(d), None),
                    data['morning_temp_list'][i],
                    data['morning_humidity_list'][i],
                    data['morning_wind_list'][i],
                    data['afternoon_temp_list'][i],
                    data['afternoon_humidity_list'][i],
                    data['afternoon_wind_list'][i],
                    thunder_map.get(d, None),  # NEW: thunder probability
                )
                for i, d in enumerate(data['date_list'])
            ]
        )

    # ------------------------------------------------------------------ #
    # Thermal balance: find the most recent stored pool temperature as    #
    # the initial condition, then step forward through the forecast.      #
    # ------------------------------------------------------------------ #
    # Look for the latest pool temp already in the DB (up to 7 days ago)
    T_pool_init_C = None
    lookback_cutoff = str(current_date - timedelta(days=7))
    cursor.execute(
        'SELECT date, temp FROM pool_temps '
        'WHERE location_id=? AND date >= ? AND date < ? '
        'ORDER BY date DESC LIMIT 7',
        (location_id, lookback_cutoff, str(current_date))
    )
    for seed_row in cursor.fetchall():
        candidate_pool_temp_C = float(seed_row[1])
        if is_plausible_pool_temp_C(candidate_pool_temp_C):
            T_pool_init_C = candidate_pool_temp_C
            print(f"  Using stored pool temp from {seed_row[0]}: {T_pool_init_C:.2f} °C "
                  f"({celsius_to_fahrenheit(T_pool_init_C):.1f} °F)")
            break
        print(
            f"  WARNING: ignoring implausible stored pool temp from {seed_row[0]}: "
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

    # ---------------------------------------------------------------------------
    # Sub-daily solar allocation constants
    # ---------------------------------------------------------------------------
    # Fraction of daily solar energy falling within each window, estimated from
    # a half-sine solar curve (sunrise ~6 AM, sunset ~6 PM, 12-hour solar day):
    #   midnight → 9 AM  ≈ 14.7%  (only the 6–9 AM chunk is sunlit)
    #   9 AM → 3 PM      ≈ 70.8%  (midday peak)
    DT_MORNING_DAYS   = 9.0 / 24.0   # midnight → 9 AM (hours)
    DT_AFTERNOON_DAYS = 6.0 / 24.0   # 9 AM → 3 PM (hours)
    SOLAR_FRAC_MORNING   = 0.147
    SOLAR_FRAC_AFTERNOON = 0.708

    T_pool_air_C = T_pool_init_C   # tracks simple air-only baseline
    T_pool_old_C = T_pool_init_C   # tracks original model (no ground flux)
    T_pool_new_C = T_pool_init_C   # tracks active bias-corrected model (written to DB)
    # ---------------------------------------------------------------------------
    # Bias correction comparison tracks — all three methods run in parallel.
    # T_pool_new_C uses ACTIVE_BIAS_CORRECTION for DB output.
    # The others are comparison-only and written to the comparison file.
    # ---------------------------------------------------------------------------
    T_pool_evap_mult_C  = T_pool_init_C   # evap_multiplier  (CE_MULTIPLIER)
    T_pool_wind_floor_C = T_pool_init_C   # wind_floor       (EVAP_WIND_FLOOR_MS)
    T_pool_penman_C     = T_pool_init_C   # penman           (Penman 1948)
    mc_prev_samples = []
    MC_SAMPLES = max(50, int(os.getenv('WAVE_POOL_MC_SAMPLES', '10')))
    for _ in range(MC_SAMPLES):
        # Start near initial condition with a small spread.
        init_sample = random.gauss(T_pool_init_C, fahrenheit_to_celsius(0.5))
        mc_prev_samples.append(clamp(init_sample, MIN_POOL_TEMP_C, MAX_POOL_TEMP_C))
    comparison_rows = []           # for the per-location comparison file

    for i, d in enumerate(data['date_list']):
        T_air_C      = data['average_temp_list'][i]
        RH_pct       = data['average_humidity_list'][i]
        wind_ms      = data['average_wind_list'][i]
        solar_MJm2 = coerce_float(
            solar_map.get(str(d), SOLAR_FALLBACK_MJM2),
            fallback=SOLAR_FALLBACK_MJM2,
            field_name=f'solar_MJm2[{d}]'
        )

        # Morning / afternoon air conditions (fall back to daily mean if absent)
        m_T_air = coerce_float(data['morning_temp_list'][i],     fallback=T_air_C)
        m_RH    = coerce_float(data['morning_humidity_list'][i], fallback=RH_pct)
        m_wind  = coerce_float(data['morning_wind_list'][i],     fallback=wind_ms)
        a_T_air = coerce_float(data['afternoon_temp_list'][i],   fallback=T_air_C)
        a_RH    = coerce_float(data['afternoon_humidity_list'][i], fallback=RH_pct)
        a_wind  = coerce_float(data['afternoon_wind_list'][i],   fallback=wind_ms)

        # Effective solar for each sub-daily period:
        #   effective_solar = (energy_during_period / period_duration)
        # expressed in the same MJ m⁻² day⁻¹ units the model expects.
        morning_solar   = solar_MJm2 * SOLAR_FRAC_MORNING   / DT_MORNING_DAYS
        afternoon_solar = solar_MJm2 * SOLAR_FRAC_AFTERNOON / DT_AFTERNOON_DAYS

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
        # Save pool temp at start of this day (= end of previous day) for
        # sub-daily estimates.
        T_pool_start_C = T_pool_new_C
        T_pool_new_C, fluxes = pool_thermal_balance_step(
            T_pool_new_C, T_air_C, RH_pct, wind_ms, solar_MJm2,
            depth_m=depth_m,
            ground_temp_C=ground_temp_C,
            bottom_u_Wm2K=bottom_u_Wm2K,
            include_ground=True,
            dt_days=1.0
        )

        # --- bias correction comparison runs (comparison file only, not DB) ---
        T_pool_evap_mult_C, _ = pool_thermal_balance_step(
            T_pool_evap_mult_C, T_air_C, RH_pct, wind_ms, solar_MJm2,
            depth_m=depth_m, ground_temp_C=ground_temp_C,
            bottom_u_Wm2K=bottom_u_Wm2K, include_ground=True, dt_days=1.0,
            bias_correction='evap_multiplier', ce_multiplier=CE_MULTIPLIER,
        )
        T_pool_wind_floor_C, _ = pool_thermal_balance_step(
            T_pool_wind_floor_C, T_air_C, RH_pct, wind_ms, solar_MJm2,
            depth_m=depth_m, ground_temp_C=ground_temp_C,
            bottom_u_Wm2K=bottom_u_Wm2K, include_ground=True, dt_days=1.0,
            bias_correction='wind_floor', evap_wind_floor_ms=EVAP_WIND_FLOOR_MS,
        )
        T_pool_penman_C, _ = pool_thermal_balance_step(
            T_pool_penman_C, T_air_C, RH_pct, wind_ms, solar_MJm2,
            depth_m=depth_m, ground_temp_C=ground_temp_C,
            bottom_u_Wm2K=bottom_u_Wm2K, include_ground=True, dt_days=1.0,
            bias_correction='penman',
        )

        # --- sub-daily estimates: 9 AM and 3 PM ---
        # 9 AM: step from start-of-day pool temp for 9 hours using morning conditions
        T_morning_C, _ = pool_thermal_balance_step(
            T_pool_start_C, m_T_air, m_RH, m_wind, morning_solar,
            depth_m=depth_m,
            ground_temp_C=ground_temp_C,
            bottom_u_Wm2K=bottom_u_Wm2K,
            include_ground=True,
            dt_days=DT_MORNING_DAYS
        )
        # 3 PM: step from 9 AM pool temp for 6 more hours using afternoon conditions
        T_afternoon_C, _ = pool_thermal_balance_step(
            T_morning_C, a_T_air, a_RH, a_wind, afternoon_solar,
            depth_m=depth_m,
            ground_temp_C=ground_temp_C,
            bottom_u_Wm2K=bottom_u_Wm2K,
            include_ground=True,
            dt_days=DT_AFTERNOON_DAYS
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
            'INSERT OR REPLACE INTO pool_temps '
            '(location_id, date, temp, heat_fluxes, morning_temp_C, afternoon_temp_C) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (location_id, str(d), T_pool_new_C, json.dumps(fluxes),
             T_morning_C, T_afternoon_C)
        )
        cursor.execute(
            'INSERT OR REPLACE INTO pool_temp_ci '
            '(location_id, date, p025_C, p500_C, p975_C, mean_C, std_C, n_samples) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (location_id, str(d), p025_C, p500_C, p975_C, mc_mean_C, mc_std_C, MC_SAMPLES)
        )

        air_F          = celsius_to_fahrenheit(T_pool_air_C)
        old_F          = celsius_to_fahrenheit(T_pool_old_C)
        new_F          = celsius_to_fahrenheit(T_pool_new_C)
        morning_F      = celsius_to_fahrenheit(T_morning_C)
        afternoon_F    = celsius_to_fahrenheit(T_afternoon_C)
        evap_mult_F    = celsius_to_fahrenheit(T_pool_evap_mult_C)
        wind_floor_F   = celsius_to_fahrenheit(T_pool_wind_floor_C)
        penman_F       = celsius_to_fahrenheit(T_pool_penman_C)
        delta_new_old  = new_F - old_F
        delta_old_air  = old_F - air_F
        delta_new_air  = new_F - air_F
        comparison_rows.append((
            str(d),
            round(air_F,        2),
            round(old_F,        2),
            round(new_F,        2),
            round(evap_mult_F,  2),
            round(wind_floor_F, 2),
            round(penman_F,     2),
            round(delta_new_old,  2),
            round(delta_old_air,  2),
            round(delta_new_air,  2),
            fluxes['Q_ground_Wm2'],
            fluxes['Q_evap_Wm2'],
            fluxes['Q_solar_Wm2'],
        ))

        print(f"  {d}: air={air_F:.1f}°F old={old_F:.1f}°F new={new_F:.1f}°F "
              f"9am={morning_F:.1f}°F 3pm={afternoon_F:.1f}°F | "
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
            f.write('date,T_air_only_F,T_old_F,T_new_F,T_evap_mult_F,T_wind_floor_F,T_penman_F,delta_new_minus_old_F,delta_old_minus_air_F,delta_new_minus_air_F,Q_ground_Wm2,Q_evap_Wm2,Q_solar_Wm2\n')
            for row in comparison_rows:
                f.write(','.join(str(v) for v in row) + '\n')
        print(f"  Comparison written → {cmp_filename}")
    else:
        print(f"  TEST MODE summary for {name} (first 3 rows):")
        for row in comparison_rows[:3]:
            print(
                f"    {row[0]} air={row[1]} old={row[2]} new={row[3]} "
                f"evap_mult={row[4]} wind_floor={row[5]} penman={row[6]}"
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
        f.write('location,date,T_air_only_F,T_old_F,T_new_F,T_evap_mult_F,T_wind_floor_F,T_penman_F,delta_new_minus_old_F,delta_old_minus_air_F,delta_new_minus_air_F,Q_ground_Wm2,Q_evap_Wm2,Q_solar_Wm2\n')
        for loc_name, rows in all_comparison_rows.items():
            for row in rows:
                f.write(f"{loc_name}," + ','.join(str(v) for v in row) + '\n')
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

        # Write extended weather forecast file (pool temp + air met data + thunder)
        weather_name = name.replace(" ", "_")
        cursor.execute(
            'SELECT pt.date, pt.temp, ci.p025_C, ci.p975_C, '
            'at.temp, at.wind_speed, at.wind_direction, at.humidity, '
            'pt.morning_temp_C, pt.afternoon_temp_C, at.thunder_prob '
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
                pool_temp_F  = celsius_to_fahrenheit(wrow[1])
                ci_low_F     = celsius_to_fahrenheit(wrow[2]) if wrow[2] is not None else pool_temp_F
                ci_high_F    = celsius_to_fahrenheit(wrow[3]) if wrow[3] is not None else pool_temp_F
                air_temp_F   = celsius_to_fahrenheit(wrow[4]) if wrow[4] is not None else 0.0
                wind_mph     = (wrow[5] / 0.44704) if wrow[5] is not None else 0.0
                wind_dir     = wrow[6] if wrow[6] is not None else 0.0
                humidity     = wrow[7] if wrow[7] is not None else 0.0
                morning_F    = celsius_to_fahrenheit(wrow[8])  if wrow[8]  is not None else pool_temp_F
                afternoon_F  = celsius_to_fahrenheit(wrow[9])  if wrow[9]  is not None else pool_temp_F
                thunder_prob = wrow[10] if wrow[10] is not None else 0.0  # NEW

                # Calculate wind ratings for both breaks
                rights_ratings = calculate_wind_ratings(int(wind_dir), 'Rights', name)
                lefts_ratings = calculate_wind_ratings(int(wind_dir), 'Lefts', name)

                rights_barrel_rating = rights_ratings['barrel_rating'] or ''
                rights_air_rating = rights_ratings['air_rating'] or ''
                lefts_barrel_rating = lefts_ratings['barrel_rating'] or ''
                lefts_air_rating = lefts_ratings['air_rating'] or ''

                wf.write(
                    f"{wrow[0]},{pool_temp_F:.2f},{ci_low_F:.2f},{ci_high_F:.2f},{air_temp_F:.2f},"
                    f"{wind_mph:.1f},{wind_dir:.1f},{humidity:.1f},"
                    f"{morning_F:.2f},{afternoon_F:.2f},{thunder_prob:.0f},"
                    f"{rights_barrel_rating},{rights_air_rating},{lefts_barrel_rating},{lefts_air_rating}\n"
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

# Write last-data-fetch timestamp so the frontend info box can display it.
if WRITE_FILES:
    fetch_ts_path = os.path.join(DATA_DIR, 'last_data_fetch.txt')
    with open(fetch_ts_path, 'w') as f:
        f.write(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') + '\n')
    print(f"Wrote last_data_fetch timestamp to {fetch_ts_path}")
else:
    print("TEST MODE: skipped last_data_fetch.txt write.")

conn.commit()
conn.close()

print('Script completed successfully')
