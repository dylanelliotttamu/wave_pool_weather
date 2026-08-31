#!/usr/bin/env python3
"""
Shared pool-thermal-physics helpers (WAVE-FEST model).

Extracted from pull_api_data.py so the same one-state energy-balance model
can be reused by both the daily forecast pipeline and the climatology
build script (scripts/build_climatology.py), without either script having
to duplicate the physics or import the other (which would trigger live
network calls / DB writes at import time).
"""
import json
import math

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
# Evaporation bias-correction defaults
#
# Ported from the worktree-bias-correction branch (experiments/
# bias_correction_review.ipynb + experiments/flux_and_bias_changes.ipynb),
# which found the original bulk-transfer evaporation term under-cools the
# pool on hot/calm days. That branch added a selectable bias_correction mode
# to pool_thermal_balance_step but never defined these three constants
# anywhere, so calling it with a bias-correction mode raised NameError.
# Values below are the notebook's own working defaults (see the CE_MULTIPLIER
# sweep in bias_correction_review.ipynb, tuned against Waco summer data).
#
# ACTIVE_BIAS_CORRECTION defaults to 'none' so every existing call site
# (pull_api_data.py forecasts, build_climatology.py) keeps its original,
# unchanged numeric output unless it explicitly opts into a correction mode.
# ---------------------------------------------------------------------------
ACTIVE_BIAS_CORRECTION = 'none'   # one of: 'none', 'evap_multiplier', 'wind_floor', 'penman'
CE_MULTIPLIER = 1.25              # evap_multiplier scale factor (Waco summer fit)
EVAP_WIND_FLOOR_MS = 1.5          # wind_floor minimum effective wind speed (m/s)


# ---------------------------------------------------------------------------
# Unit-conversion / general helpers
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
def penman_evap_Wm2(T_pool_C, T_air_C, RH_pct, wind_ms, solar_Wm2):
    """
    Penman (1948) open-water evaporation expressed as a heat flux (W m⁻²,
    negative = cooling).

    Formula:
        E_mm_day = (Δ·Rn/λ + γ·Ea) / (Δ + γ)

    where:
        Δ  = slope of saturation vapor pressure curve at T_air  (kPa K⁻¹)
        γ  = psychrometric constant ≈ 0.067 kPa K⁻¹
        Rn = net radiation estimated from solar input (MJ m⁻² day⁻¹)
        λ  = latent heat of vaporization = 2.45 MJ kg⁻¹
        Ea = aerodynamic evaporation = f(u) × (e_s(T_pool) − e_a)
             f(u) = 6.43 × (1 + 0.536·U)  [Monteith & Unsworth wind function,
                    mm day⁻¹ kPa⁻¹]

    Differences from the bulk-transfer baseline:
      - The Δ/(Δ+γ) weight on the radiation term means sunny calm days
        produce MORE evaporation than the linear-wind bulk formula would
        predict — exactly the scenario where the baseline under-cools.
      - At T_air = 35°C: Δ ≈ 0.245, γ = 0.067, Δ/(Δ+γ) ≈ 0.78 so ~78% of
        evaporation is radiation-driven, only 22% wind-driven.
      - No additional tuning constants — all parameters are standard physics.
    """
    RH = max(0.0, min(1.0, RH_pct / 100.0))

    # Slope of saturation vapor pressure curve at air temp (kPa K⁻¹)
    e_s_air = saturation_vapor_pressure_kPa(T_air_C)
    delta = 4098.0 * e_s_air / (T_air_C + 237.3) ** 2

    gamma = 0.067  # psychrometric constant (kPa K⁻¹) at sea level

    # Net radiation: use incoming solar as proxy (W m⁻² → MJ m⁻² day⁻¹).
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
                  pool is warmer than dew point). Method selectable via
                  bias_correction — see below.
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
    bias_correction : evaporation-model override — one of 'none',
                  'evap_multiplier', 'wind_floor', 'penman'. Defaults to
                  module-level ACTIVE_BIAS_CORRECTION ('none') so existing
                  callers are unaffected unless they opt in explicitly.
    ce_multiplier      : override for CE_MULTIPLIER (evap_multiplier mode).
    evap_wind_floor_ms : override for EVAP_WIND_FLOOR_MS (wind_floor mode).

    Returns
    -------
    T_pool_new_C  : updated pool temperature (°C)
    heat_fluxes   : dict with individual flux components (W m⁻²), dT (°C),
                    and which bias_correction method was actually used.
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

    # 4. Evaporative heat flux — method selected by bias_correction param.
    #    Falls back to module-level ACTIVE_BIAS_CORRECTION when not specified.
    method     = bias_correction    if bias_correction    is not None else ACTIVE_BIAS_CORRECTION
    ce_mult    = ce_multiplier      if ce_multiplier      is not None else CE_MULTIPLIER
    wind_floor = evap_wind_floor_ms if evap_wind_floor_ms is not None else EVAP_WIND_FLOOR_MS

    EVAP_BASE = 1.2 * 1.3e-3 * L_VAP * 0.622 / 101.325  # ≈ 23.5 W m⁻² (m s⁻¹)⁻¹ kPa⁻¹
    e_s_pool  = saturation_vapor_pressure_kPa(T_pool_C)
    e_a       = saturation_vapor_pressure_kPa(T_air_C) * RH

    if method == 'evap_multiplier':
        # Scale C_E by ce_mult. Notebook sensitivity sweep suggests ~1.25x
        # for Waco summer conditions.
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


def bottom_u_value_Wm2K(k_soil_Wm1K, soil_depth_m):
    """Effective bottom U-value combining the concrete slab and soil layer."""
    R_concrete = L_CONCRETE_M / K_CONCRETE
    R_soil     = soil_depth_m / k_soil_Wm1K
    return 1.0 / (R_concrete + R_soil)
