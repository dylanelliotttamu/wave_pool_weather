import ast
import re
import unittest
from datetime import datetime
from pathlib import Path


def _load_parse_weather_data():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "pull_api_data.py"
    source = script_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(script_path))

    needed_functions = {
        "fahrenheit_to_celsius",
        "mph_to_ms",
        "kmh_to_ms",
        "parse_weather_data",
    }
    selected_nodes = []
    needed_assignments = {
        "direction_map",
        "WIND_SPEED_RANGE_PATTERN",
        "WIND_SPEED_SINGLE_PATTERN",
    }
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in needed_assignments:
                    selected_nodes.append(node)
                    break
        elif isinstance(node, ast.FunctionDef) and node.name in needed_functions:
            selected_nodes.append(node)

    module = ast.Module(body=selected_nodes, type_ignores=[])
    namespace = {"datetime": datetime, "re": re}
    exec(compile(module, str(script_path), "exec"), namespace)
    return namespace["parse_weather_data"]


class ParseWeatherDataTests(unittest.TestCase):
    def setUp(self):
        self.parse_weather_data = _load_parse_weather_data()

    def test_handles_calm_and_ranged_wind_speed_strings(self):
        payload = {
            "properties": {
                "periods": [
                    {
                        "startTime": "2026-05-07T08:00:00+00:00",
                        "temperature": 70,
                        "relativeHumidity": {"value": 50},
                        "windSpeed": "Calm",
                        "windDirection": "N",
                    },
                    {
                        "startTime": "2026-05-07T14:00:00+00:00",
                        "temperature": 74,
                        "relativeHumidity": {"value": 60},
                        "windSpeed": "5 to 10 mph",
                        "windDirection": "NW",
                    },
                ]
            }
        }

        parsed = self.parse_weather_data(payload)

        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed["date_list"]), 1)
        self.assertGreater(parsed["average_wind_list"][0], 0.0)

    def test_skips_malformed_periods_instead_of_failing_entire_parse(self):
        payload = {
            "properties": {
                "periods": [
                    {
                        "startTime": "2026-05-07T08:00:00+00:00",
                        "temperature": {"value": None, "unitCode": "wmoUnit:degC"},
                        "relativeHumidity": {"value": 45},
                        "windSpeed": "8 mph",
                        "windDirection": "N",
                    },
                    {
                        "startTime": "2026-05-07T09:00:00+00:00",
                        "temperature": {"value": 20.0, "unitCode": "wmoUnit:degC"},
                        "relativeHumidity": {"value": 55},
                        "windSpeed": "8 mph",
                        "windDirection": "NE",
                    },
                ]
            }
        }

        parsed = self.parse_weather_data(payload)

        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed["date_list"]), 1)
        self.assertAlmostEqual(parsed["average_temp_list"][0], 20.0)


if __name__ == "__main__":
    unittest.main()
