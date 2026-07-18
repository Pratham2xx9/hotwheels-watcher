#!/usr/bin/env python3
"""
Hot Wheels MRP Watcher
-----------------------
Searches Amazon.in and FirstCry for "hot wheels" listings and sends a
Telegram notification whenever a listing is found priced near one of the
known MRP price points.
"""

import os
import re
import json
import time
import sys
import requests
from bs4 import BeautifulSoup

SEARCH_TERM = "hot wheels"

KNOWN_MRPS = [179, 299, 298, 167, 549, 599, 749, 899]
TOLERANCE_RS = 100

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
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "")


def proxied_get(url, timeout=30):
    if SCRAPERAPI_KEY:
        proxy_url = "https://api.scraperapi.com/"
        params = {"api_key": SCRAPERAPI_KEY, "url": url}
        return requests.get(proxy_url, params=params, timeout=timeout)
    return requests.get(url, headers=HEADERS, timeout=timeout)


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
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


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

            container = link_el
            for _ in range(4):
                if container.parent:
                    container = container.parent

            price_match = re.search(r"₹\s?[\d,]+(?:\.\d+)?", container.get_text())
            if not price_match:
                continue
            price = parse_price(price_match.group())
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

        candidates = soup.select("a[title]")
        print(f"[DEBUG] FirstCry title-links found: {len(candidates)}")

        seen_links = set()
        for link_el in candidates:
            title = link_el.get("title", "").strip()
            href = link_el.get("href", "")
            if not title or not href:
                continue
            full_link = href if href.startswith("http") else "https://www.firstcry.com" + href
            full_link = full_link.split("?")[0]
            if full_link in seen_links:
                continue

            container = link_el
            for _ in range(5):
                if container.parent:
                    container = container.parent

            price_match = re.search(r"₹\s?[\d,]+(?:\.\d+)?", container.get_text())
            if not price_match:
                continue
            price = parse_price(price_match.group())
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


def main():
    seen = load_seen()
    new_seen = set(seen)

    amazon_listings = get_amazon_listings()
    firstcry_listings = get_firstcry_listings()
    print(f"[DEBUG] Amazon listings fetched: {len(amazon_listings)}")
    print(f"[DEBUG] FirstCry listings fetched: {len(firstcry_listings)}")

    all_listings = amazon_listings + firstcry_listings
    print(f"Fetched {len(all_listings)} total listings.")

    if all_listings:
        print("[DEBUG] All prices found this run:")
        for item in all_listings:
            print(f"  - {item['site']}: Rs.{item['price']:.0f} | {item['title'][:70]}")
    else:
        print("[DEBUG] No listings fetched at all from either site.")

    found_any = False
    for item in all_listings:
        band_label, mrp = matches_mrp_band(item["price"])
        if not band_label:
            continue

        key = item["link"]
        if key in seen:
            continue

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
        time.sleep(1)

    if not found_any:
        print("No new near-MRP Hot Wheels listings this run.")

    save_seen(new_seen)


if __name__ == "__main__":
    sys.exit(main())
