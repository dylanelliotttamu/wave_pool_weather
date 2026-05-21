#!/usr/bin/env python3
"""
Test file to explore lightning probability data from NWS API and OpenMeteo API.

This script investigates:
1. What lightning-related data is available from the NWS API
2. What lightning-related data is available from the OpenMeteo API
3. How to extract and structure this data for our application
"""

import json
import urllib.request
import unittest
from datetime import date, timedelta
from pprint import pprint


class TestNWSLightningData(unittest.TestCase):
    """Explore NWS API for lightning probability data."""

    def setUp(self):
        """Set up test location (Waco, TX BSR Surf Resort)."""
        self.lat = 31.6212
        self.lon = -97.0037
        self.location_name = "Waco, TX"

    def fetch_json(self, url, timeout=15):
        """Helper to fetch JSON from a URL."""
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            return None

    def test_nws_points_endpoint(self):
        """
        Test 1: Explore the NWS /points endpoint to see what forecast URLs are available.

        The /points endpoint should give us:
        - forecast (text forecast)
        - forecastHourly (hourly forecast)
        - forecastGridData (detailed grid data - THIS might have lightning!)
        """
        print("\n" + "="*80)
        print("TEST 1: NWS /points endpoint exploration")
        print("="*80)

        url = f'https://api.weather.gov/points/{self.lat},{self.lon}'
        print(f"\nFetching: {url}")

        data = self.fetch_json(url)
        self.assertIsNotNone(data, "Failed to fetch NWS points data")

        if data and 'properties' in data:
            props = data['properties']
            print("\nAvailable forecast URLs:")
            print(f"  - forecast:          {props.get('forecast')}")
            print(f"  - forecastHourly:    {props.get('forecastHourly')}")
            print(f"  - forecastGridData:  {props.get('forecastGridData')}")
            print(f"  - observationStations: {props.get('observationStations')}")

            # Store the gridData URL for the next test
            self.grid_data_url = props.get('forecastGridData')
            print(f"\n⚡ GridData URL (likely contains lightning data): {self.grid_data_url}")

    def test_nws_grid_data_endpoint(self):
        """
        Test 2: Explore the NWS /gridpoints endpoint for detailed weather data.

        This endpoint contains detailed hourly forecast data including:
        - temperature, dewpoint, humidity
        - wind speed and direction
        - precipitation probability
        - POSSIBLY: lightning probability or thunderstorm probability
        """
        print("\n" + "="*80)
        print("TEST 2: NWS /gridpoints (forecastGridData) endpoint exploration")
        print("="*80)

        # First get the grid URL
        points_url = f'https://api.weather.gov/points/{self.lat},{self.lon}'
        points_data = self.fetch_json(points_url)

        if not points_data or 'properties' not in points_data:
            self.skipTest("Could not fetch points data")

        grid_url = points_data['properties'].get('forecastGridData')
        print(f"\nFetching grid data: {grid_url}")

        grid_data = self.fetch_json(grid_url)
        self.assertIsNotNone(grid_data, "Failed to fetch grid data")

        if grid_data and 'properties' in grid_data:
            props = grid_data['properties']
            print(f"\nAvailable data fields in gridData:")
            print(f"Total fields: {len(props.keys())}")

            # Print all available fields
            for key in sorted(props.keys()):
                print(f"  - {key}")

            # Look specifically for lightning-related fields
            print("\n⚡ Lightning-related fields:")
            lightning_fields = [k for k in props.keys() if 'lightning' in k.lower()
                               or 'thunder' in k.lower()
                               or 'storm' in k.lower()]

            if lightning_fields:
                for field in lightning_fields:
                    print(f"\n  Found: {field}")
                    data = props[field]
                    if isinstance(data, dict) and 'values' in data:
                        print(f"    Sample values (first 3):")
                        for i, val in enumerate(data['values'][:3]):
                            print(f"      {i+1}. {val}")
            else:
                print("  ⚠️  No fields with 'lightning', 'thunder', or 'storm' found")

            # Check for probabilityOfThunder or probabilityOfPrecipitation
            if 'probabilityOfPrecipitation' in props:
                print("\n  Found: probabilityOfPrecipitation")
                precip_data = props['probabilityOfPrecipitation']
                if 'values' in precip_data:
                    print(f"    Sample values (first 3):")
                    for i, val in enumerate(precip_data['values'][:3]):
                        print(f"      {i+1}. {val}")


class TestOpenMeteoLightningData(unittest.TestCase):
    """Explore OpenMeteo API for lightning probability data."""

    def setUp(self):
        """Set up test location (Waco, TX BSR Surf Resort)."""
        self.lat = 31.6212
        self.lon = -97.0037
        self.location_name = "Waco, TX"

    def fetch_json(self, url, timeout=15):
        """Helper to fetch JSON from a URL."""
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            return None

    def test_openmeteo_forecast_params(self):
        """
        Test 3: Explore OpenMeteo forecast API parameters.

        OpenMeteo has different API endpoints:
        - /v1/forecast - General weather forecast
        - /v1/ecmwf - ECMWF model (premium)
        - /v1/dwd-icon - DWD ICON model (Germany)

        Let's check what lightning/storm parameters are available.
        """
        print("\n" + "="*80)
        print("TEST 3: OpenMeteo forecast API - Available Parameters")
        print("="*80)

        # Start with basic parameters
        start_date = date.today()
        end_date = start_date + timedelta(days=7)

        # Try to request all possible hourly variables
        # Based on OpenMeteo docs, these might include lightning/storm data
        hourly_params = [
            'temperature_2m',
            'precipitation',
            'precipitation_probability',
            'weathercode',  # Weather condition code (might indicate thunderstorms)
            'cape',  # Convective Available Potential Energy (thunderstorm indicator)
            'lightning_potential',  # If available
            'lifted_index',  # Atmospheric stability index
        ]

        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.lat}&longitude={self.lon}"
            f"&hourly={','.join(hourly_params)}"
            f"&timezone=auto"
            f"&start_date={start_date}&end_date={end_date}"
        )

        print(f"\nTrying URL with multiple parameters...")
        print(f"Requested params: {hourly_params}")

        data = self.fetch_json(url)

        if data:
            print("\n✅ Successfully fetched data!")
            if 'hourly' in data:
                print(f"\nAvailable hourly fields returned:")
                for key in data['hourly'].keys():
                    print(f"  - {key}")

                # Show sample data for storm-related fields
                if 'cape' in data['hourly']:
                    print("\n  CAPE (Convective Available Potential Energy) - first 5 values:")
                    print(f"    {data['hourly']['cape'][:5]}")

                if 'weathercode' in data['hourly']:
                    print("\n  Weather code - first 5 values:")
                    print(f"    {data['hourly']['weathercode'][:5]}")
                    print("    Note: Codes 95-99 typically indicate thunderstorms")

            if 'error' in data or 'reason' in data:
                print(f"\n⚠️  API returned error: {data}")
        else:
            print("\n❌ Failed to fetch data")

    def test_openmeteo_weather_codes(self):
        """
        Test 4: Get weather codes and identify thunderstorm conditions.

        OpenMeteo weather codes:
        - 95: Thunderstorm, slight or moderate
        - 96, 99: Thunderstorm with slight/heavy hail

        This can help us identify when lightning is likely.
        """
        print("\n" + "="*80)
        print("TEST 4: OpenMeteo Weather Codes for Thunderstorm Detection")
        print("="*80)

        start_date = date.today()
        end_date = start_date + timedelta(days=7)

        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.lat}&longitude={self.lon}"
            f"&hourly=weathercode,precipitation_probability,cape"
            f"&timezone=auto"
            f"&start_date={start_date}&end_date={end_date}"
        )

        print(f"\nFetching weather codes and related data...")
        data = self.fetch_json(url)

        if data and 'hourly' in data:
            times = data['hourly'].get('time', [])
            codes = data['hourly'].get('weathercode', [])
            precip_prob = data['hourly'].get('precipitation_probability', [])
            cape_values = data['hourly'].get('cape', [])

            print(f"\nAnalyzing {len(times)} hours of forecast data...")

            # Find potential thunderstorm hours
            thunderstorm_hours = []
            for i, (time, code) in enumerate(zip(times, codes)):
                if code in [95, 96, 99]:  # Thunderstorm codes
                    prob = precip_prob[i] if i < len(precip_prob) else 'N/A'
                    cape = cape_values[i] if i < len(cape_values) else 'N/A'
                    thunderstorm_hours.append({
                        'time': time,
                        'code': code,
                        'precip_prob': prob,
                        'cape': cape
                    })

            if thunderstorm_hours:
                print(f"\n⚡ Found {len(thunderstorm_hours)} hours with thunderstorm forecast:")
                for hour in thunderstorm_hours[:5]:  # Show first 5
                    print(f"  {hour}")
            else:
                print("\n  No thunderstorms in the 7-day forecast")
                print("  Showing first 10 hours of forecast:")
                for i in range(min(10, len(times))):
                    code = codes[i] if i < len(codes) else 'N/A'
                    prob = precip_prob[i] if i < len(precip_prob) else 'N/A'
                    cape = cape_values[i] if i < len(cape_values) else 'N/A'
                    print(f"    {times[i]}: code={code}, precip%={prob}, CAPE={cape}")

            # Print weather code reference
            print("\n📋 Weather Code Reference (WMO codes):")
            print("   0: Clear sky")
            print("   1-3: Mainly clear, partly cloudy, overcast")
            print("   45-48: Fog")
            print("   51-67: Rain/drizzle variations")
            print("   71-77: Snow")
            print("   80-82: Rain showers")
            print("   85-86: Snow showers")
            print("   95: ⚡ Thunderstorm slight or moderate")
            print("   96, 99: ⚡ Thunderstorm with hail")


class TestLightningSummary(unittest.TestCase):
    """Summary test to compare both APIs."""

    def test_create_lightning_data_summary(self):
        """
        Test 5: Create a summary of findings from both APIs.

        This will help us decide which API to use for lightning probability.
        """
        print("\n" + "="*80)
        print("TEST 5: LIGHTNING DATA AVAILABILITY SUMMARY")
        print("="*80)

        print("\n📊 NWS API (National Weather Service):")
        print("  Pros:")
        print("    - US-specific, high accuracy")
        print("    - Free, no API key required")
        print("    - Already used in the project")
        print("  Cons:")
        print("    - May not have direct 'lightning probability' field")
        print("    - Need to check gridData endpoint for storm data")
        print("  Data to explore:")
        print("    - forecastGridData endpoint")
        print("    - Look for thunder/lightning fields")
        print("    - May have probabilityOfThunder")

        print("\n📊 OpenMeteo API:")
        print("  Pros:")
        print("    - Global coverage")
        print("    - Free tier available")
        print("    - Weather codes clearly indicate thunderstorms")
        print("    - CAPE values (thunderstorm indicator)")
        print("  Cons:")
        print("    - No direct 'lightning_probability' percentage")
        print("    - Must infer from weather codes (95, 96, 99)")
        print("  Available data:")
        print("    - weathercode (95-99 = thunderstorm)")
        print("    - precipitation_probability")
        print("    - CAPE (convective available potential energy)")

        print("\n💡 RECOMMENDATIONS:")
        print("  1. Check NWS gridData endpoint first - it might have direct lightning data")
        print("  2. If not available, use OpenMeteo weather codes + CAPE as proxy")
        print("  3. Consider combining both:")
        print("     - NWS for primary forecast")
        print("     - OpenMeteo weather codes as backup/supplement")
        print("  4. CAPE > 1000 J/kg often indicates thunderstorm potential")
        print("  5. Weather code 95-99 = definite thunderstorm forecast")

        self.assertTrue(True)  # Always pass, this is informational


if __name__ == '__main__':
    # Run tests with verbose output
    unittest.main(verbosity=2)
