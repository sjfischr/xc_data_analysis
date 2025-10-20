# Dataset Column Reference

## Primary Data Columns

### Race Identification
- `season_year` - Year of the season (2023, 2024, 2025)
- `meet_number` - Meet number within season (1, 2, 3)
- `meet_name` - Full meet name
- `division` - Division category (2nd Grade, Frosh, JV, Varsity)
- `gender` - Athlete gender (Boys, Girls)

### Athlete Information
- `athlete_full_name` - Standardized full name of athlete
- `team_name` - Team/school name
- `grade` - Grade level
- `bib` - Race bib number

### Performance Data
- `finish_time_str` - Finish time as string (MM:SS.ss format)
- `finish_time_s` - Finish time in seconds (numeric)
- `place_overall` - Overall place in race
- `place_gender` - Place within gender
- `place_grade` - Place within grade

## Normalized Metrics (for cross-division comparisons)

### Distance
- `distance_km` - Race distance in kilometers
  - 2nd Grade: 2.0 km
  - Frosh: 2.0 km
  - JV: 3.0 km
  - Varsity: 4.0 km
- `distance_mi` - Race distance in miles
  - 2nd Grade: 1.24 mi
  - Frosh: 1.24 mi
  - JV: 1.86 mi
  - Varsity: 2.49 mi

### Pace (Metric)
- `pace_per_km_min` - Pace in minutes per kilometer (numeric)
- `pace_per_km_str` - Pace formatted as MM:SS per km

### Pace (Imperial) - **DEFAULT FOR US**
- `pace_per_mi_min` - Pace in minutes per mile (numeric)
- `pace_per_mi_str` - Pace formatted as MM:SS per mile

### Speed
- `speed_kmh` - Speed in kilometers per hour
- `speed_mph` - Speed in miles per hour

## Dataset Statistics

- **Total Records**: 3,684
- **Unique Athletes**: 1,417
- **Years**: 2023-2025
- **Meets**: 3 per season
- **Divisions**: 4 (2nd Grade, Frosh, JV, Varsity)

## Usage Notes

### For Fair Comparisons
Use normalized metrics when comparing:
- Athletes across different divisions
- Performance progression when athlete moves up divisions
- Team comparisons with mixed division athletes

**Recommended metrics for comparisons:**
- `pace_per_mi_min` or `pace_per_mi_str` (US standard)
- `speed_mph` (easier for general audiences)

### For Division-Specific Analysis
Raw times (`finish_time_s`, `finish_time_str`) are fine when:
- Comparing within same division
- Ranking athletes in a specific race
- Historical tracking within one division

### Example Analysis

**Bad comparison (raw times):**
- Frosh 2km: 11:37.5
- JV 3km: 16:50.8
- ❌ Looks like 45% slower!

**Good comparison (normalized pace):**
- Frosh 2km: 5:48 per mile
- JV 3km: 5:36 per mile
- ✅ Actually 3% faster!

## Average Pace by Division

- **2nd Grade** (2km): 10:36 per mile
- **Frosh** (2km): 9:40 per mile
- **JV** (3km): 9:58 per mile
- **Varsity** (4km): 9:10 per mile
