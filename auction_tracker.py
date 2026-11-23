#!/usr/bin/env python3
"""
Texas Auction Tracker — scrapes Hubzu, Xome, Auction.com, ServiceLink
and writes results to a Google Sheet. Runs on GitHub Actions, no Pax needed.

NO paid API calls — pure Python + requests + BeautifulSoup.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import gspread
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials

# ── Config ──────────────────────────────────────────────────────────────
SHEET_ID = "1pniC4BlhVocnmG368mKM90Xn99HOiPulwm98JlnicB4"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
CT = timezone(timedelta(hours=-6))  # Central Time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Google Sheets helpers ───────────────────────────────────────────────

def get_sheet():
    """Authenticate and return a gspread spreadsheet object."""
    creds_file = os.environ.get("GOOGLE_CREDS_FILE", "credentials.json")
    creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID)


def safe_request(url, retries=3, delay=2, timeout=30):
    """GET with retries and polite delay."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                wait = delay * (attempt + 2)
                print(f"  Rate limited on {url}, waiting {wait}s...")
                time.sleep(wait)
                continue
            print(f"  HTTP {resp.status_code} for {url}")
            return resp
        except requests.RequestException as e:
            print(f"  Request error ({attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return None


# ── Scraper: Hubzu ──────────────────────────────────────────────────────

def scrape_hubzu(max_pages=30):
    """Scrape Hubzu Texas auction listings."""
    print("\n=== Scraping Hubzu ===")
    properties = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        url = f"https://www.hubzu.com/tx/auctions?pageNumber={page}"
        print(f"  Page {page}...")
        resp = safe_request(url)
        if not resp or resp.status_code != 200:
            print(f"  Stopping Hubzu at page {page}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        # Find property cards/links
        links = soup.find_all("a", href=re.compile(r"/\d+-.*-home/"))
        if not links:
            # Try alternate patterns
            links = soup.find_all("a", href=re.compile(r"/property/"))
            if not links:
                print(f"  No listings found on page {page}, stopping")
                break

        page_count = 0
        for link in links:
            href = link.get("href", "")
            # Extract property ID from URL
            id_match = re.search(r"/(\d+)-", href)
            if not id_match:
                id_match = re.search(r"/property/(\d+)", href)
            if not id_match:
                continue

            prop_id = id_match.group(1)
            full_id = f"Hubzu|{prop_id}"

            if full_id in seen_ids:
                continue
            seen_ids.add(full_id)

            # Extract address from link text or nearby elements
            address = link.get_text(strip=True)
            if not address or len(address) < 5:
                # Try parent card
                card = link.find_parent(class_=re.compile(r"card|listing|property"))
                if card:
                    addr_el = card.find(class_=re.compile(r"address|location|title"))
                    if addr_el:
                        address = addr_el.get_text(strip=True)

            # Extract price
            price = ""
            card = link.find_parent(class_=re.compile(r"card|listing|property|item"))
            if card:
                price_el = card.find(class_=re.compile(r"price|bid|amount"))
                if price_el:
                    price_text = price_el.get_text(strip=True)
                    price_match = re.search(r"\$[\d,]+", price_text)
                    if price_match:
                        price = price_match.group(0)

            # Parse city/zip from address
            city, zipcode = parse_city_zip(address)

            full_url = href if href.startswith("http") else f"https://www.hubzu.com{href}"

            properties.append({
                "id": full_id,
                "site": "Hubzu",
                "address": address,
                "city": city,
                "zip": zipcode,
                "start_price": price,
                "url": full_url,
                "status": "LIVE",
            })
            page_count += 1

        print(f"  Found {page_count} properties on page {page}")
        if page_count == 0:
            break
        time.sleep(1.5)  # Be polite

    print(f"  Total Hubzu: {len(properties)}")
    return properties


# ── Scraper: Xome ───────────────────────────────────────────────────────

def scrape_xome(max_pages=20):
    """Scrape Xome Texas auction listings."""
    print("\n=== Scraping Xome ===")
    properties = []
    seen_ids = set()

    # Xome main Texas auction page
    for page in range(1, max_pages + 1):
        url = f"https://www.xome.com/auctions/tx?page={page}"
        print(f"  Page {page}...")
        resp = safe_request(url)
        if not resp or resp.status_code != 200:
            print(f"  Stopping Xome at page {page}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        # Find property links — Xome uses numeric IDs in URLs
        links = soup.find_all("a", href=re.compile(r"/auctions/.*-\d+$"))
        if not links:
            links = soup.find_all("a", href=re.compile(r"/auctions/[a-z0-9-]+-[a-z]{2}-\d{5}"))
            if not links:
                print(f"  No listings found on page {page}, stopping")
                break

        page_count = 0
        for link in links:
            href = link.get("href", "")
            # Extract Xome property ID (last number in URL)
            id_match = re.search(r"-(\d{6,})$", href)
            if not id_match:
                continue

            prop_id = id_match.group(1)
            full_id = f"Xome|{prop_id}"

            if full_id in seen_ids:
                continue
            seen_ids.add(full_id)

            # Address from link text
            address = link.get_text(strip=True)
            if not address or len(address) < 5:
                parent = link.find_parent(class_=re.compile(r"card|listing|property"))
                if parent:
                    addr_el = parent.find(class_=re.compile(r"address|location"))
                    if addr_el:
                        address = addr_el.get_text(strip=True)

            # Price
            price = ""
            card = link.find_parent(class_=re.compile(r"card|listing|property|item"))
            if card:
                price_el = card.find(string=re.compile(r"\$[\d,]+"))
                if price_el:
                    price_match = re.search(r"\$[\d,]+", str(price_el))
                    if price_match:
                        price = price_match.group(0)

            city, zipcode = parse_city_zip(address)
            full_url = href if href.startswith("http") else f"https://www.xome.com{href}"

            properties.append({
                "id": full_id,
                "site": "Xome",
                "address": address,
                "city": city,
                "zip": zipcode,
                "start_price": price,
                "url": full_url,
                "status": "LIVE",
            })
            page_count += 1

        print(f"  Found {page_count} properties on page {page}")
        if page_count == 0:
            break
        time.sleep(1.5)

    print(f"  Total Xome: {len(properties)}")
    return properties


# ── Scraper: Auction.com ────────────────────────────────────────────────

def scrape_auction_com(max_pages=20):
    """Scrape Auction.com Texas listings via their search API."""
    print("\n=== Scraping Auction.com ===")
    properties = []
    seen_ids = set()

    # Auction.com uses a JSON API behind their search
    api_base = "https://www.auction.com/api/search"

    for page in range(1, max_pages + 1):
        # Try the HTML page first
        url = f"https://www.auction.com/residential/TX/active_lt/?page={page}"
        print(f"  Page {page}...")
        resp = safe_request(url)
        if not resp or resp.status_code != 200:
            # Try alternate URL pattern
            url = f"https://www.auction.com/residential/TX/?page={page}"
            resp = safe_request(url)
            if not resp or resp.status_code != 200:
                print(f"  Stopping Auction.com at page {page}")
                break

        soup = BeautifulSoup(resp.text, "html.parser")

        # Look for property data in script tags (Auction.com embeds JSON)
        script_data = None
        for script in soup.find_all("script"):
            text = script.string or ""
            if "listingId" in text or "auctionId" in text or "propertyData" in text:
                # Try to extract JSON
                json_match = re.search(r'(\[{.*?"address".*?}\])', text, re.DOTALL)
                if json_match:
                    try:
                        script_data = json.loads(json_match.group(1))
                    except json.JSONDecodeError:
                        pass
                # Try __NEXT_DATA__ or similar
                json_match = re.search(r'"listings"\s*:\s*(\[.*?\])\s*[,}]', text, re.DOTALL)
                if json_match:
                    try:
                        script_data = json.loads(json_match.group(1))
                    except json.JSONDecodeError:
                        pass

        if script_data and isinstance(script_data, list):
            page_count = 0
            for item in script_data:
                prop_id = str(item.get("listingId", item.get("auctionId", item.get("id", ""))))
                if not prop_id:
                    continue
                full_id = f"Auction.com|{prop_id}"
                if full_id in seen_ids:
                    continue
                seen_ids.add(full_id)

                address = item.get("address", item.get("streetAddress", ""))
                city = item.get("city", "")
                zipcode = item.get("zip", item.get("zipCode", ""))
                price = ""
                for key in ["startingBid", "currentBid", "openingBid", "reservePrice", "price"]:
                    if key in item and item[key]:
                        price = f"${int(float(str(item[key]).replace(',','').replace('$',''))):,}"
                        break

                detail_url = item.get("url", item.get("detailUrl", ""))
                if detail_url and not detail_url.startswith("http"):
                    detail_url = f"https://www.auction.com{detail_url}"

                properties.append({
                    "id": full_id,
                    "site": "Auction.com",
                    "address": address,
                    "city": city,
                    "zip": str(zipcode),
                    "start_price": price,
                    "url": detail_url or url,
                    "status": "LIVE",
                })
                page_count += 1
            print(f"  Found {page_count} from JSON data")
            if page_count == 0:
                break
        else:
            # Fallback: parse HTML links
            links = soup.find_all("a", href=re.compile(r"/details/"))
            if not links:
                links = soup.find_all("a", href=re.compile(r"/residential/.*\d+"))

            page_count = 0
            for link in links:
                href = link.get("href", "")
                id_match = re.search(r"[/-](\d{5,})", href)
                if not id_match:
                    continue

                prop_id = id_match.group(1)
                full_id = f"Auction.com|{prop_id}"
                if full_id in seen_ids:
                    continue
                seen_ids.add(full_id)

                address = link.get_text(strip=True)
                city, zipcode = parse_city_zip(address)
                full_url = href if href.startswith("http") else f"https://www.auction.com{href}"

                properties.append({
                    "id": full_id,
                    "site": "Auction.com",
                    "address": address,
                    "city": city,
                    "zip": zipcode,
                    "url": full_url,
                    "start_price": "",
                    "status": "LIVE",
                })
                page_count += 1

            print(f"  Found {page_count} from HTML links")
            if page_count == 0:
                break

        time.sleep(2)  # Auction.com is stricter

    print(f"  Total Auction.com: {len(properties)}")
    return properties


# ── Scraper: ServiceLink ────────────────────────────────────────────────

def scrape_servicelink(max_pages=10):
    """Scrape ServiceLink Auction Texas listings."""
    print("\n=== Scraping ServiceLink ===")
    properties = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        url = f"https://www.servicelinkauction.com/search?state=TX&page={page}"
        print(f"  Page {page}...")
        resp = safe_request(url)
        if not resp or resp.status_code != 200:
            # Try alternate URLs
            url = f"https://www.servicelinkauction.com/TX?page={page}"
            resp = safe_request(url)
            if not resp or resp.status_code != 200:
                print(f"  Stopping ServiceLink at page {page}")
                break

        soup = BeautifulSoup(resp.text, "html.parser")

        # ServiceLink property links
        links = soup.find_all("a", href=re.compile(r"/(property|listing|auction)/.*tx.*", re.IGNORECASE))
        if not links:
            # Broader search
            links = soup.find_all("a", href=re.compile(r"/[a-z0-9-]+-tx-\d{5}", re.IGNORECASE))
            if not links:
                # Even broader
                links = soup.find_all("a", href=re.compile(r"(united-states|tx)", re.IGNORECASE))

        if not links:
            print(f"  No listings found on page {page}, stopping")
            break

        page_count = 0
        for link in links:
            href = link.get("href", "")
            # Skip non-property links
            if any(x in href.lower() for x in ["login", "register", "about", "contact", "help", "faq", "blog"]):
                continue

            # Create a slug-based ID
            slug = href.rstrip("/").split("/")[-1]
            if len(slug) < 5:
                continue
            full_id = f"ServiceLink|{slug}"
            if full_id in seen_ids:
                continue
            seen_ids.add(full_id)

            # Extract address
            address = link.get_text(strip=True)
            if not address or len(address) < 5:
                card = link.find_parent(class_=re.compile(r"card|listing|property|result"))
                if card:
                    addr_el = card.find(class_=re.compile(r"address|location|title"))
                    if addr_el:
                        address = addr_el.get_text(strip=True)

            # Price
            price = ""
            card = link.find_parent(class_=re.compile(r"card|listing|property|result|item"))
            if card:
                price_el = card.find(string=re.compile(r"\$[\d,]+"))
                if price_el:
                    price_match = re.search(r"\$[\d,]+", str(price_el))
                    if price_match:
                        price = price_match.group(0)

            city, zipcode = parse_city_zip(address)
            full_url = href if href.startswith("http") else f"https://www.servicelinkauction.com{href}"

            properties.append({
                "id": full_id,
                "site": "ServiceLink",
                "address": address,
                "city": city,
                "zip": zipcode,
                "start_price": price,
                "url": full_url,
                "status": "LIVE",
            })
            page_count += 1

        print(f"  Found {page_count} properties on page {page}")
        if page_count == 0:
            break
        time.sleep(1.5)

    print(f"  Total ServiceLink: {len(properties)}")
    return properties


# ── Helpers ─────────────────────────────────────────────────────────────

def parse_city_zip(address: str) -> tuple[str, str]:
    """Extract city and ZIP from an address string."""
    if not address:
        return ("", "")

    # Try to find ZIP
    zip_match = re.search(r"\b(\d{5})\b", address)
    zipcode = zip_match.group(1) if zip_match else ""

    # Try to find city — look for "City, TX" or "City Tx"
    city_match = re.search(r"([A-Za-z\s]+?)[\s,]+(?:TX|Tx|tx|Texas)\b", address)
    city = city_match.group(1).strip() if city_match else ""

    return (city, zipcode)


def dedup_properties(existing_ids: set, new_props: list) -> list:
    """Remove properties already in the sheet."""
    unique = []
    for p in new_props:
        if p["id"] not in existing_ids:
            unique.append(p)
            existing_ids.add(p["id"])
    return unique


# ── Sheet writing ───────────────────────────────────────────────────────

def write_to_sheet(spreadsheet, properties: list):
    """Write new properties to the Listings and Cloud Results tabs, log to Cloud Log."""
    now_str = datetime.now(CT).strftime("%Y-%m-%d %H:%M:%S")

    # Get existing IDs from Listings
    listings_ws = spreadsheet.worksheet("Listings")
    existing_data = listings_ws.col_values(1)  # Column A = id
    existing_ids = set(existing_data[1:])  # Skip header

    # Get existing IDs from Cloud Results
    cloud_ws = spreadsheet.worksheet("Cloud Results")
    cloud_data = cloud_ws.col_values(1)
    cloud_ids = set(cloud_data[1:])

    # Dedup against both
    all_existing = existing_ids | cloud_ids
    new_props = dedup_properties(all_existing, properties)

    print(f"\n--- Writing results ---")
    print(f"  Total scraped: {len(properties)}")
    print(f"  Already in sheet: {len(properties) - len(new_props)}")
    print(f"  New to add: {len(new_props)}")

    if new_props:
        # Append to Cloud Results
        rows = []
        for p in new_props:
            rows.append([
                p["id"],
                p["site"],
                p["address"],
                p["city"],
                p["zip"],
                p["start_price"],
                p.get("url", ""),
                now_str,
                p.get("status", "LIVE"),
            ])

        # Ensure Cloud Results has the right headers
        cr_headers = cloud_ws.row_values(1)
        if not cr_headers or cr_headers[0] != "id":
            cloud_ws.update("A1:I1", [[
                "id", "site", "address", "city", "zip",
                "start_price", "url", "scraped_at", "status"
            ]])

        cloud_ws.append_rows(rows, value_input_option="USER_ENTERED")
        print(f"  Appended {len(rows)} rows to Cloud Results")

        # Also append to Listings (keeping same column structure)
        listing_rows = []
        for p in new_props:
            listing_rows.append([
                p["id"],
                p["site"],
                p["address"],
                p["city"],
                p["zip"],
                "",  # auction_date — will be filled by detail scraping later
                p["start_price"],
                p.get("url", ""),
            ])
        listings_ws.append_rows(listing_rows, value_input_option="USER_ENTERED")
        print(f"  Appended {len(listing_rows)} rows to Listings")

    # Always log to Cloud Log
    log_ws = spreadsheet.worksheet("Cloud Log")
    log_rows = []
    for p in properties[:500]:  # Cap at 500 log entries per run
        log_rows.append([
            now_str,
            p["id"],
            p["address"],
            p.get("status", "LIVE"),
        ])
    if log_rows:
        log_ws.append_rows(log_rows, value_input_option="USER_ENTERED")
        print(f"  Logged {len(log_rows)} entries to Cloud Log")

    return len(new_props)


# ── Main ────────────────────────────────────────────────────────────────

def run_tracker():
    """Main entry point — scrape all sites and write to sheet."""
    start = time.time()
    now = datetime.now(CT)
    print(f"=== Texas Auction Tracker — {now.strftime('%Y-%m-%d %H:%M CT')} ===")

    # Check if it's a weekday (Mon-Fri)
    if now.weekday() >= 5:  # 5=Sat, 6=Sun
        print("Weekend — skipping (Mon-Fri only)")
        return

    # Check hours (7 AM - 6 PM CT)
    if not (7 <= now.hour < 18):
        print(f"Outside hours ({now.hour}:00 CT) — skipping (7AM-6PM only)")
        return

    print("Connecting to Google Sheet...")
    spreadsheet = get_sheet()

    all_properties = []

    # Scrape all sites
    try:
        all_properties.extend(scrape_hubzu())
    except Exception as e:
        print(f"  ERROR scraping Hubzu: {e}")

    try:
        all_properties.extend(scrape_xome())
    except Exception as e:
        print(f"  ERROR scraping Xome: {e}")

    try:
        all_properties.extend(scrape_auction_com())
    except Exception as e:
        print(f"  ERROR scraping Auction.com: {e}")

    try:
        all_properties.extend(scrape_servicelink())
    except Exception as e:
        print(f"  ERROR scraping ServiceLink: {e}")

    print(f"\n=== Total properties scraped: {len(all_properties)} ===")

    if all_properties:
        new_count = write_to_sheet(spreadsheet, all_properties)
        print(f"\n=== Done — {new_count} new properties added ===")
    else:
        print("\n=== No properties found — check site access ===")

    elapsed = time.time() - start
    print(f"Elapsed: {elapsed:.1f}s")


if __name__ == "__main__":
    run_tracker()
