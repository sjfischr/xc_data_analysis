import argparse
import asyncio
import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import pandas as pd
from dateutil import parser as dateparser
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# -------- Utilities --------

def ensure_dirs():
    os.makedirs("data/raw", exist_ok=True)
    os.makedirs("data/merged", exist_ok=True)

def normalize_filename(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", s.strip())
    return s.strip("_").lower() or "results"

HASH_PARAM_DEFAULT_DELIMS = {
    "perpage": ":",
    "resultsetid": "-",
}

def _split_hash_part(part: str):
    for delim in ("-", ":"):
        if delim in part:
            key, val = part.split(delim, 1)
            if key:
                return key.strip(), val.strip(), delim
    return None, None, None

def extract_hash_params(url: str) -> dict:
    if "#" not in url:
        return {}
    _, frag = url.split("#", 1)
    params = {}
    for part in frag.split(";"):
        part = part.strip()
        if not part:
            continue
        key, val, delim = _split_hash_part(part)
        if key:
            params[key.lower()] = {"value": val, "delim": delim}
    return params

def add_or_replace_hash_param(url: str, key: str, value: str) -> str:
    """Add or replace a hash parameter while preserving the delimiter style."""
    key_lower = key.lower()
    default_delim = HASH_PARAM_DEFAULT_DELIMS.get(key_lower, ":")
    if "#" not in url:
        return f"{url}#{key}{default_delim}{value}"

    base, frag = url.split("#", 1)
    parts = []
    replaced = False
    for part in frag.split(";"):
        part = part.strip()
        if not part:
            continue
        part_key, _, part_delim = _split_hash_part(part)
        if part_key and part_key.lower() == key_lower:
            delim = part_delim or default_delim
            parts.append(f"{key}{delim}{value}")
            replaced = True
        else:
            parts.append(part)

    if not replaced:
        parts.append(f"{key}{default_delim}{value}")

    return f"{base}#{';'.join(parts)}"

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def time_to_seconds(t: str):
    if not t or pd.isna(t):
        return None
    s = str(t).strip()
    if not s:
        return None
    
    # Remove any non-numeric characters except : and .
    # This handles cases like "9:01.65" or "1:23:45.67"
    cleaned = re.sub(r'[^\d:.]', '', s)
    if not cleaned:
        return None
    
    # Split by colon to get time parts
    parts = cleaned.split(":")
    try:
        if len(parts) == 2:
            # mm:ss.milliseconds format
            mm, ss = parts
            return int(mm) * 60 + float(ss)
        elif len(parts) == 3:
            # hh:mm:ss.milliseconds format  
            hh, mm, ss = parts
            return int(hh) * 3600 + int(mm) * 60 + float(ss)
        elif len(parts) == 1:
            # Just seconds (unlikely but possible)
            return float(parts[0])
        else:
            return None
    except (ValueError, TypeError):
        return None

def stable_hash(*values) -> str:
    h = hashlib.sha1()
    for v in values:
        h.update((str(v) if v is not None else "").encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:16]

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
        
        # Extract series and number from patterns like:
        # "NVJCYO Cross Country Developmental Meet 1"
        # "NVJCYO Cross Country Developmental Meet 2"
        series_match = re.search(r"(.+?)\s+Meet\s+(\d+)$", full_meet_name, re.IGNORECASE)
        if series_match:
            meet_info["meet_series"] = series_match.group(1).strip()
            meet_info["meet_number"] = int(series_match.group(2))
            meet_info["meet_order"] = int(series_match.group(2))  # Same as number for this format
        else:
            # Try other patterns like "Championship", "Invitational", etc.
            meet_info["meet_series"] = full_meet_name
            
            # Look for ordinal numbers (1st, 2nd, 3rd, etc.)
            ordinal_match = re.search(r"(\d+)(?:st|nd|rd|th)", full_meet_name, re.IGNORECASE)
            if ordinal_match:
                meet_info["meet_number"] = int(ordinal_match.group(1))
                meet_info["meet_order"] = int(ordinal_match.group(1))
    
    return meet_info

# -------- Core Extraction --------

RESULTS_TABLE_SELECTORS = [
    "table",                              # fallback
    "table.rsu-results__table",           # common RunSignup class
    "div.results table",                  # sometimes wrapped
]

RESULTSET_DROPDOWN_SELECTORS = [
    "select#resultSetId",                 # native select
    "select[name='resultSetId']",
    "div.resultset-select select",        # custom wrap
]

async def find_first(page, selectors, timeout=6000):
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout, state="visible")
            if el:
                return el, sel
        except PWTimeout:
            continue
    return None, None

async def get_table_data(page):
    # Try to find a results table
    table_el, sel = await find_first(page, RESULTS_TABLE_SELECTORS, timeout=10000)
    if not table_el:
        return None, None

    # Extract headers
    headers = await page.eval_on_selector_all(
        f"{sel} thead tr th",
        "ths => ths.map(th => th.innerText.trim())"
    )
    if not headers or len(headers) == 0:
        # Try first row as header
        headers = await page.eval_on_selector_all(
            f"{sel} tr:first-child th, {sel} tr:first-child td",
            "cells => cells.map(td => td.innerText.trim())"
        )


    # Extract rows
    rows = await page.eval_on_selector_all(
        f"{sel} tbody tr",
        """trs => trs.map(tr => {
            const tds = Array.from(tr.querySelectorAll('td'));
            return tds.map(td => td.innerText.trim());
        })"""
    )

    # Fallback if no tbody
    if not rows or len(rows) == 0:
        rows = await page.eval_on_selector_all(
            f"{sel} tr:not(:first-child)",
            """trs => trs.map(tr => {
                const tds = Array.from(tr.querySelectorAll('td'));
                return tds.map(td => td.innerText.trim());
            })"""
        )

    # Column count alignment
    if headers and rows:
        col_count = max(len(headers), max((len(r) for r in rows), default=0))
        if len(headers) < col_count:
            headers = headers + [f"col_{i}" for i in range(len(headers), col_count)]
        aligned_rows = []
        for r in rows:
            if len(r) < col_count:
                r = r + [""] * (col_count - len(r))
            elif len(r) > col_count:
                r = r[:col_count]
            aligned_rows.append(r)
        rows = aligned_rows

    return headers, rows

async def get_current_resultset_label(page):
    # Try to read the label of the active result set (useful for filenames/metadata)
    # Many pages show an <option selected>
    for sel in RESULTSET_DROPDOWN_SELECTORS:
        try:
            selected = await page.eval_on_selector_all(
                sel + " option[selected]",
                "opts => opts.map(o => o.textContent.trim())"
            )
            if selected and len(selected) > 0:
                return selected[0]
        except PWTimeout:
            pass
    # Fallback: text near the table header
    try:
        h = await page.text_content("h2, h3")
        if h:
            return h.strip()
    except:
        pass
    return "results"

async def iterate_all_resultsets(page):
    """Return a list of (value, text) for all options, for --all mode."""
    for sel in RESULTSET_DROPDOWN_SELECTORS:
        try:
            dropdown = await page.wait_for_selector(sel, timeout=3000)
            options = await page.eval_on_selector_all(
                sel + " option",
                "opts => opts.map(o => ({value: o.value, text: o.textContent.trim(), selected: o.selected}))"
            )
            if options and len(options) > 0:
                return sel, options
        except PWTimeout:
            continue
    return None, []

async def scrape_resultset(page, url, perpage=5000, set_value=None, set_text=None):
    # Force perpage in the hash (if supported by this page variant)
    effective_url = add_or_replace_hash_param(url, "perpage", str(perpage))
    hash_params = extract_hash_params(effective_url)
    target_resultset = (hash_params.get("resultsetid") or {}).get("value")

    # Determine which result set we want BEFORE navigating
    desired_resultset = set_value or target_resultset

    # If we have a specific result set ID, include it in the URL hash before loading
    if desired_resultset:
        effective_url = add_or_replace_hash_param(effective_url, "resultSetId", str(desired_resultset))
    
    await page.goto(effective_url, wait_until="domcontentloaded")
    
    # Wait for any initial JS to execute
    await page.wait_for_timeout(1200)

    # Additional step: if there's a dropdown and we have a desired result set, ensure it's selected
    if desired_resultset:
        dropdown_el, dropdown_selector = await find_first(page, RESULTSET_DROPDOWN_SELECTORS, timeout=3000)
        if dropdown_el:
            # Check current selection and only change if needed
            try:
                current_value = await dropdown_el.evaluate("el => el.value")
                if current_value != str(desired_resultset):
                    await dropdown_el.select_option(value=str(desired_resultset))
                    # Give the client-side JS a moment to fetch the new table content
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except PWTimeout:
                        pass
                    await page.wait_for_timeout(1500)
            except Exception as e:
                # If dropdown interaction fails, the hash-based navigation should still work
                pass

    # Wait for table to appear and settle
    await page.wait_for_timeout(1000)  # small grace period
    headers, rows = await get_table_data(page)
    label = set_text or (await get_current_resultset_label(page))
    page_title = (await page.title()) or ""
    scraped_at = now_iso()

    # Page-level hints (meet name/date sometimes visible)
    # We'll try to read any "Race" header block; keep it best-effort.
    try:
        header_text = await page.text_content("div#content, main")  # big container
    except:
        header_text = ""

    # Extract meet information from page title and content
    meet_info = extract_meet_info(page_title, header_text or "")

    meta = {
        "page_title": page_title.strip(),
        "resultset_label": label.strip() if label else "results",
        "scraped_at": scraped_at,
        "source_url": effective_url,
        "page_header_excerpt": (header_text or "")[:4000],
        "meet_name": meet_info["meet_name"],
        "meet_series": meet_info["meet_series"],
        "meet_number": meet_info["meet_number"],
        "meet_order": meet_info["meet_order"]
    }
    return headers, rows, meta

def clean_athlete_name(name_str):
    """Clean up athlete names that may have line breaks or extra formatting."""
    if not name_str or pd.isna(name_str):
        return ""
    
    # Remove quotes and normalize whitespace
    cleaned = str(name_str).replace('"', '').strip()
    
    # Replace multiple whitespace (including newlines) with single spaces
    cleaned = re.sub(r'\s+', ' ', cleaned)
    
    # Handle cases where name format is "Initial FirstName LastName"
    # Look for pattern: single letter, then longer name, then last name
    parts = cleaned.split()
    if len(parts) >= 3 and len(parts[0]) == 1 and parts[0].isalpha():
        # Skip the initial, use the rest
        cleaned = ' '.join(parts[1:])
    
    return cleaned.strip()

def coerce_known_columns(df: pd.DataFrame, meta: dict = None) -> pd.DataFrame:
    # Try to standardize a few common RunSignup column labels if present
    rename_map = {
        "Place": "place_overall",
        "Gender": "gender", 
        "Bib": "bib",
        "Name": "athlete_full_name",
        "Time": "finish_time_str",
        "Clock Time": "finish_time_str",  # Common in RunSignup results
        "Chip Time": "finish_time_str",   # Alternative time column
        "Gun Time": "gun_time_str",       # Sometimes separate from chip time
        "Pace": "pace_str",
        "Team": "team_name",
        "Team Name": "team_name",         # Common variation
        "Age": "age",
        "Grade": "grade",
        "Year": "grade",                  # School grade often called "Year"
        "City": "city",
        "State": "state", 
        "Division": "division",
        "Group/Team Name": "group_team_name",  # Additional team field
    }
    
    for k, v in list(rename_map.items()):
        # case-insensitive match
        for col in df.columns:
            if col.strip().lower() == k.lower():
                df.rename(columns={col: v}, inplace=True)
                break

    # Clean up athlete names if present
    if "athlete_full_name" in df.columns:
        df["athlete_full_name"] = df["athlete_full_name"].apply(clean_athlete_name)

    # derive numeric time from any available time column
    time_columns = ["finish_time_str", "gun_time_str"]
    for time_col in time_columns:
        if time_col in df.columns:
            df[f"{time_col.replace('_str', '_s')}"] = df[time_col].apply(time_to_seconds)
            break  # Use the first available time column for the main time_s field
    
    # If no standard time column found, create empty one
    if "finish_time_s" not in df.columns:
        df["finish_time_s"] = None

    # Add meet information to all rows if metadata is provided
    if meta:
        df["meet_name"] = meta.get("meet_name", "")
        df["meet_series"] = meta.get("meet_series", "")  
        df["meet_number"] = meta.get("meet_number", None)
        df["meet_order"] = meta.get("meet_order", None)
        df["scraped_at"] = meta.get("scraped_at", "")

    return df

def read_urls_from_file(file_path: str) -> list[str]:
    """Read URLs from a control file, one per line, ignoring comments and blank lines."""
    urls = []
    total_lines = 0
    comment_lines = 0
    blank_lines = 0
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                total_lines += 1
                original_line = line
                line = line.strip()
                
                # Skip blank lines
                if not line:
                    blank_lines += 1
                    continue
                    
                # Skip comments
                if line.startswith('#'):
                    comment_lines += 1
                    continue
                
                # Basic URL validation
                if not line.startswith(('http://', 'https://')):
                    print(f"Warning: Line {line_num} doesn't appear to be a valid URL: {line}", file=sys.stderr)
                    continue
                    
                # Check if it's a RunSignup URL (optional warning)
                if 'runsignup.com' not in line.lower():
                    print(f"Info: Line {line_num} is not a RunSignup URL, but will attempt to scrape: {line}", file=sys.stderr)
                
                urls.append(line)
                
    except FileNotFoundError:
        print(f"Error: Control file '{file_path}' not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading control file '{file_path}': {e}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Read control file '{file_path}': {total_lines} total lines, {len(urls)} URLs, {comment_lines} comments, {blank_lines} blank lines")
    
    return urls

async def main():
    parser = argparse.ArgumentParser(
        description="RunSignup Results Scraper (Playwright)",
        epilog="Examples:\n"
               "  %(prog)s --url 'https://runsignup.com/race/results/?raceId=154708#resultSetId-598164;perpage:5000'\n"
               "  %(prog)s --control-file urls.txt --all\n"
               "  %(prog)s --control-file my_races.txt --perpage 1000",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Make URL and control file mutually exclusive
    url_group = parser.add_mutually_exclusive_group(required=True)
    url_group.add_argument("--url", help="Single RunSignup results URL (can include #resultSetId-...;perpage:...)")
    url_group.add_argument("--control-file", help="File containing URLs to scrape, one per line. Lines starting with # are treated as comments.")
    
    parser.add_argument("--all", action="store_true", help="Scrape all result sets from dropdown for each URL")
    parser.add_argument("--perpage", type=int, default=5000, help="Per-page row count to request via hash param (default: 5000)")
    args = parser.parse_args()

    # Determine URLs to process
    if args.url:
        urls_to_process = [args.url]
    else:
        urls_to_process = read_urls_from_file(args.control_file)

    ensure_dirs()
    
    if not urls_to_process:
        print("No URLs to process.", file=sys.stderr)
        sys.exit(1)
    
    print(f"Processing {len(urls_to_process)} URL(s)...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        raw_paths = []

        # Process each URL
        for url_index, current_url in enumerate(urls_to_process, 1):
            print(f"\n[{url_index}/{len(urls_to_process)}] Processing: {current_url}")
            
            try:
                if args.all:
                    # Load once, enumerate options, then iterate
                    await page.goto(current_url, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)  # Give the page time to load dropdown
                    dropdown_sel, options = await iterate_all_resultsets(page)
                    print(f"  Found {len(options) if options else 0} result set options")
                    if not options:
                        print("  No dropdown options found; scraping current result set only.", file=sys.stderr)

                        headers, rows, meta = await scrape_resultset(page, current_url, args.perpage)
                        if not headers or not rows:
                            print(f"  Warning: no table found for {current_url}", file=sys.stderr)
                            continue

                        df = pd.DataFrame(rows, columns=headers)
                        df = coerce_known_columns(df, meta)

                        # Include meet info and URL index in filename
                        meet_part = ""
                        if meta.get("meet_number"):
                            meet_part = f"meet_{meta['meet_number']}_"
                        elif meta.get("meet_name"):
                            meet_part = f"{normalize_filename(meta['meet_name'][:30])}_"
                        rs_slug = normalize_filename(f"{meet_part}url_{url_index}_{meta['resultset_label']}")
                        out_path = f"data/raw/{rs_slug}.csv"
                        df.to_csv(out_path, index=False)
                        raw_paths.append(out_path)
                    else:
                        # iterate through all result sets for this URL
                        for opt in options:
                            value = opt.get("value")
                            text = opt.get("text") or value
                            print(f"  Scraping result set: {text} (value={value})")

                            headers, rows, meta = await scrape_resultset(page, current_url, args.perpage, set_value=value, set_text=text)
                            if not headers or not rows:
                                print(f"    Warning: no table for {text}", file=sys.stderr)
                                continue

                            df = pd.DataFrame(rows, columns=headers)
                            df = coerce_known_columns(df, meta)

                            # Include meet info and URL index in filename
                            meet_part = ""
                            if meta.get("meet_number"):
                                meet_part = f"meet_{meta['meet_number']}_"
                            elif meta.get("meet_name"):
                                meet_part = f"{normalize_filename(meta['meet_name'][:30])}_"
                            rs_slug = normalize_filename(f"{meet_part}url_{url_index}_{text}")
                            out_path = f"data/raw/{rs_slug}.csv"
                            df.to_csv(out_path, index=False)
                            raw_paths.append(out_path)
                else:
                    # Single result set mode for this URL
                    headers, rows, meta = await scrape_resultset(page, current_url, args.perpage)
                    if not headers or not rows:
                        print(f"  Warning: no table found for {current_url}", file=sys.stderr)
                        continue

                    df = pd.DataFrame(rows, columns=headers)
                    df = coerce_known_columns(df, meta)

                    # Include meet info and URL index in filename
                    meet_part = ""
                    if meta.get("meet_number"):
                        meet_part = f"meet_{meta['meet_number']}_"
                    elif meta.get("meet_name"):
                        meet_part = f"{normalize_filename(meta['meet_name'][:30])}_"
                    rs_slug = normalize_filename(f"{meet_part}url_{url_index}_{meta['resultset_label']}")
                    out_path = f"data/raw/{rs_slug}.csv"
                    df.to_csv(out_path, index=False)
                    raw_paths.append(out_path)
            
            except Exception as e:
                print(f"  Error processing {current_url}: {e}", file=sys.stderr)
                continue

        await browser.close()

    # Merge all raw files into one CSV (best-effort column union)
    if raw_paths:
        print(f"\nSaved {len(raw_paths)} raw CSV file(s):")
        for pth in raw_paths:
            print(f"  - {pth}")

        dfs = []
        cols_union = set()
        for pth in raw_paths:
            d = pd.read_csv(pth)
            dfs.append(d)
            cols_union.update(d.columns.tolist())

        cols_union = list(cols_union)
        aligned = []
        for d in dfs:
            for c in cols_union:
                if c not in d.columns:
                    d[c] = pd.NA
            aligned.append(d[cols_union])

        merged = pd.concat(aligned, ignore_index=True)
        merged_out = "data/merged/season_results.csv"
        merged.to_csv(merged_out, index=False)
        print(f"\nMerged CSV written: {merged_out}")
    else:
        print("No data scraped.", file=sys.stderr)

if __name__ == "__main__":
    asyncio.run(main())
