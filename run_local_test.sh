#!/bin/bash
# Local test script - writes to local_test directory instead of /var/www

export WAVE_POOL_FORCE_REFRESH=1
export WAVE_POOL_MC_SAMPLES=50

# Temporarily modify script to use local paths
python3 << 'PYTHON_EOF'
import sys
sys.path.insert(0, 'scripts')

# Override the paths
import pull_api_data as script

# Redirect output to local directory
script.WRITE_FILES = True
script.BASE_OUTPUT_DIR = '/Users/dylanelliott/Documents/GitHub/wave_pool_weather/local_test'
script.DATA_DIR = f'{script.BASE_OUTPUT_DIR}/data'
script.FORECASTS_DIR = f'{script.DATA_DIR}/forecasts'
script.SITE_ROOT = script.BASE_OUTPUT_DIR
script.DB_PATH = f'{script.DATA_DIR}/wave_pool_weather_test.db'

import os
os.makedirs(script.DATA_DIR, exist_ok=True)
os.makedirs(script.FORECASTS_DIR, exist_ok=True)

print(f"Writing to: {script.FORECASTS_DIR}")
PYTHON_EOF
