"""
Apply curated name corrections to the dataset
"""

import pandas as pd

def apply_corrections():
    # Load the data
    print("Loading data...")
    df = pd.read_csv('data/merged/season_results.csv')
    print(f"Original dataset: {len(df)} records, {df['athlete_full_name'].nunique()} unique athletes")
    
    # Load corrections
    corrections = pd.read_csv('name_corrections.csv')
    
    # Filter to only apply corrections with action='apply'
    apply_corrections = corrections[corrections['action'] == 'apply']
    
    print(f"\n{'='*80}")
    print(f"APPLYING {len(apply_corrections)} NAME CORRECTIONS")
    print(f"{'='*80}\n")
    
    # Create mapping dictionary
    name_mapping = {}
    for _, row in apply_corrections.iterrows():
        name_mapping[row['original_name']] = row['corrected_name']
        print(f"✓ {row['original_name']} → {row['corrected_name']} ({row['team']})")
    
    # Apply corrections
    df_corrected = df.copy()
    df_corrected['athlete_full_name'] = df_corrected['athlete_full_name'].replace(name_mapping)
    
    # Report on items needing review
    review_items = corrections[corrections['action'] == 'review']
    if len(review_items) > 0:
        print(f"\n{'='*80}")
        print(f"⚠️  {len(review_items)} ITEMS NEED MANUAL REVIEW")
        print(f"{'='*80}\n")
        for _, row in review_items.iterrows():
            print(f"Review: {row['original_name']} ({row['team']})")
            print(f"  Notes: {row['notes']}\n")
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"Before: {df['athlete_full_name'].nunique()} unique athletes")
    print(f"After:  {df_corrected['athlete_full_name'].nunique()} unique athletes")
    print(f"Reduction: {df['athlete_full_name'].nunique() - df_corrected['athlete_full_name'].nunique()} duplicate entries removed")
    print(f"{'='*80}\n")
    
    # Save
    df_corrected.to_csv('data/merged/season_results_corrected.csv', index=False)
    print("✅ Corrected data saved to: data/merged/season_results_corrected.csv")
    
    # Create backup instructions
    print(f"\n{'='*80}")
    print("NEXT STEPS:")
    print(f"{'='*80}")
    print("1. Review the corrected data: data/merged/season_results_corrected.csv")
    print("2. Update name_corrections.csv for any items marked 'review'")
    print("3. If satisfied, backup and replace:")
    print("   Copy-Item data\\merged\\season_results.csv data\\merged\\season_results_backup.csv")
    print("   Copy-Item data\\merged\\season_results_corrected.csv data\\merged\\season_results.csv")
    print("4. Refresh your Streamlit dashboard")

if __name__ == "__main__":
    apply_corrections()
