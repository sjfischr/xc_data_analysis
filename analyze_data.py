import pandas as pd

# Load the data
df = pd.read_csv('data/merged/season_results.csv')

print(f"📊 Data Analysis:")
print(f"Total athlete results: {len(df)}")
print(f"Unique athletes: {df['athlete_full_name'].nunique()}")
print(f"Meets available: {sorted(df['meet_number'].unique())}")
print(f"Teams: {df['team_name'].nunique()}")
print(f"Grades: {sorted(df['grade'].unique())}")

# Find athletes who appear in multiple meets
athlete_counts = df['athlete_full_name'].value_counts()
multi_meet_athletes = athlete_counts[athlete_counts > 1]
print(f"\n🏃‍♀️ Athletes in multiple meets: {len(multi_meet_athletes)}")

if len(multi_meet_athletes) > 0:
    print("\n📈 Sample progress tracking (first 3 athletes):")
    for athlete in multi_meet_athletes.head(3).index:
        athlete_data = df[df['athlete_full_name'] == athlete][
            ['athlete_full_name', 'meet_number', 'place_overall', 'finish_time_str', 'finish_time_s', 'team_name']
        ].sort_values('meet_number')
        
        print(f"\n{athlete} ({athlete_data.iloc[0]['team_name']}):")
        for _, row in athlete_data.iterrows():
            improvement = ""
            if len(athlete_data) > 1:
                if row.name > athlete_data.index[0]:  # Not the first result
                    prev_time = athlete_data.iloc[list(athlete_data.index).index(row.name)-1]['finish_time_s']
                    if pd.notna(prev_time) and pd.notna(row['finish_time_s']):
                        diff = prev_time - row['finish_time_s']
                        if diff > 0:
                            improvement = f" (⬇️ {diff:.1f}s faster)"
                        else:
                            improvement = f" (⬆️ {abs(diff):.1f}s slower)"
            
            print(f"  Meet {row['meet_number']}: Place {row['place_overall']} - {row['finish_time_str']}{improvement}")

# Show teams with most athletes
print(f"\n🏫 Top teams by athlete count:")
team_counts = df['team_name'].value_counts().head(5)
for team, count in team_counts.items():
    print(f"  {team}: {count} results")