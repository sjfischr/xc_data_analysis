import pandas as pd

# Load fresh
df = pd.read_csv('data/merged/season_results.csv')

print(f"Before: {df['team_name'].nunique()} unique teams")
print(f"St. Rita Parish Alexandria records: {len(df[df['team_name'] == 'St. Rita Parish Alexandria'])}")

# Fix it
df.loc[df['team_name'] == 'St. Rita Parish Alexandria', 'team_name'] = 'St Rita'

print(f"\nAfter: {df['team_name'].nunique()} unique teams")
print(f"St Rita total records: {len(df[df['team_name'] == 'St Rita'])}")

# Save
df.to_csv('data/merged/season_results.csv', index=False)
print("\n✅ Fixed and saved!")
