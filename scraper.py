#!/usr/bin/env python3
"""
Black Friday Deal Scraper — Pure Python, NO LLM API calls.
Scrapes major deal aggregator sites and writes new deals to Google Sheets.

Usage:
    python scraper.py                  # scrape all sites, write to Google Sheet
    python scraper.py --dry-run        # scrape but only print results, don't write
    python scraper.py --sites slickdeals dealnews   # scrape specific sites only
    python scraper.py --category laptops             # filter to a category

Future: wire into DeepSeek harness via command interface (see harness_interface.py)
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
SPREADSHEET_ID = "1VJyOWfomUrkcBruveSjDeBSUAPynJqkudLn94HWWCp8"

# Sheet tab names matching Sam's existing spreadsheet
CATEGORY_TAB_MAP = {
    "laptop": "Laptops",
    "laptops": "Laptops",
    "computer": "Computers & Desktops",
    "computers": "Computers & Desktops",
    "desktop": "Computers & Desktops",
    "desktops": "Computers & Desktops",
    "pc": "Computers & Desktops",
    "eyeglasses": "Eyeglasses (Rx)",
    "glasses": "Eyeglasses (Rx)",
    "rx": "Eyeglasses (Rx)",
    "clothes": "Clothes",
    "clothing": "Clothes",
    "apparel": "Clothes",
    "fashion": "Clothes",
    "shoes": "Shoes",
    "footwear": "Shoes",
    "sneakers": "Shoes",
    "perfume": "Perfume & Cologne",
    "cologne": "Perfume & Cologne",
    "fragrance": "Perfume & Cologne",
    "coupon": "ALL COUPON CODES",
    "coupons": "ALL COUPON CODES",
    "code": "ALL COUPON CODES",
    "codes": "ALL COUPON CODES",
}
DEFAULT_TAB = "Other"
SCRAPED_TAB = "Scraped Deals"  # New tab for bulk-scraped deals

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# Delay between requests to be polite
REQUEST_DELAY = 2  # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bf_scraper")


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def fetch_page(url: str, retries: int = 3) -> Optional[BeautifulSoup]:
    """Fetch a URL and return parsed BeautifulSoup, or None on failure."""
    for attempt in range(retries):
        try:
            log.info(f"Fetching: {url}")
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except requests.RequestException as e:
            log.warning(f"Attempt {attempt+1}/{retries} failed for {url}: {e}")
            if attempt < retries - 1:
                time.sleep(REQUEST_DELAY * (attempt + 1))
    return None


def clean_price(text: str) -> str:
    """Extract a price like '$299.99' from messy text."""
    if not text:
        return ""
    match = re.search(r"\$[\d,]+\.?\d*", text.replace(",", ""))
    if match:
        return match.group()
    # Try just digits
    match = re.search(r"[\d,]+\.?\d*", text)
    if match:
        val = match.group().replace(",", "")
        try:
            return f"${float(val):.2f}"
        except ValueError:
            pass
    return text.strip()


def calc_savings_pct(sale_price: str, reg_price: str) -> str:
    """Calculate savings percentage from two price strings."""
    try:
        sale = float(re.sub(r"[^\d.]", "", sale_price))
        reg = float(re.sub(r"[^\d.]", "", reg_price))
        if reg > 0 and sale < reg:
            return f"{((reg - sale) / reg * 100):.0f}%"
    except (ValueError, ZeroDivisionError):
        pass
    return ""


def categorize_deal(item_name: str) -> str:
    """Guess the tab/category from the item name — expanded keyword lists."""
    name_lower = item_name.lower()

    laptop_kw = [
        "laptop", "macbook", "chromebook", "notebook", "thinkpad", "ideapad",
        "pavilion", "probook", "elitebook", "spectre x360", "envy x360",
        "surface laptop", "surface pro", "zenbook", "vivobook", "swift go",
        "inspiron 14", "inspiron 15", "inspiron 16", "latitude", "xps 13",
        "xps 14", "xps 15", "xps 16", "gram 14", "gram 15", "gram 16",
        "macbook air", "macbook pro",
    ]
    if any(kw in name_lower for kw in laptop_kw):
        return "Laptops"

    pc_kw = [
        "desktop", "imac", "mac mini", "mac studio", "mac pro", "pc tower",
        "all-in-one", "mini pc", "nuc", "optiplex", "precision tower",
        "gaming pc", "gaming desktop",
    ]
    if any(kw in name_lower for kw in pc_kw):
        return "Computers & Desktops"

    # Shoes BEFORE clothes — "nike" could be shoes or clothes, but shoes-specific
    # keywords should take priority
    shoe_kw = [
        "shoe", "shoes", "sneaker", "sneakers", "boot", "boots", "sandal",
        "sandals", "slipper", "slippers", "loafer", "loafers", "mule",
        "trainer", "trainers", "running shoe", "walking shoe",
        "air max", "air force 1", "air jordan", "dunk low", "dunk high",
        "ultraboost", "superstar", "stan smith", "forum", "gazelle", "samba",
        "new balance 574", "new balance 990", "new balance 550",
        "crocs", "birkenstock", "ugg", "uggs", "heel", "heels", "pump",
        "oxford", "derby", "clog", "clogs", "flip flop", "slide",
        "runfalcon", "duramo", "gel-kayano", "gel-nimbus",
        "footwear",
    ]
    if any(kw in name_lower for kw in shoe_kw):
        return "Shoes"

    clothes_kw = [
        "shirt", "t-shirt", "tee", "polo", "blouse", "top",
        "jacket", "jeans", "pants", "hoodie", "sweater", "sweatshirt",
        "dress", "coat", "blazer", "shorts", "legging", "leggings",
        "cardigan", "parka", "vest", "fleece", "pullover", "jogger",
        "joggers", "tank top", "crew neck", "crewneck", "v-neck",
        "puffer", "windbreaker", "tracksuit", "track pants",
        "skirt", "romper", "jumpsuit", "overalls", "denim",
        "underwear", "boxer", "bra", "pajama", "robe",
        "beanie", "scarf", "gloves", "belt", "sock", "socks",
        "apparel", "clothing", "activewear", "athleisure",
        "levi's", "hanes", "champion", "fruit of the loom",
        "old navy", "gap ", "uniqlo", "h&m", "zara",
    ]
    # Use word-boundary regex to avoid false matches like "steel" → "tee"
    if any(re.search(r"\b" + re.escape(kw) + r"\b", name_lower) for kw in clothes_kw):
        return "Clothes"

    perfume_kw = [
        "perfume", "cologne", "fragrance", "eau de", "toilette", "parfum",
        "body spray", "body mist", "aftershave", "scent",
        "versace", "dior sauvage", "bleu de chanel", "acqua di gio",
        "ysl", "tom ford", "gucci guilty", "dolce & gabbana",
        "fragrancenet", "sephora perfume",
    ]
    if any(kw in name_lower for kw in perfume_kw):
        return "Perfume & Cologne"

    glasses_kw = [
        "eyeglasses", "glasses", "prescription", "rx ", " rx",
        "frames", "lenses", "bifocal", "progressive",
        "zenni", "eyebuydirect", "glassesusa", "warby parker",
        "blue light", "reading glasses", "sunglasses",
    ]
    if any(kw in name_lower for kw in glasses_kw):
        return "Eyeglasses (Rx)"

    return DEFAULT_TAB


# ---------------------------------------------------------------------------
# POST-PROCESSING — extract brand, store, prices from title text
# ---------------------------------------------------------------------------
KNOWN_BRANDS = [
    # Electronics
    "Apple", "Samsung", "Sony", "LG", "Dell", "HP", "Lenovo", "Asus", "Acer",
    "Microsoft", "Google", "Amazon", "Bose", "JBL", "Beats", "Sennheiser",
    "Canon", "Nikon", "GoPro", "Ring", "Roku", "TCL", "Hisense", "Vizio",
    "Intel", "AMD", "Nvidia", "Logitech", "Razer", "Corsair", "SteelSeries",
    "Western Digital", "Seagate", "SanDisk", "Kingston", "Crucial",
    "Anker", "Belkin", "TP-Link", "Netgear", "Dyson", "iRobot", "Roomba",
    "KitchenAid", "Ninja", "Instant Pot", "Keurig", "Cuisinart", "Vitamix",
    "Xbox", "PlayStation", "Nintendo", "Garmin", "Fitbit",
    # Shoes / Clothes
    "Nike", "Adidas", "New Balance", "Jordan", "Puma", "Reebok", "Converse",
    "Vans", "Crocs", "UGG", "Birkenstock", "Skechers", "ASICS", "Under Armour",
    "Levi's", "Hanes", "Champion", "Columbia", "North Face", "Patagonia",
    "Calvin Klein", "Ralph Lauren", "Tommy Hilfiger",
    # Fragrance
    "Versace", "Dior", "Chanel", "Gucci", "YSL", "Tom Ford",
    "Dolce & Gabbana", "Burberry", "Prada", "Hugo Boss",
    "FragranceNet",
    # Eyewear
    "Ray-Ban", "Oakley", "Zenni", "EyeBuyDirect", "GlassesUSA", "Warby Parker",
    # Other
    "LEGO", "Hasbro", "Mattel", "DeWalt", "Milwaukee", "Makita", "BLACK+DECKER",
    "Shark", "Eufy", "Ecovacs", "Owala", "Stanley", "YETI", "Hydro Flask",
]

KNOWN_STORES = [
    "Amazon", "Walmart", "Target", "Best Buy", "Costco", "Sam's Club",
    "Home Depot", "Lowe's", "Macy's", "Nordstrom", "Kohl's", "JCPenney",
    "Dick's Sporting", "Dicks Sporting", "REI", "Academy Sports",
    "Newegg", "B&H Photo", "B&H", "Micro Center", "Microcenter",
    "GameStop", "eBay", "Wayfair", "Overstock", "Zappos",
    "Old Navy", "Gap", "Banana Republic", "Nike.com", "Adidas.com",
    "SHEIN", "ASOS", "H&M", "Uniqlo", "Zara",
    "Sephora", "Ulta", "Bath & Body Works",
    "Lego.com", "Disney Store", "Apple Store",
    "Woot", "Staples", "Office Depot", "Lenovo.com",
    "Dell.com", "HP.com",
]

# Junk patterns — deals matching these are filtered out
JUNK_PATTERNS = [
    r"^#ERROR",
    r"^Buy Now\b",
    r"^Shop Now\b",
    r"^Click Here\b",
    r"^Sign Up\b",
    r"\bbrokerage account\b",
    r"\bcredit card\b",
    r"\bbank account\b",
    r"\bsign[- ]?up bonus\b",
    r"\bcashback reward\b",
    r"\bWin up to\b",
    r"\bGift Card\b.*\bWin\b",
    r"^Get up to \$[\d,]+ (?:bonus|reward)",
    r"\bNew .+ account\b.*\bget\b",
    r"\bRefer a friend\b",
    r"\bfree trial\b",
    r"\bsubscription\b.*\bfree month",
    r"-- Plus, Win",
    r"is Here --",
    r"^Save on\s",  # generic headlines like "Save on Electronics"
    r"^Top \d+ ",   # "Top 10 Black Friday Deals" = article, not a deal
    r"^Best \d+ ",  # "Best 25 Deals" = article
]

JUNK_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in JUNK_PATTERNS]


def is_junk_deal(deal: dict) -> bool:
    """Return True if this deal is junk (financial promo, article headline, etc)."""
    title = deal.get("item", "")
    if not title or len(title) < 5:
        return True
    for pat in JUNK_PATTERNS_COMPILED:
        if pat.search(title):
            return True
    # No price at all and title is very short or generic
    if not deal.get("sale_price") and len(title) < 25:
        return True
    return False


def extract_brand(title: str) -> str:
    """Extract brand name from deal title by matching known brands."""
    title_lower = title.lower()
    best_match = ""
    best_pos = len(title)
    for brand in KNOWN_BRANDS:
        # Word-boundary match so "Hanes" doesn't match inside "changes"
        pattern = re.compile(r'\b' + re.escape(brand) + r'\b', re.IGNORECASE)
        m = pattern.search(title)
        if m and m.start() < best_pos:
            best_match = brand
            best_pos = m.start()
    return best_match


def extract_store_from_title(title: str) -> str:
    """Extract store name from patterns like 'at Amazon', '@ Home Depot', 'from Walmart'."""
    # Try "at Store", "@ Store", "from Store", "via Store"
    for prep in [r"\bat\b", r"@", r"\bfrom\b", r"\bvia\b"]:
        for store in KNOWN_STORES:
            pattern = re.compile(
                prep + r"\s+" + re.escape(store), re.IGNORECASE
            )
            if pattern.search(title):
                return store
    # Also check if the title starts with a store name
    title_stripped = title.strip()
    for store in KNOWN_STORES:
        if title_stripped.lower().startswith(store.lower()):
            return store
    return ""


def extract_prices_from_title(title: str) -> tuple[str, str]:
    """Extract sale price and regular price from title text.

    Returns (sale_price, regular_price).
    Handles patterns like:
        "$299.99 (reg $399.99)"
        "$50 (was $80)"
        "$99.99 - $10 Rebate = $89.99"
        "Save $100 on ... $299 (orig $399)"
        "$29.92: Yooka-Replaylee"
        "... $22.50 + Free Shipping"
    """
    # Clean price string to float
    def to_float(p):
        try:
            return float(p.replace("$", "").replace(",", ""))
        except ValueError:
            return 0.0

    # --- Handle "= $X" rebate pattern FIRST ---
    # e.g. "$99.99 - $10 Rebate = $89.99" → sale=$89.99, reg=$99.99
    equals_match = re.search(r"=\s*(\$[\d,]+\.?\d*)", title)
    if equals_match:
        final_price = equals_match.group(1)
        all_prices = re.findall(r"\$[\d,]+\.?\d*", title)
        all_vals = [(p, to_float(p)) for p in all_prices if to_float(p) > 0]
        if len(all_vals) >= 2:
            reg_price = max(all_vals, key=lambda x: x[1])[0]
            if to_float(reg_price) > to_float(final_price):
                return (final_price, reg_price)
        return (final_price, "")

    # --- Strip shipping thresholds before finding prices ---
    # e.g. "Free Shipping w/ Prime or on $35+" — $35+ is NOT a price
    cleaned_title = re.sub(r"(?:on|over|orders?|shipping)\s+\$[\d,]+\.?\d*\+", "", title, flags=re.IGNORECASE)
    cleaned_title = re.sub(r"\$[\d,]+\.?\d*\+", "", cleaned_title)

    # Find ALL dollar amounts in the cleaned title
    prices = re.findall(r"\$[\d,]+\.?\d*", cleaned_title)
    if not prices:
        return ("", "")

    price_vals = [(p, to_float(p)) for p in prices]
    price_vals = [(p, v) for p, v in price_vals if v > 0]

    if not price_vals:
        return ("", "")

    # Look for explicit "reg", "was", "orig", "normally", "regular", "msrp", "list"
    reg_match = re.search(
        r"(?:reg(?:ular)?|was|orig(?:inally)?|normally|msrp|list(?:\s+price)?|retail)\s*:?\s*(\$[\d,]+\.?\d*)",
        cleaned_title, re.IGNORECASE
    )
    reg_price = ""
    if reg_match:
        reg_price = reg_match.group(1)
        # The sale price is the smallest non-reg price
        sale_candidates = [p for p, v in price_vals if p != reg_price]
        if sale_candidates:
            sale_price = min(sale_candidates, key=lambda x: to_float(x))
        else:
            sale_price = price_vals[0][0]
        return (sale_price, reg_price)

    # If 2+ prices and no explicit marker, largest = regular, smallest = sale
    if len(price_vals) >= 2:
        sorted_prices = sorted(price_vals, key=lambda x: x[1])
        sale_price = sorted_prices[0][0]
        reg_price = sorted_prices[-1][0]
        # Only if reg is meaningfully higher (at least 10% more)
        if sorted_prices[-1][1] > sorted_prices[0][1] * 1.10:
            return (sale_price, reg_price)

    # Single price — it's the sale price, no regular price known
    return (price_vals[0][0], "")


def postprocess_deals(deals: list[dict]) -> list[dict]:
    """Post-process all deals: extract brand, store, prices; filter junk; re-categorize."""
    cleaned = []
    for d in deals:
        # Skip coupon entries — they don't need this processing
        if d.get("category") == "ALL COUPON CODES":
            cleaned.append(d)
            continue

        # --- Bug 5: Filter junk ---
        if is_junk_deal(d):
            log.debug(f"Filtered junk: {d.get('item', '')[:60]}")
            continue

        title = d.get("item", "")

        # --- Bug 1: Extract brand ---
        if not d.get("brand"):
            d["brand"] = extract_brand(title)

        # --- Bug 2: Extract store from title if missing ---
        if not d.get("store"):
            d["store"] = extract_store_from_title(title)

        # --- Bug 3 & 7: Extract / fix prices from title ---
        title_sale, title_reg = extract_prices_from_title(title)
        if not d.get("sale_price") and title_sale:
            d["sale_price"] = title_sale
        if not d.get("regular_price") and title_reg:
            d["regular_price"] = title_reg

        # --- Bug 4: Compute savings % if we now have both prices ---
        if not d.get("savings_pct") and d.get("sale_price") and d.get("regular_price"):
            d["savings_pct"] = calc_savings_pct(d["sale_price"], d["regular_price"])

        # --- Bug 6: Re-categorize if still "Other" ---
        if d.get("category", DEFAULT_TAB) == DEFAULT_TAB:
            d["category"] = categorize_deal(title)

        cleaned.append(d)

    log.info(f"Post-processing: {len(deals)} in → {len(cleaned)} out ({len(deals) - len(cleaned)} junk filtered)")
    return cleaned


# ---------------------------------------------------------------------------
# SCRAPERS — one function per site
# Each returns a list of dicts with keys:
#   item, brand, store, sale_price, regular_price, savings_pct,
#   source_site, url, coupon_code, category
# ---------------------------------------------------------------------------

def scrape_slickdeals() -> list[dict]:
    """Scrape Slickdeals Black Friday deals."""
    deals = []
    urls = [
        "https://slickdeals.net/deals/black-friday/",
        "https://slickdeals.net/best-deals/top-voted-black-friday-deals/",
        "https://slickdeals.net/deals/?fso=1&pno=1&lno=50&forumid%5B%5D=71",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        # Try multiple selector patterns since SD changes layout
        deal_cards = soup.select(".dealCard, .bp-p-dealCard, .resultRow, .dealTile, [data-role='deal']")
        if not deal_cards:
            # Fallback: look for any links with price info
            deal_cards = soup.select("li.fpGridBox, div.fpGridBox, div.dealRow")

        for card in deal_cards:
            try:
                # Title
                title_el = card.select_one(
                    "a.bp-p-dealCard_title, .dealCard__title a, .itemTitle a, "
                    "[data-role='dealTitle'], .dealTitle a, a[data-role='title']"
                )
                if not title_el:
                    title_el = card.select_one("a[href*='/f/'], a[href*='/deal/']")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                link = title_el.get("href", "")
                if link and not link.startswith("http"):
                    link = "https://slickdeals.net" + link

                # Store
                store_el = card.select_one(
                    ".bp-p-dealCard_storeName, .dealCard__store, .storeName, "
                    "[data-role='store'], .dealStore"
                )
                store = store_el.get_text(strip=True) if store_el else ""

                # Prices
                price_el = card.select_one(
                    ".bp-p-dealCard_price, .dealCard__price, .itemPrice, "
                    "[data-role='price'], .dealPrice, .price"
                )
                sale_price = clean_price(price_el.get_text()) if price_el else ""

                orig_el = card.select_one(
                    ".bp-p-dealCard_originalPrice, .dealCard__originalPrice, "
                    ".originalPrice, [data-role='originalPrice'], .oldPrice"
                )
                reg_price = clean_price(orig_el.get_text()) if orig_el else ""

                savings = calc_savings_pct(sale_price, reg_price)

                deals.append({
                    "item": title,
                    "brand": "",
                    "store": store,
                    "sale_price": sale_price,
                    "regular_price": reg_price,
                    "savings_pct": savings,
                    "source_site": "Slickdeals",
                    "url": link,
                    "coupon_code": "",
                    "category": categorize_deal(title),
                })
            except Exception as e:
                log.debug(f"Slickdeals parse error: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"Slickdeals: found {len(deals)} deals")
    return deals


def scrape_dealnews() -> list[dict]:
    """Scrape DealNews Black Friday deals."""
    deals = []
    urls = [
        "https://www.dealnews.com/f1676/Black-Friday/?pf=1",
        "https://www.dealnews.com/f1676/Black-Friday/",
        "https://www.dealnews.com/c33/Electronics/f1676/Black-Friday/",
        "https://www.dealnews.com/c89/Clothing/f1676/Black-Friday/",
        "https://www.dealnews.com/c196/Home-Garden/f1676/Black-Friday/",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        cards = soup.select(
            ".contentCard, .dealCard, article.deal, div[data-deal-id], "
            ".content-card, .dealContent"
        )
        if not cards:
            # Broader fallback
            cards = soup.select("div.limit-width-container article, section article")

        for card in cards:
            try:
                title_el = card.select_one(
                    "a.contentCard__title, h2 a, .dealTitle a, "
                    ".summary a, a[data-click-event='deal_title']"
                )
                if not title_el:
                    title_el = card.select_one("a[href*='/lw/']")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                link = title_el.get("href", "")
                if link and not link.startswith("http"):
                    link = "https://www.dealnews.com" + link

                store_el = card.select_one(
                    ".contentCard__store, .storeName, .store-name, "
                    "span[data-role='store']"
                )
                store = store_el.get_text(strip=True) if store_el else ""

                price_el = card.select_one(
                    ".contentCard__price, .price, .dealPrice, "
                    "span[data-role='price']"
                )
                sale_price = clean_price(price_el.get_text()) if price_el else ""

                orig_el = card.select_one(
                    ".contentCard__originalPrice, .originalPrice, .was-price, "
                    "span.lineThrough, s, del"
                )
                reg_price = clean_price(orig_el.get_text()) if orig_el else ""

                # Look for coupon codes
                coupon_el = card.select_one(
                    ".couponCode, .promoCode, [data-role='coupon']"
                )
                coupon = coupon_el.get_text(strip=True) if coupon_el else ""

                savings = calc_savings_pct(sale_price, reg_price)

                deals.append({
                    "item": title,
                    "brand": "",
                    "store": store,
                    "sale_price": sale_price,
                    "regular_price": reg_price,
                    "savings_pct": savings,
                    "source_site": "DealNews",
                    "url": link,
                    "coupon_code": coupon,
                    "category": categorize_deal(title),
                })
            except Exception as e:
                log.debug(f"DealNews parse error: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"DealNews: found {len(deals)} deals")
    return deals


def scrape_9to5toys() -> list[dict]:
    """Scrape 9to5Toys Black Friday guide."""
    deals = []
    urls = [
        "https://9to5toys.com/guides/black-friday-2026/",
        "https://9to5toys.com/guides/black-friday/",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        # 9to5Toys uses article cards / post listings
        articles = soup.select(
            "article, .post-block, .river-block, .guide-deal, "
            "div[class*='post'], div[class*='deal']"
        )
        if not articles:
            articles = soup.select("h2, h3")  # fallback: headings with deal names

        for art in articles:
            try:
                title_el = art.select_one("h2 a, h3 a, .post-block__title a, a.river-block__title")
                if not title_el:
                    if art.name in ("h2", "h3"):
                        a_tag = art.find("a")
                        if a_tag:
                            title_el = a_tag
                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                link = title_el.get("href", "")

                # Try to extract price from title or surrounding text
                price_match = re.search(r"\$[\d,]+\.?\d*", title)
                sale_price = price_match.group() if price_match else ""

                # Look for original price pattern like "Reg. $X" or "(was $X)"
                parent_text = art.get_text() if art.name not in ("h2", "h3") else ""
                reg_match = re.search(r"(?:Reg\.?|was|from|list)\s*\$[\d,]+\.?\d*", parent_text, re.I)
                reg_price = clean_price(reg_match.group()) if reg_match else ""

                # Store extraction from title
                store_patterns = [
                    r"at (Amazon|Best Buy|Walmart|Target|Home Depot|Costco|B&H|Adorama|Newegg|Lowe'?s)",
                    r"(Amazon|Best Buy|Walmart|Target|Home Depot|Costco|B&H|Adorama|Newegg|Lowe'?s)",
                ]
                store = ""
                for pat in store_patterns:
                    m = re.search(pat, title, re.I)
                    if m:
                        store = m.group(1) if m.lastindex else m.group()
                        break

                savings = calc_savings_pct(sale_price, reg_price)

                deals.append({
                    "item": title,
                    "brand": "",
                    "store": store,
                    "sale_price": sale_price,
                    "regular_price": reg_price,
                    "savings_pct": savings,
                    "source_site": "9to5Toys",
                    "url": link,
                    "coupon_code": "",
                    "category": categorize_deal(title),
                })
            except Exception as e:
                log.debug(f"9to5Toys parse error: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"9to5Toys: found {len(deals)} deals")
    return deals


def scrape_hip2save() -> list[dict]:
    """Scrape Hip2Save Black Friday deals."""
    deals = []
    urls = [
        "https://hip2save.com/black-friday/",
        "https://hip2save.com/category/holidays/black-friday/",
        "https://hip2save.com/category/sales-deals/black-friday/",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        posts = soup.select(
            "article, .post-card, .deal-card, .entry, "
            "div[class*='post-listing'], div[class*='deal']"
        )

        for post in posts:
            try:
                title_el = post.select_one(
                    "h2 a, h3 a, .entry-title a, .post-card__title a, "
                    "a[class*='title']"
                )
                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                link = title_el.get("href", "")

                # Price from title
                price_match = re.search(r"\$[\d,]+\.?\d*", title)
                sale_price = price_match.group() if price_match else ""

                # Reg price
                text = post.get_text()
                reg_match = re.search(r"(?:Reg\.?|was|regular)\s*\$[\d,]+\.?\d*", text, re.I)
                reg_price = clean_price(reg_match.group()) if reg_match else ""

                # Store from title
                store = ""
                for s in ["Amazon", "Best Buy", "Walmart", "Target", "Kohl's", "Costco",
                           "Home Depot", "Macy's", "JCPenney", "Nordstrom", "Old Navy",
                           "Gap", "Bath & Body Works"]:
                    if s.lower() in title.lower():
                        store = s
                        break

                savings = calc_savings_pct(sale_price, reg_price)

                deals.append({
                    "item": title,
                    "brand": "",
                    "store": store,
                    "sale_price": sale_price,
                    "regular_price": reg_price,
                    "savings_pct": savings,
                    "source_site": "Hip2Save",
                    "url": link,
                    "coupon_code": "",
                    "category": categorize_deal(title),
                })
            except Exception as e:
                log.debug(f"Hip2Save parse error: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"Hip2Save: found {len(deals)} deals")
    return deals


def scrape_krazycouponlady() -> list[dict]:
    """Scrape TheKrazyCouponLady Black Friday ads/deals."""
    deals = []
    urls = [
        "https://thekrazycouponlady.com/deals/black-friday-ad",
        "https://thekrazycouponlady.com/deals/black-friday-ads",
        "https://thekrazycouponlady.com/tips/money/best-black-friday-deals",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        cards = soup.select(
            "article, .deal-card, .product-card, div[class*='deal'], "
            ".content-card, div[data-deal], .kclDealCard"
        )

        for card in cards:
            try:
                title_el = card.select_one(
                    "h2 a, h3 a, a[class*='title'], .deal-title a, "
                    ".product-title a, a[data-role='title']"
                )
                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                link = title_el.get("href", "")
                if link and not link.startswith("http"):
                    link = "https://thekrazycouponlady.com" + link

                price_match = re.search(r"\$[\d,]+\.?\d*", title)
                sale_price = price_match.group() if price_match else ""

                text = card.get_text()
                reg_match = re.search(r"(?:Reg\.?|was|regular|normally)\s*\$[\d,]+\.?\d*", text, re.I)
                reg_price = clean_price(reg_match.group()) if reg_match else ""

                store = ""
                for s in ["Walmart", "Target", "Amazon", "Kohl's", "Best Buy", "Costco",
                           "Macy's", "JCPenney", "Home Depot", "Lowe's"]:
                    if s.lower() in title.lower() or s.lower() in text.lower()[:200]:
                        store = s
                        break

                # Coupon code
                coupon_match = re.search(r"(?:code|coupon)[:\s]+([A-Z0-9]+)", text, re.I)
                coupon = coupon_match.group(1) if coupon_match else ""

                savings = calc_savings_pct(sale_price, reg_price)

                deals.append({
                    "item": title,
                    "brand": "",
                    "store": store,
                    "sale_price": sale_price,
                    "regular_price": reg_price,
                    "savings_pct": savings,
                    "source_site": "KrazyCouponLady",
                    "url": link,
                    "coupon_code": coupon,
                    "category": categorize_deal(title),
                })
            except Exception as e:
                log.debug(f"KrazyCouponLady parse error: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"KrazyCouponLady: found {len(deals)} deals")
    return deals


def scrape_bfads() -> list[dict]:
    """Scrape BFAds.net — a classic Black Friday ad scan site."""
    deals = []
    urls = [
        "https://www.bfads.net/stores/all-background-activities",
        "https://www.bfads.net/",
    ]

    # First get store list
    soup = fetch_page("https://www.bfads.net/")
    if not soup:
        return deals

    # Find store ad links
    store_links = soup.select("a[href*='/stores/'], a[href*='/Black-Friday/']")
    store_urls = set()
    for link in store_links[:20]:  # Limit to 20 stores
        href = link.get("href", "")
        if href and "/stores/" in href:
            if not href.startswith("http"):
                href = "https://www.bfads.net" + href
            store_urls.add(href)

    for store_url in list(store_urls)[:15]:
        soup = fetch_page(store_url)
        if not soup:
            continue

        items = soup.select(
            ".adItem, .dealItem, .item, article, "
            "div[class*='item'], div[class*='deal']"
        )

        store_name = ""
        h1 = soup.select_one("h1")
        if h1:
            store_name = h1.get_text(strip=True).replace("Black Friday Ad", "").strip()

        for item in items:
            try:
                title_el = item.select_one(
                    "h3, h4, .itemTitle, .dealTitle, a[class*='title']"
                )
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)

                link_el = item.select_one("a[href]")
                link = link_el.get("href", "") if link_el else ""
                if link and not link.startswith("http"):
                    link = "https://www.bfads.net" + link

                price_el = item.select_one(
                    ".itemPrice, .dealPrice, .price, span[class*='price']"
                )
                sale_price = clean_price(price_el.get_text()) if price_el else ""
                if not sale_price:
                    pm = re.search(r"\$[\d,]+\.?\d*", title)
                    sale_price = pm.group() if pm else ""

                orig_el = item.select_one(".originalPrice, .was, .reg, s, del")
                reg_price = clean_price(orig_el.get_text()) if orig_el else ""

                savings = calc_savings_pct(sale_price, reg_price)

                deals.append({
                    "item": title,
                    "brand": "",
                    "store": store_name,
                    "sale_price": sale_price,
                    "regular_price": reg_price,
                    "savings_pct": savings,
                    "source_site": "BFAds.net",
                    "url": link,
                    "coupon_code": "",
                    "category": categorize_deal(title),
                })
            except Exception as e:
                log.debug(f"BFAds parse error: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"BFAds.net: found {len(deals)} deals")
    return deals


def scrape_blackfriday_com() -> list[dict]:
    """Scrape BlackFriday.com."""
    deals = []
    urls = [
        "https://www.blackfriday.com/ads",
        "https://www.blackfriday.com/deals",
        "https://www.blackfriday.com/",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        cards = soup.select(
            ".deal-card, .ad-card, article, .product-card, "
            "div[class*='deal'], div[class*='product']"
        )

        for card in cards:
            try:
                title_el = card.select_one("h2 a, h3 a, a[class*='title'], .deal-title")
                if not title_el:
                    continue

                title = title_el.get_text(strip=True) if hasattr(title_el, 'get_text') else str(title_el)
                link = ""
                if hasattr(title_el, 'get'):
                    link = title_el.get("href", "")
                    if link and not link.startswith("http"):
                        link = "https://www.blackfriday.com" + link

                price_match = re.search(r"\$[\d,]+\.?\d*", title)
                sale_price = price_match.group() if price_match else ""

                text = card.get_text()
                reg_match = re.search(r"(?:Reg\.?|was|regular)\s*\$[\d,]+\.?\d*", text, re.I)
                reg_price = clean_price(reg_match.group()) if reg_match else ""

                store = ""
                for s in ["Amazon", "Best Buy", "Walmart", "Target", "Costco", "Home Depot",
                           "Kohl's", "Macy's", "JCPenney"]:
                    if s.lower() in text.lower()[:300]:
                        store = s
                        break

                savings = calc_savings_pct(sale_price, reg_price)

                deals.append({
                    "item": title,
                    "brand": "",
                    "store": store,
                    "sale_price": sale_price,
                    "regular_price": reg_price,
                    "savings_pct": savings,
                    "source_site": "BlackFriday.com",
                    "url": link,
                    "coupon_code": "",
                    "category": categorize_deal(title),
                })
            except Exception as e:
                log.debug(f"BlackFriday.com parse error: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"BlackFriday.com: found {len(deals)} deals")
    return deals


def scrape_digital_trends() -> list[dict]:
    """Scrape Digital Trends Black Friday roundups."""
    deals = []
    urls = [
        "https://www.digitaltrends.com/best-of/black-friday/",
        "https://www.digitaltrends.com/dtdeals/best-black-friday-deals/",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        # DT uses article/post format
        articles = soup.select("article, .b-snippet, div[class*='snippet'], div[class*='article']")
        if not articles:
            # Try list items
            articles = soup.select("li[class*='deal'], h2, h3")

        for art in articles:
            try:
                title_el = art.select_one("h2 a, h3 a, a[class*='title']")
                if not title_el:
                    if art.name in ("h2", "h3"):
                        a_tag = art.find("a")
                        if a_tag:
                            title_el = a_tag
                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                link = title_el.get("href", "")

                price_match = re.search(r"\$[\d,]+\.?\d*", title)
                sale_price = price_match.group() if price_match else ""

                text = art.get_text() if art.name not in ("h2", "h3") else ""
                reg_match = re.search(r"(?:Reg\.?|was|regular|normally)\s*\$[\d,]+\.?\d*", text, re.I)
                reg_price = clean_price(reg_match.group()) if reg_match else ""

                store = ""
                for s in ["Amazon", "Best Buy", "Walmart", "Target", "B&H", "Dell", "HP"]:
                    if s.lower() in title.lower():
                        store = s
                        break

                savings = calc_savings_pct(sale_price, reg_price)

                deals.append({
                    "item": title,
                    "brand": "",
                    "store": store,
                    "sale_price": sale_price,
                    "regular_price": reg_price,
                    "savings_pct": savings,
                    "source_site": "Digital Trends",
                    "url": link,
                    "coupon_code": "",
                    "category": categorize_deal(title),
                })
            except Exception as e:
                log.debug(f"Digital Trends parse error: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"Digital Trends: found {len(deals)} deals")
    return deals


def scrape_passionatepennypincher() -> list[dict]:
    """Scrape PassionatePennyPincher Black Friday page."""
    deals = []
    url = "https://passionatepennypincher.com/black-friday/"
    soup = fetch_page(url)
    if not soup:
        return deals

    # PPP has a list-style layout
    posts = soup.select("article, .entry, .post, div[class*='post']")
    for post in posts:
        try:
            title_el = post.select_one("h2 a, h3 a, .entry-title a")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")

            price_match = re.search(r"\$[\d,]+\.?\d*", title)
            sale_price = price_match.group() if price_match else ""

            text = post.get_text()
            reg_match = re.search(r"(?:Reg\.?|was|regular)\s*\$[\d,]+\.?\d*", text, re.I)
            reg_price = clean_price(reg_match.group()) if reg_match else ""

            store = ""
            for s in ["Amazon", "Walmart", "Target", "Kohl's", "Best Buy"]:
                if s.lower() in title.lower():
                    store = s
                    break

            savings = calc_savings_pct(sale_price, reg_price)

            deals.append({
                "item": title,
                "brand": "",
                "store": store,
                "sale_price": sale_price,
                "regular_price": reg_price,
                "savings_pct": savings,
                "source_site": "PassionatePennyPincher",
                "url": link,
                "coupon_code": "",
                "category": categorize_deal(title),
            })
        except Exception as e:
            log.debug(f"PPP parse error: {e}")

    log.info(f"PassionatePennyPincher: found {len(deals)} deals")
    return deals


def scrape_groupon_bf() -> list[dict]:
    """Scrape Groupon Black Friday coupons page."""
    deals = []
    urls = [
        "https://www.groupon.com/coupons/seasonal/black-friday",
        "https://www.groupon.com/coupons/blog/black-friday",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        cards = soup.select(
            ".coupon-card, .deal-card, article, .cui-card, "
            "div[class*='coupon'], div[class*='deal']"
        )

        for card in cards:
            try:
                title_el = card.select_one("h2 a, h3 a, a[class*='title'], .deal-title")
                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                link = title_el.get("href", "")
                if link and not link.startswith("http"):
                    link = "https://www.groupon.com" + link

                # Coupon codes from Groupon
                coupon_el = card.select_one(".coupon-code, .code, [data-role='code']")
                coupon = coupon_el.get_text(strip=True) if coupon_el else ""

                # Discount text
                disc_el = card.select_one(".discount, .savings, [class*='discount']")
                disc = disc_el.get_text(strip=True) if disc_el else ""

                store_el = card.select_one(".store-name, .merchant, [class*='store']")
                store = store_el.get_text(strip=True) if store_el else ""

                deals.append({
                    "item": title,
                    "brand": "",
                    "store": store,
                    "sale_price": disc,
                    "regular_price": "",
                    "savings_pct": "",
                    "source_site": "Groupon",
                    "url": link,
                    "coupon_code": coupon,
                    "category": categorize_deal(title),
                })
            except Exception as e:
                log.debug(f"Groupon parse error: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"Groupon: found {len(deals)} deals")
    return deals


# ---------------------------------------------------------------------------
# COUPON CODE SCRAPERS — dedicated agents to find ALL coupon codes
# ---------------------------------------------------------------------------

def scrape_retailmenot_coupons() -> list[dict]:
    """Scrape RetailMeNot for coupon codes across major BF stores."""
    coupons = []
    # Target the stores Sam tracks in his sheet
    stores = [
        "kohls", "nike", "adidas", "macys", "nordstrom", "jcpenney",
        "old-navy", "gap", "sephora", "ulta", "bath-and-body-works",
        "best-buy", "amazon", "walmart", "target", "shein", "zenni-optical",
        "foot-locker", "new-balance", "puma", "columbia", "coach-outlet",
        "lululemon", "warby-parker", "eyebuydirect", "glassesusa",
        "fragrancenet", "hp", "dell", "lenovo", "samsung", "costco",
        "home-depot", "lowes", "asics", "saucony", "timberland",
        "brooks-running", "hoka", "merrell", "bloomingdales",
        "rei", "patagonia", "carhartt", "uniqlo", "zara", "skims",
        "allsaints", "converse", "vans",
    ]

    for store_slug in stores:
        url = f"https://www.retailmenot.com/view/{store_slug}.com"
        soup = fetch_page(url)
        if not soup:
            continue

        # RetailMeNot coupon cards
        cards = soup.select(
            "[data-testid='coupon-card'], .offer-card, .coupon-item, "
            "div[class*='offer'], div[class*='coupon'], article[class*='offer']"
        )
        if not cards:
            # Try broader selectors
            cards = soup.select("div[data-offer], li[data-offer], section[class*='deal']")

        store_name = store_slug.replace("-", " ").title()

        for card in cards:
            try:
                # Code
                code_el = card.select_one(
                    "[data-testid='coupon-code'], .coupon-code, .code, "
                    "span[class*='code'], button[class*='code'], [data-code]"
                )
                code = ""
                if code_el:
                    code = code_el.get_text(strip=True)
                    if not code and code_el.get("data-code"):
                        code = code_el["data-code"]

                # Description
                desc_el = card.select_one(
                    "[data-testid='offer-title'], .offer-title, .description, "
                    "h3, h4, span[class*='title'], p[class*='description']"
                )
                desc = desc_el.get_text(strip=True) if desc_el else ""

                # Discount value
                disc_el = card.select_one(
                    ".offer-value, .discount, [class*='value'], "
                    "[class*='discount'], strong"
                )
                discount = disc_el.get_text(strip=True) if disc_el else ""

                # Expiry
                exp_el = card.select_one(
                    ".expiry, .expires, [class*='expir'], [class*='valid']"
                )
                expiry = exp_el.get_text(strip=True) if exp_el else ""

                if desc or code:
                    coupons.append({
                        "item": f"{store_name} — {desc}" if desc else f"{store_name} Coupon",
                        "brand": store_name,
                        "store": store_name,
                        "sale_price": discount,
                        "regular_price": "",
                        "savings_pct": discount if "%" in discount else "",
                        "source_site": "RetailMeNot",
                        "url": url,
                        "coupon_code": code if code else "See site",
                        "category": "ALL COUPON CODES",
                        "details": desc,
                        "valid_through": expiry,
                        "stackable": "",
                    })
            except Exception as e:
                log.debug(f"RetailMeNot parse error for {store_slug}: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"RetailMeNot: found {len(coupons)} coupon codes")
    return coupons


def scrape_coupons_com() -> list[dict]:
    """Scrape Coupons.com for printable and digital coupon codes."""
    coupons = []
    stores = [
        "kohls", "nike", "adidas", "macys", "nordstrom", "jcpenney",
        "old-navy", "gap", "sephora", "ulta", "best-buy", "target",
        "walmart", "shein", "foot-locker", "new-balance", "puma",
        "bloomingdales", "hp", "dell", "lenovo", "samsung",
        "fragrancenet", "coach-outlet", "columbia", "bath-body-works",
    ]

    for store in stores:
        url = f"https://www.coupons.com/coupon-codes/{store}/"
        soup = fetch_page(url)
        if not soup:
            continue

        cards = soup.select(
            ".coupon-card, .offer-card, [data-testid*='coupon'], "
            "article[class*='coupon'], div[class*='offer-item'], "
            "li[class*='coupon'], div[class*='deal-card']"
        )

        store_name = store.replace("-", " ").title()

        for card in cards:
            try:
                desc_el = card.select_one(
                    "h3, h4, .offer-title, .coupon-title, "
                    "[class*='title'], [class*='description']"
                )
                desc = desc_el.get_text(strip=True) if desc_el else ""

                code_el = card.select_one(
                    ".code, .coupon-code, [class*='code'], button[class*='reveal']"
                )
                code = code_el.get_text(strip=True) if code_el else ""

                disc_el = card.select_one(
                    ".discount, .value, [class*='savings'], strong"
                )
                discount = disc_el.get_text(strip=True) if disc_el else ""

                exp_el = card.select_one("[class*='expir'], [class*='valid']")
                expiry = exp_el.get_text(strip=True) if exp_el else ""

                if desc or code:
                    coupons.append({
                        "item": f"{store_name} — {desc}" if desc else f"{store_name} Coupon",
                        "brand": store_name,
                        "store": store_name,
                        "sale_price": discount,
                        "regular_price": "",
                        "savings_pct": discount if "%" in discount else "",
                        "source_site": "Coupons.com",
                        "url": url,
                        "coupon_code": code if code else "Auto-applied",
                        "category": "ALL COUPON CODES",
                        "details": desc,
                        "valid_through": expiry,
                        "stackable": "",
                    })
            except Exception as e:
                log.debug(f"Coupons.com parse error for {store}: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"Coupons.com: found {len(coupons)} coupon codes")
    return coupons


def scrape_dealspotr_coupons() -> list[dict]:
    """Scrape Dealspotr for verified coupon codes."""
    coupons = []
    stores = [
        "kohls", "nike", "adidas", "macys", "nordstrom", "jcpenney",
        "old-navy", "sephora", "ulta", "shein", "zenni-optical",
        "foot-locker", "new-balance", "puma", "hp", "dell", "lenovo",
        "fragrancenet", "coach-outlet", "eyebuydirect", "glassesusa",
        "warby-parker", "columbia", "lululemon", "samsung",
    ]

    for store in stores:
        url = f"https://dealspotr.com/{store}-coupons"
        soup = fetch_page(url)
        if not soup:
            continue

        cards = soup.select(
            ".deal-card, .coupon-card, article, [class*='offer'], "
            "div[class*='coupon'], div[class*='deal-item']"
        )

        store_name = store.replace("-", " ").title()

        for card in cards:
            try:
                desc_el = card.select_one("h3, h4, .title, [class*='title']")
                desc = desc_el.get_text(strip=True) if desc_el else ""

                code_el = card.select_one(".code, [class*='code'], .promo-code")
                code = code_el.get_text(strip=True) if code_el else ""

                disc_el = card.select_one(".discount, [class*='discount'], .value")
                discount = disc_el.get_text(strip=True) if disc_el else ""

                if desc or code:
                    coupons.append({
                        "item": f"{store_name} — {desc}" if desc else f"{store_name} Coupon",
                        "brand": store_name,
                        "store": store_name,
                        "sale_price": discount,
                        "regular_price": "",
                        "savings_pct": "",
                        "source_site": "Dealspotr",
                        "url": url,
                        "coupon_code": code if code else "See site",
                        "category": "ALL COUPON CODES",
                    })
            except Exception as e:
                log.debug(f"Dealspotr parse error for {store}: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"Dealspotr: found {len(coupons)} coupon codes")
    return coupons


def scrape_offers_com_coupons() -> list[dict]:
    """Scrape Offers.com for coupon codes."""
    coupons = []
    stores = [
        "kohls", "nike", "adidas", "macys", "nordstrom", "jcpenney",
        "old-navy", "sephora", "ulta", "best-buy", "target",
        "shein", "foot-locker", "puma", "hp", "dell", "samsung",
        "fragrancenet", "coach-outlet", "columbia", "bath-body-works",
    ]

    for store in stores:
        url = f"https://www.offers.com/{store}/"
        soup = fetch_page(url)
        if not soup:
            continue

        cards = soup.select(
            ".offer-card, .coupon, article[class*='offer'], "
            "div[class*='coupon'], li[class*='offer']"
        )

        store_name = store.replace("-", " ").title()

        for card in cards:
            try:
                desc_el = card.select_one("h3, h4, .offer-title, [class*='title']")
                desc = desc_el.get_text(strip=True) if desc_el else ""

                code_el = card.select_one(".code, [class*='code']")
                code = code_el.get_text(strip=True) if code_el else ""

                disc_el = card.select_one(".value, .discount, [class*='discount']")
                discount = disc_el.get_text(strip=True) if disc_el else ""

                if desc or code:
                    coupons.append({
                        "item": f"{store_name} — {desc}" if desc else f"{store_name} Coupon",
                        "brand": store_name,
                        "store": store_name,
                        "sale_price": discount,
                        "regular_price": "",
                        "savings_pct": "",
                        "source_site": "Offers.com",
                        "url": url,
                        "coupon_code": code if code else "See site",
                        "category": "ALL COUPON CODES",
                    })
            except Exception as e:
                log.debug(f"Offers.com parse error for {store}: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"Offers.com: found {len(coupons)} coupon codes")
    return coupons


def scrape_goodshop_coupons() -> list[dict]:
    """Scrape Goodshop for coupon codes (also donates to charity)."""
    coupons = []
    stores = [
        "kohls", "nike", "adidas", "macys", "nordstrom",
        "old-navy", "sephora", "ulta", "best-buy", "target",
        "shein", "puma", "hp", "dell", "lenovo", "samsung",
        "fragrancenet", "foot-locker", "columbia",
    ]

    for store in stores:
        url = f"https://www.goodshop.com/coupons/{store}"
        soup = fetch_page(url)
        if not soup:
            continue

        cards = soup.select(
            ".coupon-card, .deal, article, div[class*='coupon'], "
            "div[class*='deal'], li[class*='coupon']"
        )

        store_name = store.replace("-", " ").title()

        for card in cards:
            try:
                desc_el = card.select_one("h3, h4, .title, .description, [class*='title']")
                desc = desc_el.get_text(strip=True) if desc_el else ""

                code_el = card.select_one(".code, [class*='code']")
                code = code_el.get_text(strip=True) if code_el else ""

                disc_el = card.select_one(".savings, .discount, [class*='value']")
                discount = disc_el.get_text(strip=True) if disc_el else ""

                if desc or code:
                    coupons.append({
                        "item": f"{store_name} — {desc}" if desc else f"{store_name} Coupon",
                        "brand": store_name,
                        "store": store_name,
                        "sale_price": discount,
                        "regular_price": "",
                        "savings_pct": "",
                        "source_site": "Goodshop",
                        "url": url,
                        "coupon_code": code if code else "See site",
                        "category": "ALL COUPON CODES",
                    })
            except Exception as e:
                log.debug(f"Goodshop parse error for {store}: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"Goodshop: found {len(coupons)} coupon codes")
    return coupons


# ---------------------------------------------------------------------------
# DIRECT RETAILER SCRAPERS — sites that deal aggregators miss
# ---------------------------------------------------------------------------

def scrape_zenni_optical() -> list[dict]:
    """Scrape Zenni Optical directly for Black Friday deals and codes."""
    deals = []

    urls = [
        "https://www.zennioptical.com/b/black-friday",
        "https://www.zennioptical.com/b/sale",
        "https://www.zennioptical.com/b/deals",
        "https://www.zennioptical.com/",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        # Look for promo banners / coupon codes
        banners = soup.select(
            "[class*='promo'], [class*='banner'], [class*='coupon'], "
            "[class*='sale'], [class*='offer'], [class*='deal'], "
            "div[class*='hero'], section[class*='promo']"
        )

        for banner in banners:
            text = banner.get_text(separator=" ", strip=True)
            if not text or len(text) < 10:
                continue

            # Find coupon codes in the text
            code_matches = re.findall(r'\b([A-Z0-9]{4,15})\b', text)
            # Filter to likely coupon codes (not random words)
            codes = [c for c in code_matches if any(kw in c.upper() for kw in
                     ["BF", "SAVE", "OFF", "DEAL", "BLACK", "FRIDAY", "BOGO", "FREE",
                      "EARLY", "SALE", "20", "25", "30", "40", "50"])
                     or (len(c) >= 5 and c.isalnum())]

            # Also check for % off patterns
            disc_match = re.search(r'(\d+%\s*off|\$\d+\s*off|buy\s+\d+\s+get|bogo|free)', text, re.I)
            discount = disc_match.group() if disc_match else ""

            if codes or discount:
                deals.append({
                    "item": f"Zenni Optical — {text[:100]}",
                    "brand": "Zenni Optical",
                    "store": "Zenni Optical",
                    "sale_price": discount,
                    "regular_price": "",
                    "savings_pct": discount if "%" in discount else "",
                    "source_site": "Zenni (Direct)",
                    "url": url,
                    "coupon_code": codes[0] if codes else "See site",
                    "category": "Eyeglasses (Rx)",
                })

        # Also look for product deals
        products = soup.select(
            ".product-card, .product-tile, [class*='product-item'], "
            "[class*='product-card'], article[class*='product']"
        )

        for prod in products:
            try:
                title_el = prod.select_one(
                    "h3, h4, .product-name, .product-title, "
                    "[class*='name'], [class*='title'], a"
                )
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)

                price_el = prod.select_one(
                    ".sale-price, .special-price, [class*='sale'], "
                    "[class*='price']:not([class*='old']):not([class*='regular'])"
                )
                sale_price = clean_price(price_el.get_text()) if price_el else ""

                reg_el = prod.select_one(
                    ".old-price, .regular-price, [class*='was'], "
                    "[class*='old'], [class*='regular'], s, del"
                )
                reg_price = clean_price(reg_el.get_text()) if reg_el else ""

                link_el = prod.select_one("a[href]")
                link = ""
                if link_el:
                    link = link_el.get("href", "")
                    if link and not link.startswith("http"):
                        link = "https://www.zennioptical.com" + link

                savings = calc_savings_pct(sale_price, reg_price)

                if title and (sale_price or reg_price):
                    deals.append({
                        "item": title,
                        "brand": "Zenni Optical",
                        "store": "Zenni Optical",
                        "sale_price": sale_price,
                        "regular_price": reg_price,
                        "savings_pct": savings,
                        "source_site": "Zenni (Direct)",
                        "url": link or url,
                        "coupon_code": "",
                        "category": "Eyeglasses (Rx)",
                    })
            except Exception as e:
                log.debug(f"Zenni product parse error: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"Zenni Optical: found {len(deals)} deals/coupons")
    return deals


def scrape_shein() -> list[dict]:
    """Scrape SHEIN for Black Friday deals and codes."""
    deals = []

    urls = [
        "https://us.shein.com/black-friday-sale.html",
        "https://us.shein.com/campaign/blackfriday",
        "https://us.shein.com/flash-sale.html",
        "https://us.shein.com/",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        # Promo banners with coupon codes
        banners = soup.select(
            "[class*='promo'], [class*='banner'], [class*='coupon'], "
            "[class*='campaign'], [class*='flash'], [class*='deal']"
        )

        for banner in banners:
            text = banner.get_text(separator=" ", strip=True)
            if len(text) < 10:
                continue

            # Look for SHEIN-style codes
            code_matches = re.findall(r'\b([A-Z0-9]{4,15})\b', text)
            codes = [c for c in code_matches if len(c) >= 4 and not c.isdigit()]

            disc_match = re.search(r'(\d+%\s*off|\$\d+\s*off|extra\s+\d+%)', text, re.I)
            discount = disc_match.group() if disc_match else ""

            if codes or discount:
                deals.append({
                    "item": f"SHEIN — {text[:120]}",
                    "brand": "SHEIN",
                    "store": "SHEIN",
                    "sale_price": discount,
                    "regular_price": "",
                    "savings_pct": "",
                    "source_site": "SHEIN (Direct)",
                    "url": url,
                    "coupon_code": codes[0] if codes else "Auto-applied",
                    "category": "Clothes",
                })

        # Product listings
        products = soup.select(
            ".product-card, [class*='product-item'], .goods-item, "
            "[class*='goods'], article[class*='product']"
        )

        for prod in products[:30]:  # Cap to avoid huge lists
            try:
                title_el = prod.select_one(
                    ".product-name, .goods-title, [class*='title'], "
                    "[class*='name'], a[class*='goods']"
                )
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)

                price_el = prod.select_one(
                    ".sale-price, .special-price, [class*='sale-price'], "
                    "[class*='current']"
                )
                sale_price = clean_price(price_el.get_text()) if price_el else ""

                reg_el = prod.select_one(
                    ".original-price, [class*='original'], [class*='del-price'], "
                    "del, s"
                )
                reg_price = clean_price(reg_el.get_text()) if reg_el else ""

                link_el = prod.select_one("a[href]")
                link = ""
                if link_el:
                    link = link_el.get("href", "")
                    if link and not link.startswith("http"):
                        link = "https://us.shein.com" + link

                savings = calc_savings_pct(sale_price, reg_price)

                if title and sale_price:
                    deals.append({
                        "item": title,
                        "brand": "SHEIN",
                        "store": "SHEIN",
                        "sale_price": sale_price,
                        "regular_price": reg_price,
                        "savings_pct": savings,
                        "source_site": "SHEIN (Direct)",
                        "url": link or url,
                        "coupon_code": "",
                        "category": categorize_deal(title) if categorize_deal(title) != DEFAULT_TAB else "Clothes",
                    })
            except Exception as e:
                log.debug(f"SHEIN product parse error: {e}")

        time.sleep(REQUEST_DELAY)

    log.info(f"SHEIN: found {len(deals)} deals")
    return deals


def scrape_eyebuydirect() -> list[dict]:
    """Scrape EyeBuyDirect for glasses deals."""
    deals = []
    urls = [
        "https://www.eyebuydirect.com/black-friday",
        "https://www.eyebuydirect.com/deals",
        "https://www.eyebuydirect.com/",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        banners = soup.select(
            "[class*='promo'], [class*='banner'], [class*='sale'], "
            "[class*='offer'], [class*='hero']"
        )
        for banner in banners:
            text = banner.get_text(separator=" ", strip=True)
            if len(text) < 10:
                continue

            code_matches = re.findall(r'\b([A-Z0-9]{4,15})\b', text)
            codes = [c for c in code_matches if len(c) >= 4 and not c.isdigit()]

            disc_match = re.search(r'(\d+%\s*off|\$\d+\s*off|buy\s+\d+\s+get|bogo)', text, re.I)
            discount = disc_match.group() if disc_match else ""

            if codes or discount:
                deals.append({
                    "item": f"EyeBuyDirect — {text[:100]}",
                    "brand": "EyeBuyDirect",
                    "store": "EyeBuyDirect",
                    "sale_price": discount,
                    "regular_price": "",
                    "savings_pct": "",
                    "source_site": "EyeBuyDirect (Direct)",
                    "url": url,
                    "coupon_code": codes[0] if codes else "See site",
                    "category": "Eyeglasses (Rx)",
                })

        time.sleep(REQUEST_DELAY)

    log.info(f"EyeBuyDirect: found {len(deals)} deals")
    return deals


def scrape_fragrancenet() -> list[dict]:
    """Scrape FragranceNet for perfume/cologne deals and coupon codes."""
    deals = []
    urls = [
        "https://www.fragrancenet.com/black-friday",
        "https://www.fragrancenet.com/",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        # Look for coupon codes in banners/promos
        banners = soup.select(
            "[class*='promo'], [class*='banner'], [class*='coupon'], "
            "[class*='offer'], [class*='hero'], [class*='sale']"
        )

        for banner in banners:
            text = banner.get_text(separator=" ", strip=True)
            if len(text) < 10:
                continue

            code_matches = re.findall(r'\b([A-Z0-9]{4,15})\b', text)
            codes = [c for c in code_matches if len(c) >= 4 and not c.isdigit()
                     and any(ch.isalpha() for ch in c)]

            disc_match = re.search(r'(\d+%\s*off|\$\d+\s*off)', text, re.I)
            discount = disc_match.group() if disc_match else ""

            if codes or discount:
                deals.append({
                    "item": f"FragranceNet — {text[:100]}",
                    "brand": "FragranceNet",
                    "store": "FragranceNet",
                    "sale_price": discount,
                    "regular_price": "",
                    "savings_pct": "",
                    "source_site": "FragranceNet (Direct)",
                    "url": url,
                    "coupon_code": codes[0] if codes else "See site",
                    "category": "Perfume & Cologne",
                })

        time.sleep(REQUEST_DELAY)

    log.info(f"FragranceNet: found {len(deals)} deals")
    return deals


def scrape_glassesusa() -> list[dict]:
    """Scrape GlassesUSA for deals."""
    deals = []
    urls = [
        "https://www.glassesusa.com/black-friday",
        "https://www.glassesusa.com/deals",
        "https://www.glassesusa.com/",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        banners = soup.select(
            "[class*='promo'], [class*='banner'], [class*='sale'], [class*='offer']"
        )

        for banner in banners:
            text = banner.get_text(separator=" ", strip=True)
            if len(text) < 10:
                continue

            code_matches = re.findall(r'\b([A-Z0-9]{4,15})\b', text)
            codes = [c for c in code_matches if len(c) >= 4 and not c.isdigit()]

            disc_match = re.search(r'(\d+%\s*off|\$\d+\s*off|free\s+lenses)', text, re.I)
            discount = disc_match.group() if disc_match else ""

            if codes or discount:
                deals.append({
                    "item": f"GlassesUSA — {text[:100]}",
                    "brand": "GlassesUSA",
                    "store": "GlassesUSA",
                    "sale_price": discount,
                    "regular_price": "",
                    "savings_pct": "",
                    "source_site": "GlassesUSA (Direct)",
                    "url": url,
                    "coupon_code": codes[0] if codes else "See site",
                    "category": "Eyeglasses (Rx)",
                })

        time.sleep(REQUEST_DELAY)

    log.info(f"GlassesUSA: found {len(deals)} deals")
    return deals


def scrape_nike_direct() -> list[dict]:
    """Scrape Nike.com sale page for BF deals."""
    deals = []
    urls = [
        "https://www.nike.com/w/sale-3yaep",
        "https://www.nike.com/w/black-friday-sale",
        "https://www.nike.com/",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        # Promo banners for codes
        banners = soup.select(
            "[class*='promo'], [class*='banner'], [class*='message'], "
            "[data-testid*='promo'], [class*='hero']"
        )

        for banner in banners:
            text = banner.get_text(separator=" ", strip=True)
            if len(text) < 10:
                continue

            code_matches = re.findall(r'\b([A-Z0-9]{5,15})\b', text)
            codes = [c for c in code_matches if any(ch.isalpha() for ch in c)]

            disc_match = re.search(r'(extra\s+\d+%|\d+%\s*off|member\s+\d+%)', text, re.I)
            discount = disc_match.group() if disc_match else ""

            if codes or discount:
                cat = "Shoes" if "shoe" in text.lower() or "sneaker" in text.lower() else "Clothes"
                deals.append({
                    "item": f"Nike — {text[:100]}",
                    "brand": "Nike",
                    "store": "Nike.com",
                    "sale_price": discount,
                    "regular_price": "",
                    "savings_pct": "",
                    "source_site": "Nike (Direct)",
                    "url": url,
                    "coupon_code": codes[0] if codes else "No code — auto-applied",
                    "category": cat,
                })

        time.sleep(REQUEST_DELAY)

    log.info(f"Nike Direct: found {len(deals)} deals")
    return deals


def scrape_adidas_direct() -> list[dict]:
    """Scrape Adidas.com sale page for BF deals."""
    deals = []
    urls = [
        "https://www.adidas.com/us/black-friday",
        "https://www.adidas.com/us/sale",
        "https://www.adidas.com/us",
    ]

    for url in urls:
        soup = fetch_page(url)
        if not soup:
            continue

        banners = soup.select(
            "[class*='promo'], [class*='banner'], [class*='message'], "
            "[class*='hero'], [class*='callout']"
        )

        for banner in banners:
            text = banner.get_text(separator=" ", strip=True)
            if len(text) < 10:
                continue

            code_matches = re.findall(r'\b([A-Z0-9]{5,15})\b', text)
            codes = [c for c in code_matches if any(ch.isalpha() for ch in c)]

            disc_match = re.search(r'(extra\s+\d+%|\d+%\s*off|adiclub\s+\d+%)', text, re.I)
            discount = disc_match.group() if disc_match else ""

            if codes or discount:
                cat = "Shoes" if "shoe" in text.lower() else "Clothes"
                deals.append({
                    "item": f"Adidas — {text[:100]}",
                    "brand": "Adidas",
                    "store": "Adidas.com",
                    "sale_price": discount,
                    "regular_price": "",
                    "savings_pct": "",
                    "source_site": "Adidas (Direct)",
                    "url": url,
                    "coupon_code": codes[0] if codes else "adiClub auto-applied",
                    "category": cat,
                })

        time.sleep(REQUEST_DELAY)

    log.info(f"Adidas Direct: found {len(deals)} deals")
    return deals


# ---------------------------------------------------------------------------
# REGISTRY — maps site name to scraper function
# ---------------------------------------------------------------------------
SCRAPER_REGISTRY = {
    # Deal aggregators (original 10)
    "slickdeals": scrape_slickdeals,
    "dealnews": scrape_dealnews,
    "9to5toys": scrape_9to5toys,
    "hip2save": scrape_hip2save,
    "krazycouponlady": scrape_krazycouponlady,
    "bfads": scrape_bfads,
    "blackfriday": scrape_blackfriday_com,
    "digitaltrends": scrape_digital_trends,
    "pennypincher": scrape_passionatepennypincher,
    "groupon": scrape_groupon_bf,
    # Direct retailer scrapers (NEW)
    "zenni": scrape_zenni_optical,
    "shein": scrape_shein,
    "eyebuydirect": scrape_eyebuydirect,
    "fragrancenet": scrape_fragrancenet,
    "glassesusa": scrape_glassesusa,
    "nike": scrape_nike_direct,
    "adidas": scrape_adidas_direct,
}

# Coupon-only scrapers — run separately to find ALL codes
COUPON_REGISTRY = {
    "retailmenot": scrape_retailmenot_coupons,
    "couponscom": scrape_coupons_com,
    "dealspotr": scrape_dealspotr_coupons,
    "offerscom": scrape_offers_com_coupons,
    "goodshop": scrape_goodshop_coupons,
}


# ---------------------------------------------------------------------------
# DEDUP
# ---------------------------------------------------------------------------
def dedup_deals(deals: list[dict]) -> list[dict]:
    """Remove duplicate deals based on item name similarity."""
    seen = set()
    unique = []
    for d in deals:
        # Normalize title for dedup
        key = re.sub(r"[^a-z0-9]", "", d["item"].lower())[:60]
        if key and key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


# ---------------------------------------------------------------------------
# GOOGLE SHEETS WRITER
# ---------------------------------------------------------------------------
def write_to_google_sheets(deals: list[dict], spreadsheet_id: str):
    """Write deals to Google Sheet using gspread + service account."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        log.error("gspread not installed. Run: pip install gspread google-auth")
        sys.exit(1)

    # Look for credentials file
    creds_paths = [
        os.path.join(os.path.dirname(__file__), "credentials.json"),
        os.path.join(os.path.dirname(__file__), "service_account.json"),
        os.path.expanduser("~/.config/gspread/service_account.json"),
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
    ]

    creds_file = None
    for p in creds_paths:
        if p and os.path.isfile(p):
            creds_file = p
            break

    if not creds_file:
        log.error(
            "No Google credentials found!\n"
            "To set up:\n"
            "1. Go to https://console.cloud.google.com/\n"
            "2. Create a project (or use existing)\n"
            "3. Enable Google Sheets API\n"
            "4. Create a Service Account, download JSON key\n"
            "5. Save it as 'credentials.json' in this folder\n"
            "6. Share your Google Sheet with the service account email\n"
        )
        # Save deals to local JSON as fallback
        fallback = os.path.join(os.path.dirname(__file__), "deals_output.json")
        with open(fallback, "w") as f:
            json.dump(deals, f, indent=2)
        log.info(f"Deals saved to {fallback} (can import manually)")
        return

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
    gc = gspread.authorize(creds)

    try:
        sh = gc.open_by_key(spreadsheet_id)
    except gspread.SpreadsheetNotFound:
        log.error(f"Spreadsheet {spreadsheet_id} not found. Did you share it with the service account?")
        return

    # --- Strategy: write all scraped deals to a "Scraped Deals" tab ---
    # Also try to route deals to existing category tabs.

    # Ensure "Scraped Deals" tab exists
    try:
        ws = sh.worksheet(SCRAPED_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SCRAPED_TAB, rows=2000, cols=12)
        # Write header
        ws.update("A1:L1", [[
            "Item", "Brand", "Store", "Sale Price", "Regular Price",
            "Savings %", "Source Site", "URL", "Coupon Code",
            "Category", "Scraped At", "Verdict"
        ]])
        log.info(f"Created '{SCRAPED_TAB}' tab with headers")

    # Get existing data to avoid duplicates
    existing = ws.get_all_values()
    existing_items = set()
    for row in existing[1:]:  # skip header
        if row:
            existing_items.add(re.sub(r"[^a-z0-9]", "", row[0].lower())[:60])

    # Filter to truly new deals
    new_deals = []
    for d in deals:
        key = re.sub(r"[^a-z0-9]", "", d["item"].lower())[:60]
        if key and key not in existing_items:
            new_deals.append(d)

    if not new_deals:
        log.info("No new deals to add — all already in sheet.")
        return

    # Prepare rows
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = []
    for d in new_deals:
        rows.append([
            d["item"],
            d["brand"],
            d["store"],
            d["sale_price"],
            d["regular_price"],
            d["savings_pct"],
            d["source_site"],
            d["url"],
            d["coupon_code"],
            d["category"],
            now,
            "",  # Verdict — Sam fills manually or harness fills later
        ])

    # Append in batches of 100 (Sheets API limit friendly)
    batch_size = 100
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        ws.append_rows(batch, value_input_option="USER_ENTERED")
        log.info(f"Appended {len(batch)} rows (batch {i // batch_size + 1})")
        time.sleep(1)  # Rate limit

    log.info(f"✅ Added {len(new_deals)} new deals to '{SCRAPED_TAB}' tab")

    # --- Also try to add to category-specific tabs ---
    category_groups: dict[str, list[dict]] = {}
    for d in new_deals:
        cat = d["category"]
        if cat != DEFAULT_TAB:
            category_groups.setdefault(cat, []).append(d)

    for tab_name, cat_deals in category_groups.items():
        try:
            cat_ws = sh.worksheet(tab_name)

            if tab_name == "ALL COUPON CODES":
                # Coupon tab has different columns matching Sam's sheet:
                # Store | Coupon Code | Discount | Details | Valid Through | Stackable? | Category
                coupon_rows = []
                for d in cat_deals:
                    coupon_rows.append([
                        d.get("store", d.get("brand", "")),
                        d.get("coupon_code", ""),
                        d.get("sale_price", d.get("savings_pct", "")),  # discount value
                        d.get("details", d.get("item", "")),
                        d.get("valid_through", ""),
                        d.get("stackable", ""),
                        categorize_deal(d.get("item", "")) if categorize_deal(d.get("item", "")) != DEFAULT_TAB else "",
                    ])
                # Batch append
                for i in range(0, len(coupon_rows), batch_size):
                    batch = coupon_rows[i : i + batch_size]
                    cat_ws.append_rows(batch, value_input_option="USER_ENTERED")
                    time.sleep(1)
                log.info(f"Added {len(coupon_rows)} coupons to 'ALL COUPON CODES' tab")
            else:
                # Regular deal tabs — match Sam's sheet columns:
                # Item | Brand | Doorbuster Store | Doorbuster Price | Regular Price |
                # Savings % | Price at Amazon | Price at Other Stores | VERDICT |
                # Coupon Code | Price After Coupon | URL (buy link) | Source Site
                deal_rows = []
                for d in cat_deals:
                    deal_rows.append([
                        d["item"],           # A: Item
                        d["brand"],          # B: Brand
                        d["store"],          # C: Doorbuster Store
                        d["sale_price"],     # D: Doorbuster Price
                        d["regular_price"],  # E: Regular Price
                        d["savings_pct"],    # F: Savings %
                        "",                  # G: Price at Amazon (harness fills)
                        "",                  # H: Price at Other Stores (harness fills)
                        "",                  # I: VERDICT (harness fills)
                        d["coupon_code"],    # J: Coupon Code
                        "",                  # K: Price After Coupon (harness fills)
                        d["url"],            # L: URL (direct buy link)
                        d["source_site"],    # M: Source Site
                    ])
                for i in range(0, len(deal_rows), batch_size):
                    batch = deal_rows[i : i + batch_size]
                    cat_ws.append_rows(batch, value_input_option="USER_ENTERED")
                    time.sleep(1)
                log.info(f"Added {len(deal_rows)} deals to '{tab_name}' tab")
                time.sleep(1)
        except gspread.WorksheetNotFound:
            log.debug(f"Tab '{tab_name}' not found, deals stay in Scraped Deals tab")
        except Exception as e:
            log.debug(f"Error writing to '{tab_name}': {e}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def run_scraper(
    sites: list[str] | None = None,
    category_filter: str | None = None,
    include_coupons: bool = True,
    coupons_only: bool = False,
) -> list[dict]:
    """Run all scrapers and return combined deal list.

    Args:
        sites: specific site keys to scrape (from SCRAPER_REGISTRY or COUPON_REGISTRY)
        category_filter: only return deals matching this category
        include_coupons: also run the coupon-code scrapers (default True)
        coupons_only: ONLY run coupon scrapers, skip deal aggregators
    """
    all_deals = []

    # --- Deal aggregators + direct retailers ---
    if not coupons_only:
        scrapers_to_run = SCRAPER_REGISTRY
        if sites:
            scrapers_to_run = {k: v for k, v in SCRAPER_REGISTRY.items() if k in sites}

        for name, func in scrapers_to_run.items():
            try:
                log.info(f"--- Scraping {name} ---")
                deals = func()
                all_deals.extend(deals)
            except Exception as e:
                log.error(f"Error scraping {name}: {e}")

    # --- Coupon code scrapers ---
    if include_coupons or coupons_only:
        coupon_scrapers = COUPON_REGISTRY
        if sites:
            coupon_scrapers = {k: v for k, v in COUPON_REGISTRY.items() if k in sites}
            # If user specified deal sites only, still run all coupon scrapers
            if not coupon_scrapers and not coupons_only:
                coupon_scrapers = COUPON_REGISTRY

        for name, func in coupon_scrapers.items():
            try:
                log.info(f"--- Scraping coupons: {name} ---")
                coupons = func()
                all_deals.extend(coupons)
            except Exception as e:
                log.error(f"Error scraping coupons from {name}: {e}")

    # Dedup
    all_deals = dedup_deals(all_deals)
    log.info(f"Total unique deals after dedup: {len(all_deals)}")

    # Post-process: extract brand, store, prices; filter junk; fix categories
    all_deals = postprocess_deals(all_deals)

    # Category filter
    if category_filter:
        tab = CATEGORY_TAB_MAP.get(category_filter.lower(), category_filter)
        all_deals = [d for d in all_deals if d["category"] == tab]
        log.info(f"After category filter '{tab}': {len(all_deals)} deals")

    return all_deals


def main():
    all_site_keys = list(SCRAPER_REGISTRY.keys()) + list(COUPON_REGISTRY.keys())

    parser = argparse.ArgumentParser(description="Black Friday Deal & Coupon Scraper")
    parser.add_argument("--dry-run", action="store_true", help="Scrape but don't write to Sheet")
    parser.add_argument("--sites", nargs="+", help="Specific sites to scrape",
                        choices=all_site_keys)
    parser.add_argument("--category", help="Filter to a specific category")
    parser.add_argument("--sheet-id", default=SPREADSHEET_ID, help="Google Sheet ID")
    parser.add_argument("--output-json", help="Also save results to a JSON file")
    parser.add_argument("--coupons-only", action="store_true",
                        help="Only scrape coupon codes, skip deal aggregators")
    parser.add_argument("--no-coupons", action="store_true",
                        help="Skip coupon scrapers, only run deal aggregators")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode = "COUPONS ONLY" if args.coupons_only else ("DEALS ONLY" if args.no_coupons else "ALL (deals + coupons)")
    log.info(f"🔍 Starting Black Friday Scraper — mode: {mode}")
    log.info(f"Sites: {args.sites or 'ALL'}")

    deals = run_scraper(
        sites=args.sites,
        category_filter=args.category,
        include_coupons=not args.no_coupons,
        coupons_only=args.coupons_only,
    )

    if not deals:
        log.warning("No deals found! Sites may have changed their layout.")
        return

    log.info(f"\n{'='*60}")
    log.info(f"Found {len(deals)} total deals/coupons")

    # Print summary by source
    by_source: dict[str, int] = {}
    for d in deals:
        by_source[d["source_site"]] = by_source.get(d["source_site"], 0) + 1
    for src, count in sorted(by_source.items()):
        log.info(f"  {src}: {count}")

    # Print summary by category
    by_cat: dict[str, int] = {}
    for d in deals:
        by_cat[d["category"]] = by_cat.get(d["category"], 0) + 1
    log.info(f"\nBy category:")
    for cat, count in sorted(by_cat.items()):
        log.info(f"  {cat}: {count}")

    # Save JSON if requested
    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump(deals, f, indent=2)
        log.info(f"\nSaved to {args.output_json}")

    # Write to Google Sheets
    if not args.dry_run:
        log.info(f"\n📊 Writing to Google Sheet {args.sheet_id}...")
        write_to_google_sheets(deals, args.sheet_id)
    else:
        log.info("\n[DRY RUN] Skipping Google Sheet write")
        # Print first 10 deals as preview
        log.info("\nSample deals:")
        for d in deals[:10]:
            log.info(f"  [{d['source_site']}] {d['item'][:70]} | {d['sale_price']} | {d['store']}")

    log.info("\n✅ Done!")


if __name__ == "__main__":
    main()
