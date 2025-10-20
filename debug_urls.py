import asyncio
from playwright.async_api import async_playwright

async def debug_url(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # Run with browser visible
        page = await browser.new_page()
        
        print(f"\n{'='*80}")
        print(f"Testing URL: {url}")
        print(f"{'='*80}\n")
        
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)  # Wait for JS to settle
        
        # Get page title
        title = await page.title()
        print(f"Page title: {title}")
        
        # Check if dropdown exists
        try:
            dropdown = await page.wait_for_selector("select#resultSetId", timeout=3000)
            if dropdown:
                current_value = await dropdown.evaluate("el => el.value")
                options = await page.eval_on_selector_all(
                    "select#resultSetId option",
                    "opts => opts.map(o => ({value: o.value, text: o.textContent.trim(), selected: o.selected}))"
                )
                print(f"\nDropdown found!")
                print(f"Current value: {current_value}")
                print(f"Available options ({len(options)} total):")
                for opt in options[:10]:  # Show first 10
                    selected = " [SELECTED]" if opt['selected'] else ""
                    print(f"  - {opt['value']}: {opt['text']}{selected}")
        except Exception as e:
            print(f"No dropdown found: {e}")
        
        # Get first few rows of the table
        try:
            rows = await page.eval_on_selector_all(
                "table tbody tr",
                """trs => trs.slice(0, 3).map(tr => {
                    const tds = Array.from(tr.querySelectorAll('td'));
                    return tds.map(td => td.innerText.trim());
                })"""
            )
            print(f"\nFirst 3 rows of table:")
            for i, row in enumerate(rows, 1):
                print(f"  Row {i}: {row[:5]}")  # Show first 5 columns
        except Exception as e:
            print(f"Error getting table rows: {e}")
        
        input("\nPress Enter to close browser and test next URL...")
        await browser.close()

async def main():
    urls = [
        "https://runsignup.com/race/results/?raceId=154708#resultSetId-598164;perpage:5000",
        "https://runsignup.com/race/results/?raceId=154708#resultSetId-598315;perpage:5000",
    ]
    
    for url in urls:
        await debug_url(url)

if __name__ == "__main__":
    asyncio.run(main())
