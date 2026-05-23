"""Tests for morning (9 AM) and afternoon (3 PM) pool temperature estimates."""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault('WAVE_POOL_TEST_MODE', '1')

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / 'scripts' / 'pull_api_data.py'
SPEC = importlib.util.spec_from_file_location('pull_api_data', MODULE_PATH)
pull_api_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pull_api_data)

parse_weather_data = pull_api_data.parse_weather_data
pool_thermal_balance_step = pull_api_data.pool_thermal_balance_step
celsius_to_fahrenheit = pull_api_data.celsius_to_fahrenheit
fahrenheit_to_celsius = pull_api_data.fahrenheit_to_celsius


def _make_period(hour, temp_F, wind_mph, humidity, wind_dir="N"):
    """Build a minimal NWS-style hourly forecast period dict."""
    return {
        "startTime": f"2026-06-01T{hour:02d}:00:00+00:00",
        "temperature": float(temp_F),
        "relativeHumidity": float(humidity),
        "windSpeed": f"{wind_mph} mph",
        "windDirection": wind_dir,
    }


def _build_fake_nws_json(morning_F=65.0, afternoon_F=85.0, evening_F=75.0,
                          morning_wind=5, afternoon_wind=15, evening_wind=10):
    """Build a fake NWS hourly JSON for 2026-06-01 with distinct morning/afternoon conditions."""
    periods = []
    for h in range(0, 9):
        periods.append(_make_period(h, morning_F, morning_wind, 70))
    for h in range(9, 15):
        periods.append(_make_period(h, afternoon_F, afternoon_wind, 40))
    for h in range(15, 24):
        periods.append(_make_period(h, evening_F, evening_wind, 55))
    return {"properties": {"periods": periods}}


class TestParseWeatherDataMorningAfternoon(unittest.TestCase):
    def setUp(self):
        fake_json = _build_fake_nws_json()
        self.result = parse_weather_data(fake_json)

    def test_result_not_none(self):
        self.assertIsNotNone(self.result)

    def test_morning_keys_present(self):
        self.assertIn('morning_temp_list', self.result)
        self.assertIn('morning_humidity_list', self.result)
        self.assertIn('morning_wind_list', self.result)

    def test_afternoon_keys_present(self):
        self.assertIn('afternoon_temp_list', self.result)
        self.assertIn('afternoon_humidity_list', self.result)
        self.assertIn('afternoon_wind_list', self.result)

    def test_morning_temp_matches_input(self):
        expected_C = fahrenheit_to_celsius(65.0)
        self.assertAlmostEqual(self.result['morning_temp_list'][0], expected_C, places=1)

    def test_afternoon_temp_matches_input(self):
        expected_C = fahrenheit_to_celsius(85.0)
        self.assertAlmostEqual(self.result['afternoon_temp_list'][0], expected_C, places=1)

    def test_morning_cooler_than_afternoon(self):
        self.assertLess(self.result['morning_temp_list'][0],
                        self.result['afternoon_temp_list'][0])

    def test_morning_wind_less_than_afternoon(self):
        self.assertLess(self.result['morning_wind_list'][0],
                        self.result['afternoon_wind_list'][0])

    def test_fallback_when_no_morning_data(self):
        """If only afternoon periods exist the morning values fall back to daily mean."""
        periods = [_make_period(h, 85.0, 10, 50) for h in range(9, 15)]
        result = parse_weather_data({"properties": {"periods": periods}})
        self.assertIsNotNone(result)
        avg = result['average_temp_list'][0]
        self.assertAlmostEqual(result['morning_temp_list'][0], avg, places=3)

    def test_daily_wind_and_direction_use_daytime_hours_only(self):
        periods = []
        for hour in range(0, 7):
            periods.append(_make_period(hour, 70.0, 1, 60, wind_dir='N'))
        for hour in range(7, 20):
            periods.append(_make_period(hour, 80.0, 10, 45, wind_dir='E'))
        for hour in range(20, 24):
            periods.append(_make_period(hour, 68.0, 1, 70, wind_dir='N'))

        result = parse_weather_data({"properties": {"periods": periods}})

        self.assertIsNotNone(result)
        self.assertAlmostEqual(result['average_wind_list'][0], pull_api_data.mph_to_ms(10), places=3)
        self.assertEqual(result['average_wind_direction_list'][0], 90.0)


class TestSubDailyThermalBalance(unittest.TestCase):
    """Verify that the 3 PM pool temp is higher than the 9 AM pool temp on a
    typical summer day with significant afternoon solar input."""

    DT_MORNING   = 9.0 / 24.0
    DT_AFTERNOON = 6.0 / 24.0
    SOLAR_FRAC_M = 0.147
    SOLAR_FRAC_A = 0.708

    def test_afternoon_warmer_than_morning(self):
        T_start = 25.0       # °C  (77 °F start of day)
        daily_solar = 20.0   # MJ/m²/day  (clear summer day)
        T_air_morning   = fahrenheit_to_celsius(65.0)
        T_air_afternoon = fahrenheit_to_celsius(88.0)

        morning_solar   = daily_solar * self.SOLAR_FRAC_M / self.DT_MORNING
        afternoon_solar = daily_solar * self.SOLAR_FRAC_A / self.DT_AFTERNOON

        T_morning, _ = pool_thermal_balance_step(
            T_start, T_air_morning, 70, 2.0, morning_solar,
            dt_days=self.DT_MORNING
        )
        T_afternoon, _ = pool_thermal_balance_step(
            T_morning, T_air_afternoon, 40, 3.0, afternoon_solar,
            dt_days=self.DT_AFTERNOON
        )
        self.assertGreater(T_afternoon, T_morning)

    def test_morning_result_is_finite(self):
        T_morning, _ = pool_thermal_balance_step(
            20.0, 18.0, 60, 3.0,
            20.0 * self.SOLAR_FRAC_M / self.DT_MORNING,
            dt_days=self.DT_MORNING
        )
        self.assertTrue(
            pull_api_data.math.isfinite(T_morning),
            "Morning pool temp should be a finite number"
        )


if __name__ == '__main__':
    unittest.main()
