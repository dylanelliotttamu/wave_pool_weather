import importlib.util
import math
import os
import sys
import unittest
from pathlib import Path

# pull_api_data.py runs DB + file setup at import time.
# Test mode disables file I/O and uses an in-memory DB so the import is safe.
os.environ.setdefault('WAVE_POOL_TEST_MODE', '1')

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / 'scripts' / 'pull_api_data.py'
SPEC = importlib.util.spec_from_file_location('pull_api_data', MODULE_PATH)
pull_api_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pull_api_data)

coerce_float = pull_api_data.coerce_float


class TestCoerceFloat(unittest.TestCase):
    # --- already-numeric inputs ---
    def test_int_input(self):
        self.assertEqual(coerce_float(15), 15.0)

    def test_float_input(self):
        self.assertAlmostEqual(coerce_float(3.14), 3.14)

    def test_zero(self):
        self.assertEqual(coerce_float(0), 0.0)

    def test_negative_float(self):
        self.assertAlmostEqual(coerce_float(-7.5), -7.5)

    # --- non-finite / bad numeric ---
    def test_nan_returns_fallback(self):
        self.assertEqual(coerce_float(float('nan'), fallback=99.0), 99.0)

    def test_inf_returns_fallback(self):
        self.assertEqual(coerce_float(float('inf'), fallback=5.0), 5.0)

    # --- string inputs (the main crash surface) ---
    def test_numeric_string(self):
        self.assertAlmostEqual(coerce_float('18.5'), 18.5)

    def test_numeric_string_with_whitespace(self):
        self.assertAlmostEqual(coerce_float('  22.31  '), 22.31)

    def test_empty_string_returns_fallback(self):
        self.assertEqual(coerce_float('', fallback=7.0), 7.0)

    def test_whitespace_only_string_returns_fallback(self):
        self.assertEqual(coerce_float('   ', fallback=7.0), 7.0)

    def test_json_encoded_float_string(self):
        # Mirrors a solar_radiation value accidentally stored as JSON text.
        self.assertAlmostEqual(coerce_float('15.0'), 15.0)

    def test_non_numeric_string_returns_fallback(self):
        self.assertEqual(coerce_float('N/A', fallback=0.0), 0.0)

    # --- list / tuple inputs (the exact crash that was reported) ---
    def test_single_element_list(self):
        # This is the type that caused the original TypeError.
        self.assertAlmostEqual(coerce_float([22.13]), 22.13)

    def test_single_element_tuple(self):
        self.assertAlmostEqual(coerce_float((10.0,)), 10.0)

    def test_list_with_string_element(self):
        self.assertAlmostEqual(coerce_float(['18.5']), 18.5)

    def test_empty_list_returns_fallback(self):
        self.assertEqual(coerce_float([], fallback=3.0), 3.0)

    # --- default fallback ---
    def test_default_fallback_is_zero(self):
        self.assertEqual(coerce_float('bad'), 0.0)

    def test_bool_not_treated_as_int(self):
        # bool is a subclass of int in Python; coerce_float should NOT silently
        # convert True → 1.0.  It should fall through to the warning path and
        # return the fallback.
        self.assertEqual(coerce_float(True, fallback=-1.0), -1.0)


if __name__ == '__main__':
    unittest.main()
