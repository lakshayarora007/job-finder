"""
Playwright-based fetcher for company career pages that don't run Greenhouse
or Lever (i.e. custom-built sites). Requires per-company CSS selectors since
every custom site is laid out differently.

Add entries to companies.json under "generic" like:

  {
    "name": "ExampleCorp",
    "url": "https://example.com/careers",
    "card_selector": "div.job-card",
    "title_selector": "h3",
    "location_selector": ".job-location",
    "link_selector": "a",
    "link_attr": "href"
  }

Custom sites rarely expose a reliable "posted date" in the HTML, so jobs
found here are kept regardless of age (marked "unknown") - review manually.
"""
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from scraper import is_fresher_pm_title, matches_target_city


def fetch_generic(company):
    jobs = []
    url = company["url"]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (job-aggregator-script)")
            page.goto(url, timeout=30000, wait_until="networkidle")
            cards = page.query_selector_all(company["card_selector"])

            for card in cards:
                title_el = card.query_selector(company["title_selector"])
                if not title_el:
                    continue
                title = title_el.inner_text().strip()
                if not is_fresher_pm_title(title):
                    continue

                loc_el = card.query_selector(company.get("location_selector", ""))
                location = loc_el.inner_text().strip() if loc_el else ""
                if not matches_target_city(location):
                    continue

                link_el = card.query_selector(company.get("link_selector", "a"))
                href = link_el.get_attribute(company.get("link_attr", "href")) if link_el else ""
                full_url = urljoin(url, href) if href else url

                jobs.append({
                    "title": title,
                    "company": company["name"],
                    "location": location,
                    "posted_date": "unknown",
                    "url": full_url,
                    "source": "generic",
                    "score": 1 if "intern" in title.lower() else 0,
                })

            browser.close()
    except Exception as e:
        print(f"  [generic] {company['name']}: scrape failed ({e})")

    return jobs
