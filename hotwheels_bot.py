#!/usr/bin/env python3
"""
Hot Wheels MRP Watcher
-----------------------
Searches Amazon.in and FirstCry for "hot wheels" listings and sends a
Telegram notification whenever a listing is found priced near one of the
known MRP bands:

    Mainline        -> MRP 179
    Silver Series    -> MRP 299
    Premium Series   -> MRP 549

"Near MRP" = price is not marked up much above MRP (no scalper pricing)
and not absurdly low (likely a different/damaged item). Tolerance is
configurable below.

Notes / limitations:
- Amazon actively blocks scrapers with captchas and bot-detection. This
  script uses polite headers and light retry logic, but it WILL
  occasionally fail to fetch Amazon results. That's expected -- it will
  just try again on the next scheduled run.
- Keep the run interval reasonable (>= 10-15 min) to avoid getting your
  IP rate-limited or blocked.
- This is for personal shopping alerts only. Don't hammer the sites.
"""

import os
import re
import json
import time
import sys
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

SEARCH_TERM = "hot wheels"

# All known Hot Wheels MRP price points. Any listing within +/- TOLERANCE_RS
# rupees of ANY of these prices will trigger a notification.
KNOWN_MRPS = [179, 299, 298, 167, 549, 599, 749, 899]

TOLERANCE_RS = 100  # allow price to be off by up to +-100 rupees from any known MRP

SEEN_FILE = os.path.join(os.path.dirname(__file__), "seen.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ScraperAPI (https://www.scraperapi.com/) routes requests through rotating
# residential/datacenter proxies so Amazon doesn't instantly block the
# request the way it blocks GitHub's shared runner IPs (503 errors).
# Free tier gives 1000 requests/month. Leave blank to skip proxying
# (Amazon will likely keep returning 503 from GitHub Actions in that case).
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "")


def proxied_get(url, timeout=30):
    """Fetch a URL, routing through ScraperAPI if a key is configured."""
    if SCRAPERAPI_KEY:
        proxy_url = "https://api.scraperapi.com/"
        params = {"api_key": SCRAPERAPI_KEY, "url": url}
        return requests.get(proxy_url, params=params, timeout=timeout)
    return requests.get(url, headers=HEADERS, timeout=timeout)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=2)


def matches_mrp_band(price):
    """Return the closest known MRP if price is within TOLERANCE_RS of it."""
    best_mrp = None
    best_diff = None
    for mrp in KNOWN_MRPS:
        diff = abs(price - mrp)
        if diff <= TOLERANCE_RS and (best_diff is None or diff < best_diff):
            best_mrp = mrp
            best_diff = diff
    if best_mrp is None:
        return None, None
    return f"~₹{best_mrp} MRP", best_mrp


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram not configured, printing instead:\n", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[ERROR] Telegram send failed: {r.status_code} {r.text}")
    except Exception as e:
        print(f"[ERROR] Telegram send exception: {e}")


def parse_price(text):
    """Extract a numeric price from a messy string like '₹1,299' """
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# AMAZON SCRAPER
# ---------------------------------------------------------------------------

def get_amazon_listings():
    url = f"https://www.amazon.in/s?k={SEARCH_TERM.replace(' ', '+')}"
    results = []
    try:
        resp = proxied_get(url, timeout=30)
        if resp.status_code != 200:
            print(f"[WARN] Amazon returned status {resp.status_code}")
            return results
        print(f"[DEBUG] Amazon page fetched OK, {len(resp.text)} chars")
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("div[data-component-type='s-search-result']")
        print(f"[DEBUG] Amazon result cards found: {len(cards)}")
        for card in cards:
            title_el = card.select_one("h2 span")
            link_el = card.select_one("h2 a")
            price_el = card.select_one("span.a-price > span.a-offscreen")
            if not (title_el and link_el and price_el):
                continue
            title = title_el.get_text(strip=True)
            price = parse_price(price_el.get_text(strip=True))
            href = link_el.get("href", "")
            link = "https://www.amazon.in" + href if href.startswith("/") else href
            if price is None:
                continue
            results.append({
                "site": "Amazon",
                "title": title,
                "price": price,
                "link": link.split("?")[0],
            })
    except Exception as e:
        print(f"[ERROR] Amazon scrape failed: {e}")
    return results


# ---------------------------------------------------------------------------
# FIRSTCRY SCRAPER
# ---------------------------------------------------------------------------

def get_firstcry_listings():
    url = f"https://www.firstcry.com/search?q={SEARCH_TERM.replace(' ', '%20')}"
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"[WARN] FirstCry returned status {resp.status_code}")
            return results
        print(f"[DEBUG] FirstCry page fetched OK, {len(resp.text)} chars")
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("li.pdct_list, div.prod_dtl, div.item")
        print(f"[DEBUG] FirstCry result cards found: {len(cards)}")
        for card in cards:
            title_el = card.select_one("a[title]")
            price_el = card.select_one(".prc, .price, span.rupee")
            if not (title_el and price_el):
                continue
            title = title_el.get("title") or title_el.get_text(strip=True)
            price = parse_price(price_el.get_text(strip=True))
            href = title_el.get("href", "")
            link = href if href.startswith("http") else "https://www.firstcry.com" + href
            if price is None:
                continue
            results.append({
                "site": "FirstCry",
                "title": title,
                "price": price,
                "link": link.split("?")[0],
            })
    except Exception as e:
        print(f"[ERROR] FirstCry scrape failed: {e}")
    return results


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    seen = load_seen()
    new_seen = set(seen)

    amazon_listings = get_amazon_listings()
    firstcry_listings = get_firstcry_listings()
    print(f"[DEBUG] Amazon listings fetched: {len(amazon_listings)}")
    print(f"[DEBUG] FirstCry listings fetched: {len(firstcry_listings)}")

    all_listings = amazon_listings + firstcry_listings
    print(f"Fetched {len(all_listings)} total listings.")

    # Print every price found, so we can see in the Actions log whether
    # scraping is actually returning real data or coming back empty/blocked.
    if all_listings:
        print("[DEBUG] All prices found this run:")
        for item in all_listings:
            print(f"  - {item['site']}: Rs.{item['price']:.0f} | {item['title'][:70]}")
    else:
        print("[DEBUG] No listings fetched at all from either site. "
              "This usually means the site blocked the scraper (captcha/bot "
              "detection) or the page structure changed. Check for a 403/blocked "
              "warning above.")

    found_any = False
    for item in all_listings:
        band_label, mrp = matches_mrp_band(item["price"])
        if not band_label:
            continue

        key = item["link"]
        if key in seen:
            continue  # already notified

        found_any = True
        new_seen.add(key)

        msg = (
            f"🔥 <b>Hot Wheels near MRP found!</b>\n"
            f"Site: {item['site']}\n"
            f"Matched: {band_label}\n"
            f"Price: ₹{item['price']:.0f}\n"
            f"Title: {item['title'][:120]}\n"
            f"{item['link']}"
        )
        print(msg)
        send_telegram(msg)
        time.sleep(1)  # be nice to Telegram API

    if not found_any:
        print("No new near-MRP Hot Wheels listings this run.")

    save_seen(new_seen)


if __name__ == "__main__":
    sys.exit(main())
