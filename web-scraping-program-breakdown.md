# Web Scraping Program — Functional & Technical Breakdown

## 1. Overview

The web scraping program is a configurable, target-driven data collection system.

Instead of creating a separate scraper for every website, the platform should allow the user to define:

- What type of records they want
- Which targeting criteria determine matching records
- Which fields must be extracted
- Which sources should be used
- How many records are required
- What output format should be produced

The system then determines an appropriate collection strategy, discovers or uses sources, crawls relevant pages, extracts structured data, validates and normalizes the results, removes duplicates, and generates the requested output.

### Core flow

```text
INPUT
  ↓
Target Definition
  ↓
Source Selection / Discovery
  ↓
Scraping Strategy
  ↓
URL Discovery
  ↓
Crawling / Fetching
  ↓
Extraction
  ↓
Normalization
  ↓
Validation
  ↓
Deduplication
  ↓
Record Limit / Stop Condition
  ↓
Output Generation
```

---

# 2. Core Input Model

The scraping job should be represented as a structured configuration.

```text
SCRAPING JOB
│
├── Target
│   ├── Entity Type
│   ├── Targeting Fields
│   ├── Keywords
│   └── Filters
│
├── Fields
│   └── Fields to Extract
│
├── Sources
│   ├── User Provided
│   └── Automatically Discovered
│
├── Records
│   ├── Minimum
│   └── Maximum
│
└── Output
    ├── Format
    └── Options
```

---

# 3. Input: Target Definition

The target describes what the scraper is expected to find.

## 3.1 Entity Type

The entity type determines the kind of records being collected.

Examples:

```text
Business
Company
Restaurant
Hotel
Product
Person
Job
Property
Event
Website
Supplier
Contact
```

The entity type can determine:

- Available targeting fields
- Default extraction fields
- Search query generation
- Validation rules
- Deduplication rules
- Recommended sources

---

# 4. Input: Specific Targeting Fields

Targeting fields determine which records qualify.

The program should support dynamic targeting fields instead of hard-coded fields.

## 4.1 Geographic Targeting

Possible fields:

```text
Country
Province / State
City
Suburb
District
Postal Code
Address
Radius
Latitude
Longitude
```

Example:

```json
{
  "location": {
    "country": "South Africa",
    "province": "Western Cape",
    "city": "Cape Town"
  }
}
```

## 4.2 Industry Targeting

Possible fields:

```text
Industry
Sub-industry
Sector
Business Category
Business Type
Keywords
```

Example:

```json
{
  "industry": [
    "Software",
    "Technology"
  ],
  "keywords": [
    "SaaS",
    "cloud",
    "software development"
  ]
}
```

## 4.3 Additional Targeting Fields

Depending on the entity type:

```text
Company Size
Revenue Range
Founded Year
Rating
Price Range
Business Status
Product Category
Cuisine
Property Type
Job Type
Experience Level
Technology Used
Funding Stage
```

---

# 5. Target Match Rules

The scraper should distinguish between:

### Required criteria

A record must match.

```text
Location = Cape Town
Industry = Software
```

### Preferred criteria

A record should match where possible but does not have to.

```text
Technology = SaaS
Company Size = 11–50
```

### Exclusion criteria

A record must not match.

```text
Exclude:
- Government
- Non-profit
- Closed businesses
```

Example:

```json
{
  "targeting": {
    "required": {
      "city": "Cape Town",
      "industry": ["Software"]
    },
    "preferred": {
      "keywords": ["SaaS", "cloud"]
    },
    "excluded": {
      "company_types": ["Government"]
    }
  }
}
```

---

# 6. Input: Fields to Be Extracted

The extraction schema defines what information should be returned for each record.

Example:

```text
Company Name
Website
Email
Phone
Address
City
Province
Country
Industry
Description
Contact Person
Social Links
Source URL
```

## 6.1 Field Definition

Each field should support:

```text
Field Name
Display Label
Data Type
Required
Extraction Priority
Validation Rule
Transformation Rule
Default Value
```

Example:

```json
{
  "name": "email",
  "label": "Email",
  "type": "email",
  "required": false,
  "priority": "high"
}
```

## 6.2 Supported Data Types

```text
Text
Number
Integer
Decimal
Boolean
Date
DateTime
URL
Email
Phone
Address
Array
Object
JSON
```

## 6.3 Custom Fields

Users should be able to add custom fields.

Example:

```text
Field: LinkedIn URL
Type: URL

Field: Number of Employees
Type: Integer

Field: Speciality
Type: Text
```

---

# 7. Input: Sources

Sources are optional.

The scraper should support two modes.

## 7.1 User-Provided Sources

The user explicitly selects or enters sources.

```text
Source URLs
Search engines
Directories
APIs
Public websites
Sitemaps
Uploaded URL lists
```

Example:

```json
{
  "sources": {
    "mode": "manual",
    "urls": [
      "https://example.com",
      "https://directory.example.com"
    ]
  }
}
```

## 7.2 Automatic Source Discovery

When the user does not provide sources, the system decides which sources are appropriate.

```text
Target
+
Location
+
Industry
+
Requested Fields
+
Record Count
        ↓
Source Discovery
        ↓
Candidate Sources
        ↓
Source Ranking
        ↓
Selected Sources
```

The system should evaluate sources based on:

```text
Relevance
Coverage
Data Quality
Accessibility
Reliability
Extraction Difficulty
Duplicate Risk
Estimated Yield
```

---

# 8. Source Types

The system should support different source classes.

```text
Search Engine Results
Business Directories
Industry Directories
Listing Websites
Company Websites
Product Catalogs
Marketplaces
Public APIs
Sitemaps
RSS / Feeds
Public Databases
Government/Public Records
```

Each source should have a source profile.

```text
SOURCE
├── Name
├── Base URL
├── Type
├── Priority
├── Access Method
├── Rate Limit
├── Pagination Strategy
├── Search Strategy
├── Extraction Strategy
├── Authentication
└── Reliability Score
```

---

# 9. Query Generation

When source discovery or search is required, the system should convert the target definition into queries.

Example target:

```text
Software companies
Cape Town
SaaS / cloud
```

Generated queries:

```text
"software companies Cape Town"
"SaaS companies Cape Town"
"cloud software companies Cape Town"
"software development Cape Town"
"technology companies Cape Town"
```

Query generation should support:

- Exact phrases
- Synonyms
- Industry variations
- Location variations
- Category variations
- Exclusion terms
- Language variations

---

# 10. Source Discovery Engine

The source discovery engine identifies useful sources before scraping begins.

```text
TARGET DEFINITION
        ↓
Query Generator
        ↓
Search / Discovery
        ↓
Candidate Sources
        ↓
Source Classification
        ↓
Source Scoring
        ↓
Selected Sources
```

A source score may consider:

```text
Relevance        30%
Data Coverage    25%
Data Quality     20%
Accessibility    15%
Yield Estimate   10%
```

---

# 11. Scraping Strategy

The scraper should select a strategy based on the structure of the source.

Supported strategies:

```text
Direct Page Extraction
Search → Listing → Detail
Category Traversal
Pagination
Infinite Scroll
Sitemap Discovery
API Extraction
Linked Page Traversal
Structured Data Extraction
```

Typical flow:

```text
Search Page
    ↓
Listing Page
    ↓
Detail Page
    ↓
Extract Record
```

---

# 12. URL Discovery

URL discovery identifies pages likely to contain matching records.

```text
Source
  ↓
Search / Navigation
  ↓
Candidate URLs
  ↓
URL Classification
  ↓
Relevant URLs
```

The system should classify URLs into:

```text
Listing Page
Detail Page
Category Page
Search Page
Product Page
Company Page
Contact Page
Other
```

URLs should also be normalized before crawling.

---

# 13. Crawl Scheduler

The crawl scheduler controls which URLs should be processed and when.

Responsibilities:

```text
URL Queue
Priority
Concurrency
Retry Logic
Rate Limiting
Timeouts
Depth Limits
Domain Limits
Duplicate URL Detection
Failure Handling
```

Example queue priorities:

```text
High:
  Detail pages likely to contain target records

Medium:
  Listing/category pages

Low:
  Navigation pages
```

---

# 14. Fetching Layer

The fetching layer retrieves pages and other source content.

Possible methods:

```text
HTTP Requests
Headless Browser
API Client
Sitemap Reader
Feed Reader
```

The system should support pages that require JavaScript rendering.

Suggested selection:

```text
Simple/static page
    → HTTP fetch

Dynamic page
    → Browser rendering

API source
    → API client
```

The implementation should respect applicable source terms, robots directives, access controls, and rate limits.

---

# 15. Extraction Engine

The extraction engine converts source content into structured data.

## 15.1 Extraction Methods

Preferred hierarchy:

```text
API
  ↓
Structured Data / JSON-LD
  ↓
Known Selectors
  ↓
Metadata
  ↓
Page Text
  ↓
Semantic / AI Extraction
```

Supported mechanisms:

```text
CSS Selectors
XPath
JSON-LD
OpenGraph
HTML Metadata
Tables
Embedded JSON
API Responses
Semantic Extraction
```

AI/semantic extraction should be treated as a fallback or targeted extraction method rather than the default for every page.

---

# 16. Field Extraction

Each field should have its own extraction process.

Example:

```text
company_name
    ├── JSON-LD
    ├── H1
    ├── title
    └── fallback semantic extraction

website
    ├── canonical URL
    └── homepage link

email
    ├── mailto:
    ├── visible text
    └── structured data

phone
    ├── tel:
    ├── structured data
    └── visible text
```

The engine should store provenance for extracted values where practical.

Example:

```json
{
  "field": "email",
  "value": "hello@example.com",
  "source": "https://example.com/contact",
  "method": "mailto",
  "confidence": 0.98
}
```

---

# 17. Data Normalization

Scraped values will often have inconsistent formats.

The normalization layer standardizes them.

## 17.1 Phone Numbers

Input:

```text
+27 21 123 4567
021 123 4567
+27211234567
```

Normalized:

```text
+27211234567
```

## 17.2 Locations

Input:

```text
Cape Town
Cape Town, WC
Cape Town, Western Cape
```

Normalized:

```text
City: Cape Town
Province: Western Cape
Country: South Africa
```

## 17.3 URLs

Normalize:

```text
HTTP → HTTPS where appropriate
Remove unnecessary tracking parameters
Normalize trailing slashes
Normalize host casing
```

## 17.4 Names

Normalize:

```text
Whitespace
Case
Unicode
Punctuation
Legal suffixes where appropriate
```

---

# 18. Validation

Each record should be validated before it enters the final result set.

Validation categories:

```text
Schema Validation
Field Validation
Format Validation
Target Validation
Source Validation
Completeness Validation
```

Examples:

```text
Company Name
✓ Present

Website
✓ Valid URL

Email
✓ Valid email structure

Phone
✓ Valid phone structure

Location
✓ Matches target

Industry
✓ Matches requested industry
```

---

# 19. Record Quality Score

Each record should receive a quality score.

Example:

```text
Completeness        30%
Field Validity      25%
Target Match        20%
Source Reliability  15%
Uniqueness          10%
```

Example result:

```text
Quality Score: 92 / 100
```

The score can be used to:

- Filter low-quality records
- Sort results
- Determine whether additional scraping is required
- Report job quality

---

# 20. Deduplication

Duplicates should be removed before output.

Possible matching keys:

```text
Website Domain
Canonical URL
Email
Phone
Business Registration ID
Source-specific ID
Entity Name + Address
Entity Name + Website
```

A matching strategy should support exact and fuzzy matching.

Example:

```text
ABC Software
ABC Software Ltd
ABC Software Cape Town

→ One canonical entity
```

The system should preserve source provenance.

```json
{
  "company_name": "ABC Software",
  "website": "https://abcsoftware.com",
  "sources": [
    "https://directory.example/abc",
    "https://abcsoftware.com"
  ]
}
```

---

# 21. Record Count and Range

The user should be able to request an exact number or a range.

Examples:

```text
10
100
500
1,000
```

or:

```text
100–500
500–1,000
1,000–5,000
```

A robust configuration should store:

```text
Minimum Records
Maximum Records
Stop-at-Minimum
```

Example:

```json
{
  "records": {
    "minimum": 500,
    "maximum": 1000,
    "stop_at_minimum": true
  }
}
```

The scraper should normally continue until the minimum is reached.

If:

```text
minimum = 500
maximum = 1000
stop_at_minimum = true
```

the system can stop after obtaining 500 valid unique records.

If:

```text
stop_at_minimum = false
```

the system can continue toward the maximum.

The system should account for invalid records and duplicates when determining whether the requested count has been satisfied.

---

# 22. Output Formats

Supported outputs should include:

```text
CSV
XLSX
JSON
JSONL / NDJSON
XML
SQL
Parquet
```

Optional destinations:

```text
Database
API Endpoint
Cloud Storage
Local File
```

---

# 23. Output Configuration

```text
Output
├── Format
├── Filename
├── Encoding
├── Include Source URLs
├── Include Extraction Timestamp
├── Include Quality Score
├── Include Provenance
└── Include Raw Data
```

Example:

```json
{
  "output": {
    "format": "xlsx",
    "filename": "cape-town-software-companies",
    "include_source": true,
    "include_timestamp": true,
    "include_quality_score": true,
    "include_provenance": true,
    "include_raw_data": false
  }
}
```

---

# 24. Complete Input Schema

A complete job can be represented as:

```json
{
  "entity": "business",
  "targeting": {
    "required": {
      "location": {
        "country": "South Africa",
        "province": "Western Cape",
        "city": "Cape Town"
      },
      "industry": [
        "Software",
        "Technology"
      ]
    },
    "preferred": {
      "keywords": [
        "SaaS",
        "cloud"
      ]
    },
    "excluded": {
      "business_types": [
        "Government"
      ]
    }
  },
  "fields": [
    {
      "name": "company_name",
      "type": "text",
      "required": true
    },
    {
      "name": "website",
      "type": "url",
      "required": true
    },
    {
      "name": "email",
      "type": "email",
      "required": false
    },
    {
      "name": "phone",
      "type": "phone",
      "required": false
    },
    {
      "name": "address",
      "type": "address",
      "required": false
    },
    {
      "name": "industry",
      "type": "text",
      "required": false
    },
    {
      "name": "description",
      "type": "text",
      "required": false
    }
  ],
  "sources": {
    "mode": "auto",
    "urls": []
  },
  "records": {
    "minimum": 500,
    "maximum": 1000,
    "stop_at_minimum": true
  },
  "output": {
    "format": "xlsx",
    "filename": "cape-town-software-companies",
    "include_source": true,
    "include_timestamp": true,
    "include_quality_score": true,
    "include_provenance": true
  }
}
```

---

# 25. Program Architecture

Recommended modules:

```text
web-scraper/
│
├── job/
│   ├── input-parser
│   ├── job-validator
│   ├── job-manager
│   └── job-state
│
├── target/
│   ├── target-parser
│   ├── query-generator
│   └── target-matcher
│
├── sources/
│   ├── source-discovery
│   ├── source-manager
│   ├── source-ranking
│   └── source-profiles
│
├── discovery/
│   ├── search
│   ├── sitemap
│   ├── category
│   └── url-classifier
│
├── crawling/
│   ├── scheduler
│   ├── queue
│   ├── fetcher
│   ├── browser
│   ├── rate-limiter
│   └── retry-manager
│
├── extraction/
│   ├── structured-data
│   ├── selectors
│   ├── metadata
│   ├── semantic
│   └── field-extractor
│
├── processing/
│   ├── normalization
│   ├── validation
│   ├── deduplication
│   └── quality-scoring
│
├── storage/
│   ├── raw-pages
│   ├── records
│   └── job-results
│
├── output/
│   ├── csv
│   ├── xlsx
│   ├── json
│   ├── jsonl
│   ├── sql
│   └── parquet
│
└── monitoring/
    ├── logging
    ├── metrics
    ├── audit
    └── job-monitor
```

---

# 26. Job Lifecycle

A scraping job should use explicit states.

```text
CREATED
  ↓
CONFIGURING
  ↓
VALIDATING
  ↓
DISCOVERING_SOURCES
  ↓
DISCOVERING_URLS
  ↓
SCRAPING
  ↓
EXTRACTING
  ↓
NORMALIZING
  ↓
VALIDATING_RECORDS
  ↓
DEDUPLICATING
  ↓
CHECKING_TARGET_COUNT
  ↓
EXPORTING
  ↓
COMPLETED
```

Failure and control states:

```text
PAUSED
FAILED
CANCELLED
RATE_LIMITED
SOURCE_BLOCKED
PARTIALLY_COMPLETED
```

---

# 27. Scraping Dashboard

A running job should expose real-time progress.

Example:

```text
Cape Town Software Companies

Status: Scraping

Records
742 / 1,000

Valid
708

Duplicates
31

Rejected
3

Sources
14

Pages Crawled
2,481

Success Rate
94.8%

Average Quality
91%

Current Source
example-directory.com
```

## Pipeline Metrics

```text
Sources discovered        14
URLs discovered         3,102
Pages scraped           2,481
Records extracted       1,034
Records validated         821
Duplicates removed         79
Final unique records      742
```

---

# 28. User Interface

The interface should use a multi-step scraping-job wizard.

## Step 1 — Target

```text
Entity Type
[ Business ▼ ]

Describe Target
[ Software companies in Cape Town ]
```

## Step 2 — Targeting

```text
Location
[ Country ]
[ Province ]
[ City ]

Industry
[ Software ]

Keywords
[ SaaS ] [ Cloud ] [+]

Additional Filters
[ Company Size ]
[ Rating ]
[ Founded Year ]
```

## Step 3 — Fields

```text
Fields to Extract

☑ Company Name
☑ Website
☑ Email
☑ Phone
☑ Address
☑ Industry
☑ Description
☐ Contact Person
☐ Social Media
☐ LinkedIn URL

[ + Add Custom Field ]
```

## Step 4 — Sources

```text
○ Automatically find sources
○ Choose sources myself

Selected Sources:
[ + Add Source ]
```

If automatic discovery is selected:

```text
Source Discovery
✓ Search Engines
✓ Business Directories
✓ Company Websites
✓ Industry Directories
```

## Step 5 — Record Count

```text
Minimum Records
[ 500 ]

Maximum Records
[ 1,000 ]

Stop at minimum
[✓]
```

## Step 6 — Output

```text
Output Format
[ XLSX ▼ ]

☑ Source URL
☑ Extraction Timestamp
☑ Quality Score
☑ Provenance

Filename
[ cape-town-software-companies ]
```

## Step 7 — Review

```text
TARGET
Software companies
Cape Town, South Africa

FIELDS
7 fields

SOURCES
Automatic discovery

RECORDS
500–1,000

OUTPUT
XLSX

[ Start Scraping ]
```

---

# 29. Results View

After completion, users should be able to inspect the extracted dataset.

Recommended areas:

```text
Results Table
Filters
Search
Sort
Column Management
Record Details
Source Preview
Quality Score
Extraction Provenance
Export
```

Example table:

| Company | Website | Email | Phone | Location | Industry | Quality |
|---|---|---|---|---|---|---:|
| ABC Software | abc.co.za | info@abc.co.za | +27... | Cape Town | Software | 96 |
| XYZ Cloud | xyz.co.za | hello@xyz.co.za | +27... | Cape Town | SaaS | 92 |

---

# 30. Record Detail

Selecting a record should expose:

```text
Canonical Record
├── Entity Information
├── Extracted Fields
├── Validation Results
├── Quality Score
├── Sources
├── Provenance
└── Raw Extraction Data
```

Example:

```text
Company Name
ABC Software

Website
https://abcsoftware.com

Email
hello@abcsoftware.com

Phone
+27211234567

Quality
96 / 100

Sources
- Directory A
- Company Website

Extraction:
Email → mailto link
Phone → JSON-LD
Company Name → JSON-LD
```

---

# 31. Storage Model

The system should separate raw source data from normalized records.

## Raw Source

```text
raw_pages
├── job_id
├── source_id
├── url
├── fetched_at
├── status_code
├── content_type
├── raw_content
└── metadata
```

## Extracted Record

```text
records
├── job_id
├── record_id
├── entity_type
├── normalized_data
├── quality_score
├── created_at
└── updated_at
```

## Provenance

```text
record_provenance
├── record_id
├── field_name
├── source_url
├── extraction_method
├── confidence
└── extracted_at
```

---

# 32. Important Product Design Principle

The platform should be **schema-first and target-first**, not website-first.

Avoid designing the application around:

```text
"Scrape Website X"
```

Instead, design around:

```text
"What records do I need?"

"How do I define a matching record?"

"What fields do I need?"

"How many records do I need?"

"Where should the system look?"
```

The platform then determines:

```text
WHERE to look
HOW to search
HOW to crawl
WHAT extraction strategy to use
HOW to validate data
HOW to normalize data
HOW to remove duplicates
WHEN to stop
HOW to export the results
```

This allows one scraping platform to support many use cases without creating a custom scraper for every website.

---

# 33. Recommended High-Level Architecture

```text
┌──────────────────────────────────────────────────────┐
│                 SCRAPING PLATFORM                    │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────────┐      ┌─────────────────────────┐  │
│  │ Job Builder  │─────▶│ Target / Schema Engine  │  │
│  └──────────────┘      └────────────┬────────────┘  │
│                                     │               │
│                                     ▼               │
│                         ┌─────────────────────────┐ │
│                         │ Source Discovery        │ │
│                         └────────────┬────────────┘ │
│                                      │              │
│                                      ▼              │
│                         ┌─────────────────────────┐ │
│                         │ Crawl Scheduler         │ │
│                         └────────────┬────────────┘ │
│                                      │              │
│                                      ▼              │
│                         ┌─────────────────────────┐ │
│                         │ Fetch / Browser Layer   │ │
│                         └────────────┬────────────┘ │
│                                      │              │
│                                      ▼              │
│                         ┌─────────────────────────┐ │
│                         │ Extraction Engine       │ │
│                         └────────────┬────────────┘ │
│                                      │              │
│                                      ▼              │
│                         ┌─────────────────────────┐ │
│                         │ Normalization           │ │
│                         └────────────┬────────────┘ │
│                                      │              │
│                                      ▼              │
│                         ┌─────────────────────────┐ │
│                         │ Validation              │ │
│                         └────────────┬────────────┘ │
│                                      │              │
│                                      ▼              │
│                         ┌─────────────────────────┐ │
│                         │ Deduplication           │ │
│                         └────────────┬────────────┘ │
│                                      │              │
│                                      ▼              │
│                         ┌─────────────────────────┐ │
│                         │ Record Store            │ │
│                         └────────────┬────────────┘ │
│                                      │              │
│                                      ▼              │
│                         ┌─────────────────────────┐ │
│                         │ Output / Export         │ │
│                         └─────────────────────────┘ │
│                                                      │
├──────────────────────────────────────────────────────┤
│ Monitoring │ Logging │ Metrics │ Audit │ Job Control │
└──────────────────────────────────────────────────────┘
```

---

# 34. Example End-to-End Job

User request:

```text
Find 500–1,000 software companies in Cape Town.

Required fields:
- Company name
- Website
- Email
- Phone
- Address

No sources provided.

Output:
XLSX
```

The system should execute approximately:

```text
1. Parse target
        ↓
2. Determine entity = business/company
        ↓
3. Parse location = Cape Town, South Africa
        ↓
4. Parse industry = software
        ↓
5. Build requested extraction schema
        ↓
6. Discover relevant sources
        ↓
7. Rank sources
        ↓
8. Generate search queries
        ↓
9. Discover listing/detail URLs
        ↓
10. Crawl pages
        ↓
11. Extract requested fields
        ↓
12. Normalize data
        ↓
13. Validate records
        ↓
14. Score records
        ↓
15. Deduplicate
        ↓
16. Check valid unique count
        ↓
17. Continue scraping if below minimum
        ↓
18. Export final dataset
```

---

# 35. Minimum Viable Product

The MVP should prioritize a reliable end-to-end pipeline.

## MVP Features

```text
✓ Create scraping job
✓ Entity type
✓ Location targeting
✓ Industry targeting
✓ Keyword targeting
✓ Custom extraction fields
✓ Manual source URLs
✓ Automatic source discovery
✓ HTTP scraping
✓ Basic browser rendering
✓ Structured data extraction
✓ CSS/XPath extraction
✓ Field normalization
✓ Validation
✓ Deduplication
✓ Record count target
✓ CSV output
✓ JSON output
✓ XLSX output
✓ Job progress
✓ Error logging
```

## Later Features

```text
API integrations
Advanced source ranking
AI-assisted extraction
Advanced fuzzy deduplication
Distributed crawling
Proxy management
Browser fingerprint management
Scheduled scraping
Change detection
Database destinations
Webhooks
REST API
Team workspaces
Usage limits
Dataset versioning
Historical snapshots
```

---

# 36. Final Product Model

At the product level, the scraper should be thought of as:

```text
USER DEFINES
│
├── WHAT
│   └── Entity
│
├── WHO / WHERE
│   └── Targeting
│
├── WHAT DATA
│   └── Fields
│
├── WHERE FROM
│   └── Sources (optional)
│
├── HOW MANY
│   └── Record Range
│
└── IN WHAT FORM
    └── Output
```

The platform then handles:

```text
DISCOVERY
→ CRAWLING
→ EXTRACTION
→ NORMALIZATION
→ VALIDATION
→ DEDUPLICATION
→ QUALITY SCORING
→ OUTPUT
```

This creates a general-purpose web data collection platform where the user's input defines the desired dataset and the system dynamically determines the best available collection path.
