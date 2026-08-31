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


def bottom_u_value_Wm2K(k_soil_Wm1K, soil_depth_m):
    """Effective bottom U-value combining the concrete slab and soil layer."""
    R_concrete = L_CONCRETE_M / K_CONCRETE
    R_soil     = soil_depth_m / k_soil_Wm1K
    return 1.0 / (R_concrete + R_soil)
