import pandas as pd
import os

# Possible locations for the merged results file
possible_paths = [
    'data/merged/season_results.csv',
    'merged_results.csv',
    'season_results.csv',
    '../merged_results.csv',
]

# Try to find the file
data_file = None
for path in possible_paths:
    if os.path.exists(path):
        data_file = path
        print(f"✅ Found data file: {path}")
        break

if not data_file:
    # List all CSV files in current directory and subdirectories
    print("\n🔍 Looking for CSV files in current directory:")
    for root, dirs, files in os.walk('.'):
        for file in files:
            if file.endswith('.csv'):
                full_path = os.path.join(root, file)
                size = os.path.getsize(full_path)
                print(f"  - {full_path} ({size:,} bytes)")
    
    data_file = input("\nEnter the full path to your merged_results file: ")

# Load and verify the data
try:
    print(f"\n📊 Loading data from: {data_file}")
    df = pd.read_csv(data_file)
    
    print(f"\n✅ Successfully loaded!")
    print(f"   Total rows: {len(df):,}")
    print(f"   Total columns: {len(df.columns)}")
    
    print(f"\n📋 Columns found:")
    for col in df.columns:
        print(f"   - {col}")
    
    print(f"\n📊 Data summary:")
    print(f"   Unique athletes: {df['athlete_full_name'].nunique() if 'athlete_full_name' in df.columns else 'N/A'}")
    print(f"   Unique meets: {df['meet_number'].nunique() if 'meet_number' in df.columns else 'N/A'}")
    if 'meet_number' in df.columns:
        print(f"   Meet numbers: {sorted(df['meet_number'].unique())}")
    print(f"   Teams: {df['team_name'].nunique() if 'team_name' in df.columns else 'N/A'}")
    
    # Check for athletes in multiple meets
    if 'athlete_full_name' in df.columns and 'meet_number' in df.columns:
        athlete_counts = df.groupby('athlete_full_name')['meet_number'].nunique()
        multi_meet = athlete_counts[athlete_counts > 1]
        print(f"   Athletes in multiple meets: {len(multi_meet)}")
        
        if len(multi_meet) > 0:
            print(f"\n📈 Sample athletes with progress data:")
            for athlete in list(multi_meet.index)[:5]:
                meets = sorted(df[df['athlete_full_name'] == athlete]['meet_number'].unique())
                print(f"      {athlete}: Meets {meets}")
    
    # Show first few rows
    print(f"\n📄 First 3 rows preview:")
    print(df.head(3).to_string())
    
    # Copy to the expected location for dashboard
    expected_path = 'data/merged/season_results.csv'
    if data_file != expected_path:
        os.makedirs('data/merged', exist_ok=True)
        df.to_csv(expected_path, index=False)
        print(f"\n✅ Copied data to dashboard location: {expected_path}")
        print(f"   Dashboard will now use this data!")
    
except FileNotFoundError:
    print(f"\n❌ Error: File not found at '{data_file}'")
    print(f"   Please check the file path and try again.")
except Exception as e:
    print(f"\n❌ Error loading data: {e}")
    print(f"   File might be corrupted or in wrong format.")