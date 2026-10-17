# Web Scraper

A configurable, **target-driven** web scraper for macOS. You describe the records you need
(what, where, which fields, how many, which format); the program finds sources, crawls politely,
extracts and cleans the data, removes duplicates, scores every record and exports the result.

It implements the design in [`web-scraping-program-breakdown.md`](web-scraping-program-breakdown.md):
schema-first and target-first, not website-first.

```
Target → source discovery → URL discovery → crawl → extract → normalize → validate
       → deduplicate → record limit / stop condition → CSV · XLSX · JSON · JSONL · XML · SQL · Parquet
```

- **Two ways to use it:** a local web app (7-step job wizard, live dashboard, results browser)
  and a command line tool.
- **Automatic source discovery:** search queries are generated from your target. Results are
  ranked into directories/“top companies” lists and company websites.
- **Smart extraction:** JSON-LD / microdata (schema.org), OpenGraph and meta tags, `mailto:` / `tel:`
  links, Cloudflare-protected e-mails, social profiles, labelled values in tables, plus your own
  CSS selectors, XPath, regex or JSON-LD paths for custom fields.
- **One record per company:** directory listings and the company's own website are merged.
  Contact and about pages are visited to fill in missing e-mail, phone and address.
- **Clean data:** E.164 phone numbers, canonical URLs (tracking parameters removed, HTTPS where the
  site supports it), country and province names, numbers, dates, de-duplicated names.
- **Quality score per record** (completeness 30% · validity 25% · target match 20% · source
  reliability 15% · uniqueness 10%), plus validation results and **provenance** (where each value
  came from, how it was extracted, and with what confidence).
- **Polite by default:** respects `robots.txt` and `Crawl-delay`, waits between requests to the same
  site, backs off on HTTP 429, and stops crawling sites that block it.
- **JavaScript sites (optional):** pages that need a browser are rendered with Playwright. If
  Google Chrome is installed, it is used, so no extra download is needed.

---

## Install on a Mac

Requirements: macOS with Python 3.9 or newer. Apple's Command Line Tools include Python 3.9.
Homebrew's or python.org's Python also work.

```bash
git clone https://github.com/joshstevensonx/web-scraper.git
cd web-scraper
./install.sh
```

The installer creates a private virtual environment in `.venv` and installs everything there,
so nothing touches your system Python. It also offers to add:

- **JavaScript rendering** (Playwright + Chromium, ~150 MB): `./install.sh --with-browser`
- **Parquet export** (pyarrow): `./install.sh --with-parquet`

If Python is missing, the installer tells you how to get it (`xcode-select --install` or
`brew install python`).

## Start the app

**Double-click `Web Scraper.command`** in Finder. A Terminal window starts the app and your browser
opens <http://127.0.0.1:8765>. Close the Terminal window to stop it. The first double-click runs the
installer automatically.

> If macOS says the file can't be opened (downloaded ZIPs are quarantined), right-click it → **Open**,
> or run `chmod +x "Web Scraper.command" install.sh` once in Terminal.

From Terminal instead:

```bash
.venv/bin/webscraper ui          # web app
.venv/bin/webscraper --help      # command line
```

Results are saved in **`~/WebScraper/output`**. Job history and records are kept in
`~/WebScraper/webscraper.db`, and logs are in `~/WebScraper/logs`. Set `WEBSCRAPER_HOME` to use a
different folder.

---

## Using the web app

1. **Target**: choose the entity type (business, company, restaurant, hotel, supplier, website,
   product, person, contact, job, property, event or a generic record) and describe it,
   e.g. *Software companies in Cape Town*.
2. **Targeting**: location (country, province/state, city, suburb, postal code), required
   industries and keywords, preferred keywords (they raise the score), exclusions (e.g.
   *Government*) and field filters (e.g. `rating` at least 4).
3. **Fields**: tick the fields to extract, mark required ones, add custom fields. **⋯** lets you
   give a CSS selector, attribute or regex when a site needs one.
4. **Sources**: find sources automatically, use your own URLs (directories, company websites,
   sitemaps, RSS feeds, JSON APIs, single pages), or both.
5. **Record count**: minimum, maximum, *stop at minimum*, and an optional minimum quality score.
6. **Output**: format(s), file name, what to include (source URLs, timestamps, quality score,
   provenance, raw data, rejected records) and advanced crawl settings.
7. **Review**: check the summary and the generated search queries, then **Start scraping**.

The **dashboard** shows live progress: records found vs. target, valid / duplicates / rejected,
sources, pages crawled, success rate, average quality and the current source. You can pause,
resume or stop a job; stopping still exports what was found. When the job finishes, **Results**
lets you search, sort and filter records, choose columns, export in any format and open a record
to see its quality breakdown, validation checks, sources and provenance. **Edit & re-run** copies
a job into the wizard.

## Using the command line

```bash
webscraper new                                    # interactive wizard → job file (and run it)
webscraper run examples/books-toscrape.json       # run a job with a live terminal dashboard
webscraper run job.json -f csv,xlsx --max 200     # override formats / record count
webscraper jobs                                   # list jobs
webscraper show <job-id>                          # summary + first records
webscraper export <job-id> -f json                # export a finished job again
webscraper validate job.json                      # check a job file
webscraper example product > my-job.json         # starter job file
webscraper doctor --network                       # check the installation
```

`Ctrl+C` during `run` stops gracefully and exports the records found so far.

### Examples (in `examples/`)

| File | What it shows |
|---|---|
| `cape-town-software-companies.json` | The full job from the spec: automatic discovery, location/industry targeting, exclusions, XLSX with provenance |
| `books-toscrape.json` | Product catalogue: listing → pagination → detail pages, CSS selector + transform for the star rating |
| `quotes-toscrape.json` | Repeating items on listing pages (`record_selector`) with pagination |
| `company-websites.json` | Enrich a list of company websites (`url_file`), visiting their contact pages |
| `json-api-users.json` | JSON API source with a field map |

(`books.toscrape.com`, `quotes.toscrape.com` and `jsonplaceholder.typicode.com` are public sandboxes
made for practising scraping.)

---

## Job file reference

A job is a JSON file. Only `entity` plus a target description or sources are needed. Everything
else has sensible defaults.

```jsonc
{
  "name": "Cape Town software companies",
  "entity": "business",                 // business, company, supplier, restaurant, hotel, website,
                                        // product, person, contact, job, property, event, generic
  "targeting": {
    "description": "Software companies in Cape Town",
    "required":  { "location": { "country": "South Africa", "province": "Western Cape", "city": "Cape Town" },
                   "industry": ["Software"],            // any of these must appear
                   "rating": { "min": 4 } },            // field filters: {min,max} or value(s)
    "preferred": { "keywords": ["SaaS", "cloud"] },     // raise the score, not required
    "excluded":  { "business_types": ["Government"] },  // reject records mentioning these
    "strict": true                                      // false = keep near-matches (lower score)
  },
  "fields": [
    { "name": "company_name", "required": true },
    { "name": "website", "type": "url", "required": true },
    { "name": "email", "type": "email" },
    { "name": "phone", "type": "phone" },
    { "name": "employees", "type": "text", "selector": ".company-size", "priority": "low" },
    { "name": "logo", "type": "url", "jsonld": "logo.url" },
    { "name": "vat_number", "regex": "VAT(?: No)?[:\\s]+(\\d{10})", "validation": "^\\d{10}$" }
  ],
  "sources": {
    "mode": "auto",                       // auto | manual | hybrid
    "urls": [
      "https://example-directory.com/software",
      { "url": "https://example.com/sitemap.xml", "type": "sitemap" },
      { "url": "https://shop.example/catalogue", "type": "listing",
        "record_selector": ".product-card", "detail_link_pattern": "/product/",
        "follow_pattern": "/catalogue", "exclude_pattern": "\\?sort=", "max_pages": 300 }
    ],
    "url_file": "urls.txt",               // one URL per line
    "search_provider": "auto",            // auto | duckduckgo | brave | searxng | none
    "max_queries": 8, "max_sources": 30,
    "exclude_domains": ["example-spam.com"],
    "discovery": { "directories": true, "company_websites": true }
  },
  "records": { "minimum": 500, "maximum": 1000, "stop_at_minimum": true, "min_quality": 0 },
  "crawl": {
    "delay": 1.0,              // seconds between requests to the same site
    "concurrency": 6,          // parallel requests (never more than one per site)
    "max_pages": 0,            // 0 = derived from the record target
    "max_depth": 3,
    "render_js": "auto",       // auto | never | always (needs Playwright)
    "respect_robots": true,
    "enrich_pages": 3,         // contact/about pages to visit per company website
    "max_runtime_minutes": 0,
    "record_mode": "auto"      // auto | site (one record per website) | page (one per page)
  },
  "output": {
    "format": "xlsx",          // csv, xlsx, json, jsonl, xml, sql, parquet — or a list
    "filename": "cape-town-software-companies",
    "directory": "",           // default ~/WebScraper/output
    "include_source": true, "include_timestamp": true, "include_quality_score": true,
    "include_provenance": true, "include_raw_data": false, "include_rejected": false
  }
}
```

**Shorthands:** `"sources": ["https://…"]`, `"count": 100`, `"range": "100-500"`,
`"output": "csv"`, flat location keys (`"city": "Cape Town"`).

**Field types:** text, number, integer, decimal, boolean, date, datetime, url, email, phone, address,
array, object, json.

**Built-in field names** are recognized automatically and need no selectors: name / company_name /
title, website, email, phone, address, city, province/state, country, postal_code, description,
industry, social_links, linkedin_url (and other networks), contact_person, logo, image, price,
currency, sku, brand, availability, rating, review_count, opening_hours, founded_year, employees,
latitude/longitude, salary, employment_type, date_posted, start_date, venue, bedrooms, bathrooms,
and more. Any other name is a **custom field**. It is found by a matching label on the page
("Founded: 2012"), a meta tag or JSON-LD key, or by your `selector` / `xpath` / `regex` /
`jsonld` / `meta` rule. `transform` post-processes text values, e.g.
`"after:Price:|strip|upper"` (`lower`, `upper`, `title`, `digits`, `before:X`, `after:X`,
`replace:a=>b`, `truncate:N`, `prefix:X`, `suffix:X`, `strip_query`).

**Source types:** `auto` (detected), `directory`/`listing`, `website` (a company's own site),
`detail` (a single record page), `sitemap`, `feed` (RSS/Atom) and `api` (JSON). API sources use
`records_path`, `field_map`, `page_param` and `headers`.

## How it works

| Stage | What happens |
|---|---|
| Target | Job parsed and validated. A description like "X in Y" becomes industry and location criteria |
| Query generation | Industry/keyword synonyms × entity nouns × location, with exclusions as `-term` |
| Source discovery | Search results are classified (directory/list vs. website) and scored: relevance 30%, coverage 25%, quality 20%, accessibility 15%, yield 10% |
| Scheduling | Priority queue per host: detail and contact pages first, then listings. One request per host at a time, with a delay per host |
| Fetching | HTTP with retries and back-off, robots.txt, size limits. Optional browser rendering for JavaScript pages |
| Extraction | JSON-LD/microdata → your rules → metadata → links → labelled values → page text. Directory pages ignore the directory's own header and footer |
| Records | `site` mode merges every page about one company (directory entry + homepage + contact page). `page` mode makes one record per detail page or listing item |
| Processing | Normalization → validation → target matching → quality score → exact de-duplication (domain, URL, e-mail, phone, name + address) → fuzzy name de-duplication |
| Stop condition | Stops when enough valid unique records are found. If the queue runs dry first, runs more searches and reads sitemaps |
| Output | Requested formats + database (records, provenance, raw page log, events) |

Job states: `CREATED → VALIDATING → DISCOVERING_SOURCES → DISCOVERING_URLS → SCRAPING → NORMALIZING
→ VALIDATING_RECORDS → DEDUPLICATING → CHECKING_TARGET_COUNT → EXPORTING → COMPLETED`, plus
`PAUSED`, `CANCELLED`, `FAILED`, `PARTIALLY_COMPLETED` (fewer records than the minimum) and
`SOURCE_BLOCKED`.

## Search providers

Automatic discovery needs a search engine:

- **DuckDuckGo** (default, no key). Fine for personal use. Queries are spaced out, but DuckDuckGo
  may temporarily rate-limit heavy use or some networks (cloud servers and VPNs especially). The job
  log says so when it happens.
- **Brave Search API**: get a key at <https://brave.com/search/api/>, then
  `export BRAVE_API_KEY=…` before starting the app.
- **SearXNG**: point `SEARXNG_URL` at an instance with JSON output enabled.

You can always skip discovery and give your own source URLs (`"mode": "manual"`).

## Responsible use

This tool collects publicly available information. You are responsible for how you use it:

- Respect each website's terms of service. Keep `respect_robots` on and keep sensible delays.
- Personal data (names, e-mails, phone numbers) is regulated in many places, for example GDPR in
  Europe and POPIA in South Africa. Only collect what you have a lawful basis to process, and store
  it securely.
- Don't use it to get around logins, paywalls or anti-bot protections.

## Troubleshooting

| Problem | Fix |
|---|---|
| `0 valid records` | Open the job's **Activity** tab: it lists the most common rejection reasons. Loosen the targeting (turn off *Strict matching*), make fewer fields *required*, or add sources |
| "DuckDuckGo is rate limiting" | Wait a while, set `BRAVE_API_KEY`, or provide source URLs |
| Pages show no data / "needs JavaScript" | `./install.sh --with-browser` (or install Google Chrome) |
| "Source blocked" | The site refused the crawler (HTTP 403/429). Lower `concurrency`, raise `delay`, or use another source |
| Port 8765 in use | The app is probably already running. Open <http://127.0.0.1:8765>, or start it with `webscraper ui --port 8800` |
| Check the setup | `.venv/bin/webscraper doctor --network` |

## Development

```bash
./install.sh --yes            # installs the package in editable mode with test dependencies
.venv/bin/python -m pytest    # offline test-suite (uses an in-process fake web)
```

Project layout (mirrors section 25 of the spec):

```
webscraper/
  job/          models (input parser + validator), state, runner (pipeline), manager
  target/       entity profiles, query generator, target matcher
  sources/      search providers, source discovery and ranking
  discovery/    URL classifier, sitemap and feed readers
  crawling/     frontier (scheduler/queue), fetcher, browser, robots, rate limiter
  extraction/   page model, structured data, metadata/patterns, selectors, semantic labels, field extractor
  processing/   normalization, validation, deduplication + record assembly, quality scoring
  storage/      SQLite: jobs, raw_pages, records, record_provenance, events
  output/       CSV, XLSX, JSON, JSONL, XML, SQL, Parquet writers
  monitoring/   metrics and the terminal dashboard
  web/          local web app (stdlib HTTP server + vanilla JS, works offline)
```
