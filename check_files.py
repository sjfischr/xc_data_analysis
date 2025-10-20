import pandas as pd

files = [
    'data/raw/meet_2_url_1_age_group_division_results.csv',
    'data/raw/meet_2_url_2_age_group_division_results.csv', 
    'data/raw/meet_2_url_3_age_group_division_results.csv',
    'data/raw/meet_2_url_4_age_group_division_results.csv'
]

for f in files:
    df = pd.read_csv(f)
    grades = sorted(df['grade'].unique())
    print(f'{f}: {len(df)} rows, grades={grades}')
    print(f'  First athlete: {df.iloc[0]["athlete_full_name"]} - {df.iloc[0]["team_name"]} - Grade {df.iloc[0]["grade"]}')
    print()
