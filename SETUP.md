# Black Friday Deal & Coupon Scraper — Setup Guide

## What This Does
Scrapes **17 deal sites + 5 coupon code sites** for ALL Black Friday deals AND coupon codes, then writes them to your Google Sheet. Pure Python — no LLM API calls, no paid tokens. Runs automatically on GitHub Actions.

## Architecture
```
Python Scraper (GitHub Actions, runs every 4 hrs)
    ↓ writes deals + coupons
Google Sheet (storage layer)
    ↑ reads + analyzes
DeepSeek Harness (computing agent)
```

---

## Option A: GitHub Actions (Recommended — runs automatically)

### 1. Create a GitHub repo
```bash
# Create repo on GitHub (or use existing one)
gh repo create bf-scraper --private
cd bf-scraper
# Copy all files from bf_scraper/ into this repo
git add .
git commit -m "Add Black Friday scraper"
git push
```

### 2. Set up Google credentials
1. Go to https://console.cloud.google.com/
2. Create a project (or pick an existing one)
3. Enable the **Google Sheets API** and **Google Drive API**
4. Go to **Credentials** → **Create Credentials** → **Service Account**
5. Create the account, then go to its **Keys** tab → **Add Key** → **JSON**
6. Download the JSON file
7. Open the JSON — copy the `client_email` value
8. Open your Google Sheet → **Share** → paste that email → give **Editor** access

### 3. Add secrets to GitHub
Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these secrets:
- **`GOOGLE_CREDENTIALS_JSON`** — Paste the ENTIRE contents of the JSON key file
- **`SHEET_ID`** (optional) — Your Google Sheet ID. Default: `1VJyOWfomUrkcBruveSjDeBSUAPynJqkudLn94HWWCp8`

### 4. It's running!
The workflow runs automatically:
- **Every 4 hours** during November
- **Every 2 hours** during Thanksgiving-Cyber Monday weekend (Nov 25-30)
- **Every 4 hours** Dec 1-5

You can also trigger it manually: **Actions** tab → **Black Friday Deal & Coupon Scraper** → **Run workflow**

Manual trigger options:
- **Mode**: `all` (default), `deals-only`, `coupons-only`
- **Sites**: comma-separated site names (leave empty for all)
- **Dry run**: test without writing to sheet

---

## Option B: Run Locally

### 1. Install dependencies
```bash
cd bf_scraper
pip install -r requirements.txt
```

### 2. Set up Google credentials (same as step 2 above)
Save the JSON key file as `credentials.json` in this folder.

### 3. Run it
```bash
python scraper.py
```

---

## CLI Options
```bash
# Dry run — scrape but don't write to sheet
python scraper.py --dry-run

# Scrape only specific sites
python scraper.py --sites slickdeals dealnews zenni shein

# Only scrape coupon codes (skip deal aggregators)
python scraper.py --coupons-only

# Only scrape deals (skip coupon sites)
python scraper.py --no-coupons

# Filter to a category
python scraper.py --category laptops

# Save results as JSON too
python scraper.py --output-json deals.json

# Verbose/debug mode
python scraper.py -v
```

## Available Sites

**Deal aggregators (10):**
slickdeals, dealnews, 9to5toys, hip2save, krazycouponlady, bfads, blackfriday, digitaltrends, pennypincher, groupon

**Direct retailers (7):**
zenni, shein, eyebuydirect, fragrancenet, glassesusa, nike, adidas

**Coupon code scrapers (5):**
retailmenot, couponscom, dealspotr, offerscom, goodshop

## What Gets Written to the Sheet

- **"Scraped Deals"** tab — all scraped deals land here first
- Deals auto-route to category tabs (Laptops, Shoes, Clothes, Eyeglasses, Perfume, etc.)
- **"ALL COUPON CODES"** tab — coupon codes go here with Store, Code, Discount, Details, Valid Through, Stackable columns
- Duplicates are skipped — safe to run multiple times

## DeepSeek Harness Integration

See **HARNESS_PROMPT.md** for the full prompt to paste into your DeepSeek harness.

```python
from harness_interface import HarnessAPI
api = HarnessAPI()

# Scrape everything (deals + coupons)
result = api.execute("scrape all")

# Scrape coupon codes only
result = api.execute("scrape coupons")

# Search for specific deals
result = api.execute("find laptop deals under 500")

# Find coupons for a store
result = api.execute("coupon kohls")
result = api.execute("coupon nike")

# Push to sheet
result = api.execute("update sheet")

# Check status
result = api.execute("status")

# List all available sites
result = api.execute("list sites")
```

## Troubleshooting
- **"No Google credentials found"** → Follow the credentials setup above. For GitHub Actions, make sure `GOOGLE_CREDENTIALS_JSON` secret is set.
- **"Spreadsheet not found"** → Make sure you shared the sheet with the service account email
- **No deals found from a site** → The site may have changed its HTML layout. Run with `-v` to see debug output. Each scraper tries multiple CSS selectors as fallbacks.
- **Rate limiting** → The scraper waits 2 seconds between requests. If a site blocks you, increase `REQUEST_DELAY` in scraper.py.
- **GitHub Actions not running** → Check the Actions tab for errors. Make sure the workflow file is at `.github/workflows/scraper.yml`
