"""
Standardize team names to remove duplicates
"""
import pandas as pd

# Define team name mappings
# Format: old_name -> standardized_name
team_name_mapping = {
    # St Agnes
    'St. Agnes Parish': 'St Agnes',
    
    # St Ambrose
    'St. Ambrose Parish': 'St Ambrose',
    
    # St Ann
    'St. Ann Parish': 'St Ann',
    
    # St Anthony
    'St. Anthony of Padua Parish': 'St Anthony',
    
    # St Bernadette
    'St. Bernadette Parish': 'St Bernadette',
    
    # St Francis
    'St. Francis of Assisi Parish': 'St Francis',
    
    # St James
    'St. James Parish': 'St James',
    
    # St John the Evangelist (also fix capitalization)
    'St. John The Evangelist Parish': 'St John the Evangelist',
    'St. John the Evangelist Parish': 'St John the Evangelist',
    
    # St Joseph
    'St. Joseph Parish': 'St Joseph',
    
    # St Louis
    'St. Louis Parish': 'St Louis',
    
    # St Mark
    'St. Mark Parish': 'St Mark',
    
    # St Michael
    'St. Michael Parish': 'St Michael',
    
    # St Rita
    'St. Rita Parish Alexandria': 'St Rita',
    
    # St Theresa
    'St. Theresa Parish': 'St Theresa',
    
    # St Thomas More (also fix typo "Caedral")
    'St. Thomas More Caedral Parish': 'St Thomas More',
    'St. Thomas More Cathedral Parish': 'St Thomas More',
    
    # St Veronica
    'St. Veronica Parish': 'St Veronica',
    
    # Basilica of St Mary
    'Basilica of Saint Mary Parish': 'Basilica of St Mary',
    
    # Blessed Sacrament
    'Blessed Sacrament Parish': 'Blessed Sacrament',
    
    # Holy Family
    'Holy Family Parish': 'Holy Family',
    
    # Holy Spirit
    'Holy Spirit Parish': 'Holy Spirit',
    
    # Our Lady of Hope (OLOH)
    'Our Lady of Hope Parish': 'OLOH',
    
    # Queen of Apostles (Q of A)
    'Queen of Apostles Parish': 'Q of A',
}

print("="*80)
print("STANDARDIZING TEAM NAMES")
print("="*80)

# Load data
df = pd.read_csv('data/merged/season_results.csv')

print(f"\nOriginal dataset: {len(df)} records")
print(f"Original unique teams: {df['team_name'].nunique()}")

# Apply mappings
df['team_name'] = df['team_name'].replace(team_name_mapping)

print(f"\nAfter standardization:")
print(f"Total records: {len(df)} (unchanged)")
print(f"Unique teams: {df['team_name'].nunique()}")

print(f"\n{len(team_name_mapping)} team name variations standardized:")
for old_name, new_name in sorted(team_name_mapping.items()):
    print(f"  {old_name} → {new_name}")

# Show updated team counts
print("\n" + "="*80)
print("UPDATED TEAM COUNTS")
print("="*80)
teams = df['team_name'].value_counts()
for team in sorted(teams.index):
    count = teams[team]
    print(f"{team:<50} {count:>5} records")

# Save updated dataset
df.to_csv('data/merged/season_results.csv', index=False)

print("\n" + "="*80)
print("✅ COMPLETE - Updated file saved")
print("="*80)
print("\nFile: data/merged/season_results.csv")
