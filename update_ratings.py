#!/usr/bin/env python3
import urllib.request
import urllib.error
import re
import json
import time
import sys
import subprocess
import tempfile
import os

def clean_url(url):
    """Remove query parameters and trailing slashes for consistent lookup."""
    if '?' in url:
        url = url.split('?')[0]
    return url.rstrip('/')

def fetch_html_via_safari(url):
    """Automate Safari to load the page and capture the fully rendered HTML."""
    applescript = f'''
    tell application "Safari"
        make new document with properties {{URL:"{url}"}}
        delay 5
        set theSource to source of document 1
        close document 1
        return theSource
    end tell
    '''
    
    fd, path = tempfile.mkstemp(suffix='.scpt')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(applescript)
        
        result = subprocess.run(['osascript', path], capture_output=True, text=True, encoding='utf-8', errors='ignore')
        return result.stdout
    except Exception as e:
        print(f"Safari Automation Error for {url}: {e}", file=sys.stderr)
        return None
    finally:
        os.remove(path)

def normalize_price_to_eur(price_str, currency_str):
    if not price_str:
        return None
    try:
        # Clean price string (remove any spaces, letters, etc.)
        cleaned = re.sub(r'[^\d\.]', '', price_str)
        val = float(cleaned)
        
        # Clean currency
        curr = currency_str.upper().strip() if currency_str else "EUR"
        
        if curr == "EUR":
            return str(round(val))
        elif curr == "HUF":
            # Convert HUF to EUR (approx 400 HUF = 1 EUR)
            return str(round(val / 400.0))
        elif curr == "USD":
            # Convert USD to EUR (approx 1.1 USD = 1 EUR)
            return str(round(val / 1.1))
        else:
            # Fallback to assuming EUR if not known
            return str(round(val))
    except Exception:
        return None

def parse_json_ld(html):
    """Find and parse all json-ld scripts to find aggregateRating and price details."""
    pattern = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)
    matches = pattern.findall(html)
    
    rating_res = None
    price_res = None
    
    for match in matches:
        try:
            data = json.loads(match.strip())
            
            def find_rating(obj):
                if isinstance(obj, dict):
                    # Check standard Schema.org AggregateRating
                    if 'aggregateRating' in obj and isinstance(obj['aggregateRating'], dict):
                        rating_obj = obj['aggregateRating']
                        val = rating_obj.get('ratingValue')
                        count = rating_obj.get('reviewCount')
                        if val is not None and count is not None:
                            return {
                                'rating': str(val),
                                'reviews': str(count)
                            }
                    # Check Product type directly
                    if obj.get('@type') == 'Product' and 'aggregateRating' in obj:
                        rating_obj = obj['aggregateRating']
                        val = rating_obj.get('ratingValue')
                        count = rating_obj.get('reviewCount')
                        if val is not None and count is not None:
                            return {
                                'rating': str(val),
                                'reviews': str(count)
                            }
                    # Recursively search child dictionaries
                    for k, v in obj.items():
                        res = find_rating(v)
                        if res:
                            return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_rating(item)
                        if res:
                            return res
                return None
                
            def find_price(obj):
                if isinstance(obj, dict):
                    if 'offers' in obj:
                        offers_obj = obj['offers']
                        if isinstance(offers_obj, dict):
                            price = offers_obj.get('lowPrice') or offers_obj.get('price')
                            currency = offers_obj.get('priceCurrency')
                            if price is not None:
                                return {
                                    'price': str(price),
                                    'currency': str(currency) if currency else None
                                }
                        elif isinstance(offers_obj, list) and len(offers_obj) > 0:
                            lowest = None
                            curr = None
                            for offer in offers_obj:
                                if isinstance(offer, dict):
                                    price = offer.get('price')
                                    currency = offer.get('priceCurrency')
                                    if price is not None:
                                        try:
                                            p_val = float(price)
                                            if lowest is None or p_val < lowest:
                                                lowest = p_val
                                                curr = currency
                                        except ValueError:
                                            pass
                            if lowest is not None:
                                return {
                                    'price': str(lowest),
                                    'currency': str(curr) if curr else None
                                }
                    for k, v in obj.items():
                        res = find_price(v)
                        if res:
                            return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_price(item)
                        if res:
                            return res
                return None
            
            if not rating_res:
                rating_res = find_rating(data)
            if not price_res:
                price_res = find_price(data)
                
        except Exception:
            continue
            
    # Combine results
    if rating_res:
        res = {
            'rating': rating_res['rating'],
            'reviews': rating_res['reviews'],
            'price': None
        }
        if price_res:
            res['price'] = normalize_price_to_eur(price_res['price'], price_res['currency'])
        return res
        
    return None

def extract_links_from_page(page_url):
    """Scrape the Webflow page using urllib to discover all partner review links."""
    print(f"Discovering links from live page: {page_url}...")
    try:
        req = urllib.request.Request(
            page_url, 
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"Failed to fetch Webflow page: {e}. Exiting.", file=sys.stderr)
        return []
        
    # Find all anchor hrefs matching partners
    pattern = re.compile(r'href=["\'](https?://[^"\']*(?:getyourguide\.com|viator\.com|tripadvisor\.(?:com|tp\.st))[^"\']*)["\']', re.IGNORECASE)
    found_urls = pattern.findall(html)
    
    # Clean URLs and filter for actual product pages (skip directories or widget frames)
    unique_urls = []
    seen = set()
    for url in found_urls:
        cleaned = url.replace('&amp;', '&')
        base = clean_url(cleaned)
        
        # Smart Filter:
        # 1. GetYourGuide product pages must have the activity ID token '-t' followed by digits (e.g., -t12345)
        # 2. Viator product pages must contain '/tours/'
        # 3. TripAdvisor links (shortlinks or full links)
        is_gyg_product = 'getyourguide.com' in base and re.search(r'-t\d+', base)
        is_viator_product = 'viator.com/tours/' in base
        is_ta_link = 'tripadvisor' in base or 'tp.st' in base
        
        if (is_gyg_product or is_viator_product or is_ta_link) and base not in seen:
            seen.add(base)
            unique_urls.append(cleaned)
            
    print(f"Discovered {len(unique_urls)} unique product links to scrape.")
    return unique_urls

def main():
    webflow_pages = [
        "https://www.budapestadventures.com/best-budapest-river-cruises",
        "https://www.budapestadventures.com/best-dinner-cruises-budapest",
        "https://www.budapestadventures.com/best-night-cruises-budapest",
        "https://www.budapestadventures.com/unlimited-prosecco-cruises-budapest",
        "https://www.budapestadventures.com/best-private-boat-tours-budapest"
    ]
    
    partner_urls = []
    for page in webflow_pages:
        urls = extract_links_from_page(page)
        partner_urls.extend(urls)
        
    # Deduplicate while preserving order
    partner_urls = list(dict.fromkeys(partner_urls))
    
    if not partner_urls:
        print("No partner product links found. Exiting.")
        return
        
    ratings_db = {}
    
    for i, raw_url in enumerate(partner_urls, 1):
        print(f"\n[{i}/{len(partner_urls)}] Processing: {raw_url}")
        
        # Fetch page source via Safari to bypass WAF blocks
        html = fetch_html_via_safari(raw_url)
        if not html:
            print(f"-> Skipping: Failed to load page in Safari.")
            continue
            
        print(f"-> HTML retrieved: {len(html)} characters.")
        
        # Parse rating and reviews from json-ld
        result = parse_json_ld(html)
        
        # Fallback regex search if JSON-LD parsing was blocked or structured differently
        if not result:
            rating_match = re.search(r'itemprop=["\']ratingValue["\'][^>]*content=["\']([^"\']+)["\']', html)
            reviews_match = re.search(r'itemprop=["\']reviewCount["\'][^>]*content=["\']([^"\']+)["\']', html)
            if rating_match and reviews_match:
                result = {
                    'rating': rating_match.group(1),
                    'reviews': reviews_match.group(1),
                    'price': None
                }
                print("-> Found rating via meta tag fallback.")
                
        # Find price fallback if result exists but price is None
        if result:
            if result.get('price') is None:
                # itemprop="price" or itemprop="lowPrice"
                price_match = re.search(r'itemprop=["\'](price|lowPrice)["\'][^>]*content=["\']([^"\']+)["\']', html)
                curr_match = re.search(r'itemprop=["\']priceCurrency["\'][^>]*content=["\']([^"\']+)["\']', html)
                if price_match:
                    price_val = price_match.group(2)
                    curr_val = curr_match.group(1) if curr_match else "EUR"
                    result['price'] = normalize_price_to_eur(price_val, curr_val)
                    print(f"-> Found price via meta tag fallback: {result['price']} EUR")
                else:
                    # Generic lowPrice/price JSON regex matches
                    price_json_match = re.search(r'"(?:lowPrice|price)"\s*:\s*(\d+(?:\.\d+)?)', html)
                    curr_json_match = re.search(r'"priceCurrency"\s*:\s*["\']([^"\']+)["\']', html)
                    if price_json_match:
                        price_val = price_json_match.group(1)
                        curr_val = curr_json_match.group(1) if curr_json_match else "EUR"
                        result['price'] = normalize_price_to_eur(price_val, curr_val)
                        print(f"-> Found price via JSON regex fallback: {result['price']} EUR")

            print(f"-> SUCCESS! Rating: {result['rating']}, Reviews: {result['reviews']}, Price: {result.get('price')}")
            # Map raw, clean, and resolved formats for 100% lookup match in Webflow JS
            ratings_db[raw_url] = result
            ratings_db[clean_url(raw_url)] = result
        else:
            print("-> Warning: Could not find aggregateRating on this page.")
            
        # Short cooldown to keep Safari stable
        time.sleep(1)
        
    # Add execution timestamp for client-side SEO date updates
    import datetime
    ratings_db["_timestamp"] = datetime.datetime.utcnow().isoformat() + "Z"

    # Write to ratings.json in the workspace
    output_file = "ratings.json"
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(ratings_db, f, indent=2, ensure_ascii=False)
        print(f"\nSuccessfully wrote ratings database to {output_file} ({len(ratings_db)} mappings).")
    except Exception as e:
        print(f"Error writing output file: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
