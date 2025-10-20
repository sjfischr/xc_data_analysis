"""
Parse saved HTML pages from data/pages folder
Extracts meet info from filename and table data from HTML
Supports both .htm and .mhtml formats
"""
import os
import re
import pandas as pd
from bs4 import BeautifulSoup
from pathlib import Path

def decode_mhtml(mhtml_content: str) -> str:
    """
    Extract and decode HTML content from MHTML file
    MHTML uses quoted-printable encoding which needs to be decoded
    """
    # Find the HTML section (after Content-Type: text/html)
    html_section_match = re.search(
        r'Content-Type: text/html.*?Content-Transfer-Encoding: quoted-printable.*?\n\n(.*?)(?=------MultipartBoundary|$)', 
        mhtml_content, 
        re.DOTALL
    )
    
    if not html_section_match:
        # Try to find HTML directly
        html_start = mhtml_content.find('<!DOCTYPE html>')
        if html_start == -1:
            html_start = mhtml_content.find('<html')
        if html_start != -1:
            return mhtml_content[html_start:]
        return mhtml_content
    
    encoded_html = html_section_match.group(1)
    
    # Decode quoted-printable encoding
    # =3D means =, =20 means space, etc.
    decoded_html = encoded_html.replace('=3D', '=')
    decoded_html = decoded_html.replace('=\n', '')  # Remove soft line breaks
    decoded_html = decoded_html.replace('=20', ' ')
    
    # Decode remaining =XX patterns
    decoded_html = re.sub(r'=([0-9A-F]{2})', 
                          lambda m: chr(int(m.group(1), 16)), 
                          decoded_html)
    
    return decoded_html

def parse_filename(filename: str) -> dict:
    """
    Extract meet info from filename patterns like:
    - "NVJCYO Cross Country Developmental Meet 1 Results Varsity Boys 2025.htm"
    - "NVJCYO Cross Country Developmental Meet 2 Results JV Girls 2023.htm"
    - "NVJCYO Cross Country Developmental Meet 2 Results JV Girls.htm" (assumes current year)
    """
    info = {
        "meet_number": None,
        "season_year": None,
        "division": None,
        "gender": None,
        "meet_series": None,
        "filename": filename
    }
    
    # Extract meet series name
    if 'Championship' in filename:
        info["meet_series"] = "NYJCYO Cross Country Championship"
    else:
        info["meet_series"] = "NVJCYO Cross Country Developmental"
    
    # Extract meet number (e.g., "Meet 1", "Meet 2", "Meet 3")
    meet_match = re.search(r'Meet\s+(\d+)', filename, re.IGNORECASE)
    if meet_match:
        info["meet_number"] = int(meet_match.group(1))
    
    # Extract year from filename (e.g., "2023.htm", "2024.mhtml", "2025.htm")
    year_match = re.search(r'(\d{4})\.(htm|mhtml)', filename)
    if year_match:
        info["season_year"] = int(year_match.group(1))
    else:
        # Default to 2025 if no year in filename (for backwards compatibility)
        info["season_year"] = 2025
    
    # Extract division (Varsity, JV, Frosh, 2nd Grade)
    if 'Varsity' in filename:
        info["division"] = "Varsity"
    elif 'JV' in filename:
        info["division"] = "JV"
    elif 'Frosh' in filename:
        info["division"] = "Frosh"
    elif '2nd Grade' in filename or '2ndGrade' in filename:
        info["division"] = "2nd Grade"
    
    # Extract gender
    if 'Boys' in filename:
        info["gender"] = "Boys"
    elif 'Girls' in filename:
        info["gender"] = "Girls"
    
    return info

def parse_html_table(html_path: str) -> pd.DataFrame:
    """Extract table data from saved HTML or MHTML file"""
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check if it's an MHTML file
    if html_path.endswith('.mhtml') or 'MIME-Version:' in content[:1000]:
        content = decode_mhtml(content)
    
    soup = BeautifulSoup(content, 'html.parser')
    
    # Find the results table - try multiple selectors
    table = soup.find('table', class_='rsu-results__table')
    if not table:
        table = soup.find('table')
    
    if not table:
        print(f"  Warning: No table found in {html_path}")
        return None
    
    # Extract headers
    headers = []
    header_row = table.find('thead')
    if header_row:
        headers = [th.get_text(strip=True, separator=' ') for th in header_row.find_all('th')]
    else:
        # Try first row
        first_row = table.find('tr')
        if first_row:
            headers = [cell.get_text(strip=True, separator=' ') for cell in first_row.find_all(['th', 'td'])]
    
    # Extract data rows
    rows = []
    tbody = table.find('tbody')
    if tbody:
        for tr in tbody.find_all('tr'):
            row = [td.get_text(strip=True, separator=' ') for td in tr.find_all('td')]
            if row:  # Skip empty rows
                rows.append(row)
    else:
        # If no tbody, get all rows except first (header)
        all_rows = table.find_all('tr')[1:]
        for tr in all_rows:
            row = [td.get_text(strip=True, separator=' ') for td in tr.find_all('td')]
            if row:
                rows.append(row)
    
    if not headers or not rows:
        return None
    
    # Create DataFrame
    df = pd.DataFrame(rows, columns=headers[:len(rows[0])])
    return df

def clean_athlete_name(name_str):
    """Clean up athlete names"""
    if not name_str or pd.isna(name_str):
        return ""
    
    cleaned = str(name_str).replace('"', '').strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    
    # Handle "Initial FirstName LastName" format
    parts = cleaned.split()
    if len(parts) >= 3 and len(parts[0]) == 1 and parts[0].isalpha():
        cleaned = ' '.join(parts[1:])
    
    # Fix RunSignup HTML artifact where first letter is doubled
    # e.g., "TTommyVolinsky" -> "Tommy Volinsky"
    # Pattern: Capital letter repeated + concatenated words
    match = re.match(r'^([A-Z])\1([A-Z][a-z]+)([A-Z][a-z]+.*)$', cleaned)
    if match:
        # Reconstructed: FirstName + space + LastName(s)
        first_name = match.group(2)
        last_name = match.group(3)
        cleaned = f"{first_name} {last_name}"
    
    return cleaned.strip()

def time_to_seconds(t):
    """Convert time string to seconds"""
    if pd.isna(t) or not t:
        return None
    s = str(t).strip()
    if not s:
        return None
    
    cleaned = re.sub(r'[^\d:.]', '', s)
    if not cleaned:
        return None
    
    parts = cleaned.split(":")
    try:
        if len(parts) == 2:
            mm, ss = parts
            return int(mm) * 60 + float(ss)
        elif len(parts) == 3:
            hh, mm, ss = parts
            return int(hh) * 3600 + int(mm) * 60 + float(ss)
        elif len(parts) == 1:
            return float(parts[0])
        else:
            return None
    except (ValueError, TypeError):
        return None

def standardize_columns(df: pd.DataFrame, file_info: dict) -> pd.DataFrame:
    """Standardize column names and add metadata"""
    rename_map = {
        "Place": "place_overall",
        "Finish Place": "place_overall",  # Added for Meet 3 files
        "Bib": "bib",
        "Bib Number": "bib",  # Added for 2023 Meet 1 files
        "Name": "athlete_full_name",
        "Participant Name": "athlete_full_name",  # Added for 2023 files
        "Time": "finish_time_str",
        "Clock Time": "finish_time_str",
        "Chip Time": "finish_time_str",
        "Finish Time": "finish_time_str",  # Added for Meet 3 files
        "Pace": "pace_str",
        "Team": "team_name",
        "Team Name": "team_name",
        "Age": "age",
        "Grade": "grade",
        "Year": "grade",
        "Gender": "gender",
    }
    
    # Rename columns (case-insensitive)
    for old_name, new_name in rename_map.items():
        for col in df.columns:
            if col.strip().lower() == old_name.lower():
                # Avoid duplicate columns
                if new_name not in df.columns:
                    df.rename(columns={col: new_name}, inplace=True)
                break
    
    # Clean athlete names - find any name column and clean it
    name_column = None
    for col in df.columns:
        if 'name' in col.lower() and ('athlete' in col.lower() or 'participant' in col.lower() or col.lower() == 'name'):
            name_column = col
            break
    
    # Apply cleaning to the name column if found
    if name_column and name_column in df.columns:
        df[name_column] = df[name_column].apply(clean_athlete_name)
    
    # Also ensure athlete_full_name is cleaned if it exists
    if "athlete_full_name" in df.columns:
        df["athlete_full_name"] = df["athlete_full_name"].apply(clean_athlete_name)
    
    # Convert times to seconds
    if "finish_time_str" in df.columns:
        # Apply element-wise to avoid Series ambiguity
        df["finish_time_s"] = df["finish_time_str"].map(time_to_seconds)
    
    # Add metadata from filename
    df["season_year"] = file_info["season_year"]
    df["meet_number"] = file_info["meet_number"]
    df["meet_series"] = file_info["meet_series"]
    df["meet_name"] = f"{file_info['meet_series']} Meet {file_info['meet_number']}"
    df["meet_order"] = file_info["meet_number"]
    df["division"] = file_info["division"]
    
    # Add gender if not in data
    if "gender" not in df.columns and file_info["gender"]:
        df["gender"] = file_info["gender"]
    
    return df

def main():
    pages_dir = Path("data/pages")
    output_dir = Path("data/raw")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Find all HTML and MHTML files
    html_files = (list(pages_dir.glob("*.htm")) + 
                  list(pages_dir.glob("*.html")) + 
                  list(pages_dir.glob("*.mhtml")))
    
    print(f"Found {len(html_files)} HTML/MHTML files to process")
    print(f"  .htm/.html files: {len(list(pages_dir.glob('*.htm'))) + len(list(pages_dir.glob('*.html')))}")
    print(f"  .mhtml files: {len(list(pages_dir.glob('*.mhtml')))}\n")
    
    processed = []
    years_found = set()
    
    for html_file in html_files:
        filename = html_file.name
        print(f"Processing: {filename}")
        
        # Parse filename to get metadata
        file_info = parse_filename(filename)
        years_found.add(file_info['season_year'])
        print(f"  {file_info['season_year']} | Meet {file_info['meet_number']} | {file_info['division']} {file_info['gender']}")
        
        # Parse HTML table
        df = parse_html_table(str(html_file))
        if df is None or len(df) == 0:
            print(f"  Skipping - no data found")
            continue
        
        # Standardize columns and add metadata
        df = standardize_columns(df, file_info)
        
        # Create output filename with year
        year = file_info['season_year']
        meet_num = file_info['meet_number']
        division = file_info['division'].lower()
        gender = file_info['gender'].lower()
        output_filename = f"{year}_meet_{meet_num}_{division}_{gender}.csv"
        output_path = output_dir / output_filename
        
        # Save to CSV
        df.to_csv(output_path, index=False)
        print(f"  Saved {len(df)} rows to {output_filename}\n")
        
        processed.append(output_path)
    
    print(f"\n{'='*60}")
    print(f"SUCCESS! Processed {len(processed)} files successfully!")
    print(f"Seasons found: {sorted(years_found)}")
    print(f"{'='*60}")
    print("\nNext steps:")
    print("  1. Run: python manual_merge.py")
    print("  2. Run: python clean_duplicates.py")
    print("  3. Run: python -m streamlit run dashboard.py")

if __name__ == "__main__":
    main()
