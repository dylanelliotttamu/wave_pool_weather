#!/usr/bin/env python3
# Import necessary libraries
import urllib.request
import json
from datetime import datetime, date, timedelta
import time
import sqlite3
import os
import json

todays_date = date.today()
todays_time = datetime.now().time()

print("Starting data pull from NWS API...")
print(f"Today's date: {todays_date}")
print(f"Current time: {todays_time}")

# Locations dictionary
locations = {
    'Waco': {'lat': 31.6212, 'lon': -97.0037},
    'Palm Springs': {'lat': 33.8303, 'lon': -116.5453},
    'Lemoore': {'lat': 36.3008, 'lon': -119.7829},
    'Atlantic Park Virginia Beach': {'lat': 36.8529, 'lon': -75.9779},
    'Oceanside': {'lat': 33.1959, 'lon': -117.3795},
}

# Direction to degrees
direction_map = {
    'N': 0, 'NNE': 22.5, 'NE': 45, 'ENE': 67.5,
    'E': 90, 'ESE': 112.5, 'SE': 135, 'SSE': 157.5,
    'S': 180, 'SSW': 202.5, 'SW': 225, 'WSW': 247.5,
    'W': 270, 'WNW': 292.5, 'NW': 315, 'NNW': 337.5
}

# Database setup
DATA_DIR = '/var/www/html/data'
FORECASTS_DIR = '/var/www/html/data/forecasts'
SITE_ROOT = '/var/www/html'

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(FORECASTS_DIR, exist_ok=True)
conn = sqlite3.connect(os.path.join(DATA_DIR, 'wave_pool_weather.db'))
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE,
    lat REAL,
    lon REAL
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS air_temps (
    location_id INTEGER,
    date TEXT,
    temp REAL,
    humidity REAL,
    wind_speed REAL,
    wind_direction REAL,
    FOREIGN KEY(location_id) REFERENCES locations(id),
    UNIQUE(location_id, date)
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS pool_temps (
    location_id INTEGER,
    date TEXT,
    temp REAL,
    FOREIGN KEY(location_id) REFERENCES locations(id),
    UNIQUE(location_id, date)
)''')

# Insert locations if not exist
for name, coords in locations.items():
    cursor.execute('INSERT OR IGNORE INTO locations (name, lat, lon) VALUES (?, ?, ?)', (name, coords['lat'], coords['lon']))

conn.commit()

def fetch_json_from_url(url):
    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read().decode())
        return data
    
def get_hourly_forecast_url(input_data):
    try:
        hourly_forecast_url = input_data["properties"]["forecastHourly"]
        return hourly_forecast_url
    except KeyError as e:
        print(f"KeyError: {e}")
        return None

# Given lat, lon of a wave pool (US), retrieve the hourly forecast link
def retrieve_the_hourly_url_given_only_lat_and_lon(input_lat, input_lon):
    data_1 = fetch_json_from_url(f'https://api.weather.gov/points/{input_lat},{input_lon}')
    hourly_forecast_url = get_hourly_forecast_url(data_1)
    print('hourly_forecast_url = ', hourly_forecast_url)    
    return hourly_forecast_url

# Use url and return data (returns 7-days of hourly data)
def request_data(url, retries=3, delay=2):
    hourly_weather_json_data = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url) as response:
                hourly_weather_json_data = json.loads(response.read().decode())
                return hourly_weather_json_data
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                print(f"Failed to retrieve data after {retries} attempts.")
                return None

# frequency this script is ran will be determined by another script that will call this one

# Main function to pull data from NWS api
def pull_api_temp_data_main(lat, lon, timestep_in_hours):
    try:
        # pull data from NWS api
        if timestep_in_hours == 1:
            hourly_forecast_url = retrieve_the_hourly_url_given_only_lat_and_lon(lat, lon)
            if hourly_forecast_url:
                hourly_weather_json_data = request_data(hourly_forecast_url)
                if hourly_weather_json_data:
                    return parse_weather_data(hourly_weather_json_data)
        else:
            print('Invalid timestep')
    except Exception as e:
        print(f"Error pulling data from NWS api: {e}")
    return None

def parse_weather_data(inputhourlyjsonweather_data):
    print(f"Data keys: {inputhourlyjsonweather_data.keys()}")
    try:  
        # Access periods
        periods = inputhourlyjsonweather_data["properties"]["periods"]
        print(f"Periods type: {type(periods)}, len: {len(periods) if hasattr(periods, '__len__') else 'N/A'}")
        
        # Check if periods is list
        if not isinstance(periods, list):
            print(f"Unexpected periods type: {type(periods)}, content: {periods}")
            return None
        
        # Dictionary to hold aggregated data by day
        daily_data = {}

        for period in periods:
            start_time = period["startTime"]
            date_obj = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S%z").date()
            temperature = float(period["temperature"]["value"] if isinstance(period["temperature"], dict) else period["temperature"])
            humidity = float(period["relativeHumidity"]["value"] if isinstance(period["relativeHumidity"], dict) else period["relativeHumidity"])
            wind_str = period["windSpeed"]["value"] if isinstance(period["windSpeed"], dict) else period["windSpeed"]
            wind_speed = float(wind_str.split()[0]) if isinstance(wind_str, str) else float(wind_str)
            wind_direction_str = period["windDirection"]["value"] if isinstance(period["windDirection"], dict) else period["windDirection"]
            wind_direction = direction_map.get(wind_direction_str, 0)  # default to 0 if unknown

            if date_obj not in daily_data:
                daily_data[date_obj] = {"temperatures": [], "humidities": [], "wind_speeds": [], "wind_directions": []}
            
            daily_data[date_obj]["temperatures"].append(temperature)
            daily_data[date_obj]["humidities"].append(humidity)
            daily_data[date_obj]["wind_speeds"].append(wind_speed)
            daily_data[date_obj]["wind_directions"].append(wind_direction)

        date_list = []
        average_temp_list = []
        average_humidity_list = []
        average_wind_list = []
        average_wind_direction_list = []

        for date_obj in sorted(daily_data.keys()):
            data = daily_data[date_obj]
            avg_temperature = (max(data["temperatures"]) + min(data["temperatures"])) / 2 
            avg_humidity = sum(data["humidities"]) / len(data["humidities"])
            avg_wind = sum(data["wind_speeds"]) / len(data["wind_speeds"])
            avg_wind_dir = sum(data["wind_directions"]) / len(data["wind_directions"])
            
            date_list.append(date_obj)
            average_temp_list.append(avg_temperature)
            average_humidity_list.append(avg_humidity)
            average_wind_list.append(avg_wind)
            average_wind_direction_list.append(avg_wind_dir)
        
        return {
            'date_list': date_list,
            'average_temp_list': average_temp_list,
            'average_humidity_list': average_humidity_list,
            'average_wind_list': average_wind_list,
            'average_wind_direction_list': average_wind_direction_list
        }
    except Exception as e:
        print(f"Error parsing data: {e}")
        return None


'''

Begin main part of script and call functions

'''

# Main execution
current_date = date.today()

for name, coords in locations.items():
    lat, lon = coords['lat'], coords['lon']
    print(f"Processing {name}...")
    
    cursor.execute('SELECT id FROM locations WHERE name=?', (name,))
    location_id = cursor.fetchone()[0]
    
    data = pull_api_temp_data_main(lat, lon, 1)
    if not data:
        print(f"Failed to get data for {name}")
        continue
    
    # Insert forecasted air temps
    for i in range(len(data['date_list'])):
        cursor.execute('INSERT OR REPLACE INTO air_temps (location_id, date, temp, humidity, wind_speed, wind_direction) VALUES (?, ?, ?, ?, ?, ?)',
            (location_id, str(data['date_list'][i]), data['average_temp_list'][i], data['average_humidity_list'][i], data['average_wind_list'][i], data['average_wind_direction_list'][i]))
    
    # Calculate pool temps
    yesterday = current_date - timedelta(days=1)
    two_days_ago = current_date - timedelta(days=2)
    
    cursor.execute('SELECT temp FROM air_temps WHERE location_id=? AND date=?', (location_id, str(two_days_ago)))
    two_days_temp = cursor.fetchone()
    cursor.execute('SELECT temp FROM air_temps WHERE location_id=? AND date=?', (location_id, str(yesterday)))
    one_day_temp = cursor.fetchone()
    
    if two_days_temp and one_day_temp:
        today_pool_temp = (two_days_temp[0] + one_day_temp[0]) / 2
        cursor.execute('INSERT OR REPLACE INTO pool_temps (location_id, date, temp) VALUES (?, ?, ?)', (location_id, str(current_date), today_pool_temp))
    
    # Forecasted pool temps
    for i in range(len(data['date_list'])):
        if i == 0:
            prev_temp = one_day_temp[0] if one_day_temp else data['average_temp_list'][0]
            forecasted_pool = (data['average_temp_list'][0] + prev_temp) / 2
        else:
            forecasted_pool = (data['average_temp_list'][i] + data['average_temp_list'][i-1]) / 2
        cursor.execute('INSERT OR REPLACE INTO pool_temps (location_id, date, temp) VALUES (?, ?, ?)', (location_id, str(data['date_list'][i]), forecasted_pool))

# Export forecasted pool temps for all locations to text files
for name in locations.keys():
    cursor.execute('SELECT date, temp FROM pool_temps WHERE location_id = (SELECT id FROM locations WHERE name = ?) ORDER BY date', (name,))
    rows = cursor.fetchall()
    filename = os.path.join(FORECASTS_DIR, f'forecasted_pool_temps_{name.replace(" ", "_")}.txt')
    with open(filename, 'w') as f:
        for row in rows:
            f.write(f"{row[0]},{row[1]}\n")

# Keep the old Waco file for backward compatibility
cursor.execute('SELECT date, temp FROM pool_temps WHERE location_id = (SELECT id FROM locations WHERE name = "Waco") ORDER BY date')
pool_rows = cursor.fetchall()
with open(os.path.join(FORECASTS_DIR, 'forecasted_pool_temps.txt'), 'w') as f:
    for row in pool_rows:
        f.write(f"{row[0]},{row[1]}\n")

# Generate dashboard.html with real data
dashboard_location = 'Waco'

cursor.execute('SELECT date, temp, humidity, wind_speed FROM air_temps WHERE location_id = (SELECT id FROM locations WHERE name = ?) ORDER BY date', (dashboard_location,))
air_rows = cursor.fetchall()
dates = [row[0] for row in air_rows]
air_temps = [row[1] for row in air_rows]
humidities = [row[2] for row in air_rows]
wind_speeds = [row[3] for row in air_rows]

cursor.execute('SELECT date, temp FROM pool_temps WHERE location_id = (SELECT id FROM locations WHERE name = ?) ORDER BY date', (dashboard_location,))
dashboard_pool_rows = cursor.fetchall()
pool_temps = [row[1] for row in dashboard_pool_rows]

# For wind rose, mock for now
bins = [10, 20, 15, 5, 10, 5, 15, 20]

dashboard_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Wave Pool Weather Dashboard</title>
    <link rel="icon" href="favicon.ico">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{
            font-family: 'Lato', sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f4f4f4;
        }}
        .navbar {{
            background-color: #222;
            overflow: hidden;
            width: 100%;
            display: flex;
            justify-content: center;
            position: sticky;
            top: 0;
            z-index: 1000;
        }}
        .navbar a {{
            color: #ccc;
            text-align: center;
            padding: 14px 20px;
            text-decoration: none;
            font-size: 17px;
            transition: color 0.3s ease;
        }}
        .navbar a:hover {{
            color: #8db600;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1 {{
            text-align: center;
            color: #333;
        }}
        .chart-container {{
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            margin: 20px 0;
            padding: 20px;
        }}
        .chart {{
            width: 100%;
            height: 400px;
        }}
    </style>
</head>
<body>
    <!-- Navigation Bar -->
    <div class="navbar">
        <a href="index.html">Home</a>
        <a href="pool_temp_forecasts.html">Pool Temperature Forecasts</a>
        <a href="news.html">Wave Pool News</a>
        <a href="about_contact.html">About/Contact</a>
        <a href="dashboard.html">Dashboard</a>
    </div>

    <div class="container">
        <h1>Wave Pool Weather Dashboard - {dashboard_location}</h1>

        <div class="chart-container">
            <h2>{dashboard_location} Pool Temperature Forecast</h2>
            <canvas id="poolTempChart" class="chart"></canvas>
        </div>

        <div class="chart-container">
            <h2>{dashboard_location} Air Temperature Forecast</h2>
            <canvas id="airTempChart" class="chart"></canvas>
        </div>

        <div class="chart-container">
            <h2>{dashboard_location} Humidity Forecast</h2>
            <canvas id="humidityChart" class="chart"></canvas>
        </div>

        <div class="chart-container">
            <h2>{dashboard_location} Wind Speed Forecast</h2>
            <canvas id="windChart" class="chart"></canvas>
        </div>

        <div class="chart-container">
            <h2>{dashboard_location} Wind Rose</h2>
            <canvas id="windRoseChart" class="chart"></canvas>
        </div>
    </div>

    <script>
        const chartLocation = {json.dumps(dashboard_location)};
        const realData = {{
            dates: {dates},
            poolTemps: {pool_temps},
            airTemps: {air_temps},
            humidities: {humidities},
            windSpeeds: {wind_speeds},
            windBins: {bins}
        }};

        // Pool Temp Chart
        new Chart(document.getElementById('poolTempChart'), {{
            type: 'line',
            data: {{
                labels: realData.dates,
                datasets: [{{
                    label: 'Pool Temperature (°F)',
                    data: realData.poolTemps,
                    borderColor: 'blue',
                    fill: false
                }}]
            }},
            options: {{
                plugins: {{
                    title: {{
                        display: true,
                        text: `${{chartLocation}} Pool Temperature Forecast (°F)`
                    }}
                }},
                scales: {{
                    x: {{
                        title: {{
                            display: true,
                            text: 'Date'
                        }}
                    }},
                    y: {{
                        title: {{
                            display: true,
                            text: 'Pool Temperature (°F)'
                        }}
                    }}
                }}
            }}
        }});

        // Air Temp Chart
        new Chart(document.getElementById('airTempChart'), {{
            type: 'line',
            data: {{
                labels: realData.dates,
                datasets: [{{
                    label: 'Air Temperature (°F)',
                    data: realData.airTemps,
                    borderColor: 'red',
                    fill: false
                }}]
            }},
            options: {{
                plugins: {{
                    title: {{
                        display: true,
                        text: `${{chartLocation}} Air Temperature Forecast (°F)`
                    }}
                }},
                scales: {{
                    x: {{
                        title: {{
                            display: true,
                            text: 'Date'
                        }}
                    }},
                    y: {{
                        title: {{
                            display: true,
                            text: 'Air Temperature (°F)'
                        }}
                    }}
                }}
            }}
        }});

        // Humidity Chart
        new Chart(document.getElementById('humidityChart'), {{
            type: 'bar',
            data: {{
                labels: realData.dates,
                datasets: [{{
                    label: 'Humidity (%)',
                    data: realData.humidities,
                    backgroundColor: 'green'
                }}]
            }},
            options: {{
                plugins: {{
                    title: {{
                        display: true,
                        text: `${{chartLocation}} Humidity Forecast (%)`
                    }}
                }},
                scales: {{
                    x: {{
                        title: {{
                            display: true,
                            text: 'Date'
                        }}
                    }},
                    y: {{
                        title: {{
                            display: true,
                            text: 'Humidity (%)'
                        }}
                    }}
                }}
            }}
        }});

        // Wind Speed Chart
        new Chart(document.getElementById('windChart'), {{
            type: 'line',
            data: {{
                labels: realData.dates,
                datasets: [{{
                    label: 'Wind Speed (mph)',
                    data: realData.windSpeeds,
                    borderColor: 'orange',
                    fill: false
                }}]
            }},
            options: {{
                plugins: {{
                    title: {{
                        display: true,
                        text: `${{chartLocation}} Wind Speed Forecast (mph)`
                    }}
                }},
                scales: {{
                    x: {{
                        title: {{
                            display: true,
                            text: 'Date'
                        }}
                    }},
                    y: {{
                        title: {{
                            display: true,
                            text: 'Wind Speed (mph)'
                        }}
                    }}
                }}
            }}
        }});

        // Wind Rose
        new Chart(document.getElementById('windRoseChart'), {{
            type: 'polarArea',
            data: {{
                labels: ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'],
                datasets: [{{
                    label: 'Wind Direction Frequency',
                    data: realData.windBins,
                    backgroundColor: [
                        'rgba(255, 99, 132, 0.5)',
                        'rgba(54, 162, 235, 0.5)',
                        'rgba(255, 205, 86, 0.5)',
                        'rgba(75, 192, 192, 0.5)',
                        'rgba(153, 102, 255, 0.5)',
                        'rgba(255, 159, 64, 0.5)',
                        'rgba(199, 199, 199, 0.5)',
                        'rgba(83, 102, 255, 0.5)'
                    ]
                }}]
            }},
            options: {{
                plugins: {{
                    title: {{
                        display: true,
                        text: `${{chartLocation}} Wind Direction Frequency` 
                    }}
                }},
                scales: {{
                    r: {{
                        title: {{
                            display: true,
                            text: 'Frequency (count)'
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>'''

with open(os.path.join(SITE_ROOT, 'dashboard.html'), 'w') as f:
    f.write(dashboard_html)
conn.commit()
conn.close()

print('Script completed successfully')
