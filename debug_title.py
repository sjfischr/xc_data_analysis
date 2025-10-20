import asyncio
from playwright.async_api import async_playwright

async def test_page_title():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        url = "https://runsignup.com/race/results/?raceId=154708#resultSetId-598164;perpage:5000"
        print(f"Testing URL: {url}")
        
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)  # Wait for content to load
        
        title = await page.title()
        print(f"Page title: '{title}'")
        
        # Test the meet extraction function with updated patterns
        import re
        def extract_meet_info(page_title: str, page_content: str = "") -> dict:
            meet_info = {
                "meet_name": "",
                "meet_series": "",
                "meet_number": None,
                "meet_order": None
            }
            
            # Look for patterns in page title:
            # 1. "Results For..." or "Results ForNVJCYO..." (with/without space)
            # 2. "NVJCYO Cross Country Developmental Meet 2 Results" (meet name + Results)
            
            title_match = None
            
            # Pattern 1: "Results For..." 
            title_match = re.search(r"Results\s*For\s*(.+?)(?:\s*$|\s*\|)", page_title, re.IGNORECASE)
            
            # Pattern 2: "...Results" at the end
            if not title_match:
                title_match = re.search(r"(.+?)\s+Results\s*$", page_title, re.IGNORECASE)
            
            if title_match:
                full_meet_name = title_match.group(1).strip()
                meet_info["meet_name"] = full_meet_name
                
                series_match = re.search(r"(.+?)\s+Meet\s+(\d+)$", full_meet_name, re.IGNORECASE)
                if series_match:
                    meet_info["meet_series"] = series_match.group(1).strip()
                    meet_info["meet_number"] = int(series_match.group(2))
                    meet_info["meet_order"] = int(series_match.group(2))
                else:
                    meet_info["meet_series"] = full_meet_name
                    
                    ordinal_match = re.search(r"(\d+)(?:st|nd|rd|th)", full_meet_name, re.IGNORECASE)
                    if ordinal_match:
                        meet_info["meet_number"] = int(ordinal_match.group(1))
                        meet_info["meet_order"] = int(ordinal_match.group(1))
            
            return meet_info
        
        meet_info = extract_meet_info(title)
        print(f"Meet info extracted: {meet_info}")
        
        await browser.close()

asyncio.run(test_page_title())