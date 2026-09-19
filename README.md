# PM Fresher Jobs Aggregator

Fresher-level Product Manager / APM / PM Intern / Product Analyst / Business
Analyst jobs, filtered to Delhi-NCR (Delhi, Noida, Gurgaon), Hyderabad, Pune,
Bangalore, Mumbai, posted in the last 30 days — pulled from company career
pages via their public job-board APIs (Greenhouse, Lever, Zoho Recruit) and
refreshed daily.

## How it works

- `companies.json` — list of companies to check, grouped by:
  - `greenhouse` / `lever` — companies with a public job-board API (real scraping)
  - `zoho_recruit` — companies using Zoho Recruit's public "Career Site"
    template (URLs like `careers.<company>.com/jobs/Careers/...`). Common
    among mid-size Indian companies, not bot-protected, and needs no
    per-company selectors — just the careers listing URL.
  - `makemytrip` — MakeMyTrip's careers site is a Darwinbox-backed SPA, but it
    exposes a clean public JSON proxy at `careers.makemytrip.com/api/jobs` (no
    key, returns every live posting with structured title / location /
    experience-range / created-date). One caveat: the Akamai edge silently drops
    requests whose User-Agent isn't a real browser, so this fetch uses a
    browser UA (`BROWSER_USER_AGENT`). Same shape works for other MMT group
    brands on the same proxy — add `{ "name": "...", "api_url": "..." }` here.
  - `generic` — custom career sites (needs Playwright + CSS selectors, see below)
  - `manual_search` — companies with no scrapeable job data (custom SPA,
    bot-protected, or no structured job board at all — e.g. Zomato/Blinkit
    ("Eternal"), Bombay Shaving Company, Policybazaar — whose own careers page
    is Akamai-blocked and which posts via Naukri + email, so there's no public
    JSON API to hit). For these the dashboard
    just gives you one-click LinkedIn/Naukri search links instead of trying
    to scrape — safer (no ToS risk, no blocking) and more reliable than a
    scraper that breaks on the next site redesign.
- `scraper.py` — fetches jobs from each company's public API plus no-key
  public remote-job APIs (Remotive, Arbeitnow, We Work Remotely RSS,
  Working Nomads JSON), filters by
  title (fresher BA/APM/product/data/ops analyst/intern roles, seniors excluded), by city, and
  by posting date, dedupes, and writes:
  - `output/jobs.csv`, `output/dashboard.html` (static, no server needed)
  - `output/jobs.json` (consumed by the Next.js dashboard in `web/`)
- `generic_scraper.py` — Playwright-based fallback for companies that don't
  use Greenhouse/Lever (custom career sites). Only runs for entries you add
  under `"generic"` in companies.json, since each custom site needs its own
  CSS selectors.
- `run_scraper.bat` — what Task Scheduler runs; logs to `output/run_log.txt`.
- `web/` — Next.js app that reads `output/jobs.json` and renders a searchable
  table plus the manual-search links.

## API keys (LinkedIn via Apify, JSearch)

`secrets.json` in the project root (gitignored) holds optional keys:

```json
{ "apify_token": "...", "jsearch_api_key": "...", "nvidia_api_key": "..." }
```

- `apify_token` — Apify Console → Settings → API & Integrations → Personal API
  token. Enables the LinkedIn public-search source.
- `jsearch_api_key` — RapidAPI key subscribed to the free JSearch plan.
  Enables the Google-for-Jobs source.

Environment variables `APIFY_TOKEN` / `JSEARCH_API_KEY` override the file.
Missing keys just skip those sources — everything else still runs.

## Running it manually

```
cd D:\jobfinder
python scraper.py
```

This refreshes `output/jobs.csv`, `output/dashboard.html`, and
`output/jobs.json`. Then either:

- Open `output/dashboard.html` directly in a browser (no server needed), or
- Run the Next.js dashboard:
  ```
  cd D:\jobfinder\web
  npm run dev
  ```
  and open http://localhost:3000

## Scheduled run (already set up)

A Windows Task Scheduler job named **"PM Fresher Jobs Scraper"** is
registered to run `run_scraper.bat` daily at **9:00 AM** (needs the laptop
on and awake at that time). To check/edit it:

- Open **Task Scheduler** app → look under "Task Scheduler Library" for
  "PM Fresher Jobs Scraper"
- Or from a terminal: `schtasks /query /tn "PM Fresher Jobs Scraper"`
- To change the time: `schtasks /change /tn "PM Fresher Jobs Scraper" /st 08:00`
- To delete it: `schtasks /delete /tn "PM Fresher Jobs Scraper" /f`

## Adding more companies

Most Indian startups/tech companies run either Greenhouse or Lever. To check
which one a company uses, try in a browser:

- `https://boards-api.greenhouse.io/v1/boards/<guess-a-token>/jobs`
- `https://api.lever.co/v0/postings/<guess-a-token>?mode=json`

The token is usually visible in the company's existing careers page URL
(e.g. `jobs.lever.co/<token>` or `job-boards.greenhouse.io/<token>`). If you
find a working one, add it to `companies.json` under the right list.

If a company's careers page URL looks like
`careers.<company>.com/jobs/Careers/<id>/<slug>?source=CareerSite`, it's on
Zoho Recruit — just add `{ "name": "...", "url": "https://careers.<company>.com/jobs/Careers" }`
under `"zoho_recruit"` in companies.json, no selectors needed.

For companies with fully custom career sites, add an entry under
`"generic"` with CSS selectors (see comment at top of `generic_scraper.py`).

Either way, Playwright needs to be installed once:

```
pip install -r requirements.txt
playwright install chromium
```

## Currently tracked companies

Naukri (via Apify actor valig/naukri-jobs-scraper — server-side experience=0
filter plus a structured min/max experience field, no key needed beyond
`apify_token`)
Indeed India (via Apify actor valig/indeed-jobs-scraper, queried per target
city — `apify_token`)
Internshala (via Apify actor logiover/internshala-scraper, queried for both
`internships` and `jobs` listing types across BA/product/data-analyst
categories and target cities — `apify_token`; ~$3/1000 results, capped at 60
listings per listing type per run)
SmartRecruiters (no-key public API): ServiceNow, Visa, Grab
Workday (unauthenticated /wday/cxs JSON): Adobe, Salesforce
LinkedIn (public search, via Apify actor curious_coder/linkedin-jobs-scraper —
needs `apify_token` in `secrets.json`; incognito mode, 10 jobs/run ≈ $0.01/run
against the free $5/month credit)
LinkedIn hiring posts (via Apify actor harvestapi/linkedin-post-search — the
"we're hiring, apply via this Google Form / mail your resume" posts that never
appear in the jobs tab; post text is parsed by a free NVIDIA-hosted LLM
(`nvidia_api_key`, build.nvidia.com) into role/company/apply-email/apply-link,
with regex fallback; ~$1-2/month at 3 queries x 10 posts daily)
JSearch / Google for Jobs (RapidAPI — needs `jsearch_api_key` in `secrets.json`;
6 queries/run stays inside the ~200/month free tier)
Greenhouse: Groww, Razorpay, PhonePe, Postman, Slice, Zenoti, InMobi, Affle, Observe.AI, HighRadius, Turing, Stripe, Coinbase, Databricks, Twilio, Coursera, Druva, GitLab, Elastic, ZoomInfo, Airbnb, Amplitude, Mixpanel, Cloudflare, Fastly, Datadog, New Relic, Okta, Cockroach Labs, Temporal Technologies, DevRev, Atomicwork, MongoDB
Lever: Meesho, CRED, Zeta, Freshworks, Hevo Data, Mindtickle, Porter, Paytm, FamPay, Plivo
Ashby: Atlan, Sarvam AI, SpotDraft, Navi
MakeMyTrip (Darwinbox JSON proxy): MakeMyTrip
Oracle ORC: EXL
No-key public APIs: Remotive, Arbeitnow, We Work Remotely (RSS), Working Nomads (JSON)

## Fresher-level filtering

Every source is filtered to titles matching the fresher/APM/analyst role
keywords and excludes senior/lead/director titles (`is_fresher_pm_title`),
plus a max-experience cutoff of 2 years wherever the posting states one
(`exceeds_fresher_experience` / `MAX_EXPERIENCE_YEARS`) — either from the JD
text or, where the source provides it, a structured experience field (Naukri,
MakeMyTrip, EXL's Oracle ORC).

Naukri/Indeed/LinkedIn also routinely list telecalling, door-to-door, or
inside-sales roles under an "Analyst"/"Business Analyst" title. `scraper.py`
checks the JD text against `SALES_DISGUISE_KEYWORDS` (cold calling, telesales,
BPO, sales target, lead conversion, etc.) and drops matches — applied on
Naukri, Indeed, LinkedIn jobs, and LinkedIn posts.

## Known limitation

Fresher/APM-specific PM openings are genuinely rare at any given moment —
don't be surprised if `output/jobs.csv` is empty on some days. The pipeline
itself is verified working (it correctly picks up matching roles when they
exist and excludes senior titles like "Product Manager II" or "Product
Design Manager"). Widening the company list (see above) is the main lever
for getting more hits. Big tech (Amazon, Google, Microsoft, Flipkart etc.)
and companies on Workday aren't covered yet since they don't expose a
simple public JSON API — would need the generic Playwright path with
per-site selectors.
