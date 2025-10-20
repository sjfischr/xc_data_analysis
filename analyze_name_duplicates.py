"""
Script to analyze and fix potential duplicate athlete names due to name variations
(e.g., nicknames vs full names like Gwen vs Gwendolyn)
"""

import pandas as pd
from difflib import SequenceMatcher
from collections import defaultdict
import re

def load_data():
    """Load the season results data"""
    df = pd.read_csv('data/merged/season_results.csv')
    return df

def similarity_ratio(name1, name2):
    """Calculate similarity between two names"""
    return SequenceMatcher(None, name1.lower(), name2.lower()).ratio()

def get_first_last_name(full_name):
    """Extract first and last name from full name"""
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return full_name, ""

def normalize_team_name(team_name):
    """Normalize team names to handle variations like 'St. Agnes' vs 'St Agnes Parish'"""
    if pd.isna(team_name):
        return ""
    
    team = str(team_name).lower()
    # Remove common suffixes
    team = team.replace(' parish', '').replace(' school', '')
    # Standardize saint abbreviations
    team = team.replace('st.', 'st').replace('saint', 'st')
    # Remove extra spaces
    team = ' '.join(team.split())
    return team

def find_potential_duplicates(df, similarity_threshold=0.85):
    """
    Find potential duplicate athletes based on name similarity WITHIN THE SAME TEAM
    Returns a dictionary of potential duplicates grouped together
    """
    # Create normalized team column
    df['team_normalized'] = df['team_name'].apply(normalize_team_name)
    
    potential_duplicates = []
    processed = set()
    
    # Group by normalized team name first
    for team in df['team_normalized'].unique():
        if not team:  # Skip empty team names
            continue
            
        team_df = df[df['team_normalized'] == team]
        athletes = team_df['athlete_full_name'].dropna().unique()
        
        if len(athletes) < 2:
            continue
        
        # Group by last name for efficiency within this team
        last_name_groups = defaultdict(list)
        for athlete in athletes:
            first, last = get_first_last_name(athlete)
            last_name_groups[last.lower()].append(athlete)
        
        # Check within each last name group for this team
        for last_name, names in last_name_groups.items():
            if len(names) < 2:
                continue
                
            for i, name1 in enumerate(names):
                if name1 in processed:
                    continue
                    
                duplicates_group = [name1]
                
                for name2 in names[i+1:]:
                    if name2 in processed:
                        continue
                        
                    # Calculate similarity
                    ratio = similarity_ratio(name1, name2)
                    
                    # Check if one name is contained in the other (nickname case)
                    first1, _ = get_first_last_name(name1)
                    first2, _ = get_first_last_name(name2)
                    
                    is_substring = (first1.lower() in first2.lower() or 
                                  first2.lower() in first1.lower())
                    
                    if ratio >= similarity_threshold or is_substring:
                        duplicates_group.append(name2)
                        processed.add(name2)
                
                if len(duplicates_group) > 1:
                    potential_duplicates.append({
                        'names': duplicates_group,
                        'team': team,
                        'original_teams': team_df[team_df['athlete_full_name'].isin(duplicates_group)]['team_name'].unique()
                    })
                    processed.add(name1)
    
    return potential_duplicates

def analyze_duplicates(df, duplicates):
    """Analyze the extent of the duplicate issue"""
    total_duplicates = sum(len(group['names']) for group in duplicates)
    
    print("\n" + "="*80)
    print("DUPLICATE NAMES ANALYSIS REPORT")
    print("="*80)
    print(f"\nTotal unique athlete names in dataset: {df['athlete_full_name'].nunique()}")
    print(f"Total potential duplicate groups found: {len(duplicates)}")
    print(f"Total names affected by duplicates: {total_duplicates}")
    print(f"Percentage of names affected: {(total_duplicates / df['athlete_full_name'].nunique()) * 100:.2f}%")
    
    print("\n" + "-"*80)
    print("DETAILED DUPLICATE GROUPS (Same Team Only):")
    print("-"*80)
    
    for i, group in enumerate(duplicates, 1):
        team_names = ', '.join(str(t) for t in group['original_teams'])
        print(f"\n{i}. Team: {team_names}")
        for name in group['names']:
            count = len(df[df['athlete_full_name'] == name])
            seasons = df[df['athlete_full_name'] == name]['season_year'].dropna().unique()
            print(f"   - {name}")
            print(f"     Appearances: {count}, Seasons: {sorted([int(s) for s in seasons])}")
    
    return duplicates

def create_name_mapping(duplicates):
    """
    Create a mapping from variant names to standardized names
    Preference: longer name (likely full name) over shorter name (likely nickname)
    """
    name_mapping = {}
    
    print("\n" + "="*80)
    print("SUGGESTED NAME STANDARDIZATION")
    print("="*80)
    
    for group in duplicates:
        names = group['names']
        team_names = ', '.join(str(t) for t in group['original_teams'])
        
        # Choose the longest name as the canonical name (usually the full name)
        canonical_name = max(names, key=len)
        
        print(f"\nTeam: {team_names}")
        print(f"Standardize to: {canonical_name}")
        for name in names:
            if name != canonical_name:
                name_mapping[name] = canonical_name
                print(f"  {name} → {canonical_name}")
    
    return name_mapping

def apply_name_fixes(df, name_mapping):
    """Apply the name standardization to the dataframe"""
    df_fixed = df.copy()
    
    # Replace names according to mapping
    df_fixed['athlete_full_name'] = df_fixed['athlete_full_name'].replace(name_mapping)
    
    print("\n" + "="*80)
    print("CHANGES APPLIED")
    print("="*80)
    print(f"Total names standardized: {len(name_mapping)}")
    print(f"Unique athletes before: {df['athlete_full_name'].nunique()}")
    print(f"Unique athletes after: {df_fixed['athlete_full_name'].nunique()}")
    print(f"Reduction: {df['athlete_full_name'].nunique() - df_fixed['athlete_full_name'].nunique()}")
    
    return df_fixed

def save_fixed_data(df_fixed, name_mapping):
    """Save the fixed data and the mapping"""
    # Save fixed dataset
    df_fixed.to_csv('data/merged/season_results_fixed.csv', index=False)
    print("\n✅ Fixed data saved to: data/merged/season_results_fixed.csv")
    
    # Save name mapping for reference
    mapping_df = pd.DataFrame([
        {'Original_Name': k, 'Standardized_Name': v} 
        for k, v in name_mapping.items()
    ])
    mapping_df.to_csv('data/merged/name_mapping.csv', index=False)
    print("✅ Name mapping saved to: data/merged/name_mapping.csv")

def main():
    print("Loading data...")
    df = load_data()
    
    print(f"Dataset loaded: {len(df)} records")
    print(f"Unique athletes: {df['athlete_full_name'].nunique()}")
    
    print("\nSearching for potential duplicates...")
    duplicates = find_potential_duplicates(df, similarity_threshold=0.85)
    
    # Analyze and display the issue
    analyze_duplicates(df, duplicates)
    
    if duplicates:
        # Create name mapping
        name_mapping = create_name_mapping(duplicates)
        
        print("\n" + "="*80)
        response = input("\nDo you want to apply these fixes? (yes/no): ").strip().lower()
        
        if response == 'yes':
            df_fixed = apply_name_fixes(df, name_mapping)
            save_fixed_data(df_fixed, name_mapping)
            
            print("\n" + "="*80)
            print("NEXT STEPS:")
            print("="*80)
            print("1. Review the fixed data in: data/merged/season_results_fixed.csv")
            print("2. If satisfied, backup the original and replace it:")
            print("   - Backup: copy season_results.csv to season_results_backup.csv")
            print("   - Replace: copy season_results_fixed.csv to season_results.csv")
            print("3. Update dashboard.py to use the fixed data (or rename the fixed file)")
        else:
            print("\nNo changes applied. Review the suggestions and run again if needed.")
    else:
        print("\n✅ No duplicate names found! Your data looks clean.")

if __name__ == "__main__":
    main()
