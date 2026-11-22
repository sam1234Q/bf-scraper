#!/usr/bin/env python3
"""
Harness Interface — Wire this scraper into Sam's DeepSeek harness.

The harness can import and call these functions to:
  1. Run a scrape on demand
  2. Add specific deals to the sheet
  3. Search existing scraped data
  4. Update deal verdicts

Usage from the harness:
    from harness_interface import HarnessAPI
    api = HarnessAPI()
    api.execute("scrape slickdeals dealnews")
    api.execute("find laptop deals under $500")
    api.execute("update sheet")
"""

import json
import os
import re
import sys

from scraper import (
    COUPON_REGISTRY,
    SCRAPER_REGISTRY,
    SPREADSHEET_ID,
    dedup_deals,
    run_scraper,
    write_to_google_sheets,
)


class HarnessAPI:
    """Simple command interface for the DeepSeek harness to call."""

    def __init__(self, sheet_id: str = SPREADSHEET_ID):
        self.sheet_id = sheet_id
        self.last_results: list[dict] = []
        self.cache_file = os.path.join(os.path.dirname(__file__), "deal_cache.json")

    def execute(self, command: str) -> dict:
        """
        Parse and execute a natural-language-ish command.
        Returns a dict with status, message, and optional data.

        Commands the harness can send:
            "scrape all"                     → scrape all sites
            "scrape slickdeals dealnews"     → scrape specific sites
            "scrape laptops"                 → scrape + filter to laptops
            "find <keyword>"                 → search cached results
            "find deals under $500"          → filter by max price
            "update sheet"                   → push last results to Google Sheet
            "list sites"                     → show available scraper sites
            "list categories"                → show category tab names
            "status"                         → how many deals cached
            "export json"                    → save cache to JSON file
        """
        cmd = command.strip().lower()

        # --- LIST SITES ---
        if cmd in ("list sites", "sites", "help sites"):
            return {
                "status": "ok",
                "message": f"Deal sites: {list(SCRAPER_REGISTRY.keys())} | Coupon sites: {list(COUPON_REGISTRY.keys())}",
                "data": {
                    "deal_sites": list(SCRAPER_REGISTRY.keys()),
                    "coupon_sites": list(COUPON_REGISTRY.keys()),
                },
            }

        # --- LIST CATEGORIES ---
        if cmd in ("list categories", "categories", "cats"):
            from scraper import CATEGORY_TAB_MAP
            tabs = sorted(set(CATEGORY_TAB_MAP.values()))
            return {
                "status": "ok",
                "message": f"Category tabs: {tabs}",
                "data": tabs,
            }

        # --- STATUS ---
        if cmd in ("status", "count", "how many"):
            return {
                "status": "ok",
                "message": f"{len(self.last_results)} deals in memory",
                "data": {"count": len(self.last_results)},
            }

        # --- SCRAPE ---
        if cmd.startswith("scrape"):
            parts = cmd.replace("scrape", "").strip().split()
            sites = None
            category = None

            # Check if parts are site names or category names
            site_names = []
            for p in parts:
                if p in SCRAPER_REGISTRY:
                    site_names.append(p)
                elif p != "all":
                    category = p

            sites = site_names if site_names else None

            deals = run_scraper(sites=sites, category_filter=category)
            self.last_results = deals
            self._save_cache()

            return {
                "status": "ok",
                "message": f"Scraped {len(deals)} deals",
                "data": {
                    "count": len(deals),
                    "by_source": self._count_by("source_site"),
                    "by_category": self._count_by("category"),
                },
            }

        # --- FIND / SEARCH ---
        if cmd.startswith("find") or cmd.startswith("search"):
            query = cmd.replace("find", "").replace("search", "").strip()

            # Load from cache if memory is empty
            if not self.last_results:
                self._load_cache()

            results = self.last_results

            # Price filter
            price_match = re.search(r"under\s*\$?([\d,]+)", query)
            if price_match:
                max_price = float(price_match.group(1).replace(",", ""))
                filtered = []
                for d in results:
                    try:
                        p = float(re.sub(r"[^\d.]", "", d["sale_price"]))
                        if p <= max_price:
                            filtered.append(d)
                    except (ValueError, TypeError):
                        pass
                results = filtered
                query = re.sub(r"under\s*\$?[\d,]+", "", query).strip()

            # Keyword filter
            if query:
                keywords = query.lower().split()
                results = [
                    d for d in results
                    if all(kw in d["item"].lower() or kw in d.get("category", "").lower()
                           for kw in keywords)
                ]

            return {
                "status": "ok",
                "message": f"Found {len(results)} matching deals",
                "data": [
                    {
                        "item": d["item"][:80],
                        "price": d["sale_price"],
                        "store": d["store"],
                        "source": d["source_site"],
                    }
                    for d in results[:50]  # cap at 50 for harness response
                ],
            }

        # --- UPDATE SHEET ---
        if cmd in ("update sheet", "write sheet", "push", "sync"):
            if not self.last_results:
                self._load_cache()
            if not self.last_results:
                return {"status": "error", "message": "No deals to write. Run 'scrape' first."}

            write_to_google_sheets(self.last_results, self.sheet_id)
            return {
                "status": "ok",
                "message": f"Wrote {len(self.last_results)} deals to Google Sheet",
            }

        # --- EXPORT ---
        if cmd.startswith("export"):
            if not self.last_results:
                self._load_cache()
            out_path = os.path.join(os.path.dirname(__file__), "deals_export.json")
            with open(out_path, "w") as f:
                json.dump(self.last_results, f, indent=2)
            return {
                "status": "ok",
                "message": f"Exported {len(self.last_results)} deals to {out_path}",
            }

        # --- SCRAPE COUPONS ONLY ---
        if cmd in ("scrape coupons", "coupons", "find coupons", "get coupons"):
            deals = run_scraper(coupons_only=True)
            self.last_results = deals
            self._save_cache()
            return {
                "status": "ok",
                "message": f"Scraped {len(deals)} coupon codes",
                "data": {
                    "count": len(deals),
                    "by_source": self._count_by("source_site"),
                },
            }

        # --- COUPON [STORE] — find coupons for a specific store ---
        if cmd.startswith("coupon"):
            store_query = cmd.replace("coupon", "").replace("coupons", "").strip()
            if not self.last_results:
                self._load_cache()
            results = [
                d for d in self.last_results
                if d.get("category") == "ALL COUPON CODES"
                and (not store_query or store_query in d.get("store", "").lower()
                     or store_query in d.get("brand", "").lower())
            ]
            return {
                "status": "ok",
                "message": f"Found {len(results)} coupon codes" + (f" for '{store_query}'" if store_query else ""),
                "data": [
                    {
                        "store": d.get("store", ""),
                        "code": d.get("coupon_code", ""),
                        "discount": d.get("sale_price", ""),
                        "details": d.get("details", d.get("item", "")),
                    }
                    for d in results[:50]
                ],
            }

        return {
            "status": "error",
            "message": (
                f"Unknown command: '{command}'. "
                "Try: scrape, scrape coupons, find, coupon [store], "
                "update sheet, list sites, status, export"
            ),
        }

    def _count_by(self, field: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for d in self.last_results:
            key = d.get(field, "unknown")
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _save_cache(self):
        with open(self.cache_file, "w") as f:
            json.dump(self.last_results, f)

    def _load_cache(self):
        if os.path.isfile(self.cache_file):
            with open(self.cache_file) as f:
                self.last_results = json.load(f)


# ---------------------------------------------------------------------------
# CLI for quick testing
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    api = HarnessAPI()
    if len(sys.argv) > 1:
        cmd = " ".join(sys.argv[1:])
        result = api.execute(cmd)
        print(json.dumps(result, indent=2))
    else:
        print("Usage: python harness_interface.py <command>")
        print("  e.g.: python harness_interface.py scrape all")
        print("  e.g.: python harness_interface.py find laptop deals under 500")
        print("  e.g.: python harness_interface.py update sheet")
