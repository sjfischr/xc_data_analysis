import pandas as pd

# Load data
df = pd.read_csv('data/merged/season_results.csv')

# Get team name counts
teams = df['team_name'].value_counts()

print('='*80)
print(f'TEAM NAME ANALYSIS')
print('='*80)
print(f'\nTotal unique team names: {len(teams)}')
print(f'\nAll team names (sorted):')
print('-'*80)

for team in sorted(teams.index):
    count = teams[team]
    print(f'{team:<50} {count:>5} records')

print('\n' + '='*80)
print('POTENTIAL DUPLICATES (similar names)')
print('='*80)

# Look for potential duplicates
team_list = sorted(teams.index)
potential_dupes = []

for i, team1 in enumerate(team_list):
    for team2 in team_list[i+1:]:
        # Check for similar names
        t1_normalized = team1.lower().replace('.', '').replace('parish', '').replace('school', '').strip()
        t2_normalized = team2.lower().replace('.', '').replace('parish', '').replace('school', '').strip()
        
        if t1_normalized == t2_normalized or t1_normalized in t2_normalized or t2_normalized in t1_normalized:
            potential_dupes.append((team1, teams[team1], team2, teams[team2]))

if potential_dupes:
    for team1, count1, team2, count2 in potential_dupes:
        print(f'\n{team1} ({count1} records)')
        print(f'  vs')
        print(f'{team2} ({count2} records)')
else:
    print('\nNo obvious duplicates found')
