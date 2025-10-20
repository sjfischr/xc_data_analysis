import pandas as pd

# Load the merged data
df = pd.read_csv('data/merged/season_results.csv')

print("=" * 60)
print("CLEANING DUPLICATE DATA")
print("=" * 60)

print(f"\nBefore cleaning:")
print(f"   Total rows: {len(df):,}")
print(f"   Unique athletes: {df['athlete_full_name'].nunique()}")

# Remove duplicates based on athlete, meet, and time
# Keep the first occurrence of each unique combination
df_clean = df.drop_duplicates(
    subset=['athlete_full_name', 'meet_number', 'bib', 'finish_time_str'],
    keep='first'
)

print(f"\nAfter cleaning:")
print(f"   Total rows: {len(df_clean):,}")
print(f"   Rows removed: {len(df) - len(df_clean):,}")
print(f"   Unique athletes: {df_clean['athlete_full_name'].nunique()}")

# Check for athletes in multiple meets
athlete_meet_counts = df_clean.groupby('athlete_full_name')['meet_number'].nunique()
multi_meet_athletes = athlete_meet_counts[athlete_meet_counts > 1]

print(f"\nAthletes with progress data:")
print(f"   Athletes in 1 meet only: {len(athlete_meet_counts[athlete_meet_counts == 1])}")
print(f"   Athletes in 2+ meets: {len(multi_meet_athletes)}")

if len(multi_meet_athletes) > 0:
    print(f"\nSample athletes with progress (cleaned):")
    for i, athlete in enumerate(list(multi_meet_athletes.index)[:5], 1):
        athlete_data = df_clean[df_clean['athlete_full_name'] == athlete].sort_values('meet_number')
        print(f"   {i}. {athlete}")
        for _, row in athlete_data.iterrows():
            print(f"      Meet {row['meet_number']}: {row['finish_time_str']} - Place {row['place_overall']}")

# Save cleaned data
df_clean.to_csv('data/merged/season_results.csv', index=False)
print(f"\nCleaned data saved to: data/merged/season_results.csv")
print("=" * 60)