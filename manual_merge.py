import pandas as pd
import glob

# Read all CSV files in data/raw/
csv_files = glob.glob("data/raw/*.csv")
print(f"Found {len(csv_files)} CSV files")

dfs = []
for file in csv_files:
    df = pd.read_csv(file)
    print(f"File: {file} - {len(df)} rows")
    dfs.append(df)

if dfs:
    # Merge all dataframes
    merged = pd.concat(dfs, ignore_index=True)
    
    # Sort by meet_number and then by place_overall for better organization
    merged = merged.sort_values(['meet_number', 'place_overall'], na_position='last')
    
    # Save merged file
    merged.to_csv("data/merged/season_results.csv", index=False)
    print(f"\nMerged {len(merged)} total rows")
    
    # Show sample data
    print("\nSample data:")
    print(merged[['athlete_full_name', 'meet_name', 'meet_number', 'place_overall', 'finish_time_s']].head(10))
else:
    print("No data to merge")