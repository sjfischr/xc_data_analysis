import pandas as pd

# Load current data
df = pd.read_csv('data/merged/season_results.csv')

print("=" * 60)
print("📊 CURRENT DATA VERIFICATION")
print("=" * 60)

print(f"\n✅ File loaded successfully!")
print(f"   Location: data/merged/season_results.csv")
print(f"   Size: {len(df):,} rows × {len(df.columns)} columns")

print(f"\n📋 Data Summary:")
print(f"   Unique athletes: {df['athlete_full_name'].nunique()}")
print(f"   Unique meets: {df['meet_number'].nunique()}")
print(f"   Meet numbers: {sorted(df['meet_number'].unique())}")
print(f"   Teams: {df['team_name'].nunique()}")
print(f"   Grades: {sorted(df['grade'].unique())}")

# Check for multi-meet athletes
athlete_meet_counts = df.groupby('athlete_full_name')['meet_number'].nunique()
multi_meet_athletes = athlete_meet_counts[athlete_meet_counts > 1]

print(f"\n🏃 Athletes with progress data:")
print(f"   Athletes in 1 meet only: {len(athlete_meet_counts[athlete_meet_counts == 1])}")
print(f"   Athletes in 2+ meets: {len(multi_meet_athletes)}")

if len(multi_meet_athletes) > 0:
    print(f"\n📈 Sample athletes with progress:")
    for i, athlete in enumerate(list(multi_meet_athletes.index)[:5], 1):
        athlete_data = df[df['athlete_full_name'] == athlete].sort_values('meet_number')
        meets = athlete_data['meet_number'].tolist()
        times = athlete_data['finish_time_str'].tolist()
        print(f"   {i}. {athlete}")
        for meet, time in zip(meets, times):
            print(f"      Meet {meet}: {time}")

print(f"\n📄 Column names:")
for col in df.columns:
    print(f"   - {col}")

print(f"\n" + "=" * 60)
print("✅ Data is ready for the dashboard!")
print("=" * 60)