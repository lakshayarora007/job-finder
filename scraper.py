"""
Aggregates fresher/entry-level Product Manager job postings (incl. APM & PM
Intern roles) from company career pages, filtered to a target city list and
to the last 30 days. Writes output/jobs.csv and output/dashboard.html.

Data sources:
  - Greenhouse job board API (boards-api.greenhouse.io)
  - Lever postings API (api.lever.co)
  - Generic Playwright scraper for custom career sites (see generic_scraper.py)
  - DuckDuckGo + Playwright discovery for extra career pages and ATS links

Company list lives in companies.json - add more tokens there as you find them.
"""
import csv
import email.utils
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
COMPANIES_FILE = ROOT / "companies.json"
PROFILE_FILE = ROOT / "resume_profile.json"
OUTPUT_DIR = ROOT / "output"

TARGET_CITIES = [
    # Delhi-NCR (core hubs only - no Ghaziabad/Faridabad tier-2 sprawl)
    "delhi", "new delhi", "ncr", "noida", "gurgaon", "gurugram",
    # Major tech/product job hubs
    "hyderabad", "bangalore", "bengaluru", "blr", "chennai",
    "pune", "mumbai", "navi mumbai", "thane", "kolkata", "ahmedabad",
]

# Country-level or remote postings we still keep (widened radius) - but NOT an
# arbitrary "SmallTown, India", which is handled by requiring an exact/nationwide
# match rather than any substring containing "india".
NATIONWIDE_LOCATIONS = {"india", "in", "india - remote", "remote - india"}
REMOTE_INDIA_HINTS = [
    "remote india", "india remote", "remote - india", "remote, india",
    "remote (india)", "anywhere in india", "pan india", "pan-india",
    "remote - global", "global remote", "remote worldwide",
]

ROLE_KEYWORDS = [
    "product manager",
    "associate product manager",
    "product management",
    "product analyst",
    "business analyst",
    "associate business analyst",
    "junior business analyst",
    "business systems analyst",
    "systems analyst",
    "business process analyst",
    "process analyst",
    "data analyst",
    "junior data analyst",
    "associate data analyst",
    "data analytics",
    "business analytics",
    "analytics intern",
    "reporting analyst",
    "insights analyst",
    "decision analyst",
    "growth analyst",
    "strategy analyst",
    "operations analyst",
    "business operations analyst",
    "quality analyst",
    "qa analyst",
    "market research analyst",
    "research analyst",
    "management trainee",
    "business trainee",
    "product trainee",
    "graduate trainee",
    "graduate engineer trainee",
    "junior analyst",
    "associate analyst",
    "graduate analyst",
    "growth management intern",
    "product intern",
    "business analyst intern",
    "data analyst intern",
    "product owner",
]

EXCLUDE_TITLE_KEYWORDS = [
    "senior", "sr.", "sr ", "lead", "principal", "staff", "director",
    "head", "vp ", "vice president", "group product", "manager ii",
    "manager iii", "manager 2", "manager 3", "manager - ii",
    "dgm", "gm", "general manager", "tax analyst", "sales commission",
    "commission analyst", "actuarial", "payroll", "accounts payable",
    "treasury", "indirect tax", "fp&a", "finance analyst",
    # analyst domains that don't fit a BA/product/data fresher resume
    "credit analyst", "risk analyst", "investment analyst", "equity research",
    "securities analyst", "security analyst", "soc analyst", "cybersecurity",
    "gis analyst", "esg analyst", "collections analyst", "recovery analyst",
    "network analyst",
]

# Naukri/Indeed/LinkedIn routinely list telecalling or door-to-door sales roles
# under a "Business Analyst"/"Analyst" title to widen their applicant pool -
# these phrases in the JD text are a reliable tell even when the title alone
# looks legitimate.
SALES_DISGUISE_KEYWORDS = [
    "cold call", "cold-call", "telecalling", "tele-calling", "telesales", "tele-sales",
    "outbound call", "outbound calling", "inbound call", "voice process",
    "bpo", "call center", "call centre", "counsellor", "counseling sales",
    "sales target", "revenue target", "achieve target", "achieving target",
    "convert leads", "sales pitch", "door to door", "door-to-door",
    "field sales", "cross-sell", "upsell customers", "inside sales executive",
    "business development executive", "convince customers", "customer conversion calls",
]


def is_disguised_sales_role(text):
    t = normalize_text(text)
    return any(kw in t for kw in SALES_DISGUISE_KEYWORDS)


DAYS_BACK = 30
USER_AGENT = "Mozilla/5.0 (job-aggregator-script)"
# Some career edges (e.g. MakeMyTrip behind Akamai) silently drop requests whose
# User-Agent isn't a real browser, so those fetches use this instead.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
PM_SEARCH_QUERY = "Product Manager"
CITY_QUERY = "Delhi, Noida, Gurgaon, Hyderabad, Pune, Bangalore, Mumbai"
DISCOVERY_KEYWORDS = (
    '"Product Manager" OR APM OR "Associate Product Manager" OR '
    '"Product Analyst" OR "Business Analyst" OR "Product Intern"'
)
SUPPORTED_ATS_PATTERNS = (
    "boards-api.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "careers.zohorecruit.in",
    "careers.zohorecruit.com",
)

PUBLIC_API_QUERIES = [
    "business analyst",
    "associate business analyst",
    "product analyst",
    "product management intern",
    "associate product manager",
    "data analyst intern",
    "junior data analyst",
    "growth analyst",
    "strategy analyst",
    "operations analyst",
    "quality analyst",
    "qa analyst",
]


def load_profile():
    if not PROFILE_FILE.exists():
        return {}
    try:
        return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [profile] failed to read resume_profile.json ({e})", file=sys.stderr)
        return {}


def http_get_json(url, timeout=20, user_agent=USER_AGENT, headers=None):
    all_headers = {"User-Agent": user_agent}
    all_headers.update(headers or {})
    req = urllib.request.Request(url, headers=all_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def http_post_json(url, payload, timeout=60, user_agent=BROWSER_USER_AGENT, headers=None):
    all_headers = {"User-Agent": user_agent, "Content-Type": "application/json"}
    all_headers.update(headers or {})
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=all_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


SECRETS_FILE = ROOT / "secrets.json"


def load_secrets():
    """API keys for paid/keyed sources (Apify, JSearch). Looked up first in
    environment variables (APIFY_TOKEN, JSEARCH_API_KEY), then in an optional
    gitignored secrets.json ({"apify_token": "...", "jsearch_api_key": "..."}).
    Missing keys just mean those sources are skipped - never an error."""
    import os
    secrets = {}
    if SECRETS_FILE.exists():
        try:
            secrets = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [secrets] failed to read secrets.json ({e})", file=sys.stderr)
    if os.environ.get("APIFY_TOKEN"):
        secrets["apify_token"] = os.environ["APIFY_TOKEN"]
    if os.environ.get("JSEARCH_API_KEY"):
        secrets["jsearch_api_key"] = os.environ["JSEARCH_API_KEY"]
    if os.environ.get("NVIDIA_API_KEY"):
        secrets["nvidia_api_key"] = os.environ["NVIDIA_API_KEY"]
    return secrets


def http_get_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def url_is_alive(url, timeout=12):
    if not url:
        return False
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
        method="HEAD",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 400
    except Exception:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return 200 <= getattr(resp, "status", 200) < 400
        except Exception:
            return False


def normalize_text(value):
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def dedupe_query_params(url):
    """Some career sites (e.g. Elastic's Greenhouse wrapper) return absolute_url
    with a repeated query key like `?gh_jid=X&gh_jid=X`, which breaks their
    client-side router and shows a 404 in the browser even though the job is
    live. Collapse repeated keys to a single instance so the link opens cleanly.
    """
    if not url or "?" not in url:
        return url
    base, _, query = url.partition("?")
    deduped = {}
    for pair in query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        deduped[key] = value
    new_query = "&".join(f"{k}={v}" for k, v in deduped.items())
    return f"{base}?{new_query}" if new_query else base


def is_fresher_pm_title(title):
    t = normalize_text(title)
    is_target_role = any(keyword in t for keyword in ROLE_KEYWORDS) or re.search(r"\bapm\b", t)
    if not is_target_role:
        return False
    if any(x in t for x in EXCLUDE_TITLE_KEYWORDS) and "intern" not in t:
        return False
    return True


def score_job(title, location, description_text="", source="", profile=None):
    profile = profile or {}
    t = normalize_text(title)
    loc = normalize_text(location)
    desc = normalize_text(description_text)
    score = 0

    for role in profile.get("target_roles", []):
        if normalize_text(role) in t:
            score += 3
    for skill in profile.get("skills", []):
        if normalize_text(skill) in desc:
            score += 1

    if "business analyst" in t:
        score += 7
    if "associate product manager" in t:
        score += 6
    if re.search(r"\bapm\b", t):
        score += 5
    if "product manager" in t:
        score += 4
    if "product analyst" in t or "data analyst" in t:
        score += 4
    if any(x in t for x in ["intern", "trainee", "graduate", "fresher", "associate", "junior"]):
        score += 3
    if any(term in f"{t} {desc}" for term in profile.get("level_terms", [])):
        score += 2

    if any(city in loc for city in TARGET_CITIES):
        score += 3
    if "ncr" in loc:
        score += 2

    if any(x in t for x in EXCLUDE_TITLE_KEYWORDS):
        score -= 6

    if source in {"greenhouse", "lever", "zoho_recruit", "ashby", "oracle_orc", "makemytrip"}:
        score += 1
    return score


MAX_EXPERIENCE_YEARS = 2

_YRS = r"(?:years?|yrs?\.?)"
_RANGE = r"(\d+)\s*(?:\+|-|–|—|to|and)?\s*(\d+)?\s*\+?"
# Experience requirements come in many phrasings; the key gotcha is that words
# often sit *between* "years" and "experience" ("2-3 years of QA experience")
# or between "experience" and the number ("experience of at least 3 years").
# We tolerate a few filler words on either side so those aren't missed.
EXPERIENCE_PATTERNS = [
    # "2-5 years experience", "2 to 5 years of hands-on QA experience", "5+ years of experience"
    re.compile(_RANGE + r"\s*" + _YRS + r"\s+(?:of\s+)?(?:[a-z][\w+/&.\-]*\s+){0,3}?experience", re.I),
    # "experience of 2-5 years", "experience: 3 years", "min. experience 2 yrs"
    re.compile(r"experience\b[^.\n]{0,40}?" + _RANGE + r"\s*" + _YRS, re.I),
    # "minimum 2 years", "at least 3 yrs", "min. of 2 years"
    re.compile(r"(?:minimum|min\.?|at\s*least|atleast)\s*(?:of\s*)?" + _RANGE + r"\s*" + _YRS, re.I),
    # bare "3+ years" / "2+ yrs" — the trailing plus almost always denotes an experience floor
    re.compile(r"(\d+)()\s*\+\s*" + _YRS, re.I),
    # "2 to 4 years as a Product Owner", "3 years in product management" — the
    # word "experience" never appears, but "N years as/in <role>" is one anyway
    re.compile(_RANGE + r"\s*" + _YRS + r"\s+(?:of\s+)?(?:as|in)\b", re.I),
]


def exceeds_fresher_experience(description_text, max_years=MAX_EXPERIENCE_YEARS):
    if not description_text:
        return False
    # Normalise first: strip HTML tags (Greenhouse/Ashby pass raw markup, which
    # can split "years of experience" across tags) and collapse whitespace so
    # the phrase patterns match reliably regardless of source formatting.
    # html.unescape twice - some sources (e.g. EXL's Oracle ORC) double-encode
    # entities like "&amp;#43;" for "+", which a single pass leaves as "&#43;".
    text = re.sub(r"<[^>]+>", " ", description_text)
    text = html.unescape(html.unescape(text))
    text = re.sub(r"\s+", " ", text)
    lower_bounds = []
    for pattern in EXPERIENCE_PATTERNS:
        for match in pattern.finditer(text):
            try:
                lower_bounds.append(int(match.group(1)))
            except (TypeError, ValueError):
                continue
    if not lower_bounds:
        return False
    return min(lower_bounds) > max_years


def flex_field_min_experience(value):
    """EXL's Oracle ORC postings carry a structured "Experience (In Years)" flex
    field (e.g. "6-9", "0-2") alongside the free-text description - prefer this
    when present since it's more reliable than regexing prose."""
    if not value:
        return None
    numbers = [int(n) for n in re.findall(r"\d+", value)]
    return min(numbers) if numbers else None


def is_yc_company(name):
    """YC companies are tagged "(YC)" in companies.json - for these we accept any
    remote role (incl. US/global remote), since they're workable from India."""
    return "(yc)" in (name or "").lower()


def matches_target_city(location_text, allow_any_remote=False):
    loc = normalize_text(location_text)
    if not loc:
        return False
    if any(city in loc for city in TARGET_CITIES):
        return True
    # Country-level postings ("India", "India - Remote") - kept, but a specific
    # small-town "..., India" is intentionally NOT matched here.
    if loc in NATIONWIDE_LOCATIONS:
        return True
    # India-based or global remote (fresher can take these); never bare "Remote - US".
    if any(hint in loc for hint in REMOTE_INDIA_HINTS):
        return True
    # For YC companies, any remote posting counts (US/unspecified remote included).
    if allow_any_remote and "remote" in loc:
        return True
    return False


def within_days(dt, days=DAYS_BACK):
    if dt is None:
        return True  # keep if we couldn't determine a date, rather than silently drop
    now = datetime.now(timezone.utc)
    return (now - dt) <= timedelta(days=days)


def fetch_greenhouse(name, token):
    jobs = []
    try:
        data = http_get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    except Exception as e:
        print(f"  [greenhouse] {name}: fetch failed ({e})", file=sys.stderr)
        return jobs

    for j in data.get("jobs", []):
        title = j.get("title", "")
        if not is_fresher_pm_title(title):
            continue
        location = (j.get("location") or {}).get("name", "")
        if not matches_target_city(location, allow_any_remote=is_yc_company(name)):
            continue
        if exceeds_fresher_experience(j.get("content", "")):
            continue
        posted_raw = j.get("first_published") or j.get("updated_at")
        try:
            posted_dt = datetime.fromisoformat(posted_raw)
        except Exception:
            posted_dt = None
        if not within_days(posted_dt):
            continue
        url = dedupe_query_params(j.get("absolute_url", ""))
        if not url_is_alive(url):
            continue
        jobs.append({
            "title": title,
            "company": name,
            "location": location,
            "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
            "url": url,
            "source": "greenhouse",
            "score": score_job(title, location, j.get("content", ""), "greenhouse"),
        })
    return jobs


def fetch_lever(name, token):
    jobs = []
    try:
        data = http_get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    except Exception as e:
        print(f"  [lever] {name}: fetch failed ({e})", file=sys.stderr)
        return jobs

    for j in data:
        title = j.get("text", "")
        if not is_fresher_pm_title(title):
            continue
        location = j.get("categories", {}).get("location", "")
        if not matches_target_city(location, allow_any_remote=is_yc_company(name)):
            continue
        full_text = j.get("descriptionPlain", "") + " " + " ".join(
            f"{lst.get('text', '')} {lst.get('content', '')}" for lst in j.get("lists", [])
        )
        if exceeds_fresher_experience(full_text):
            continue
        created_ms = j.get("createdAt")
        posted_dt = (
            datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            if created_ms else None
        )
        if not within_days(posted_dt):
            continue
        url = j.get("hostedUrl", "")
        if not url_is_alive(url):
            continue
        jobs.append({
            "title": title,
            "company": name,
            "location": location,
            "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
            "url": url,
            "source": "lever",
            "score": score_job(title, location, full_text, "lever"),
        })
    return jobs


def fetch_zoho_recruit_jobs(company):
    try:
        from zoho_recruit_scraper import fetch_zoho_recruit
    except ImportError:
        print("  [zoho_recruit] playwright not installed, skipping", file=sys.stderr)
        return []

    jobs = []
    for j in fetch_zoho_recruit(company):
        if not is_fresher_pm_title(j["title"]):
            continue
        if not matches_target_city(j["location"]):
            continue
        if not within_days(j["posted_dt"]):
            continue
        jobs.append({
            "title": j["title"],
            "company": j["company"],
            "location": j["location"],
            "posted_date": j["posted_dt"].date().isoformat() if j["posted_dt"] else "unknown",
            "url": j["url"],
            "source": "zoho_recruit",
            "score": score_job(j["title"], j["location"], "", "zoho_recruit"),
        })
    return jobs


def dedupe(jobs):
    seen = set()
    unique = []
    for j in jobs:
        key = (
            normalize_text(j["company"]),
            normalize_text(j["title"]),
            normalize_text(j["location"]),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(j)
    return unique


def build_manual_search_links(company_entries):
    links = []
    seen = set()
    for entry in company_entries:
        if isinstance(entry, dict):
            name = entry["name"]
            career_url = entry.get("career_url")
        else:
            name = entry
            career_url = None
        # The manual_search list is hand-maintained and accrues duplicates
        # (e.g. "Awfis", "Groww" listed twice); collapse them so the dashboard
        # doesn't render duplicate cards / trip React's unique-key warning.
        key = normalize_text(name)
        if key in seen:
            continue
        seen.add(key)
        q_li = f'"{name}" ("Product Manager" OR APM OR "Product Analyst") ({CITY_QUERY.replace(", ", " OR ")})'
        q_naukri = f"{name} product manager apm analyst"
        links.append({
            "company": name,
            "linkedin_url": "https://www.linkedin.com/jobs/search/?keywords="
                + urllib.parse.quote(f"{name} Product Manager APM"),
            "naukri_url": "https://www.naukri.com/"
                + urllib.parse.quote(q_naukri.lower().replace(" ", "-"))
                + "-jobs",
            "google_url": "https://www.google.com/search?q=" + urllib.parse.quote(q_li) + "&tbs=qdr:m",
        })
        if career_url:
            links[-1]["career_url"] = career_url
    return links


CITY_LINKEDIN_GEO = {
    "Delhi": "New Delhi, Delhi, India",
    "Noida": "Noida, Uttar Pradesh, India",
    "Gurgaon": "Gurugram, Haryana, India",
    "Hyderabad": "Hyderabad, Telangana, India",
    "Pune": "Pune, Maharashtra, India",
    "Bangalore": "Bengaluru, Karnataka, India",
    "Mumbai": "Mumbai, Maharashtra, India",
    "Chennai": "Chennai, Tamil Nadu, India",
    "Kolkata": "Kolkata, West Bengal, India",
    "Ahmedabad": "Ahmedabad, Gujarat, India",
    "Remote (India)": "India",
}

# f_TPR=r2592000 = LinkedIn's "posted in last 30 days" filter (2,592,000 seconds)
LINKEDIN_KEYWORDS = (
    'Product Manager OR APM OR "Product Management Intern" OR '
    '"Associate Product Manager" OR "Product Analyst" OR "Business Analyst" OR Analyst '
    'OR "Growth Product Manager" OR "Product Owner"'
)


GENERAL_SEARCH_QUERY = (
    '("Product Manager" OR "Associate Product Manager" OR APM OR '
    '"Product Analyst" OR "Business Analyst" OR "Product Owner" OR "Growth Product Manager") '
    '(fresher OR "0-2 years" OR intern) jobs'
)


def build_city_search_links():
    links = []
    for city, geo in CITY_LINKEDIN_GEO.items():
        linkedin_url = (
            "https://www.linkedin.com/jobs/search/?keywords="
            + urllib.parse.quote(LINKEDIN_KEYWORDS)
            + "&location=" + urllib.parse.quote(geo)
            + "&f_TPR=r2592000"
        )
        naukri_url = (
            "https://www.naukri.com/product-manager-jobs?k="
            + urllib.parse.quote("product manager, product analyst, business analyst, apm, analyst")
            + "&l=" + urllib.parse.quote(city)
            + "&experience=0"
        )
        general_query = f"{GENERAL_SEARCH_QUERY} {city}"
        # tbs=qdr:m / df=m = restrict results to the past month on Google / DuckDuckGo
        google_url = (
            "https://www.google.com/search?q=" + urllib.parse.quote(general_query)
            + "&tbs=qdr:m"
        )
        duckduckgo_url = (
            "https://duckduckgo.com/?q=" + urllib.parse.quote(general_query)
            + "&df=m"
        )
        links.append({
            "city": city,
            "linkedin_url": linkedin_url,
            "naukri_url": naukri_url,
            "google_url": google_url,
            "duckduckgo_url": duckduckgo_url,
            "search_terms": general_query,
        })
    return links


def discover_links_via_duckduckgo():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [duckduckgo] playwright not installed, skipping discovery", file=sys.stderr)
        return []

    queries = [
        f'({DISCOVERY_KEYWORDS}) ({city} OR "{city} NCR") jobs site:linkedin.com/jobs OR site:jobs.lever.co OR site:boards-api.greenhouse.io'
        for city in ["Delhi", "Noida", "Gurugram", "Hyderabad", "Pune", "Bengaluru", "Mumbai"]
    ]
    queries.append(
        f'({DISCOVERY_KEYWORDS}) jobs site:jobs.lever.co OR site:boards-api.greenhouse.io OR site:jobs.ashbyhq.com OR site:apply.workable.com'
    )

    discovered = []
    seen_urls = set()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            for query in queries:
                try:
                    page.goto("https://duckduckgo.com/?q=" + urllib.parse.quote(query) + "&df=m", wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1500)
                    result_links = page.locator('a[data-testid="result-title-a"]')
                    limit = min(result_links.count(), 10)
                    for index in range(limit):
                        href = result_links.nth(index).get_attribute("href") or ""
                        if not href or href in seen_urls:
                            continue
                        if any(pattern in href for pattern in SUPPORTED_ATS_PATTERNS):
                            seen_urls.add(href)
                            discovered.append(href)
                except Exception as e:
                    print(f"  [duckduckgo] query failed ({e})", file=sys.stderr)
            browser.close()
    except Exception as e:
        print(f"  [duckduckgo] discovery failed ({e})", file=sys.stderr)

    return discovered


def parse_ashby_company_token(url):
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    return parts[0] if parts else None


def parse_workable_company_token(url):
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    return parts[0] if parts else None


def fetch_ashby(name, token):
    jobs = []
    try:
        data = http_get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    except Exception as e:
        print(f"  [ashby] {name}: fetch failed ({e})", file=sys.stderr)
        return jobs

    for job in data.get("jobs", []):
        if not job.get("isListed", True):
            continue
        title = job.get("title", "")
        if not is_fresher_pm_title(title):
            continue
        location = job.get("location", "")
        if not matches_target_city(location, allow_any_remote=is_yc_company(name)):
            continue
        description = re.sub(r"<[^>]+>", " ", job.get("descriptionHtml", ""))
        if exceeds_fresher_experience(description):
            continue
        posted_raw = job.get("publishedAt")
        try:
            posted_dt = datetime.fromisoformat(posted_raw)
        except Exception:
            posted_dt = None
        if not within_days(posted_dt):
            continue
        url = job.get("jobUrl", "")
        jobs.append({
            "title": title,
            "company": name,
            "location": location,
            "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
            "url": url,
            "source": "ashby",
            "score": score_job(title, location, description, "ashby"),
        })
    return jobs


def _mmt_slug(title):
    # MakeMyTrip's opportunity URLs slugify the title as lowercase, spaces->hyphens,
    # keeping parentheses (e.g. "project-based-internship-(technology)"). The SPA
    # actually routes off the job_id, so the slug is cosmetic - but we build a
    # faithful one anyway so the link reads cleanly.
    return re.sub(r"[^a-z0-9()]+", "-", (title or "").lower()).strip("-")


def _mmt_min_experience(job):
    """MakeMyTrip jobs carry a structured experience_from/experience_to (years),
    blank for internships. Prefer this over regexing prose - it's exact."""
    raw = str(job.get("experience_from", "")).strip()
    if not raw:
        return None
    match = re.search(r"\d+", raw)
    return int(match.group()) if match else None


def fetch_makemytrip(name, api_url, base_url):
    """MakeMyTrip (and its group brands) run on Darwinbox, but the careers site
    exposes a clean public JSON proxy at careers.makemytrip.com/api/jobs - no key,
    no bot-protection, one call returns every live posting with structured title,
    location_city, experience_from/to and created timestamp. Far more reliable
    than scraping the Akamai-fronted SPA or hitting Darwinbox directly."""
    jobs = []
    try:
        data = http_get_json(api_url, user_agent=BROWSER_USER_AGENT)
    except Exception as e:
        print(f"  [makemytrip] {name}: fetch failed ({e})", file=sys.stderr)
        return jobs

    for j in data.get("allJobs", []):
        if not j.get("post_on_careers_page", 1):
            continue
        title = j.get("job_title", "")
        if not is_fresher_pm_title(title):
            continue
        cities = j.get("location_city") or []
        if isinstance(cities, str):
            cities = [cities]
        location = ", ".join(cities)
        allow_remote = bool(j.get("is_remote"))
        if not matches_target_city(location, allow_any_remote=allow_remote):
            continue
        min_exp = _mmt_min_experience(j)
        if min_exp is not None and min_exp > MAX_EXPERIENCE_YEARS:
            continue
        try:
            posted_dt = datetime.strptime(
                j.get("job_created_timestamp", ""), "%d-%m-%Y %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
        except Exception:
            posted_dt = None
        if not within_days(posted_dt):
            continue
        job_id = j.get("job_id", "")
        url = f"{base_url.rstrip('/')}/{job_id}/{_mmt_slug(title)}" if job_id else base_url
        jobs.append({
            "title": title,
            "company": name,
            "location": location,
            "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
            "url": url,
            "source": "makemytrip",
            "score": score_job(title, location, "", "makemytrip"),
        })
    return jobs


ORC_SEARCH_KEYWORDS = [
    "Product Manager",
    "Associate Product Manager",
    "Product Analyst",
    "Business Analyst",
    "Data Analyst",
    "Operations Analyst",
    "Quality Analyst",
    "Product Owner",
]


def fetch_oracle_orc_detail(host, site, req_id):
    finder = f'ById;Id="{req_id}",siteNumber={site}'
    url = (
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
        f"?expand=all&onlyData=true&finder={urllib.parse.quote(finder, safe=';,=\"')}"
    )
    data = http_get_json(url)
    items = data.get("items", [])
    return items[0] if items else {}


def fetch_oracle_orc(name, host, site):
    """Oracle Recruiting Cloud (Fusion HCM) career sites - used by EXL and many
    large BPO/enterprise employers. Job search is a REST API on the Oracle-hosted
    domain (not the company's own www.* site), keyed by a site number like CX_2.

    The list endpoint's keyword search is a loose relevance match (it returns
    plenty of unrelated titles alongside real hits), so we still rely on
    is_fresher_pm_title for the real filtering, and run it under a handful of
    role keywords to also surface Analyst/APM openings that "Product Manager"
    alone ranks too low to return in the top results.

    Experience requirements for EXL live in a structured "Experience (In Years)"
    flex field (e.g. "6-9") that's only present on the per-job detail endpoint,
    not the search-list response - so candidates need a follow-up detail fetch.
    """
    candidates = {}
    for keyword in ORC_SEARCH_KEYWORDS:
        finder = (
            f"findReqs;siteNumber={site},"
            "facetsList=LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;CATEGORIES;ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS,"
            f"limit=100,offset=0,sortBy=POSTING_DATES_DESC,keyword={urllib.parse.quote(keyword)}"
        )
        url = (
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            "?onlyData=true&expand=requisitionList.secondaryLocations,requisitionList.workLocation"
            f"&finder={finder}"
        )
        try:
            data = http_get_json(url)
        except Exception as e:
            print(f"  [oracle_orc] {name}: fetch failed for '{keyword}' ({e})", file=sys.stderr)
            continue

        for item in data.get("items", []):
            for req in item.get("requisitionList", []):
                title = req.get("Title", "")
                if not is_fresher_pm_title(title):
                    continue
                location = req.get("PrimaryLocation", "")
                if not matches_target_city(location):
                    continue
                job_id = req.get("Id")
                if job_id in candidates:
                    continue
                candidates[job_id] = (title, location, req.get("PostedDate"))

    jobs = []
    for job_id, (title, location, posted_raw) in candidates.items():
        try:
            posted_dt = datetime.fromisoformat(posted_raw).replace(tzinfo=timezone.utc)
        except Exception:
            posted_dt = None
        if not within_days(posted_dt):
            continue

        try:
            detail = fetch_oracle_orc_detail(host, site, job_id)
        except Exception as e:
            print(f"  [oracle_orc] {name}: detail fetch failed for {job_id} ({e})", file=sys.stderr)
            continue

        flex_years = [
            flex_field_min_experience(f.get("Value"))
            for f in detail.get("requisitionFlexFields", [])
            if "experience" in (f.get("Prompt") or "").lower()
        ]
        flex_years = [y for y in flex_years if y is not None]
        if flex_years and min(flex_years) >= MAX_EXPERIENCE_YEARS:
            continue

        description = " ".join(filter(None, [
            detail.get("ExternalDescriptionStr", ""),
            detail.get("ExternalQualificationsStr", ""),
            detail.get("ExternalResponsibilitiesStr", ""),
        ]))
        if exceeds_fresher_experience(description):
            continue

        job_url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}"
        jobs.append({
            "title": title,
            "company": name,
            "location": location,
            "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
            "url": job_url,
            "source": "oracle_orc",
            "score": score_job(title, location, description, "oracle_orc"),
        })
    return jobs


def fetch_workable(url):
    jobs = []
    try:
        html = http_get_text(url)
    except Exception as e:
        print(f"  [workable] {url}: fetch failed ({e})", file=sys.stderr)
        return jobs

    title_matches = re.findall(r'<a[^>]+class="[^"]*job-card[^"]*"[^>]*href="([^"]+)".*?>(.*?)</a>', html, re.S)
    for href, title in title_matches:
        title_text = re.sub(r"<.*?>", " ", title).strip()
        if not is_fresher_pm_title(title_text):
            continue
        jobs.append({
            "title": title_text,
            "company": parse_workable_company_token(url) or "Workable company",
            "location": "",
            "posted_date": "unknown",
            "url": urllib.parse.urljoin(url, href),
            "source": "workable",
            "score": score_job(title_text, "", "", "workable"),
        })
    return jobs


def fetch_remotive(profile):
    jobs = []
    seen_urls = set()
    for query in PUBLIC_API_QUERIES:
        url = "https://remotive.com/api/remote-jobs?search=" + urllib.parse.quote(query)
        try:
            data = http_get_json(url)
        except Exception as e:
            print(f"  [remotive] fetch failed for '{query}' ({e})", file=sys.stderr)
            continue
        for item in data.get("jobs", []):
            title = item.get("title", "")
            if not is_fresher_pm_title(title):
                continue
            description = item.get("description", "")
            if exceeds_fresher_experience(description):
                continue
            job_url = item.get("url", "")
            if not job_url or job_url in seen_urls:
                continue
            seen_urls.add(job_url)
            location = item.get("candidate_required_location") or "Remote"
            if not matches_target_city(location, allow_any_remote=True):
                continue
            published = item.get("publication_date", "")
            try:
                posted_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except Exception:
                posted_dt = None
            if not within_days(posted_dt):
                continue
            jobs.append({
                "title": title,
                "company": item.get("company_name", "Remotive company"),
                "location": location,
                "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
                "url": job_url,
                "source": "remotive",
                "score": score_job(title, location, description, "remotive", profile),
            })
    return jobs


def fetch_arbeitnow(profile):
    jobs = []
    for page_no in range(1, 4):
        url = f"https://www.arbeitnow.com/api/job-board-api?page={page_no}"
        try:
            data = http_get_json(url)
        except Exception as e:
            print(f"  [arbeitnow] page {page_no}: fetch failed ({e})", file=sys.stderr)
            break
        for item in data.get("data", []):
            title = item.get("title", "")
            if not is_fresher_pm_title(title):
                continue
            description = item.get("description", "")
            if exceeds_fresher_experience(description):
                continue
            location = item.get("location") or ("Remote" if item.get("remote") else "")
            if not matches_target_city(location, allow_any_remote=bool(item.get("remote"))):
                continue
            created = item.get("created_at")
            try:
                posted_dt = datetime.fromtimestamp(created, tz=timezone.utc) if created else None
            except Exception:
                posted_dt = None
            if not within_days(posted_dt):
                continue
            jobs.append({
                "title": title,
                "company": item.get("company_name", "Arbeitnow company"),
                "location": location,
                "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
                "url": item.get("url", ""),
                "source": "arbeitnow",
                "score": score_job(title, location, description, "arbeitnow", profile),
            })
    return jobs


def fetch_weworkremotely(profile):
    """We Work Remotely publishes a public no-key RSS firehose. Each item's
    <title> is "Company: Role" and <region> carries the location ("Anywhere in
    the World", a country, or a US state). Global-remote roles are workable from
    India, so we normalize those to a remote location and reuse the shared
    fresher-title / experience / city / recency filters, like Remotive."""
    jobs = []
    seen_urls = set()
    try:
        raw = http_get_text("https://weworkremotely.com/remote-jobs.rss", timeout=25)
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"  [weworkremotely] fetch/parse failed ({e})", file=sys.stderr)
        return jobs
    for item in root.iter("item"):
        raw_title = (item.findtext("title") or "").strip()
        if not raw_title:
            continue
        # "Company: Role" -> split once; fall back to the whole string as title.
        if ": " in raw_title:
            company, title = raw_title.split(": ", 1)
        else:
            company, title = "We Work Remotely", raw_title
        if not is_fresher_pm_title(title):
            continue
        description = item.findtext("description") or ""
        if exceeds_fresher_experience(description):
            continue
        job_url = (item.findtext("link") or "").strip()
        if not job_url or job_url in seen_urls:
            continue
        seen_urls.add(job_url)
        region = (item.findtext("region") or "").strip()
        low = region.lower()
        location = "Remote (Global)" if ("anywhere" in low or "worldwide" in low) else region
        if not matches_target_city(location, allow_any_remote=True):
            continue
        try:
            posted_dt = email.utils.parsedate_to_datetime(item.findtext("pubDate"))
        except Exception:
            posted_dt = None
        if posted_dt and posted_dt.tzinfo is None:
            posted_dt = posted_dt.replace(tzinfo=timezone.utc)
        if not within_days(posted_dt):
            continue
        jobs.append({
            "title": title.strip(),
            "company": company.strip(),
            "location": location or "Remote",
            "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
            "url": job_url,
            "source": "weworkremotely",
            "score": score_job(title, location, description, "weworkremotely", profile),
        })
    return jobs


def fetch_working_nomads(profile):
    """Working Nomads exposes a public no-key JSON feed of remote jobs. Location
    is free-text ("Global", "Remote Worldwide", country lists, ...); we keep only
    global-remote or India-workable roles via the shared city filter."""
    jobs = []
    seen_urls = set()
    try:
        data = http_get_json("https://www.workingnomads.com/api/exposed_jobs/")
    except Exception as e:
        print(f"  [workingnomads] fetch failed ({e})", file=sys.stderr)
        return jobs
    for item in data if isinstance(data, list) else []:
        title = item.get("title", "")
        if not is_fresher_pm_title(title):
            continue
        description = item.get("description", "")
        if exceeds_fresher_experience(description):
            continue
        job_url = item.get("url", "")
        if not job_url or job_url in seen_urls:
            continue
        seen_urls.add(job_url)
        raw_loc = (item.get("location") or "").strip()
        low = raw_loc.lower()
        if any(k in low for k in ("global", "worldwide", "anywhere")):
            location = "Remote (Global)"
        else:
            location = raw_loc
        if not matches_target_city(location, allow_any_remote=True):
            continue
        try:
            posted_dt = datetime.fromisoformat(item.get("pub_date", ""))
        except Exception:
            posted_dt = None
        if posted_dt and posted_dt.tzinfo is None:
            posted_dt = posted_dt.replace(tzinfo=timezone.utc)
        if not within_days(posted_dt):
            continue
        jobs.append({
            "title": title,
            "company": item.get("company_name", "Working Nomads company"),
            "location": location or "Remote",
            "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
            "url": job_url,
            "source": "workingnomads",
            "score": score_job(title, location, description, "workingnomads", profile),
        })
    return jobs


def parse_relative_date(text):
    """Parse "2 weeks ago" / "Posted 3 Days Ago" / "Posted Today" style strings
    (LinkedIn cards, Workday postedOn) into an approximate UTC datetime.
    Returns None when the text carries no usable signal (e.g. "30+ Days Ago",
    which only says "older than a month" - callers treat None as unknown)."""
    t = normalize_text(text)
    if not t:
        return None
    now = datetime.now(timezone.utc)
    if "today" in t or "just now" in t or "hour" in t or "minute" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    if "30+" in t:
        return None
    m = re.search(r"(\d+)\s*(day|week|month)", t)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    days = n * {"day": 1, "week": 7, "month": 30}[unit]
    return now - timedelta(days=days)


# One India-wide public LinkedIn jobs search: fresher-level keywords,
# f_TPR=r604800 = posted in last 7 days (the scraper runs daily and dedupes,
# so 7 days gives margin for missed runs), f_E=1,2 = Internship + Entry level.
APIFY_LINKEDIN_SEARCH_URL = (
    "https://www.linkedin.com/jobs/search/?keywords="
    + urllib.parse.quote(LINKEDIN_KEYWORDS)
    + "&location=" + urllib.parse.quote("India")
    + "&f_TPR=r604800&f_E=1%2C2"
)
APIFY_JOB_COUNT = 10  # actor minimum; keeps cost ~$0.01/run on the $1/1000 plan


def fetch_apify_linkedin(secrets, profile):
    """Public LinkedIn jobs search via the Apify actor
    curious_coder/linkedin-jobs-scraper (run-sync-get-dataset-items API).
    Scrapes the logged-out /jobs/search page in incognito mode, so no LinkedIn
    account is at risk; cost is per result ($1/1000), capped by APIFY_JOB_COUNT.
    Runs only when an Apify token is configured."""
    token = secrets.get("apify_token")
    if not token:
        print("  [linkedin] no APIFY_TOKEN configured, skipping", file=sys.stderr)
        return []
    url = (
        "https://api.apify.com/v2/acts/curious_coder~linkedin-jobs-scraper"
        "/run-sync-get-dataset-items?token=" + urllib.parse.quote(token)
    )
    payload = {
        "urls": [APIFY_LINKEDIN_SEARCH_URL],
        "count": APIFY_JOB_COUNT,
        "scrapeCompany": False,
        "useIncognitoMode": True,
    }
    try:
        # Actor runs take a few minutes - the sync endpoint holds the connection.
        items = http_post_json(url, payload, timeout=420)
    except Exception as e:
        print(f"  [linkedin] apify run failed ({e})", file=sys.stderr)
        return []
    if not isinstance(items, list):
        print(f"  [linkedin] unexpected apify response shape: {type(items).__name__}", file=sys.stderr)
        return []

    jobs = []
    seen_urls = set()
    for item in items:
        title = item.get("title") or item.get("jobTitle") or ""
        if not is_fresher_pm_title(title):
            continue
        # The actor returns LinkedIn's own seniority tag - trust it for the
        # clearly-senior buckets (titles alone often hide seniority).
        seniority = normalize_text(item.get("seniorityLevel") or "")
        if seniority in ("mid-senior level", "director", "executive"):
            continue
        location = item.get("location") or ""
        if not matches_target_city(location, allow_any_remote=True):
            continue
        description = item.get("descriptionText") or item.get("description") or ""
        if exceeds_fresher_experience(description):
            continue
        if is_disguised_sales_role(title) or is_disguised_sales_role(description):
            continue
        job_url = item.get("link") or item.get("jobUrl") or item.get("url") or ""
        # Strip tracking params so dedupe across runs works on a stable URL.
        job_url = job_url.split("?")[0]
        if not job_url or job_url in seen_urls:
            continue
        seen_urls.add(job_url)
        posted_raw = item.get("postedAt") or item.get("publishedAt") or item.get("postedDate") or ""
        try:
            posted_dt = datetime.fromisoformat(str(posted_raw).replace("Z", "+00:00"))
            if posted_dt.tzinfo is None:
                posted_dt = posted_dt.replace(tzinfo=timezone.utc)
        except Exception:
            posted_dt = parse_relative_date(str(posted_raw))
        if not within_days(posted_dt):
            continue
        jobs.append({
            "title": title.strip(),
            "company": (item.get("companyName") or item.get("company") or "LinkedIn posting").strip(),
            "location": location,
            "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
            "url": job_url,
            "source": "linkedin",
            "score": score_job(title, location, description, "linkedin", profile),
        })
    return jobs


# LinkedIn posts search (hiring posts with Google Forms / emails / apply links,
# which never show up in the jobs tab). harvestapi/linkedin-post-search is a
# no-cookie pay-per-result actor; 3 queries x 10 posts daily stays around
# $1-2/month inside the free $5 Apify credit. postedLimit=24h + daily runs
# means each run only sees fresh posts.
APIFY_POSTS_QUERIES = [
    'hiring "product manager" fresher India',
    'hiring "business analyst" fresher India',
    'hiring "product analyst" OR "data analyst" fresher India',
]
APIFY_POSTS_PER_QUERY = 10

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
FORM_RE = re.compile(r"https?://(?:forms\.gle|docs\.google\.com/forms)[^\s\"'<>)]*")
LINK_RE = re.compile(r"https?://[^\s\"'<>)]+")


def nvidia_extract_post(text, api_key):
    """Ask a free NVIDIA-hosted chat model (build.nvidia.com) to pull structured
    hiring info out of a LinkedIn post. Returns a dict like
    {"is_fresher_hiring": true, "role": ..., "company": ..., "location": ...,
    "apply_email": ..., "apply_link": ...} or None on any failure - callers
    fall back to regex extraction, so this is best-effort enrichment."""
    prompt = (
        "You extract hiring info from LinkedIn posts. Reply with ONLY a JSON object, "
        "no markdown, with keys: is_fresher_hiring (true only if the post is hiring "
        "for a fresher/entry-level/0-2yr product manager, APM, product/business/data "
        "analyst or similar role in India, and NOT a telecalling/BPO/door-to-door/inside "
        "sales role dressed up as an analyst title), role (job title string), company, "
        "location, apply_email (or null), apply_link (Google Form or application URL, or null).\n\n"
        "Post:\n" + text[:6000]
    )
    try:
        data = http_post_json(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            {
                "model": "meta/llama-3.1-8b-instruct",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 300,
            },
            timeout=60,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        content = data["choices"][0]["message"]["content"].strip()
        # Models sometimes wrap JSON in ```json fences despite instructions.
        content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
        match = re.search(r"\{.*\}", content, re.S)
        return json.loads(match.group()) if match else None
    except Exception as e:
        print(f"  [nvidia] extraction failed ({e})", file=sys.stderr)
        return None


def fetch_apify_linkedin_posts(secrets, profile):
    """LinkedIn *posts* search via Apify (harvestapi/linkedin-post-search).
    Catches "we're hiring, apply via this Google Form / mail your resume" posts.
    Each post's text goes through the NVIDIA model for structured extraction
    (falling back to regex for email/form links), and only fresher-relevant
    posts with some way to apply are kept. Apply link preference:
    Google Form > other link in post > mailto: > the post itself."""
    token = secrets.get("apify_token")
    if not token:
        print("  [linkedin_post] no APIFY_TOKEN configured, skipping", file=sys.stderr)
        return []
    nvidia_key = secrets.get("nvidia_api_key")
    url = (
        "https://api.apify.com/v2/acts/harvestapi~linkedin-post-search"
        "/run-sync-get-dataset-items?token=" + urllib.parse.quote(token)
    )
    payload = {
        "searchQueries": APIFY_POSTS_QUERIES,
        "maxPosts": APIFY_POSTS_PER_QUERY,
        "postedLimit": "24h",
        "sortBy": "date",
    }
    try:
        items = http_post_json(url, payload, timeout=420)
    except Exception as e:
        print(f"  [linkedin_post] apify run failed ({e})", file=sys.stderr)
        return []
    if not isinstance(items, list):
        print(f"  [linkedin_post] unexpected apify response shape: {type(items).__name__}", file=sys.stderr)
        return []

    jobs = []
    seen = set()
    today = datetime.now(timezone.utc)
    for item in items:
        text = (
            item.get("content") or item.get("text") or item.get("postText")
            or item.get("commentary") or ""
        )
        if not text or len(text) < 40:
            continue
        post_url = (
            item.get("linkedinUrl") or item.get("url") or item.get("postUrl") or ""
        )
        # Regex baseline - always computed so a failed/absent LLM never loses a lead.
        emails = EMAIL_RE.findall(text)
        forms = FORM_RE.findall(text)
        links = [l for l in LINK_RE.findall(text) if "linkedin.com" not in l]

        extracted = nvidia_extract_post(text, nvidia_key) if nvidia_key else None
        if extracted is not None and not extracted.get("is_fresher_hiring"):
            continue
        if extracted is None:
            # No LLM verdict: keep only if the post text itself looks fresher-ish
            # and mentions a target role, to avoid dumping random posts on the dashboard.
            t = normalize_text(text)
            if not any(k in t for k in ("fresher", "entry level", "entry-level", "0-1", "intern")):
                continue
            if not any(role in t for role in ROLE_KEYWORDS):
                continue
        if exceeds_fresher_experience(text):
            continue
        if is_disguised_sales_role(text):
            continue

        role = (extracted or {}).get("role") or text.strip().splitlines()[0][:80]
        # The LLM's is_fresher_hiring is too lenient ("or similar role" pulls in
        # software engineers, FP&A, non-India posts) - re-apply the same strict
        # title and city gates every other source goes through.
        if not is_fresher_pm_title(str(role)):
            continue
        loc_check = (extracted or {}).get("location") or "India"
        if not matches_target_city(str(loc_check)):
            continue
        company = (extracted or {}).get("company") or (
            (item.get("author") or {}).get("name") if isinstance(item.get("author"), dict)
            else item.get("authorName")
        ) or "LinkedIn post"
        location = (extracted or {}).get("location") or "India"
        apply_email = (extracted or {}).get("apply_email") or (emails[0] if emails else None)
        apply_link = (extracted or {}).get("apply_link") or (forms[0] if forms else None)

        job_url = apply_link or (links[0] if links else None) or (
            f"mailto:{apply_email}" if apply_email else post_url
        )
        if not job_url:
            continue
        key = (normalize_text(str(company)), normalize_text(str(role)))
        if key in seen:
            continue
        seen.add(key)
        title = str(role)
        if apply_email and not job_url.startswith("mailto:"):
            title = f"{title} (resume: {apply_email})"
        job = {
            "title": title,
            "company": str(company),
            "location": str(location),
            "posted_date": today.date().isoformat(),
            "url": job_url,
            "source": "linkedin_post",
            "score": score_job(str(role), str(location), text, "linkedin_post", profile),
        }
        # Keep the original post URL alongside the apply link - the post often
        # has context (team, contact person) the form/email alone doesn't.
        if post_url and post_url != job_url:
            job["post_url"] = post_url
        jobs.append(job)
    return jobs


# Kept to ~6 queries/run: the JSearch free tier is ~200 requests/month and the
# scraper runs daily, so 6 x 30 = 180 stays inside it.
JSEARCH_QUERIES = [
    "associate product manager fresher India",
    "product analyst fresher India",
    "business analyst fresher India",
    "data analyst fresher India",
    "product manager intern India",
    "operations analyst fresher India",
]


def fetch_jsearch(secrets, profile):
    """JSearch (RapidAPI) queries Google for Jobs, which indexes LinkedIn,
    Naukri, Indeed and company sites - structured JSON, no scraping/blocking.
    Runs only when a RapidAPI key is configured."""
    api_key = secrets.get("jsearch_api_key")
    if not api_key:
        print("  [jsearch] no JSEARCH_API_KEY configured, skipping", file=sys.stderr)
        return []
    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    jobs = []
    seen_urls = set()
    for query in JSEARCH_QUERIES:
        url = (
            "https://jsearch.p.rapidapi.com/search?query=" + urllib.parse.quote(query)
            + "&num_pages=1&date_posted=month&country=in"
        )
        try:
            data = http_get_json(url, timeout=30, headers=headers)
        except Exception as e:
            print(f"  [jsearch] fetch failed for '{query}' ({e})", file=sys.stderr)
            continue
        for item in data.get("data", []):
            title = item.get("job_title", "")
            if not is_fresher_pm_title(title):
                continue
            description = item.get("job_description", "")
            if exceeds_fresher_experience(description):
                continue
            city = item.get("job_city") or ""
            state = item.get("job_state") or ""
            country = item.get("job_country") or ""
            if item.get("job_is_remote"):
                location = "Remote (India)" if country.upper() in ("IN", "INDIA") else "Remote"
            else:
                location = ", ".join(p for p in (city, state) if p) or country
            if not matches_target_city(location, allow_any_remote=bool(item.get("job_is_remote"))):
                continue
            job_url = item.get("job_apply_link", "")
            if not job_url or job_url in seen_urls:
                continue
            seen_urls.add(job_url)
            posted_raw = item.get("job_posted_at_datetime_utc") or ""
            try:
                posted_dt = datetime.fromisoformat(posted_raw.replace("Z", "+00:00"))
            except Exception:
                posted_dt = None
            if not within_days(posted_dt):
                continue
            jobs.append({
                "title": title,
                "company": item.get("employer_name", "Unknown"),
                "location": location,
                "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
                "url": job_url,
                "source": "jsearch",
                "score": score_job(title, location, description, "jsearch", profile),
            })
    return jobs


# Kept to 6 queries/run: Naukri actor pricing is per-result, so 6 x ~15 items
# stays around $0.03-0.05/run (~$1-1.5/month) against the free Apify credit.
NAUKRI_QUERIES = [
    "business analyst fresher",
    "associate product manager",
    "product analyst",
    "data analyst fresher",
    "operations analyst fresher",
    "product management intern",
]


def fetch_naukri(secrets, profile):
    """Naukri.com via the Apify actor valig/naukri-jobs-scraper. Naukri's own
    "0 years experience" filter (experience=0) does the heavy lifting server-side,
    and each posting carries a structured experience.minimum/maximum (exact,
    unlike regexing prose) that we check against MAX_EXPERIENCE_YEARS."""
    token = secrets.get("apify_token")
    if not token:
        print("  [naukri] no APIFY_TOKEN configured, skipping", file=sys.stderr)
        return []
    jobs = []
    seen_urls = set()
    for query in NAUKRI_QUERIES:
        url = (
            "https://api.apify.com/v2/acts/valig~naukri-jobs-scraper"
            "/run-sync-get-dataset-items?token=" + urllib.parse.quote(token)
        )
        payload = {
            "keywords": query, "location": "India", "jobAge": "7",
            "limit": 15, "sort": "f", "experience": 0,
        }
        try:
            items = http_post_json(url, payload, timeout=120)
        except Exception as e:
            print(f"  [naukri] query '{query}' failed ({e})", file=sys.stderr)
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            title = item.get("title", "")
            if not is_fresher_pm_title(title):
                continue
            exp = item.get("experience") or {}
            try:
                min_exp = int(exp.get("minimum"))
            except (TypeError, ValueError):
                min_exp = None
            if min_exp is not None and min_exp > MAX_EXPERIENCE_YEARS:
                continue
            locations = item.get("locations") or []
            location = ", ".join(l.get("label", "") for l in locations if l.get("label"))
            if not matches_target_city(location):
                continue
            description = (item.get("description") or {}).get("full", "")
            if exceeds_fresher_experience(description):
                continue
            if is_disguised_sales_role(title) or is_disguised_sales_role(description):
                continue
            job_url = item.get("url", "")
            if not job_url or job_url in seen_urls:
                continue
            seen_urls.add(job_url)
            created_ms = item.get("createdDate")
            try:
                posted_dt = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc) if created_ms else None
            except Exception:
                posted_dt = None
            if not within_days(posted_dt):
                continue
            jobs.append({
                "title": title,
                "company": (item.get("company") or {}).get("name", "Naukri posting"),
                "location": location,
                "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
                "url": job_url,
                "source": "naukri",
                "score": score_job(title, location, description, "naukri", profile),
            })
    return jobs


# One combined query per city (not per role) to keep actor calls, and cost,
# down - the title filter narrows the loose match afterward, same as Oracle ORC.
INDEED_TITLE_QUERY = (
    '"business analyst" OR "product analyst" OR "product manager" OR '
    '"data analyst" OR "associate product manager" OR "product owner"'
)
INDEED_CITIES = ["Delhi", "Noida", "Gurgaon", "Hyderabad", "Bangalore", "Pune", "Mumbai", "Chennai"]


def fetch_indeed(secrets, profile):
    """Indeed India via the Apify actor valig/indeed-jobs-scraper. The actor's
    location field is queried per target city directly (Indeed's own response
    doesn't reliably return a city name, only a state code), so results are
    already city-scoped going in."""
    token = secrets.get("apify_token")
    if not token:
        print("  [indeed] no APIFY_TOKEN configured, skipping", file=sys.stderr)
        return []
    jobs = []
    seen_urls = set()
    for city in INDEED_CITIES:
        url = (
            "https://api.apify.com/v2/acts/valig~indeed-jobs-scraper"
            "/run-sync-get-dataset-items?token=" + urllib.parse.quote(token)
        )
        payload = {
            "country": "in", "title": INDEED_TITLE_QUERY, "location": city,
            "limit": 10, "datePosted": "7",
        }
        try:
            items = http_post_json(url, payload, timeout=120)
        except Exception as e:
            print(f"  [indeed] city '{city}' failed ({e})", file=sys.stderr)
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            title = item.get("title", "")
            if not is_fresher_pm_title(title):
                continue
            description = (item.get("description") or {}).get("text", "")
            if exceeds_fresher_experience(description):
                continue
            if is_disguised_sales_role(title) or is_disguised_sales_role(description):
                continue
            job_url = item.get("url", "") or item.get("jobUrl", "")
            if not job_url or job_url in seen_urls:
                continue
            seen_urls.add(job_url)
            try:
                posted_dt = datetime.fromisoformat(
                    (item.get("datePublished") or "").replace("Z", "+00:00")
                )
            except Exception:
                posted_dt = None
            if not within_days(posted_dt):
                continue
            employer = (item.get("employer") or {}).get("name") or "Indeed posting"
            jobs.append({
                "title": title,
                "company": employer,
                "location": city,
                "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
                "url": job_url,
                "source": "indeed",
                "score": score_job(title, city, description, "indeed", profile),
            })
    return jobs


# Categories mirror resume_profile.json's target roles (business analyst /
# product / data-analyst family); cities mirror TARGET_CITIES. scrapeDetails=True
# costs more per item but is needed for the experience-years filter below, same
# as Naukri/Indeed. maxListings caps spend at ~$3/1000 results per listingType.
INTERNSHALA_CATEGORIES = [
    "business-analyst", "product-management", "data-analyst",
    "operations", "quality-assurance", "market-research",
]
INTERNSHALA_CITIES = ["delhi", "noida", "gurugram", "bangalore", "hyderabad", "pune", "mumbai"]


def fetch_internshala(secrets, profile):
    """Internshala via the Apify actor logiover/internshala-scraper. Internshala
    is India's largest internship/fresher-job board; queried once for
    listingType="internships" and once for "jobs" (Internshala's own fresher
    full-time listings), each scoped to our target categories and cities."""
    token = secrets.get("apify_token")
    if not token:
        print("  [internshala] no APIFY_TOKEN configured, skipping", file=sys.stderr)
        return []
    jobs = []
    seen_urls = set()
    url = (
        "https://api.apify.com/v2/acts/logiover~internshala-scraper"
        "/run-sync-get-dataset-items?token=" + urllib.parse.quote(token)
    )
    for listing_type in ("internships", "jobs"):
        payload = {
            "listingType": listing_type,
            "categories": INTERNSHALA_CATEGORIES,
            "cities": INTERNSHALA_CITIES,
            "maxListings": 60,
            "maxPages": 2,
            "scrapeDetails": True,
        }
        try:
            items = http_post_json(url, payload, timeout=180)
        except Exception as e:
            print(f"  [internshala] {listing_type} query failed ({e})", file=sys.stderr)
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            title = item.get("title", "")
            if not is_fresher_pm_title(title):
                continue
            location = item.get("location", "")
            if not matches_target_city(location, allow_any_remote=bool(item.get("isRemote"))):
                continue
            description = item.get("description", "") or ""
            if exceeds_fresher_experience(description):
                continue
            if is_disguised_sales_role(title) or is_disguised_sales_role(description):
                continue
            job_url = item.get("url", "")
            if not job_url or job_url in seen_urls:
                continue
            seen_urls.add(job_url)
            posted_dt = None
            posted_raw = item.get("postedAt")
            if posted_raw:
                try:
                    posted_dt = datetime.fromisoformat(str(posted_raw).replace("Z", "+00:00"))
                except Exception:
                    posted_dt = None
            if not within_days(posted_dt):
                continue
            jobs.append({
                "title": title,
                "company": item.get("company", "Internshala posting"),
                "location": location,
                "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
                "url": job_url,
                "source": "internshala",
                "score": score_job(title, location, description, "internshala", profile),
            })
    return jobs


def fetch_smartrecruiters(name, token):
    """SmartRecruiters exposes a public no-key postings API per company. The
    list response has title/city/date; the per-posting detail (fetched only for
    title+city matches) supplies the job-ad text for the experience filter."""
    jobs = []
    try:
        data = http_get_json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100")
    except Exception as e:
        print(f"  [smartrecruiters] {name}: fetch failed ({e})", file=sys.stderr)
        return jobs

    for posting in data.get("content", []):
        title = posting.get("name", "")
        if not is_fresher_pm_title(title):
            continue
        loc = posting.get("location") or {}
        location = ", ".join(filter(None, [loc.get("city"), loc.get("country", "").upper()]))
        if not matches_target_city(location, allow_any_remote=bool(loc.get("remote"))):
            continue
        try:
            posted_dt = datetime.fromisoformat(posting.get("releasedDate", "").replace("Z", "+00:00"))
        except Exception:
            posted_dt = None
        if not within_days(posted_dt):
            continue
        posting_id = posting.get("id", "")
        description = ""
        try:
            detail = http_get_json(
                f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{posting_id}"
            )
            sections = (detail.get("jobAd") or {}).get("sections") or {}
            description = " ".join(s.get("text", "") for s in sections.values() if isinstance(s, dict))
        except Exception as e:
            print(f"  [smartrecruiters] {name}: detail fetch failed for {posting_id} ({e})", file=sys.stderr)
        if exceeds_fresher_experience(description):
            continue
        jobs.append({
            "title": title,
            "company": name,
            "location": location,
            "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
            "url": f"https://jobs.smartrecruiters.com/{token}/{posting_id}",
            "source": "smartrecruiters",
            "score": score_job(title, location, description, "smartrecruiters"),
        })
    return jobs


WORKDAY_SEARCH_KEYWORDS = [
    "product manager",
    "product analyst",
    "business analyst",
    "data analyst",
]


def fetch_workday(name, host, tenant, site):
    """Workday career sites (Adobe, Salesforce, many big tech) expose an
    unauthenticated JSON search at /wday/cxs/<tenant>/<site>/jobs (POST). The
    list gives title/locationsText plus a fuzzy postedOn ("Posted 3 Days Ago");
    the per-job detail (fetched only for title+city matches) gives the full
    description for the experience filter. "Posted 30+ Days Ago" is outside our
    window and skipped outright."""
    candidates = {}
    for keyword in WORKDAY_SEARCH_KEYWORDS:
        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        try:
            data = http_post_json(
                url,
                {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": keyword},
                timeout=30,
            )
        except Exception as e:
            print(f"  [workday] {name}: fetch failed for '{keyword}' ({e})", file=sys.stderr)
            continue
        for posting in data.get("jobPostings", []):
            title = posting.get("title", "")
            if not is_fresher_pm_title(title):
                continue
            location = posting.get("locationsText", "")
            if not matches_target_city(location):
                continue
            posted_on = posting.get("postedOn", "")
            if "30+" in posted_on:
                continue
            path = posting.get("externalPath", "")
            if path:
                candidates[path] = (title, location, posted_on)

    jobs = []
    for path, (title, location, posted_on) in candidates.items():
        posted_dt = parse_relative_date(posted_on)
        if not within_days(posted_dt):
            continue
        description = ""
        try:
            detail = http_get_json(f"https://{host}/wday/cxs/{tenant}/{site}{path}", timeout=30)
            description = (detail.get("jobPostingInfo") or {}).get("jobDescription", "")
        except Exception as e:
            print(f"  [workday] {name}: detail fetch failed for {path} ({e})", file=sys.stderr)
        if exceeds_fresher_experience(description):
            continue
        jobs.append({
            "title": title,
            "company": name,
            "location": location,
            "posted_date": posted_dt.date().isoformat() if posted_dt else "unknown",
            "url": f"https://{host}/en-US/{site}{path}",
            "source": "workday",
            "score": score_job(title, location, description, "workday"),
        })
    return jobs


def write_json(jobs, manual_links, city_links, path):
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days_back": DAYS_BACK,
        "target_cities": TARGET_CITIES,
        "jobs": jobs,
        "manual_search": manual_links,
        "city_search": city_links,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_csv(jobs, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["title", "company", "location", "posted_date", "url", "post_url", "source", "score"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(jobs)


def write_html(jobs, manual_links, city_links, path):
    """Render a self-contained (no server, no external assets) dashboard.

    All job/city/company data is embedded as JSON and rendered client-side, so
    search, source-filtering and sorting are instant and the file works from a
    plain file:// open. Kept dependency-free on purpose (system fonts, inline
    CSS/JS) so it opens the same offline as online.
    """
    payload = {
        "jobs": jobs,
        "city": city_links,
        "manual": manual_links,
        "generatedAt": datetime.now().strftime("%d %b %Y, %H:%M"),
        "daysBack": DAYS_BACK,
    }
    # `<\/` guards against a "</script>" inside any string prematurely closing the tag.
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = DASHBOARD_TEMPLATE.replace("/*__DATA__*/null", data_json)
    path.write_text(html, encoding="utf-8")


DASHBOARD_TEMPLATE = r"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PM Fresher Jobs Dashboard</title>
<style>
  :root{
    --bg:#08080c; --surface:#101018; --surface-2:#15151f; --surface-3:#1b1b28;
    --border:#242433; --border-strong:#31313f;
    --text:#e9e9ee; --muted:#9797a6; --faint:#6b6b78;
    --accent:#7c6cff; --accent-2:#a48bff; --accent-ghost:rgba(124,108,255,.12);
    --good:#34d399; --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);
    --radius:14px;
  }
  html[data-theme="light"]{
    --bg:#f6f6fb; --surface:#ffffff; --surface-2:#fbfbfe; --surface-3:#f1f1f7;
    --border:#e7e7f0; --border-strong:#d8d8e4;
    --text:#16161f; --muted:#5c5c6b; --faint:#8a8a99;
    --accent:#6a4dff; --accent-2:#5a3ff0; --accent-ghost:rgba(106,77,255,.09);
    --good:#0f9d67; --shadow:0 1px 2px rgba(20,20,40,.05),0 12px 28px -16px rgba(20,20,50,.18);
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:var(--bg); color:var(--text);
    -webkit-font-smoothing:antialiased; line-height:1.5;
    padding:0 20px 80px;
  }
  a{color:inherit;text-decoration:none}
  .wrap{max-width:1080px;margin:0 auto}

  /* ---- header ---- */
  header{
    position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--bg) 82%,transparent);
    backdrop-filter:saturate(160%) blur(14px);
    margin:0 -20px;padding:18px 20px 14px;border-bottom:1px solid var(--border);
  }
  .head-row{display:flex;align-items:flex-start;gap:16px;justify-content:space-between}
  .brand{display:flex;gap:12px;align-items:center}
  .logo{width:38px;height:38px;border-radius:11px;flex:none;
    background:linear-gradient(140deg,var(--accent),var(--accent-2));
    display:grid;place-items:center;font-weight:800;color:#fff;font-size:17px;
    box-shadow:0 6px 18px -6px var(--accent)}
  h1{font-size:17px;font-weight:700;margin:0;letter-spacing:-.01em}
  .sub{font-size:12.5px;color:var(--muted);margin-top:2px}
  .sub b{color:var(--text);font-weight:600}
  .theme-btn{flex:none;width:36px;height:36px;border-radius:10px;border:1px solid var(--border-strong);
    background:var(--surface-2);color:var(--muted);cursor:pointer;font-size:16px;display:grid;place-items:center;
    transition:.15s}
  .theme-btn:hover{color:var(--text);border-color:var(--accent)}

  /* ---- stat tiles ---- */
  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0}
  .stat{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px}
  .stat .n{font-size:24px;font-weight:750;letter-spacing:-.02em}
  .stat .l{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-top:3px}
  .stat .n.accent{color:var(--accent-2)}

  /* ---- toolbar ---- */
  .toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:8px 0 18px}
  .search{position:relative;flex:1 1 260px;min-width:220px}
  .search svg{position:absolute;left:12px;top:50%;transform:translateY(-50%);opacity:.5}
  .search input{width:100%;padding:11px 14px 11px 38px;border-radius:11px;border:1px solid var(--border-strong);
    background:var(--surface);color:var(--text);font-size:14px;outline:none;transition:.15s}
  .search input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-ghost)}
  select{padding:11px 12px;border-radius:11px;border:1px solid var(--border-strong);background:var(--surface);
    color:var(--text);font-size:13.5px;outline:none;cursor:pointer}
  select:focus{border-color:var(--accent)}
  .chips{display:flex;gap:7px;flex-wrap:wrap}
  .chip{padding:8px 13px;border-radius:999px;border:1px solid var(--border-strong);background:var(--surface);
    color:var(--muted);font-size:12.5px;cursor:pointer;transition:.14s;white-space:nowrap;text-transform:capitalize}
  .chip:hover{color:var(--text);border-color:var(--faint)}
  .chip.on{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}

  /* ---- job list ---- */
  .count{font-size:12.5px;color:var(--muted);margin:0 2px 12px}
  .count b{color:var(--text)}
  .jobs{display:flex;flex-direction:column;gap:10px}
  .job{display:flex;align-items:center;gap:16px;background:var(--surface);border:1px solid var(--border);
    border-radius:var(--radius);padding:15px 17px;transition:.16s;box-shadow:var(--shadow)}
  .job:hover{border-color:var(--border-strong);transform:translateY(-1px)}
  .job-main{flex:1;min-width:0}
  .job-title{font-size:15px;font-weight:650;letter-spacing:-.01em;display:flex;align-items:center;gap:9px;flex-wrap:wrap}
  .job-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:6px;font-size:12.5px;color:var(--muted)}
  .job-meta .co{color:var(--text);font-weight:600}
  .dot{width:3px;height:3px;border-radius:50%;background:var(--faint);flex:none}
  .badge{font-size:10.5px;font-weight:700;padding:3px 8px;border-radius:6px;text-transform:capitalize;letter-spacing:.02em}
  .fresh{color:var(--good);font-weight:600}
  .job-right{display:flex;align-items:center;gap:14px;flex:none}
  .posted{font-size:12px;color:var(--muted);text-align:right;white-space:nowrap}
  .apply{padding:9px 18px;border-radius:10px;background:var(--accent);color:#fff;font-size:13px;font-weight:650;
    white-space:nowrap;transition:.14s;border:1px solid transparent}
  .apply:hover{background:var(--accent-2)}
  .apply.post-link{background:transparent;color:var(--accent-2);border-color:var(--border-strong)}
  .apply.post-link:hover{border-color:var(--accent)}
  .empty{text-align:center;padding:54px 20px;border:1px dashed var(--border-strong);border-radius:var(--radius);color:var(--muted)}
  .empty .big{font-size:15px;color:var(--text);font-weight:600;margin-bottom:6px}

  /* ---- sections ---- */
  section{margin-top:44px}
  .sec-head h2{font-size:16px;font-weight:700;margin:0}
  .sec-head p{font-size:12.5px;color:var(--muted);margin:4px 0 0}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px;margin-top:16px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:13px 15px;
    display:flex;align-items:center;justify-content:space-between;gap:10px;transition:.14s}
  .card:hover{border-color:var(--border-strong)}
  .card .nm{font-size:13.5px;font-weight:600}
  .links{display:flex;gap:11px;flex-wrap:wrap;justify-content:flex-end}
  .links a{font-size:12px;font-weight:600;transition:.14s;white-space:nowrap}
  .links a:hover{text-decoration:underline}
  .li{color:#5aa2ff}.na{color:#34d399}.go{color:#fbbf24}.dd{color:#fb923c}
  .mfilter{margin-top:14px}
  .mfilter input{max-width:320px}
  .more{margin:18px auto 0;display:block;padding:10px 20px;border-radius:10px;border:1px solid var(--border-strong);
    background:var(--surface-2);color:var(--muted);font-size:13px;font-weight:600;cursor:pointer;transition:.14s}
  .more:hover{color:var(--text);border-color:var(--accent)}
  footer{margin-top:52px;padding-top:20px;border-top:1px solid var(--border);font-size:12px;color:var(--faint);text-align:center}

  @media(max-width:640px){
    .stats{grid-template-columns:repeat(2,1fr)}
    .job{flex-direction:column;align-items:stretch;gap:12px}
    .job-right{justify-content:space-between}
    .posted{text-align:left}
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="head-row">
      <div class="brand">
        <div class="logo">PM</div>
        <div>
          <h1>PM Fresher Jobs</h1>
          <div class="sub" id="subline"></div>
        </div>
      </div>
      <button class="theme-btn" id="themeBtn" title="Toggle theme" aria-label="Toggle theme">◐</button>
    </div>
  </header>

  <div class="stats" id="stats"></div>

  <div class="toolbar">
    <label class="search">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
      <input id="q" placeholder="Search title, company, city…" autocomplete="off">
    </label>
    <select id="sort">
      <option value="relevance">Best match</option>
      <option value="latest">Most recent</option>
      <option value="company">Company A–Z</option>
    </select>
  </div>
  <div class="chips" id="sources" style="margin-bottom:18px"></div>

  <div class="count" id="count"></div>
  <div class="jobs" id="jobs"></div>

  <section id="citySec">
    <div class="sec-head">
      <h2>Search by city</h2>
      <p>Pre-filtered LinkedIn / Naukri / Google / DuckDuckGo searches (fresher PM/analyst, last 30 days).</p>
    </div>
    <div class="grid" id="cityGrid"></div>
  </section>

  <section id="manualSec">
    <div class="sec-head">
      <h2>Companies to watch manually</h2>
      <p>Custom / bot-protected career sites — direct search links instead of fragile scraping.</p>
    </div>
    <div class="search mfilter"><input id="mq" placeholder="Filter companies…" autocomplete="off"></div>
    <div class="grid" id="manualGrid"></div>
    <button class="more" id="moreBtn" hidden>Show all</button>
  </section>

  <footer>Static dashboard · regenerated by <b>scraper.py</b> · works offline from file://</footer>
</div>

<script>
const DATA = /*__DATA__*/null;
const $ = s => document.querySelector(s);
const esc = s => String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const SRC_COLORS = {greenhouse:"#34d399",lever:"#818cf8",ashby:"#fbbf24",zoho_recruit:"#22d3ee",generic:"#94a3b8",workable:"#f472b6",oracle_orc:"#f87171",remotive:"#38bdf8",arbeitnow:"#a3e635",makemytrip:"#ff6b57",weworkremotely:"#f59e0b",workingnomads:"#2dd4bf",linkedin:"#0a90ff",linkedin_post:"#38bdf8",jsearch:"#c084fc",smartrecruiters:"#4ade80",workday:"#60a5fa",naukri:"#4f46e5",indeed:"#2557a7"};

function relDate(iso){
  if(!iso||iso==="unknown") return {txt:"—",days:9999};
  const d=new Date(iso+"T00:00:00"); if(isNaN(d)) return {txt:iso,days:9999};
  const days=Math.floor((Date.now()-d.getTime())/86400000);
  if(days<=0) return {txt:"Today",days:0};
  if(days===1) return {txt:"Yesterday",days:1};
  if(days<7) return {txt:days+"d ago",days};
  if(days<30) return {txt:Math.floor(days/7)+"w ago",days};
  return {txt:iso,days};
}
function badge(src){
  const c=SRC_COLORS[src]||"#94a3b8";
  return `<span class="badge" style="color:${c};background:${c}22">${esc(src||"other")}</span>`;
}

const state={q:"",source:"all",sort:"relevance"};

function renderStats(){
  const jobs=DATA.jobs||[];
  const companies=new Set(jobs.map(j=>j.company)).size;
  const sources=new Set(jobs.map(j=>j.source||"other")).size;
  const freshest=jobs.map(j=>relDate(j.posted_date).days).sort((a,b)=>a-b)[0];
  const fTxt=freshest==null?"—":freshest===0?"Today":freshest+"d";
  const tiles=[["n accent",jobs.length,"Live roles"],["n",companies,"Companies"],["n",sources,"Sources"],["n",fTxt,"Freshest"]];
  $("#stats").innerHTML=tiles.map(([cls,n,l])=>`<div class="stat"><div class="${cls}">${n}</div><div class="l">${l}</div></div>`).join("");
  $("#subline").innerHTML=`Fresher / APM / Intern PM · Delhi-NCR, Hyderabad, Pune, Bangalore, Mumbai · <b>last ${DATA.daysBack} days</b> · updated ${esc(DATA.generatedAt)}`;
}

function renderSources(){
  const set=["all",...new Set((DATA.jobs||[]).map(j=>j.source||"other"))];
  $("#sources").innerHTML=set.map(s=>`<button class="chip${s===state.source?" on":""}" data-s="${esc(s)}">${s==="all"?"All sources":esc(s)}</button>`).join("");
  document.querySelectorAll("#sources .chip").forEach(c=>c.onclick=()=>{state.source=c.dataset.s;renderSources();renderJobs();});
}

function renderJobs(){
  let jobs=(DATA.jobs||[]).slice();
  const q=state.q.toLowerCase();
  if(q) jobs=jobs.filter(j=>`${j.title} ${j.company} ${j.location} ${j.source||""}`.toLowerCase().includes(q));
  if(state.source!=="all") jobs=jobs.filter(j=>(j.source||"other")===state.source);
  if(state.sort==="relevance") jobs.sort((a,b)=>(b.score||0)-(a.score||0)||String(b.posted_date).localeCompare(String(a.posted_date)));
  else if(state.sort==="latest") jobs.sort((a,b)=>String(b.posted_date).localeCompare(String(a.posted_date)));
  else jobs.sort((a,b)=>String(a.company).localeCompare(String(b.company)));

  $("#count").innerHTML=`<b>${jobs.length}</b> ${jobs.length===1?"role":"roles"}${q||state.source!=="all"?" match your filters":""}`;
  if(!jobs.length){
    $("#jobs").innerHTML=`<div class="empty"><div class="big">No roles match right now</div>Try clearing filters or switching sort to “Most recent”. Fresher PM openings are genuinely rare — the city searches below are your fallback.</div>`;
    return;
  }
  $("#jobs").innerHTML=jobs.map(j=>{
    const r=relDate(j.posted_date);
    const fresh=r.days<=7?`<span class="fresh">● ${esc(r.txt)}</span>`:esc(r.txt);
    return `<div class="job">
      <div class="job-main">
        <div class="job-title">${esc(j.title)} ${badge(j.source)}</div>
        <div class="job-meta"><span class="co">${esc(j.company)}</span><span class="dot"></span>${esc(j.location||"—")}</div>
      </div>
      <div class="job-right">
        <div class="posted">${fresh}</div>
        ${j.post_url?`<a class="apply post-link" href="${esc(j.post_url)}" target="_blank" rel="noopener">Post ↗</a>`:""}
        <a class="apply" href="${esc(j.url)}" target="_blank" rel="noopener">Apply →</a>
      </div>
    </div>`;
  }).join("");
}

function renderCity(){
  const c=DATA.city||[];
  if(!c.length){$("#citySec").hidden=true;return;}
  $("#cityGrid").innerHTML=c.map(x=>`<div class="card"><span class="nm">${esc(x.city)}</span>
    <span class="links">
      <a class="li" href="${esc(x.linkedin_url)}" target="_blank" rel="noopener">LinkedIn</a>
      <a class="na" href="${esc(x.naukri_url)}" target="_blank" rel="noopener">Naukri</a>
      <a class="go" href="${esc(x.google_url)}" target="_blank" rel="noopener">Google</a>
      ${x.duckduckgo_url?`<a class="dd" href="${esc(x.duckduckgo_url)}" target="_blank" rel="noopener">DDG</a>`:""}
    </span></div>`).join("");
}

let manualLimit=24;
function renderManual(){
  const all=DATA.manual||[];
  if(!all.length){$("#manualSec").hidden=true;return;}
  const mq=($("#mq").value||"").toLowerCase();
  const list=mq?all.filter(x=>x.company.toLowerCase().includes(mq)):all;
  const shown=list.slice(0,manualLimit);
  $("#manualGrid").innerHTML=shown.map(x=>`<div class="card"><span class="nm">${esc(x.company)}</span>
    <span class="links">
      <a class="li" href="${esc(x.linkedin_url)}" target="_blank" rel="noopener">LinkedIn</a>
      <a class="na" href="${esc(x.naukri_url)}" target="_blank" rel="noopener">Naukri</a>
      <a class="go" href="${esc(x.google_url)}" target="_blank" rel="noopener">Google</a>
      ${x.career_url?`<a class="dd" href="${esc(x.career_url)}" target="_blank" rel="noopener">Careers</a>`:""}
    </span></div>`).join("");
  const btn=$("#moreBtn");
  if(list.length>manualLimit){btn.hidden=false;btn.textContent=`Show all ${list.length}`;}
  else btn.hidden=true;
}

// theme
(function(){
  const saved=localStorage.getItem("pmjobs-theme");
  if(saved) document.documentElement.dataset.theme=saved;
  else if(window.matchMedia&&matchMedia("(prefers-color-scheme: light)").matches) document.documentElement.dataset.theme="light";
  $("#themeBtn").onclick=()=>{
    const t=document.documentElement.dataset.theme==="light"?"dark":"light";
    document.documentElement.dataset.theme=t;localStorage.setItem("pmjobs-theme",t);
  };
})();

$("#q").oninput=e=>{state.q=e.target.value;renderJobs();};
$("#sort").onchange=e=>{state.sort=e.target.value;renderJobs();};
$("#mq").oninput=()=>{manualLimit=24;renderManual();};
$("#moreBtn").onclick=()=>{manualLimit=1e9;renderManual();};

renderStats();renderSources();renderJobs();renderCity();renderManual();
</script>
</body>
</html>
"""


def main():
    companies = json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))
    profile = load_profile()
    all_jobs = []
    print("Fetching Greenhouse boards...")
    for c in companies.get("greenhouse", []):
        found = fetch_greenhouse(c["name"], c["token"])
        print(f"  {c['name']}: {len(found)} matching jobs")
        all_jobs.extend(found)

    print("Fetching Lever boards...")
    for c in companies.get("lever", []):
        try:
            found = fetch_lever(c["name"], c["token"])
            print(f"  {c['name']}: {len(found)} matching jobs")
            all_jobs.extend(found)
        except Exception as e:
            print(f"  [lever] {c['name']}: fetch failed ({e})", file=sys.stderr)

    generic_companies = companies.get("generic", [])
    if generic_companies:
        try:
            from generic_scraper import fetch_generic
        except ImportError:
            print("  [generic] playwright not installed, skipping custom career sites", file=sys.stderr)
            generic_companies = []
        for c in generic_companies:
            found = fetch_generic(c)
            print(f"  {c['name']}: {len(found)} matching jobs")
            all_jobs.extend(found)

    zoho_companies = companies.get("zoho_recruit", [])
    if zoho_companies:
        print("Fetching Zoho Recruit career sites...")
        for c in zoho_companies:
            found = fetch_zoho_recruit_jobs(c)
            print(f"  {c['name']}: {len(found)} matching jobs")
            all_jobs.extend(found)

    print("Fetching Oracle Recruiting Cloud boards...")
    for c in companies.get("oracle_orc", []):
        try:
            found = fetch_oracle_orc(c["name"], c["host"], c["site"])
            print(f"  {c['name']}: {len(found)} matching jobs")
            all_jobs.extend(found)
        except Exception as e:
            print(f"  [oracle_orc] {c['name']}: fetch failed ({e})", file=sys.stderr)

    print("Fetching Ashby boards...")
    for c in companies.get("ashby", []):
        try:
            found = fetch_ashby(c["name"], c["token"])
            print(f"  {c['name']}: {len(found)} matching jobs")
            all_jobs.extend(found)
        except Exception as e:
            print(f"  [ashby] {c['name']}: fetch failed ({e})", file=sys.stderr)

    print("Fetching MakeMyTrip (Darwinbox proxy) boards...")
    for c in companies.get("makemytrip", []):
        try:
            found = fetch_makemytrip(
                c["name"],
                c.get("api_url", "https://careers.makemytrip.com/api/jobs"),
                c.get("base_url", "https://careers.makemytrip.com/prod/opportunity"),
            )
            print(f"  {c['name']}: {len(found)} matching jobs")
            all_jobs.extend(found)
        except Exception as e:
            print(f"  [makemytrip] {c['name']}: fetch failed ({e})", file=sys.stderr)

    print("Fetching SmartRecruiters boards...")
    for c in companies.get("smartrecruiters", []):
        try:
            found = fetch_smartrecruiters(c["name"], c["token"])
            print(f"  {c['name']}: {len(found)} matching jobs")
            all_jobs.extend(found)
        except Exception as e:
            print(f"  [smartrecruiters] {c['name']}: fetch failed ({e})", file=sys.stderr)

    print("Fetching Workday boards...")
    for c in companies.get("workday", []):
        try:
            found = fetch_workday(c["name"], c["host"], c["tenant"], c["site"])
            print(f"  {c['name']}: {len(found)} matching jobs")
            all_jobs.extend(found)
        except Exception as e:
            print(f"  [workday] {c['name']}: fetch failed ({e})", file=sys.stderr)

    secrets = load_secrets()
    print("Fetching LinkedIn (public search via Apify)...")
    found = fetch_apify_linkedin(secrets, profile)
    print(f"  LinkedIn: {len(found)} matching jobs")
    all_jobs.extend(found)

    print("Fetching Naukri (via Apify)...")
    found = fetch_naukri(secrets, profile)
    print(f"  Naukri: {len(found)} matching jobs")
    all_jobs.extend(found)

    print("Fetching Indeed India (via Apify)...")
    found = fetch_indeed(secrets, profile)
    print(f"  Indeed: {len(found)} matching jobs")
    all_jobs.extend(found)

    print("Fetching Internshala (via Apify)...")
    found = fetch_internshala(secrets, profile)
    print(f"  Internshala: {len(found)} matching jobs")
    all_jobs.extend(found)

    print("Fetching LinkedIn hiring posts (via Apify + NVIDIA extraction)...")
    found = fetch_apify_linkedin_posts(secrets, profile)
    print(f"  LinkedIn posts: {len(found)} matching leads")
    all_jobs.extend(found)

    print("Fetching JSearch (Google for Jobs)...")
    found = fetch_jsearch(secrets, profile)
    print(f"  JSearch: {len(found)} matching jobs")
    all_jobs.extend(found)

    print("Fetching public no-key job APIs...")
    for label, fetcher in [
        ("Remotive", fetch_remotive),
        ("Arbeitnow", fetch_arbeitnow),
        ("We Work Remotely", fetch_weworkremotely),
        ("Working Nomads", fetch_working_nomads),
    ]:
        found = fetcher(profile)
        print(f"  {label}: {len(found)} matching jobs")
        all_jobs.extend(found)

    print("Discovering extra careers via DuckDuckGo...")
    for url in discover_links_via_duckduckgo():
        if "boards-api.greenhouse.io" in url:
            token_match = re.search(r"/boards/([^/]+)/jobs", url)
            if token_match:
                token = token_match.group(1)
                found = fetch_greenhouse(token.replace("-", " ").title(), token)
                print(f"  [ddg->greenhouse] {token}: {len(found)} matching jobs")
                all_jobs.extend(found)
        elif "jobs.lever.co" in url:
            token = urllib.parse.urlparse(url).path.strip("/").split("/")[0]
            if token:
                found = fetch_lever(token.replace("-", " ").title(), token)
                print(f"  [ddg->lever] {token}: {len(found)} matching jobs")
                all_jobs.extend(found)
        elif "jobs.ashbyhq.com" in url:
            token = parse_ashby_company_token(url)
            if token:
                found = fetch_ashby(token.replace("-", " ").title(), token)
                print(f"  [ddg->ashby] {token}: {len(found)} matching jobs")
                all_jobs.extend(found)
        elif "apply.workable.com" in url:
            found = fetch_workable(url)
            print(f"  [ddg->workable] {len(found)} matching jobs")
            all_jobs.extend(found)

    if not all_jobs:
        print("\nNo jobs matched this run, keeping existing output files unchanged.", file=sys.stderr)
        return

    all_jobs = dedupe(all_jobs)
    all_jobs.sort(key=lambda j: (j.get("score", 0), j["posted_date"]), reverse=True)

    manual_links = build_manual_search_links(companies.get("manual_search", []))
    city_links = build_city_search_links()

    OUTPUT_DIR.mkdir(exist_ok=True)
    write_csv(all_jobs, OUTPUT_DIR / "jobs.csv")
    write_html(all_jobs, manual_links, city_links, OUTPUT_DIR / "dashboard.html")
    write_json(all_jobs, manual_links, city_links, OUTPUT_DIR / "jobs.json")

    print(f"\nTotal: {len(all_jobs)} jobs written to output/jobs.csv, output/dashboard.html, output/jobs.json")


if __name__ == "__main__":
    main()
