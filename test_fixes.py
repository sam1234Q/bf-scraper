#!/usr/bin/env python3
"""Quick test of the new post-processing functions."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scraper import extract_brand, extract_store_from_title, extract_prices_from_title, is_junk_deal, categorize_deal

tests = [
    'Macbook Pro 14" PROLED Silver or Black - $2599 now at Microcenter',
    "Hanes Men's EcoSmart Fleece Crewneck Sweatshirt (Various Colors) $10.54 + Free Shipping w/ Prime or on $35+",
    "adidas men Runfalcon 5 Running Shoes",
    "Canon PIXMA MegaTank Wireless All-in-One Printer $149 (reg $249)",
    "40-Oz Owala FreeSip Sway Insulated Stainless Steel Water Bottle $22.50",
    "$60 Pick Up Dicks Sporting: Maxfli StraightFli Golf Balls",
    "$29.92: Yooka-Replaylee - PlayStation 5 at Amazon",
    "Target Black Friday 2026 is Here -- Plus, Win up to a $1,000 Gift Card",
    "#ERROR!",
    "Buy Now at eBay",
    "New Etrade brokerage account - get up to $1,500",
    "$99.99 - $10 Rebate = $89.99 Canon Printer",
    "Nike Air Max 90 Shoes $89.97 (was $130) at Nike.com",
    "Levi's 501 Original Fit Jeans $34.98 (orig $69.50) from Macy's",
    "Samsung 65-inch OLED 4K TV $1299 (reg $1799) at Best Buy",
]

for t in tests:
    brand = extract_brand(t)
    store = extract_store_from_title(t)
    sale, reg = extract_prices_from_title(t)
    cat = categorize_deal(t)
    junk = is_junk_deal({"item": t, "sale_price": sale})
    print(f"Title: {t[:70]}")
    print(f"  Brand={brand} | Store={store} | Sale={sale} | Reg={reg} | Cat={cat} | Junk={junk}")
    print()
