# Lightning Probability Integration Plan

## 🎯 Goal
Integrate NWS `probabilityOfThunder` data into the wave pool weather forecasting system and display it alongside the existing temperature chart improvements on the dev branch.

---

## 📊 Data Source: NWS API
Based on our testing, we found that the NWS gridData endpoint provides:
- **`probabilityOfThunder`** - Direct thunder probability (0-100%)
- **`lightningActivityLevel`** - Activity level indicators

Example data:
```json
{
  "probabilityOfThunder": {
    "values": [
      {"validTime": "2026-05-20T05:00:00+00:00/PT1H", "value": 26},
      {"validTime": "2026-05-20T06:00:00+00:00/PT3H", "value": 18},
      {"validTime": "2026-05-20T09:00:00+00:00/PT3H", "value": 10}
    ]
  }
}
```

---

## 🔧 Implementation Strategy

### Phase 1: Backend Data Collection (scripts/pull_api_data.py)

#### 1.1 Add new function to fetch thunder probability
```python
def fetch_thunder_probability_nws(lat, lon):
    """
    Fetch probabilityOfThunder from NWS gridData endpoint.
    
    Returns:
        dict: Mapping of date -> daily max thunder probability (%)
    """
    # Get grid URL from points endpoint
    points_url = f'https://api.weather.gov/points/{lat},{lon}'
    points_data = fetch_json_from_url(points_url)
    grid_url = points_data['properties']['forecastGridData']
    
    # Fetch grid data
    grid_data = fetch_json_from_url(grid_url)
    thunder_values = grid_data['properties']['probabilityOfThunder']['values']
    
    # Parse into daily max values
    daily_thunder = {}
    for entry in thunder_values:
        # Parse ISO 8601 validTime
        time_str = entry['validTime'].split('/')[0]
        date_obj = datetime.strptime(time_str, '%Y-%m-%dT%H:%M:%S%z').date()
        value = entry.get('value', 0)
        
        # Take max probability for the day
        if date_obj not in daily_thunder:
            daily_thunder[date_obj] = value
        else:
            daily_thunder[date_obj] = max(daily_thunder[date_obj], value)
    
    return daily_thunder
```

#### 1.2 Modify database schema
Add `thunder_probability` column to the `air_temps` table:

```python
# In pull_api_data.py around line 389
cursor.execute('''CREATE TABLE IF NOT EXISTS air_temps (
    location_id      INTEGER,
    date             TEXT,
    temp             REAL,
    humidity         REAL,
    wind_speed       REAL,
    wind_direction   REAL,
    solar_radiation  REAL,
    thunder_prob     REAL,        # NEW: Daily max thunder probability (%)
    FOREIGN KEY(location_id) REFERENCES locations(id),
    UNIQUE(location_id, date)
)''')

# Add migration for existing databases
for col, table, col_type in [
    ('solar_radiation', 'air_temps', 'REAL'),
    ('heat_fluxes',     'pool_temps', 'TEXT'),
    ('thunder_prob',    'air_temps', 'REAL'),  # NEW
]:
    try:
        cursor.execute(f'ALTER TABLE {table} ADD COLUMN {col} {col_type}')
    except Exception:
        pass  # column already exists
```

#### 1.3 Integrate into main data collection loop
```python
# Around line 746 in pull_api_data.py, after solar_map fetch
thunder_map = fetch_thunder_probability_nws(lat, lon)

# Store in database (around line 751)
cursor.executemany(
    'INSERT OR REPLACE INTO air_temps '
    '(location_id, date, temp, humidity, wind_speed, wind_direction, solar_radiation, thunder_prob) '
    'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    [
        (
            location_id,
            str(d),
            data['average_temp_list'][i],
            data['average_humidity_list'][i],
            data['average_wind_list'][i],
            data['average_wind_direction_list'][i],
            solar_map.get(str(d), None),
            thunder_map.get(d, None),  # NEW
        )
        for i, d in enumerate(data['date_list'])
    ]
)
```

#### 1.4 Export thunder probability to forecast files
```python
# Around line 995, modify the forecasted_weather export
cursor.execute(
    'SELECT pt.date, pt.temp, '
    'at.temp, at.wind_speed, at.wind_direction, at.humidity, at.thunder_prob '  # Added thunder_prob
    'FROM pool_temps pt '
    'JOIN air_temps at ON pt.location_id = at.location_id AND pt.date = at.date '
    'WHERE pt.location_id = (SELECT id FROM locations WHERE name = ?) AND pt.date >= ? '
    'ORDER BY pt.date',
    (name, str(current_date))
)
weather_rows = cursor.fetchall()

# Write with thunder probability
with open(weather_filename, 'w') as wf:
    for wrow in weather_rows:
        pool_temp_F = celsius_to_fahrenheit(wrow[1])
        ci_low_F    = celsius_to_fahrenheit(wrow[2]) if wrow[2] is not None else pool_temp_F
        ci_high_F   = celsius_to_fahrenheit(wrow[3]) if wrow[3] is not None else pool_temp_F
        air_temp_F  = celsius_to_fahrenheit(wrow[4]) if wrow[4] is not None else 0.0
        wind_mph    = (wrow[5] / 0.44704) if wrow[5] is not None else 0.0
        wind_dir    = wrow[6] if wrow[6] is not None else 0.0
        humidity    = wrow[7] if wrow[7] is not None else 0.0
        thunder_prob = wrow[8] if wrow[8] is not None else 0.0  # NEW
        wf.write(
            f"{wrow[0]},{pool_temp_F:.2f},{ci_low_F:.2f},{ci_high_F:.2f},{air_temp_F:.2f},"
            f"{wind_mph:.1f},{wind_dir:.1f},{humidity:.1f},{thunder_prob:.1f}\n"  # Added thunder_prob
        )
```

---

### Phase 2: Frontend Display (pool_temp_forecasts.html)

#### 2.1 Update data parsing to include thunder probability
```javascript
// Around line 257, modify parseWeatherData function
if (parts.length >= 9) {  // Updated from 8
    weatherMap[parts[0]] = {
        pool_temp:    parseFloat(parts[1]),
        air_temp:     parseFloat(parts[2]),
        wind_speed:   parseFloat(parts[3]),
        wind_dir:     parseFloat(parts[4]),
        humidity:     parseFloat(parts[5]),
        thunder_prob: parseFloat(parts[8])  // NEW
    };
}
```

#### 2.2 Add lightning icon/indicator to forecast cards
```javascript
// Around line 401, add thunder indicator after wind display
let thunderHtml = '';
if (weatherMap && weatherMap[row.date]) {
    const wd = weatherMap[row.date];
    const thunderProb = isNaN(wd.thunder_prob) ? 0 : wd.thunder_prob;
    
    if (thunderProb > 0) {
        // Color code based on probability
        let thunderColor = '#666';
        let thunderEmoji = '⚡';
        if (thunderProb >= 60) {
            thunderColor = '#ff3333';  // High risk - red
        } else if (thunderProb >= 30) {
            thunderColor = '#ffaa00';  // Moderate - orange
        } else if (thunderProb >= 10) {
            thunderColor = '#ffff66';  // Low - yellow
        }
        
        thunderHtml = `
        <div style="margin-top:8px; padding:6px; background:rgba(255,255,255,0.05); border-radius:6px;">
            <div style="font-size:12px; color:#aaa; margin-bottom:2px;">⚡ Thunder Risk</div>
            <div style="font-size:18px; font-weight:700; color:${thunderColor};">
                ${thunderProb.toFixed(0)}%
            </div>
        </div>`;
    }
}

// Add to column.innerHTML around line 409
column.innerHTML = `
    <h2>${dayOfWeek}</h2>
    <h3>${monthDay}</h3>
    <p style="color:${tempColor}; font-size:22px; font-weight:700; margin:8px 0;">${row.temp} °F</p>
    ${windHtml}
    ${thunderHtml}  <!-- NEW -->
    <p style="font-size:13px; color:#ccc; margin-top:8px;">${suitEmoji}${suitRecommendation}</p>
`;
```

#### 2.3 Add lightning probability chart (optional - can be added later)
If we want to add it to the Chart.js visualization on dev branch:

```javascript
// This would go in the chart building code on dev branch
const thunderProbs = visibleRows.map(r => {
    if (!weatherMap || !weatherMap[r.date]) return 0;
    const v = weatherMap[r.date].thunder_prob;
    return Number.isFinite(v) ? v : 0;
});

// Add as a new dataset
datasets.push({
    label: '⚡ Thunder Risk (%)',
    data: thunderProbs,
    borderColor: '#ffd700',
    backgroundColor: 'rgba(255,215,0,0.1)',
    borderWidth: 2,
    fill: false,
    yAxisID: 'y-thunder',  // Secondary Y axis
    pointRadius: 4,
    pointBackgroundColor: thunderProbs.map(p => {
        if (p >= 60) return '#ff3333';
        if (p >= 30) return '#ffaa00';
        if (p >= 10) return '#ffff66';
        return '#666';
    })
});

// Add secondary Y axis in chart options
scales: {
    // ... existing x and y axes ...
    'y-thunder': {
        type: 'linear',
        position: 'right',
        min: 0,
        max: 100,
        title: { display: true, text: 'Thunder Probability (%)', color: '#ffd700' },
        ticks: { color: '#ffd700' },
        grid: { display: false }
    }
}
```

---

## 🔀 Integration with Dev Branch

### Step-by-step merge strategy:

1. **Current state:**
   - `dev` branch: Has chart improvements with 9AM/3PM temps
   - `feature/lightning_and_air_wind` branch: Starting point for lightning work

2. **Recommended approach:**
   ```bash
   # 1. Create new feature branch from dev
   git checkout dev
   git pull origin dev
   git checkout -b feature/lightning-probability-nws
   
   # 2. Implement backend changes (Phase 1)
   # - Modify scripts/pull_api_data.py
   # - Add thunder_prob column
   # - Fetch and store data
   
   # 3. Test backend changes
   WAVE_POOL_TEST_MODE=1 python scripts/pull_api_data.py
   
   # 4. Implement frontend changes (Phase 2)
   # - Update pool_temp_forecasts.html
   # - Add thunder indicators to cards
   # - (Optional) Add to chart
   
   # 5. Test locally
   # Open pool_temp_forecasts.html in browser
   
   # 6. Commit and PR to dev
   git add .
   git commit -m "feat: add NWS thunder probability to forecasts"
   git push origin feature/lightning-probability-nws
   ```

---

## 🧪 Testing Checklist

- [ ] Backend data fetching works for all 5 locations
- [ ] Database schema migration runs without errors
- [ ] Thunder probability data is stored correctly
- [ ] Forecast files export with 9 columns (including thunder_prob)
- [ ] Frontend parses thunder probability correctly
- [ ] Thunder indicators display on forecast cards
- [ ] Color coding works (red >= 60%, orange >= 30%, yellow >= 10%)
- [ ] No errors in browser console
- [ ] Works on mobile/responsive views
- [ ] Backwards compatible with old forecast files (8 columns)

---

## 📈 Enhancement Ideas (Future)

1. **Alert system**: Show banner warning if any day has >50% thunder probability
2. **Historical tracking**: Store daily thunder probability for trend analysis
3. **Notification**: Email/SMS alerts for high thunder risk days
4. **Chart overlay**: Toggle to show/hide thunder probability on main chart
5. **Hourly breakdown**: Show hour-by-hour thunder risk for each day
6. **Lightning activity level**: Add NWS `lightningActivityLevel` as categorical indicator

---

## 🎨 UI Design Considerations

### Thunder Risk Display Options:

**Option A: Simple percentage (recommended for MVP)**
```
⚡ Thunder Risk
   45%
```

**Option B: Risk level + percentage**
```
⚡ Moderate Risk
   45% chance
```

**Option C: Icon-based severity**
```
⚡⚡ [yellow] 45%
⚡⚡⚡ [orange] 65%
⚡⚡⚡⚡ [red] 85%
```

**Recommendation:** Start with Option A (simple %), can enhance later.

---

## 📝 File Change Summary

### Files to modify:
1. **scripts/pull_api_data.py**
   - Add `fetch_thunder_probability_nws()` function
   - Modify database schema
   - Update data collection loop
   - Update export format

2. **pool_temp_forecasts.html**
   - Update weather data parser
   - Add thunder display HTML/CSS
   - (Optional) Add to chart

3. **tests/test_lightning_probability.py**
   - Already created! Use for testing

### Files created:
- [x] `tests/test_lightning_probability.py`
- [x] `tests/LIGHTNING_INTEGRATION_PLAN.md` (this file)

---

## 🚀 Next Steps

1. Review this plan
2. Decide on UI design option (A, B, or C)
3. Decide if we want chart integration in Phase 2 or later
4. Create feature branch from dev
5. Implement Phase 1 (backend)
6. Test backend
7. Implement Phase 2 (frontend)
8. Test frontend
9. Create PR to dev

---

## ⏱️ Estimated Timeline

- **Phase 1 (Backend):** 2-3 hours
  - Function implementation: 1 hour
  - Database migration: 30 min
  - Testing: 1 hour
  
- **Phase 2 (Frontend):** 1-2 hours
  - Data parsing: 30 min
  - UI display: 30 min
  - Testing: 1 hour

- **Total:** 3-5 hours for complete implementation

---

## 🎯 Success Criteria

✅ Thunder probability data fetches from NWS API  
✅ Data stores in database correctly  
✅ Data exports to forecast files  
✅ UI displays thunder risk on each forecast day  
✅ Color coding indicates risk level  
✅ No regression in existing functionality  
✅ Works across all 5 pool locations  
✅ Mobile-responsive
