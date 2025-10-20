"""
Add race distance and normalized pace metrics to the dataset
"""
import pandas as pd

# Distance by division (in kilometers)
DISTANCE_MAP = {
    '2nd Grade': 2.0,
    'Frosh': 2.0,
    'JV': 3.0,
    'Varsity': 4.0
}

print("=" * 80)
print("ADDING DISTANCE AND NORMALIZED PACE METRICS")
print("=" * 80)

# Load the data
df = pd.read_csv('data/merged/season_results.csv')

print(f"\nOriginal dataset: {len(df)} records")

# Add distance column
df['distance_km'] = df['division'].map(DISTANCE_MAP)
df['distance_mi'] = df['distance_km'] * 0.621371  # Convert to miles

# Calculate pace per kilometer (minutes per km)
# finish_time_s is in seconds, convert to minutes and divide by distance
df['pace_per_km_min'] = df['finish_time_s'] / 60 / df['distance_km']

# Calculate pace per mile (minutes per mile)
df['pace_per_mi_min'] = df['finish_time_s'] / 60 / df['distance_mi']

# Format pace as MM:SS per km
def format_pace(pace_min):
    """Convert pace in minutes to MM:SS format"""
    if pd.isna(pace_min):
        return None
    minutes = int(pace_min)
    seconds = int((pace_min - minutes) * 60)
    return f"{minutes}:{seconds:02d}"

df['pace_per_km_str'] = df['pace_per_km_min'].apply(format_pace)
df['pace_per_mi_str'] = df['pace_per_mi_min'].apply(format_pace)

# Add speed (km/h and mph) for comparison
df['speed_kmh'] = df['distance_km'] / (df['finish_time_s'] / 3600)
df['speed_mph'] = df['distance_mi'] / (df['finish_time_s'] / 3600)

print("\n✅ Added columns:")
print("   - distance_km: Race distance in kilometers")
print("   - distance_mi: Race distance in miles")
print("   - pace_per_km_min: Pace in minutes per kilometer (numeric)")
print("   - pace_per_km_str: Pace formatted as MM:SS per km")
print("   - pace_per_mi_min: Pace in minutes per mile (numeric)")
print("   - pace_per_mi_str: Pace formatted as MM:SS per mile")
print("   - speed_kmh: Speed in kilometers per hour")
print("   - speed_mph: Speed in miles per hour")

# Show summary by division
print("\n" + "=" * 80)
print("DISTANCE BY DIVISION")
print("=" * 80)

for division in sorted(df['division'].unique()):
    distance = df[df['division'] == division]['distance_km'].iloc[0]
    count = len(df[df['division'] == division])
    print(f"  {division:<12} {distance:.1f} km ({count:>5} athletes)")

# Show sample statistics
print("\n" + "=" * 80)
print("SAMPLE STATISTICS BY DIVISION")
print("=" * 80)

for division in sorted(df['division'].unique()):
    div_data = df[df['division'] == division]
    avg_pace = div_data['pace_per_mi_min'].mean()
    
    if pd.notna(avg_pace):
        minutes = int(avg_pace)
        seconds = int((avg_pace - minutes) * 60)
        avg_pace_str = f"{minutes}:{seconds:02d}"
    else:
        avg_pace_str = "N/A"
    
    print(f"  {division:<12} Avg pace: {avg_pace_str} per mile ({len(div_data)} athletes)")

# Save updated dataset
df.to_csv('data/merged/season_results.csv', index=False)

print("\n" + "=" * 80)
print("✅ COMPLETE - Dataset updated with normalized metrics")
print("=" * 80)
print("\nUpdated file: data/merged/season_results.csv")
print(f"Total records: {len(df):,}")
print(f"Records with pace data: {df['pace_per_km_min'].notna().sum():,}")
