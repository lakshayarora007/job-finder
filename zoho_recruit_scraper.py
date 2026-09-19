"""
Fetcher for company career sites built on Zoho Recruit's public "Career Site"
template (URLs like https://careers.<company>.com/jobs/Careers or a custom
domain, with job pages at /jobs/Careers/<id>/<slug>?source=CareerSite).

This template is used as-is by many mid-size Indian companies and isn't
behind bot-detection, so a single generic parser works for all of them -
no per-company CSS selectors needed, just the careers listing URL.
"""
import re
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

JOB_BLOCK_RE = re.compile(r'<div class="cw-filter-joblist"[^>]*>(.*?)</div>\s*</div>\s*</div>', re.S)
TITLE_RE = re.compile(r'class="cw-3-title[^"]*"\s+href="([^"]+)">([^<]+)</a>')
LOCATION_RE = re.compile(r'class="filter-subhead[^"]*"[^>]*>\s*([^<]+?)\s*</p>')
DATE_RE = re.compile(r'class="search-date-opened"[^>]*>\s*([\d/]+)\s*</span>')


def fetch_zoho_recruit(company):
    name = company["name"]
    url = company["url"]
    jobs = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page.goto(url, timeout=45000, wait_until="networkidle")
            page.wait_for_timeout(2000)
            html = page.content()
            browser.close()
    except Exception as e:
        print(f"  [zoho_recruit] {name}: fetch failed ({e})")
        return jobs

    for block in JOB_BLOCK_RE.findall(html):
        title_match = TITLE_RE.search(block)
        if not title_match:
            continue
        link, title = title_match.group(1), title_match.group(2).strip()
        loc_match = LOCATION_RE.search(block)
        location = loc_match.group(1).strip() if loc_match else ""
        date_match = DATE_RE.search(block)
        posted_dt = None
        if date_match:
            try:
                posted_dt = datetime.strptime(date_match.group(1), "%d/%m/%Y").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        jobs.append({
            "title": title,
            "company": name,
            "location": location,
            "posted_dt": posted_dt,
            "url": link,
            "source": "zoho_recruit",
        })
    return jobs
