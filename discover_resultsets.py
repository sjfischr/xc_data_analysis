"""
Script to discover all available result sets for a RunSign up race
"""
import asyncio
from playwright.async_api import async_playwright

async def discover_resultsets(race_url):
    """Navigate to a race results page and list all available result sets"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Just go to the base race URL without a specific resultSetId
        base_url = race_url.split('#')[0]
        print(f"\nDiscovering result sets for: {base_url}")
        print("="*80)
        
        await page.goto(base_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        
        # Find the dropdown
        try:
            dropdown = await page.wait_for_selector("select#resultSetId", timeout=5000)
            options = await page.eval_on_selector_all(
                "select#resultSetId option",
                "opts => opts.map(o => ({value: o.value, text: o.textContent.trim()}))"
            )
            
            print(f"\nFound {len(options)} result sets:\n")
            for i, opt in enumerate(options, 1):
                print(f"{i:2d}. resultSetId={opt['value']:6s} | {opt['text']}")
                
        except Exception as e:
            print(f"Error: Could not find result set dropdown: {e}")
        
        await browser.close()

async def main():
    # Test both race IDs from your URLs
    await discover_resultsets("https://runsignup.com/race/results/?raceId=154708")
    await discover_resultsets("https://runsignup.com/Race/Results/154050")

if __name__ == "__main__":
    asyncio.run(main())
