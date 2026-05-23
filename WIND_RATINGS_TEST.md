# Wind Ratings Test Guide

## Quick Start

### Option 1: View Test HTML Locally
Open `test_wind_ratings.html` in your browser to see the visual test without running the backend:
```bash
open test_wind_ratings.html
# or
python3 -m http.server 8000  # then visit http://localhost:8000/test_wind_ratings.html
```

### Option 2: Test with Forecast Data Files
Replace the production forecast files temporarily:
```bash
# Backup original
cp data/forecasts/forecasted_weather_Waco.txt data/forecasts/forecasted_weather_Waco.txt.backup
cp data/forecasts/forecasted_pool_temps_Waco.txt data/forecasts/forecasted_pool_temps_Waco.txt.backup

# Use test data
cp data/forecasts/forecasted_weather_Waco_test.txt data/forecasts/forecasted_weather_Waco.txt
cp data/forecasts/forecasted_pool_temps_Waco_test.txt data/forecasts/forecasted_pool_temps_Waco.txt

# View in browser (requires local web server)
python3 -m http.server 8000
# Visit http://localhost:8000/pool_temp_forecasts.html?pool=Waco

# Restore original
cp data/forecasts/forecasted_weather_Waco.txt.backup data/forecasts/forecasted_weather_Waco.txt
cp data/forecasts/forecasted_pool_temps_Waco.txt.backup data/forecasts/forecasted_pool_temps_Waco.txt
```

### Option 3: Run Calculation Tests
Verify the wind rating calculations are correct:
```bash
python3 scripts/test_wind_ratings.py
```

## Test Scenarios

### May 21 - E Winds (90 degrees)
**Expected Results:**
- **Rights**: Air wind = Excellent | Barrel wind = (hidden)
- **Lefts**: Air wind = Excellent | Barrel wind = (hidden)

**Why**: E winds are the peak for air generation on BOTH breaks. Perfect for airs, terrible for barrels.

---

### May 22 - SSE Winds (150 degrees)
**Expected Results:**
- **Rights**: Barrel wind = Excellent | Air wind = Excellent
- **Lefts**: (no positive ratings)

**Why**: 
- For Rights: 150 degrees is exactly ideal barrel (perfect match). Also within air range (90-180), so air is excellent.
- For Lefts: 150 degrees is 105 degrees from ideal barrel (45). Too far. Outside Lefts air range (0-90).

---

### May 23 - NE Winds (45 degrees)
**Expected Results:**
- **Rights**: Air wind = Fair | Barrel wind = (hidden)
- **Lefts**: Barrel wind = Excellent | Air wind = Excellent

**Why**:
- For Lefts: 45 degrees is EXACTLY ideal barrel (perfect!). Within air range (0-90) so air is excellent too.
- For Rights: 45 degrees is 105 degrees from ideal barrel. Outside fair threshold. But 45 degrees is exactly 45 degrees from air range start (90), which puts it at Fair threshold.

---

### May 24 - S Winds (180 degrees)
**Expected Results:**
- **Rights**: Barrel wind = Excellent | Air wind = Excellent
- **Lefts**: (no positive ratings)

**Why**:
- For Rights: 180 degrees within barrel tolerance (150 +/- 30). Also in air range (90-180) so excellent.
- For Lefts: 180 degrees is 135 degrees from ideal barrel (45), outside fair range. Outside Lefts air range (0-90).

---

### May 25 - N Winds (0 degrees/360 degrees)
**Expected Results:**
- **Rights**: (no positive ratings)
- **Lefts**: Air wind = Excellent | Barrel wind = (hidden)

**Why**:
- For Lefts: 0 degrees at edge of air range (0-90), score 70 = Excellent. Barrel outside zone.
- For Rights: 0 degrees is 150 degrees from ideal barrel. Outside air range (90-180).

---

### May 26 - W Winds (270 degrees)
**Expected Results:**
- **Rights**: (no positive ratings)
- **Lefts**: (no positive ratings)

**Why**: W winds are poor for both breaks. Far from barrel angles and air ranges.

---

### May 27 - WSW Winds (240 degrees)
**Expected Results:**
- **Rights**: (no positive ratings)
- **Lefts**: (no positive ratings)

**Why**: 240 degrees is one of worst for airs on Rights (near on-shore perpendicular). Poor for barrel on both.

---

## Test Data Format

### forecasted_weather_Waco_test.txt
CSV format with 15 columns:
```
date,pool_temp_F,ci_low_F,ci_high_F,air_temp_F,wind_mph,wind_dir_deg,humidity,%,morning_temp_F,afternoon_temp_F,thunder_prob,%,rights_barrel_rating,rights_air_rating,lefts_barrel_rating,lefts_air_rating
```

Empty cells mean that rating was not displayed (Poor or None).

### forecasted_pool_temps_Waco_test.txt
Simple CSV with 2 columns:
```
date,pool_temp_F
```

## Running All Tests

```bash
# Test Python calculations
python3 scripts/test_wind_ratings.py

# View HTML test locally
open test_wind_ratings.html

# Or use test data with full frontend
cp data/forecasts/forecasted_weather_Waco_test.txt data/forecasts/forecasted_weather_Waco.txt
cp data/forecasts/forecasted_pool_temps_Waco_test.txt data/forecasts/forecasted_pool_temps_Waco.txt
python3 -m http.server 8000
# Visit http://localhost:8000/pool_temp_forecasts.html
```

## Expected Display

Each day card should show:
```
Wednesday
2026-05-21

78.5 F
↑ 8.5 mph E

RIGHTS
(Air wind: Excellent)

LEFTS
(Air wind: Excellent)

Boardshorts
```

Days with no positive ratings show minimal or blank ratings section.

## Troubleshooting

### Ratings not showing
1. Verify all 15 CSV columns present
2. Check HTML parser handles indices 11-14
3. Open browser console for JavaScript errors

### Calculation discrepancies  
- Run `python3 scripts/test_wind_ratings.py` to verify
- Check waco_breaks_config.json has correct wave vectors
- Verify tolerance values (currently +/- 30 degrees)

### Visual issues
- Hard refresh with Ctrl+F5 to bypass cache
- Verify rating helper functions present in HTML
- Check CSS for `.ratings-section` styling applied
