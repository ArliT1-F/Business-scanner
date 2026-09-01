# Tirana Business Website Finder

A production-quality Python tool for **local-business prospecting**: it
discovers businesses in Tirana, Albania that have **no website listed on
their Google Maps / Google Business profile**, and exports a clean,
deduplicated, scored prospect list (CSV / optional XLSX).

It uses the **official Google Places API (New)** (Nearby Search endpoint)
with HTTP field masks, a geographic grid, and a SQLite database.
**No browser automation, no scraping of google.com** — the scanner is built
around the API's documented capabilities and limitations.

> **Coverage warning (read this).** The results are *businesses discovered
> through the configured Google Places searches and geographic/category
> coverage* — **not** "every business in Tirana". Google's search ranking,
> per-search result limits, the chosen categories, the bounding box, and
> general API behaviour mean that complete enumeration cannot be
> guaranteed. The geographic grid exists to *improve* discovery; it does
> not (and must not) bypass Google's result limits.
>
> Likewise, `has_google_maps_website = false` means
> **"no website is listed on Google Maps"**. It is *not* proof that the
> business has no website anywhere on the internet.

---

## 1. What it does

1. Divides an approximate Tirana bounding box into a geographic grid
   (default: 1.25 km cells, computed in ground kilometres — not raw
   degrees).
2. For every grid cell, searches a configurable list of business
   categories (36 by default) with the Places API (New) **Nearby Search**
   endpoint, requesting only the needed fields via a field mask.
3. Follows `nextPageToken` pagination, capped at Google's documented
   maximum (3 pages × 20 = 60 results per search).
4. Normalizes each result (missing optional fields become safe defaults)
   and stores it in SQLite, **deduplicating by Google Place ID**
   (`place_id` is the PRIMARY KEY; same ID seen again → row updated).
5. Classifies each business by whether `places.websiteUri` is present:
   `HAS_GOOGLE_MAPS_WEBSITE` / `NO_GOOGLE_MAPS_WEBSITE`.
6. Computes a transparent **prospect score** (established + no website =
   high score).
7. Exports sorted CSV (`prospect_score DESC, review_count DESC`) and, if
   `openpyxl` is installed, XLSX with a `Statistics` sheet.
8. Supports a **dry run** that prints the planned scan size with **zero
   API requests**, so you can estimate cost before spending any.

## 2. Architecture

```
Business-scanner/
├── main.py            # CLI (scan / export / stats commands, dry run)
├── config.py          # All defaults: bounds, grid, categories, delays, paths
├── scanner.py         # Geographic grid + scan orchestration
├── places.py          # Places API (New) client: field masks, retries,
│                      #   pagination, error mapping, response normalization
├── database.py        # SQLite schema, upsert/dedup, stats queries
├── scoring.py         # Prospect scoring (all policy in one readable place)
├── exporters.py       # CSV export (always) + optional XLSX export
├── utils.py           # Console formatting / time helpers
├── requirements.txt
├── .env.example       # GOOGLE_MAPS_API_KEY=
├── .gitignore         # .env, data/*, output/*, caches
├── data/              # SQLite database lives here (git-ignored)
├── output/            # CSV/XLSX exports live here (git-ignored)
├── examples/          # Example CSV output (committed)
└── tests/             # pytest suite (grid, normalization, scoring, database)
```

Responsibilities are strictly separated:

| Layer        | Module        | Owns                                            |
|--------------|---------------|-------------------------------------------------|
| API access   | `places.py`   | HTTP, auth header, field mask, retries, errors  |
| Scanning     | `scanner.py`  | grid generation, job loop, progress output      |
| Storage      | `database.py` | schema, upsert dedup, stats                     |
| Scoring      | `scoring.py`  | the lead score (pure function)                  |
| Export       | `exporters.py`| CSV/XLSX writing                                |
| CLI/config   | `main.py`, `config.py` | argument parsing, defaults            |

This separation is deliberate so phase-2+ features (website discovery
outside Google Maps, CRM status, rescans/change detection, dashboards) can
be added as new modules without rewriting the scanner.

## 3. Requirements

* Python **3.10+** (tested on 3.11)
* Linux (any distro; no Docker needed)
* A Google Cloud project with **Places API (New)** enabled and a valid API key
* Internet access (API calls)

Python packages (see `requirements.txt`):

* `requests`
* `python-dotenv`
* `openpyxl` — **optional**, only for XLSX export

## 4. Google Cloud setup (step by step)

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and
   create (or select) a project.
2. **Enable the API**: open
   [Places API (New)](https://console.cloud.google.com/apis/library/places-backend.googleapis.com)
   (API name: *Places API (New)*, service `places-backend.googleapis.com`)
   and click **Enable** for your project.
3. **Create an API key**: *APIs & Services → Credentials → Create
   credentials → API key*.
4. (Recommended) Restrict the key:
   * **Application restrictions** — leave unrestricted for a CLI tool, or
     restrict as fits your setup.
   * **API restrictions** — restrict to *Places API (New)* only.
5. Billing: Google requires a billing account for Maps Platform; the free
   monthly credits often cover a full scan of this project. See
   [cost considerations](#13-cost-considerations).

## 5. Installation (Linux)

```bash
# 1. Get the code
git clone <your-repo-url> Business-scanner
cd Business-scanner

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# Optional: XLSX export support
pip install openpyxl

# 4. Optional: run the test suite
pip install pytest
pytest
```

## 6. `.env` setup

```bash
cp .env.example .env
```

Edit `.env` and paste your key:

```env
GOOGLE_MAPS_API_KEY=AIza...your-key...
```

The key is loaded via `python-dotenv` and is **never printed** to the
terminal or logs. `.env` is git-ignored.

## 7. Dry run (zero API calls)

```bash
python main.py --dry-run
```

Example output:

```
Target: Tirana, Albania
Grid cells:       96
Categories:       36
Search jobs:      3456
Radius per search: 900m
Results per job:  up to 3 x 20 (Google's documented maximum)
Estimated requests: 3456 - 10368 (each job may use up to 3 pages)

No API requests will be made.
```

Use this to sanity-check the scan size (and therefore the cost) before
spending money on API calls.

## 8. Running a scan

**Small test scan first** (strongly recommended — a handful of API calls):

```bash
python main.py --categories cafe --grid-km 5
# or limit the number of search jobs:
python main.py --limit 3
```

**Full scan** (default config: ~1.25 km grid, 36 categories, 900 m radius):

```bash
python main.py
```

Useful scan flags:

```bash
python main.py --categories cafe,restaurant,bar,barber_shop
python main.py --grid-km 1.0 --radius-m 1000
python main.py --no-website-only            # final export = website-less only
python main.py --delay 2                     # be gentler on QPS limits
python main.py --quiet                       # less per-search output
python main.py --db data/other.db --csv output/other.csv
```

A scan is **resumable**: re-running it only *updates* already-known Place
IDs, it never duplicates them. If it stops early (quota, auth error,
Ctrl-C), stored results are kept and a re-run continues from there.

### Progress output

Every search job prints a block like:

```
[12/96] Cell 12
[4/36] Category: cafe
Searching:
    latitude: 41.32010
    longitude: 19.81220
    radius: 900m
Results:
    returned: 18
    new: 11
    existing: 7
Database:
    total: 1,284
    without website: 417
```

and the run ends with:

```
========================================
SCAN COMPLETE
========================================
Unique businesses:   1,284
Without website:     417
With website:        867
CSV:
output/tirana_businesses.csv
```

## 9. Custom categories

Categories are Google Places API (New) **feature types**. Pass your own
comma-separated list:

```bash
python main.py --categories cafe,restaurant,bar
```

Browse the supported types in Google's
[feature types reference](https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/SupportedAttributes).
If a type name is not valid, Google returns a 400 error for that category
(the scanner reports it and stops after 3 identical consecutive failures).

## 10. Export format

Default: `output/tirana_businesses.csv`, sorted by
`prospect_score DESC, review_count DESC`, columns:

| Column | Meaning |
|---|---|
| `place_id` | Google Place ID (unique key) |
| `name` | Business name |
| `address` | Formatted address |
| `phone` | National phone number ("" if none) |
| `rating` | Google rating (empty if unrated) |
| `review_count` | Number of Google reviews |
| `website` | `websiteUri` value — **empty = no website listed on Google Maps** |
| `maps_uri` | Google Maps link |
| `latitude` / `longitude` | Coordinates |
| `primary_type` | Main category |
| `types` | All types, comma-separated |
| `has_google_maps_website` | `true` / `false` |
| `prospect_score` | Lead score 0–100 (see below) |

`--no-website-only` exports only rows with `has_google_maps_website=false`.
A ready-made sample is in
[`examples/tirana_businesses_example.csv`](examples/tirana_businesses_example.csv).

Re-export at any time (no API calls needed):

```bash
python main.py export
python main.py export --no-website-only
python main.py export --csv output/websitelisted.csv   # any path
```

### Excel

With `pip install openpyxl`, the same data is written to
`output/tirana_businesses.xlsx` with a `Businesses` sheet and a
`Statistics` sheet (totals, average rating, top categories).

### Prospect score (0–100)

Defined in `scoring.py` — one readable place, easy to modify:

| Signal | Points |
|---|---|
| Reviews 300+ / 100–299 / 50–99 / 20–49 | +40 / +30 / +20 / +10 |
| Rating 4.7+ / 4.5+ / 4.2+ | +25 / +20 / +10 |
| Phone number available | +5 |
| **No website on Google Maps** | +30 |

A 4.8-star café with 400 reviews and a phone number but no listed website
scores 100 — a classic prospect for web-development services.

## 11. Database & statistics

Results live in `data/tirana_businesses.db` (SQLite). Schema (with indexes
on `has_google_maps_website`, `prospect_score`, `review_count`):

```sql
businesses (
    place_id TEXT PRIMARY KEY, name, address, phone, rating,
    review_count, website, maps_uri, latitude, longitude,
    primary_type, types, has_google_maps_website, prospect_score,
    discovered_at
)
```

Show what's inside without any API calls:

```bash
python main.py stats
```

## 12. Dry run / test / full scan / website-less export — exact commands

```bash
# Dry run (no API calls)
python main.py --dry-run

# Small test (cheap: 1 category, coarse grid)
python main.py --categories cafe --grid-km 5

# Full scan
python main.py

# Export only businesses without a website on Google Maps
python main.py export --no-website-only
# (or bake it into a scan:  python main.py --no-website-only)
```

## 13. Cost considerations

* Each (cell × category) job costs **at least 1 API request**; jobs with
  many results use up to 3 requests (pagination). The default configuration
  is ~3,450 jobs → roughly 3,500–10,000 requests for a full scan.
* Google charges Places API (New) calls by **field mask** (Essentials/Pro/
  Enterprise SKUs). This project requests a small, fixed set of fields —
  keep the field mask in `places.FIELD_MASK` small; do **not** switch it
  to wildcards.
* **Always run `python main.py --dry-run` and a small test scan first**
  and check the Cloud Console *APIs & Services → Usage / BigQuery
  (Maps Platform)* reports as you go.
* Set request restrictions on your API key and, if needed, daily cost
  alerts in Cloud Billing.
* Repeated scans are cheaper in terms of *discovery* but still pay for the
  requests; the database deduplicates the results.

## 14. Coverage limitations

Be precise about what this tool produces:

* **Grid coverage** — cells tile the bounding box with ~900 m radius
  searches, so most of the city area is searched, but the bounding box
  itself is approximate; businesses outside it are not found.
* **Google's result cap** — each search returns at most 60 results. Dense
  areas or popular categories can contain more businesses than that;
  ranking also influences *which* 60 you get.
* **Category coverage** — only businesses matching a searched feature type
  appear. The default list covers common prospecting categories; extend
  it with `--categories`.
* **Data freshness** — listings, ratings and websites change; a rescan is
  a snapshot in time.
* **`NO_GOOGLE_MAPS_WEBSITE` ≠ "no website anywhere"** — see the warning at
  the top. An optional second-stage web search (phase 2) could refine this
  into `WEBSITE_FOUND_ELSEWHERE` / `NO_WEBSITE_FOUND` / `UNCERTAIN`.

## 15. Google data & usage restrictions

This project respects Google's Places API terms:

* Uses the **official API only** — no scraping, no browser automation.
* Requests a **minimal field mask**, no `*`.
* Respects **rate limits** (default 0.5 s delay between requests, QPS-aware
  retries with backoff, configurable `--delay` / `--max-retries`).
* Does **not bypass** the documented per-search result maximum.
* Store only the fields you need (this app stores a small set), and be
  aware that Google's terms restrict *redistributing* Places data and
  prohibit using it for purposes like a public business directory; using
  the dataset for **private sales prospecting** is the intended use.
* The API key is never committed, printed, or logged.

## 16. Troubleshooting

| Symptom | Fix |
|---|---|
| `ERROR: GOOGLE_MAPS_API_KEY is missing.` | Create `.env` (copy `.env.example`) and set the key. Make sure you run from the project root. |
| `ERROR: Google rejected the API key.` | Check the key exists, isn't revoked, and its restrictions allow CLI use. |
| `Check that Places API (New) is enabled...` | Enable **Places API (New)** (`places-backend.googleapis.com`) in the project the key belongs to. |
| `ERROR: Google API quota appears to have been exceeded.` | Wait for the quota window / check the project's quota in Cloud Console. Re-run — the scan resumes without duplicating data. |
| `QPS / rate limit exceeded` | Increase the delay: `python main.py --delay 2`. |
| 400 `INVALID_ARGUMENT` naming a category | The feature type name isn't valid for Nearby Search — check the type spelling against Google's type list and remove it from `--categories`. |
| Very few results | Try a coarser grid (`--grid-km 2`), a bigger radius (`--radius-m 1500`), or more categories. Remember the 60-results-per-search cap. |
| `XLSX export skipped: openpyxl is not installed` | `pip install openpyxl` (CSV is unaffected). |
| Tests | `pytest` (run from the project root, inside the venv). |

## 17. Roadmap (not implemented in the MVP)

The architecture keeps these out of the scanner so they can be added as
modules later:

* **Phase 2** — website discovery outside Google Maps (multi-signal
  matching: name + phone + address + domain + socials), social-media
  detection, better scoring, category/neighborhood filters, TUI.
* **Phase 3** — web dashboard, map visualization, search/filter, prospect
  status (New → Contacted → Interested → Rejected → Client).
* **Phase 4** — CRM integration, contact workflow, periodic rescans with
  change detection (new business / website added / closed / rating drift).

## 18. Development notes

The code was developed incrementally: config → Places client → one test
search → normalization → SQLite → dedup → grid → categories → scoring →
CSV → CLI → tests. During development, never launch the full scan; use
small modes:

```bash
python main.py --dry-run
python main.py --categories cafe --grid-km 5
python main.py --limit 2
pytest
```