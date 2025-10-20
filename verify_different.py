import pandas as pd
import os

raw_dir = "data/raw"
files = sorted([f for f in os.listdir(raw_dir) if f.endswith('.csv')])

print(f"Found {len(files)} files:\n")

for f in files:
    path = os.path.join(raw_dir, f)
    df = pd.read_csv(path)
    
    grades = sorted([g for g in df['grade'].unique() if pd.notna(g)])
    meet_num = df['meet_number'].iloc[0] if len(df) > 0 else None
    first_athlete = df.iloc[0]['athlete_full_name'] if len(df) > 0 else "N/A"
    
    print(f"{f}")
    print(f"  Rows: {len(df)}")
    print(f"  Meet: {meet_num}")
    print(f"  Grades: {grades}")
    print(f"  First athlete: {first_athlete}")
    print()
