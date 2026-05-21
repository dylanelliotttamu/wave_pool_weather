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

def load_waco_breaks_config():
    """Load break configuration from JSON file."""
    config_path = os.path.join(os.path.dirname(__file__), 'waco_breaks_config.json')
    try:
        with open(config_path, 'r') as f:
            data = json.load(f)
        return data.get('Waco', {})
    except Exception as e:
        print(f"Error loading config: {e}")
        return {}

def calculate_wind_ratings(wind_direction, break_name):
    """Calculate barrel and air wind ratings for a break."""
    config = load_waco_breaks_config()

    if break_name not in config:
        return {
            'barrel_score': 0,
            'barrel_rating': None,
            'air_score': 0,
            'air_rating': None,
        }

    break_config = config[break_name]

    barrel_ideal = break_config['barrel_ideal_angle']
    barrel_tol = break_config['barrel_tolerance_degrees']
    angle_diff = calculate_angular_distance(wind_direction, barrel_ideal)

    if angle_diff <= barrel_tol:
        barrel_score = 100 - (angle_diff / barrel_tol * 30)
        barrel_rating = 'Excellent'
    else:
        barrel_score = max(0, 70 - ((angle_diff - barrel_tol) / 150 * 70))
        barrel_rating = None

    air_range_min, air_range_max = break_config['air_excellent_range']
    air_peak = break_config['air_peak_angle']

    if air_range_min <= wind_direction <= air_range_max:
        distance_to_peak = abs(wind_direction - air_peak)
        air_score = 100 - (distance_to_peak / 90 * 30)
        air_rating = 'Excellent'
    else:
        if wind_direction < air_range_min:
            distance_to_range = air_range_min - wind_direction
        else:
            distance_to_range = wind_direction - air_range_max

        if distance_to_range <= 45:
            air_score = 70 - (distance_to_range / 45 * 30)
            air_rating = 'Fair'
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
        'description': 'E winds - Perfect for air on both breaks',
        'wind_dir': 90,
        'expected': {
            'Rights': {'barrel': None, 'air': 'Excellent'},
            'Lefts': {'barrel': None, 'air': 'Excellent'},
        }
    },
    {
        'date': '2026-05-22',
        'description': 'SSE winds - Perfect barrel for Rights, good air for both',
        'wind_dir': 150,
        'expected': {
            'Rights': {'barrel': 'Excellent', 'air': 'Excellent'},
            'Lefts': {'barrel': None, 'air': None},  # 105° away from ideal, too far
        }
    },
    {
        'date': '2026-05-23',
        'description': 'NE winds - Perfect barrel for Lefts, excellent air for Lefts',
        'wind_dir': 45,
        'expected': {
            'Rights': {'barrel': None, 'air': 'Fair'},  # Exactly at Fair threshold (45° from 90°)
            'Lefts': {'barrel': 'Excellent', 'air': 'Excellent'},
        }
    },
    {
        'date': '2026-05-24',
        'description': 'S winds - Good for both barrel and air on Rights',
        'wind_dir': 180,
        'expected': {
            'Rights': {'barrel': 'Excellent', 'air': 'Excellent'},
            'Lefts': {'barrel': None, 'air': None},  # 180° is outside Lefts air range (0-90°)
        }
    },
    {
        'date': '2026-05-25',
        'description': 'N winds - Good air for Lefts',
        'wind_dir': 0,
        'expected': {
            'Rights': {'barrel': None, 'air': None},
            'Lefts': {'barrel': None, 'air': 'Excellent'},
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
        'description': 'WSW winds - Worst air for Rights (on-shore perpendicular)',
        'wind_dir': 240,
        'expected': {
            'Rights': {'barrel': None, 'air': None},
            'Lefts': {'barrel': None, 'air': None},
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
