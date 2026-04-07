# Wave Pool Weather

Wave Pool Weather is a lightweight weather analysis and forecasting website built around the reality of Central Texas extreme weather and outdoor pool conditions.

The project tracks air temperature, humidity, wind, and expected pool temperatures across multiple locations, with a focus on helping outdoor water and wave pool enthusiasts make better decisions during volatile weather.

## Purpose

Central Texas weather can change rapidly, and this site was created from living in that environment and needing a practical way to monitor how those swings affect pool comfort.

Built for wave pool fans and outdoor aquatic venues, the site is designed to:
  - present a visual dashboard for weather and pool forecasts,
  - support multiple pool locations,
  - store data in a database for historical tracking,
  - surface humidity and wind alongside temperature signals.

## Current Functionality

This repository currently includes:
  - `scripts/pull_api_data.py`: pulls forecast data from the National Weather Service API and aggregates it by day.
  - `data/wave_pool_weather.db`: an SQLite database schema that stores locations, air temperature, humidity, wind speed, wind direction, and pool temperature forecasts.
  - `pool_temp_forecasts.html`: the renamed forecast page for pool temperature predictions.
  - `dashboard.html`: a dashboard page with charts for air temperature, humidity, wind speed, pool temperature, and a wind rose.
  - `data/forecasts/`: exported forecasted pool temperature text files for each supported location.

Supported locations:
  - Waco, Texas
  - Palm Springs, California
  - Lemoore, California
  - Atlantic Park Virginia Beach, Virginia
  - Oceanside, California

## Recent Updates

Recent changes in the project include:
  - renaming the pool forecast page from `waco_water_temp.html` to `pool_temp_forecasts.html`,
  - updating navigation labels across the site to `Pool Temperature Forecasts`,
  - adding future-ready multi-location support,
  - expanding the database to include humidity, wind speed, and wind direction,
  - converting forecast storage from text-only to SQLite-backed records,
  - improving dashboard generation with real data and chart visualizations.

## Future Improvements

Next steps for the project:
  - build a full climatology view for Central Texas pool temperatures,
  - generate real wind rose figures from wind direction data,
  - extend the database with more weather variables and historical records,
  - improve site UX, add favicons, and polish mobile responsiveness,
  - automate scheduled data updates and strengthen error handling.

## Getting Started

1. Run `python3 scripts/pull_api_data.py` to fetch the latest weather and forecast data.
2. Open `dashboard.html` and `pool_temp_forecasts.html` in your browser.
3. Review data in `data/wave_pool_weather.db` or exported forecast files under `data/forecasts/`.

## Notes

This project is centered on practical forecasting for outdoor water venues in regions with strong weather swings, with the first implementation built from the experience of daily Texas weather variability.