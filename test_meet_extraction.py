import re

def extract_meet_info(page_title: str, page_content: str = "") -> dict:
    """
    Extract meet name and meet order from the page title or content.
    Returns dict with meet_name, meet_series, meet_number, meet_order
    """
    meet_info = {
        "meet_name": "",
        "meet_series": "",
        "meet_number": None,
        "meet_order": None
    }
    
    print(f"Processing title: '{page_title}'")
    
    # Look for "Results For" pattern in page title (may be "Results For" or "Results ForNVJCYO")
    title_match = re.search(r"Results\s*For\s*(.+?)(?:\s*$|\s*\|)", page_title, re.IGNORECASE)
    if title_match:
        full_meet_name = title_match.group(1).strip()
        meet_info["meet_name"] = full_meet_name
        print(f"Found meet name: '{full_meet_name}'")
        
        # Extract series and number from patterns like:
        # "NVJCYO Cross Country Developmental Meet 1"
        # "NVJCYO Cross Country Developmental Meet 2"
        series_match = re.search(r"(.+?)\s+Meet\s+(\d+)$", full_meet_name, re.IGNORECASE)
        if series_match:
            meet_info["meet_series"] = series_match.group(1).strip()
            meet_info["meet_number"] = int(series_match.group(2))
            meet_info["meet_order"] = int(series_match.group(2))  # Same as number for this format
            print(f"Found series: '{meet_info['meet_series']}', number: {meet_info['meet_number']}")
        else:
            # Try other patterns like "Championship", "Invitational", etc.
            meet_info["meet_series"] = full_meet_name
            print(f"No Meet N pattern, using full name as series: '{full_meet_name}'")
            
            # Look for ordinal numbers (1st, 2nd, 3rd, etc.)
            ordinal_match = re.search(r"(\d+)(?:st|nd|rd|th)", full_meet_name, re.IGNORECASE)
            if ordinal_match:
                meet_info["meet_number"] = int(ordinal_match.group(1))
                meet_info["meet_order"] = int(ordinal_match.group(1))
                print(f"Found ordinal: {meet_info['meet_number']}")
    else:
        print("No 'Results For' pattern found")
    
    return meet_info

# Test with actual titles from the webpage
test_titles = [
    "Results ForNVJCYO Cross Country Developmental Meet 2",
    "Results For NVJCYO Cross Country Developmental Meet 1", 
    "NVJCYO Cross Country Developmental Meet 2 - Results"
]

for title in test_titles:
    print("=" * 50)
    result = extract_meet_info(title)
    print(f"Result: {result}")
    print()