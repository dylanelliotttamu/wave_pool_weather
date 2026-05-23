#!/usr/bin/env python3
"""
Wind ratings calculation test script.
Tests the barrel and air wind rating calculations without needing the full pipeline.
Run from the repository root: python3 scripts/test_wind_ratings.py
"""

import json
import os
import sys

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

def load_breaks_config(location='Waco'):
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
        print(f"Error loading config: {e}")
        return {}

def calculate_wind_ratings(wind_direction, break_name):
    """Calculate barrel and air wind ratings for a break."""
    config = load_breaks_config('Waco')
    wind_direction = normalize_wind_direction(wind_direction)

    if break_name not in config:
        return {
            'barrel_score': 0,
            'barrel_rating': None,
            'air_score': 0,
            'air_rating': None,
        }

    break_config = config[break_name]

    barrel_ideal = break_config['barrel_ideal_angle']
    barrel_epic_tol = 15
    barrel_good_tol = 45
    angle_diff = calculate_angular_distance(wind_direction, barrel_ideal)

    if angle_diff <= barrel_epic_tol:
        barrel_score = 100 - (angle_diff / barrel_epic_tol * 20)
        barrel_rating = 'Epic'
    elif angle_diff <= barrel_good_tol:
        barrel_score = 80 - ((angle_diff - barrel_epic_tol) / (barrel_good_tol - barrel_epic_tol) * 40)
        barrel_rating = 'Good'
    else:
        barrel_score = max(0, 40 - ((angle_diff - barrel_good_tol) / 135 * 40))
        barrel_rating = None

    air_range_min, air_range_max = break_config['air_excellent_range']
    air_peak = break_config['air_peak_angle']

    if is_direction_in_range(wind_direction, air_range_min, air_range_max):
        distance_to_peak = abs(wind_direction - air_peak)
        air_score = 100 - (distance_to_peak / 90 * 30)
        air_rating = 'Epic'
    else:
        distance_to_range = angular_distance_to_range(
            wind_direction, air_range_min, air_range_max
        )

        if distance_to_range <= 45:
            air_score = 70 - (distance_to_range / 45 * 30)
            air_rating = 'Good'
        else:
            air_score = max(0, 40 - ((distance_to_range - 45) / 135 * 40))
            air_rating = None

    return {
        'barrel_score': round(barrel_score),
        'barrel_rating': barrel_rating,
        'air_score': round(air_score),
        'air_rating': air_rating,
    }

# Test scenarios from test data
TEST_CASES = [
    {
        'date': '2026-05-21',
        'description': 'E winds - epic air on both breaks',
        'wind_dir': 90,
        'expected': {
            'Rights': {'barrel': None, 'air': 'Epic'},
            'Lefts': {'barrel': None, 'air': 'Epic'},
        }
    },
    {
        'date': '2026-05-22',
        'description': 'SSE winds - good air on both breaks',
        'wind_dir': 150,
        'expected': {
            'Rights': {'barrel': None, 'air': 'Good'},
            'Lefts': {'barrel': None, 'air': 'Good'},
        }
    },
    {
        'date': '2026-05-23',
        'description': 'NE winds - epic air for Lefts and good air for Rights',
        'wind_dir': 45,
        'expected': {
            'Rights': {'barrel': None, 'air': 'Good'},
            'Lefts': {'barrel': None, 'air': 'Epic'},
        }
    },
    {
        'date': '2026-05-24',
        'description': 'S winds - Lefts barrel good, Rights air good',
        'wind_dir': 180,
        'expected': {
            'Rights': {'barrel': None, 'air': 'Good'},
            'Lefts': {'barrel': 'Good', 'air': None},
        }
    },
    {
        'date': '2026-05-25',
        'description': 'N winds - Rights barrel good, Lefts air good',
        'wind_dir': 0,
        'expected': {
            'Rights': {'barrel': 'Good', 'air': None},
            'Lefts': {'barrel': None, 'air': 'Good'},
        }
    },
    {
        'date': '2026-05-25b',
        'description': 'N winds expressed as 360 - should match 0 degrees',
        'wind_dir': 360,
        'expected': {
            'Rights': {'barrel': 'Good', 'air': None},
            'Lefts': {'barrel': None, 'air': 'Good'},
        }
    },
    {
        'date': '2026-05-26',
        'description': 'W winds - Poor conditions for both',
        'wind_dir': 270,
        'expected': {
            'Rights': {'barrel': None, 'air': None},
            'Lefts': {'barrel': None, 'air': None},
        }
    },
    {
        'date': '2026-05-27',
        'description': 'WSW winds - Lefts barrel good, air poor for both',
        'wind_dir': 240,
        'expected': {
            'Rights': {'barrel': None, 'air': None},
            'Lefts': {'barrel': 'Good', 'air': None},
        }
    },
]

def run_tests():
    """Run all test cases and report results."""
    print("\n" + "=" * 100)
    print("WIND RATINGS CALCULATION TEST SUITE")
    print("=" * 100 + "\n")

    total_tests = 0
    passed = 0
    failed = 0

    for test in TEST_CASES:
        print(f"Test: {test['date']} - {test['description']}")
        print(f"Wind Direction: {test['wind_dir']}°\n")

        for break_name in ['Rights', 'Lefts']:
            ratings = calculate_wind_ratings(test['wind_dir'], break_name)
            expected = test['expected'][break_name]

            barrel_match = ratings['barrel_rating'] == expected['barrel']
            air_match = ratings['air_rating'] == expected['air']

            total_tests += 2
            if barrel_match:
                passed += 1
            else:
                failed += 1
            if air_match:
                passed += 1
            else:
                failed += 1

            barrel_status = "✓" if barrel_match else "✗"
            air_status = "✓" if air_match else "✗"

            print(f"  {break_name}:")
            print(f"    {barrel_status} Barrel: {ratings['barrel_rating']} (score {ratings['barrel_score']}) [expected: {expected['barrel']}]")
            print(f"    {air_status} Air:    {ratings['air_rating']} (score {ratings['air_score']}) [expected: {expected['air']}]")

        print()

    print("=" * 100)
    print(f"RESULTS: {passed}/{total_tests} tests passed")
    if failed > 0:
        print(f"         {failed} tests FAILED")
        print("=" * 100)
        return False
    else:
        print("         All tests passed! ✓")
        print("=" * 100)
        return True

if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
