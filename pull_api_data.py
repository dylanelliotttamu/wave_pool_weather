#!/usr/bin/env python3
# TEST
# Python script to pull NWS api data from selected station

# Import necessary libraries
import numpy as np
import urllib.request
import json
from datetime import datetime
from datetime import date, timedelta
import time

lat, lon = 31.6212, -97.0037 # Waco, TX

def fetch_json_from_url(url):
    with urllib.request.urlopen(url) as response:
        global data_1
        data_1 = json.loads(response.read().decode())
        # print(data_1)
        return data_1
    
def get_hourly_forecast_url(input_data):
    try:
        global hourly_forecast_url
        hourly_forecast_url = input_data["properties"]["forecastHourly"]
        return hourly_forecast_url
    except KeyError as e:
        print(f"KeyError: {e}")

#given lat, lon of a wave pool (US), retrieve the hourly forecast link
def retrieve_the_hourly_url_given_only_lat_and_lon(input_lat,input_lon):
    fetch_json_from_url(f'https://api.weather.gov/points/{input_lat},{input_lon}')
    # https://api.weather.gov/points/31.6212,-97.0037
    # https://api.weather.gov/gridpoints/FWD/81,53/forecast/hourly

    get_hourly_forecast_url(data_1)
    print('hourly_forecast_url = ', hourly_forecast_url)    
    return hourly_forecast_url

# Use url and return data 
def request_data(url, retries=3, delay=2):
    
    for attempt in range(retries):
    
        try:
            with urllib.request.urlopen(url) as response: #url:
                #data = json.loads(url.read().decode())
                global hourly_weather_json_data
                hourly_weather_json_data = json.loads(response.read().decode())
                return hourly_weather_json_data
 
                # returns 7-days of hourly data
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}") 
            print(f"Error requesting data: {e}")
            if attempt < retries -1:
                time.sleep(delay)
            else:
                print(f"Failed to retrieve data after {retries} attempts.")
                return None

# frequency this script is ran will be determined by another script that will call this one

# Main function to pull data from NWS api
def pull_api_temp_data_main(lat, lon,timestep_in_hours):
    try:
        # pull data from NWS api
        if timestep_in_hours == 1:
            # url_hourly = f'https://api.weather.gov/gridpoints/SGX/{lat},{lon}/forecast/hourly' 
            # https://api.weather.gov/gridpoints/FWD/81,53/forecast/hourly
            
            retrieve_the_hourly_url_given_only_lat_and_lon(lat,lon)
            # print('hourly_forecast_url ',hourly_forecast_url)
            request_data(hourly_forecast_url)

            parse_weather_data(hourly_weather_json_data)

        else:
            print('Invalid timestep')
    except KeyError as e:
        print(f"KeyError: {e}")
        print('Error pulling data from NWS api')

def parse_weather_data(inputhourlyjsonweather_data):
    try:  
        # Access periods
        periods = inputhourlyjsonweather_data["properties"]["periods"]
        
        # Dictionary to hold aggregated data by day
        global daily_data
        daily_data = {}

        for period in periods:
            start_time = period["startTime"]
            date = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S%z").date()
            temperature = period["temperature"]
            humidity = period["relativeHumidity"]["value"]

            if date not in daily_data:
                daily_data[date] = {"temperatures": [], "humidities": []}
            
            daily_data[date]["temperatures"].append(temperature)
            daily_data[date]["humidities"].append(humidity)

        # store the average temperature and humidity I calculated for each day
        global average_temp_list
        global average_humidity_list
        global date_list

        date_list = []
        average_temp_list = []
        average_humidity_list = []

        # Calculate and print daily averages
        for date, data in daily_data.items():
            # avg_temperature = sum(data["temperatures"]) / len(data["temperatures"])
            avg_temperature = ( max(data["temperatures"]) + min(data["temperatures"]) ) / 2 

            avg_humidity = sum(data["humidities"]) / len(data["humidities"])
            # print(f"Date: {date}, Average Temperature: {avg_temperature:.2f}°F")

            #if len(data["temperatures"]) < 18:
            #    # print('not enough data for this day, it will not be appended to average_temp_list ', date)
            #    continue

            # save the date also to a list
            date_list.append(date)
            average_temp_list.append(avg_temperature)
            average_humidity_list.append(avg_humidity)
        # Round to 2 decimal places
        average_temp_list_rounded = [round(i,2) for i in average_temp_list]
        average_humidity_list_rounded = [round(i,2) for i in average_humidity_list]
        # print('average humidity list rounded ', average_humidity_list_rounded)
        # print('average temp list rounded ', average_temp_list_rounded)
        
        # print('date list ', date_list)

    except Exception as e:
        print(f"Error parsing data: {e}")


'''

Begin main part of script and call functions

'''

# Process:
# If new day: 
# Read in past2days air temp file and replace date and associated temp of 1day ago to 2 day ago
# Read in forecasted air temp file and take the 1st entry and replace with 1 day ago
# Pull api data and calculate forecasted pool temps and write to forecasted pool temps file

################### STEP 1: Get last two days of air temperature data ###################

# Read the file
with open('past_two_days_air_temp.txt', 'r') as file:
    lines = file.readlines()
    # print('lines ',lines)

# Parse the data
one_day_ago_date_in_past_two_days_air_temp_file, one_day_ago_air_temp_in_past_two_days_air_temp_file = lines[0].strip().split(',')
two_days_ago_date_in_past_two_days_air_temp_file, two_days_ago_air_temp_in_past_two_days_air_temp_file = lines[1].strip().split(',')

# print('One day ago:')
# print(f"Date: {one_day_ago_date_in_past_two_days_air_temp_file}, Air Temp: {one_day_ago_air_temp_in_past_two_days_air_temp_file}")
# make one_day_ago_air_temp_in_past_two_days_air_temp_file a float
one_day_ago_air_temp_in_past_two_days_air_temp_file = float(one_day_ago_air_temp_in_past_two_days_air_temp_file)

# print('Two days ago:')
# print(f"Date: {two_days_ago_date_in_past_two_days_air_temp_file}, Air Temp: {two_days_ago_air_temp_in_past_two_days_air_temp_file}")
# make two_day_ago_air_temp_in_past_two_days_air_temp_file a float
two_days_ago_air_temp_in_past_two_days_air_temp_file = float(two_days_ago_air_temp_in_past_two_days_air_temp_file)


current_date = datetime.now().date() 

today_pool_temp = np.average([two_days_ago_air_temp_in_past_two_days_air_temp_file, one_day_ago_air_temp_in_past_two_days_air_temp_file])


################### STEP 1 DONE  ###################
# STEP 2: pull the forecasted air temperature data for the week

pull_api_temp_data_main(lat, lon, 1)

# calculate future pool temps
# SAVE IN A LIST 
# forecasted_temps = []
# for i in range(1, 8):
#     forecasted_temp = (average_temp_list[i] + average_temp_list[i-1]) / 2
#     forecasted_temps.append(forecasted_temp)

# No need for a list right now
forecasted_1daysinfuture_pool_temp = ( average_temp_list[0] + one_day_ago_air_temp_in_past_two_days_air_temp_file ) / 2 
forecasted_2daysinfuture_pool_temp = ( average_temp_list[1] + average_temp_list[0] ) / 2
forecasted_3daysinfuture_pool_temp = ( average_temp_list[2] + average_temp_list[1] ) / 2
forecasted_4daysinfuture_pool_temp = ( average_temp_list[3] + average_temp_list[2] ) / 2
forecasted_5daysinfuture_pool_temp = ( average_temp_list[4] + average_temp_list[3] ) / 2
forecasted_6daysinfuture_pool_temp = ( average_temp_list[5] + average_temp_list[4] ) / 2

# test
print('len(date_list) ', len(date_list))

if len(date_list) > 6:
    forecasted_7daysinfuture_pool_temp = ( average_temp_list[6] + average_temp_list[5] ) / 2

################### STEP 2 DONE  ###################
# STEP 3: while still have 1 day ago data, and now i have today's air temp, overwrite two days ago
# (to set up for rerunning script tomorrow)

# Convert string dates to datetime.date objects
one_day_ago_date = datetime.strptime(one_day_ago_date_in_past_two_days_air_temp_file, '%Y-%m-%d').date()

# Calculate one day after one_day_ago_date
one_day_after = one_day_ago_date + timedelta(days=1)

# Compare current_date with one_day_after
if current_date >= one_day_after:
# if current_date >= (one_day_ago_date_in_past_two_days_air_temp_file + timedelta(days=1)) : # should always be true, but just in case
    # later I could add if its greater than two days, go retrieve the historical data from two days ago
    # print('New day, updating past two days air temp file')
    # Write the new data to the file
    with open('past_two_days_air_temp.txt', 'w') as file:
        file.write(f"{current_date},{average_temp_list[0]}\n") # today's air temp moved to file, SHOULD BE AVERAGE_TEMP_LIST[0]
        file.write(f"{one_day_ago_date_in_past_two_days_air_temp_file},{one_day_ago_air_temp_in_past_two_days_air_temp_file}\n") 
        # yesterday's air temp moved to become two days ago
# else: 
    # print('Not a new day, not updating past two days air temp file')
    
    # raise ValueError('Not a new day, not updating past two days air temp file')

################### STEP 3 DONE  ###################
# STEP4: Write the forecasted air temp data to a text file to save if I put air temps on the website later
# But definitely write the forecasted pool temps to a file

with open('forecasted_air_temp.txt', 'w') as file:
    for i in range(len(average_temp_list)):
        file.write(f"{date_list[i]},{average_temp_list[i]}\n")
    # close the file 
    file.close()

# write the forecaste future pool temps to a file
with open('forecasted_pool_temps.txt', 'w') as file:
    file.write(f"{current_date},{today_pool_temp}\n")
    file.write(f"{date_list[1]},{forecasted_1daysinfuture_pool_temp}\n")
    file.write(f"{date_list[2]},{forecasted_2daysinfuture_pool_temp}\n")
    file.write(f"{date_list[3]},{forecasted_3daysinfuture_pool_temp}\n")
    file.write(f"{date_list[4]},{forecasted_4daysinfuture_pool_temp}\n")
    file.write(f"{date_list[5]},{forecasted_5daysinfuture_pool_temp}\n")
    if len(date_list) > 6:
        file.write(f"{date_list[6]},{forecasted_6daysinfuture_pool_temp}\n")
    if len(date_list) > 7:
        file.write(f"{date_list[7]},{forecasted_7daysinfuture_pool_temp}\n")
    # close the file
    file.close()

################### STEP 4 DONE  ###################

print('Ran without error')

# If there is an error, then the website will show persistence 

# done backend
