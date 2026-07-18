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
KNOWN_MRPS = [179, 299, 298, 167, 549]

TOLERANCE_RS = 100  # allow price to be off by up to +-100 rupees from any known MRP

SEEN_FILE = os.path.join(os.path.dirname(__file__), "seen.json")

# --- Kay Kay Overseas Corporation seller watch ---
# Notifies on ANY Hot Wheels listing/restock from this specific seller,
# regardless of price/MRP match.
KAYKAY_SELLER_ID = "A2GTG1HPYW8M2P"
KAYKAY_SEEN_FILE = os.path.join(os.path.dirname(__file__), "kaykay_seen.json")

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


def load_kaykay_seen():
    if os.path.exists(KAYKAY_SEEN_FILE):
        try:
            with open(KAYKAY_SEEN_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_kaykay_seen(seen):
    with open(KAYKAY_SEEN_FILE, "w") as f:
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


def extract_price_from_container(container_text):
    """Find a price inside a block of text. Tries the rupee-symbol pattern
    first (e.g. Amazon: '₹1,299'), then falls back to a bare two-decimal
    number pattern (e.g. FirstCry renders prices as plain '164.68' with the
    ₹ symbol as a separate icon/font, not literal text)."""
    match = re.search(r"₹\s?[\d,]+(?:\.\d+)?", container_text)
    if match:
        return parse_price(match.group())
    match = re.search(r"(?<!\d)\d{2,5}\.\d{2}(?!\d)", container_text)
    if match:
        return parse_price(match.group())
    return None


# ---------------------------------------------------------------------------
# AMAZON SCRAPER
# ---------------------------------------------------------------------------

def get_kaykay_listings():
    """Search Amazon for 'hot wheels' filtered to only Kay Kay Overseas
    Corporation's listings, using Amazon's built-in seller filter (me=)."""
    url = (
        f"https://www.amazon.in/s?k={SEARCH_TERM.replace(' ', '+')}"
        f"&me={KAYKAY_SELLER_ID}"
    )
    results = []
    try:
        resp = proxied_get(url, timeout=30)
        if resp.status_code != 200:
            print(f"[WARN] Kay Kay seller page returned status {resp.status_code}")
            return results
        print(f"[DEBUG] Kay Kay seller page fetched OK, {len(resp.text)} chars")
        soup = BeautifulSoup(resp.text, "html.parser")

        product_links = soup.select("a[href*='/dp/']")
        print(f"[DEBUG] Kay Kay product links found: {len(product_links)}")

        seen_links = set()
        for link_el in product_links:
            href = link_el.get("href", "")
            if "/dp/" not in href:
                continue
            full_link = "https://www.amazon.in" + href if href.startswith("/") else href
            full_link = full_link.split("?")[0]
            if full_link in seen_links:
                continue

            title = link_el.get_text(strip=True)
            if not title:
                img = link_el.find("img")
                if img and img.get("alt"):
                    title = img.get("alt")
            if not title:
                continue

            container = link_el
            for _ in range(4):
                if container.parent:
                    container = container.parent
            price = extract_price_from_container(container.get_text())

            seen_links.add(full_link)
            results.append({
                "title": title[:150],
                "price": price,  # may be None if price wasn't detectable
                "link": full_link,
            })
    except Exception as e:
        print(f"[ERROR] Kay Kay seller scrape failed: {e}")
    return results


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

        # Amazon's CSS classes change often, but product page links always
        # contain "/dp/" -- anchor on that instead of brittle class names.
        product_links = soup.select("a[href*='/dp/']")
        print(f"[DEBUG] Amazon product links found: {len(product_links)}")

        seen_links = set()
        for link_el in product_links:
            href = link_el.get("href", "")
            if "/dp/" not in href:
                continue
            full_link = "https://www.amazon.in" + href if href.startswith("/") else href
            full_link = full_link.split("?")[0]
            if full_link in seen_links:
                continue

            title = link_el.get_text(strip=True)
            if not title:
                img = link_el.find("img")
                if img and img.get("alt"):
                    title = img.get("alt")
            if not title:
                continue

            # Walk up a few parent levels to reach a container that likely
            # includes the price text near this product link.
            container = link_el
            for _ in range(4):
                if container.parent:
                    container = container.parent

            price = extract_price_from_container(container.get_text())
            if price is None:
                continue

            seen_links.add(full_link)
            results.append({
                "site": "Amazon",
                "title": title[:150],
                "price": price,
                "link": full_link,
            })
    except Exception as e:
        print(f"[ERROR] Amazon scrape failed: {e}")
    return results


# ---------------------------------------------------------------------------
# FIRSTCRY SCRAPER
# ---------------------------------------------------------------------------

def get_firstcry_listings():
    # Dedicated Hot Wheels brand page -- lists all Hot Wheels products
    # directly, more reliable than the generic /search?q= endpoint.
    url = "https://www.firstcry.com/hot-wheels/0/0/113"
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"[WARN] FirstCry returned status {resp.status_code}")
            return results
        print(f"[DEBUG] FirstCry page fetched OK, {len(resp.text)} chars")
        soup = BeautifulSoup(resp.text, "html.parser")

        # Product tiles link to "/product-detail" pages -- anchor on that
        # stable URL pattern instead of brittle class names.
        candidates = soup.select("a[href*='/product-detail']")
        print(f"[DEBUG] FirstCry product-detail links found: {len(candidates)}")

        seen_links = set()
        for link_el in candidates:
            href = link_el.get("href", "")
            if "/product-detail" not in href:
                continue
            full_link = href if href.startswith("http") else "https://www.firstcry.com" + href
            full_link = full_link.split("?")[0]
            if full_link in seen_links:
                continue

            title = (link_el.get("title") or "").strip()
            if not title:
                title = link_el.get_text(strip=True)
            if not title:
                img = link_el.find("img")
                if img and img.get("title"):
                    title = img.get("title")
                elif img and img.get("alt"):
                    title = img.get("alt")
            if not title:
                continue

            container = link_el
            for _ in range(6):
                if container.parent:
                    container = container.parent

            price = extract_price_from_container(container.get_text())
            if price is None:
                continue

            seen_links.add(full_link)
            results.append({
                "site": "FirstCry",
                "title": title[:150],
                "price": price,
                "link": full_link,
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

    # --- Kay Kay Overseas Corporation seller watch ---
    # Notifies on ANY Hot Wheels listing from this seller, regardless of
    # price. If an item disappears (out of stock/delisted) and later
    # reappears, it's treated as a fresh restock and notified again.
    kaykay_listings = get_kaykay_listings()
    print(f"[DEBUG] Kay Kay listings fetched: {len(kaykay_listings)}")

    kaykay_seen = load_kaykay_seen()
    current_kaykay_links = {item["link"] for item in kaykay_listings}

    for item in kaykay_listings:
        if item["link"] in kaykay_seen:
            continue
        price_str = f"₹{item['price']:.0f}" if item["price"] is not None else "price not detected"
        msg = (
            f"⭐️🔥🔥 <b>KAY KAY OVERSEAS — New / Restocked Hot Wheels!</b> 🔥🔥⭐️\n"
            f"Price: {price_str}\n"
            f"Title: {item['title'][:120]}\n"
            f"{item['link']}"
        )
        print(msg)
        send_telegram(msg)
        time.sleep(1)

    save_kaykay_seen(current_kaykay_links)


if __name__ == "__main__":
    sys.exit(main())
