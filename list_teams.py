import pandas as pd

df = pd.read_csv('data/merged/season_results.csv')
teams = df['team_name'].dropna().unique()

print(f"Total unique teams: {len(teams)}")
print("\nAll teams:")
for team in sorted(teams):
    count = len(df[df['team_name'] == team])
    print(f"  {team} ({count})")
