# Dental Practice Lead Generator

Finds dental practices in **McKinney, Allen, Plano, Frisco and Wylie, TX** and ranks them by fit with your ideal
customer profile (ICP). For each practice you get the head dentist's name, email, website and phone number, a 0–100
fit score, and a one-line "why this lead" explanation.

It uses official APIs (Google Places API (New), RDAP and, if you add a key, Hunter.io) and the practices' own
websites. It does **not** scrape Google Maps pages.

---

## 1. Setup

**Easiest:** install Python, then double-click `Start Dental Leads (Windows).bat` or
`Start Dental Leads (Mac).command`. The launcher sets everything up on first run and opens the website.
`START HERE.txt` walks through it step by step. The manual steps are below.

You need Python 3.10 or newer.

```bash
cd dental_leads
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then put your key(s) in .env
```

`.env` holds your keys and is git-ignored. Never put keys in the code or in `config.yaml`.

```
GOOGLE_PLACES_API_KEY=AIza...
HUNTER_API_KEY=            # optional
```

Also open `config.yaml` and set `crawl.user_agent` to include your own contact email, so site owners can reach you.

### Getting a Google Places API key

1. Go to <https://console.cloud.google.com/> and create a project (for example "dental-leads").
2. Turn on billing for the project (**Billing** in the left menu). Google requires a billing account even when your
   usage stays inside the monthly free allowance.
3. Open **APIs & Services → Library**, search for **Places API (New)** and click **Enable**. Enable the one marked
   "(New)", not the legacy "Places API".
4. Open **APIs & Services → Credentials → Create credentials → API key**.
5. Click the new key, then under **API restrictions** choose **Restrict key** and select **Places API (New)**. Save.
6. Recommended: in **Billing → Budgets & alerts**, set a small monthly budget (e.g. $25) with email alerts.
   Under **APIs & Services → Places API (New) → Quotas** you can also cap requests per day.
7. Paste the key into `.env` as `GOOGLE_PLACES_API_KEY=...`.

**Cost:** each Text Search request returns up to 20 practices. Requesting reviews (needed for review dates) bills the
"Text Search Enterprise + Atmosphere" SKU, about $40 per 1,000 requests before Google's monthly free usage. Check
current prices at <https://developers.google.com/maps/billing-and-pricing/pricing>. Before any billable request the
tool prints an estimate and asks you to confirm. Set `places.include_reviews: false` in `config.yaml` for a cheaper
SKU; you then lose the review-date age signal.

---

## 2. Using the website (easiest)

```bash
python -m leadgen.web
```

This starts the app on your computer and opens <http://127.0.0.1:5000> in your browser. It has three pages:

- **Settings:** paste your Google Places key (and optional Hunter key), then adjust your ideal customer profile,
  scoring weights, chain list, search queries and email options. Keys go into `.env`; other changes go into
  `settings.local.yaml`, which overrides `config.yaml`. **Reset to defaults** deletes the overrides.
- **Search:** pick cities, optionally set "stop after N practices", and click **Check cost**. You see the maximum
  number of billable Google requests and dollars before you click **Start search**. Progress and the live log appear
  below, and **Stop** keeps whatever practices are already finished.
- **Leads:** ranked results for any past search, with search, city, minimum-score and email filters, sortable columns,
  click-to-expand details, and **Download CSV / Excel** buttons. Email badges show *verified on site*,
  *found by Hunter* or *pattern guess*.

The site only listens on your own computer by default. To open it from your phone on the same Wi-Fi, first set a
password, which protects your keys and your Google credit:

```bash
echo "LEADGEN_WEB_PASSWORD=pick-a-long-password" >> .env
python -m leadgen.web --host 0.0.0.0
```

Then browse to `http://<your-computer's-IP>:5000` and log in with any username and that password. Don't expose the
app to the open internet.

The command line below does the same things and shares the same settings, database and cache.

## 3. Running from the command line

**Start with a small test run:**

```bash
python -m leadgen --city Plano --limit 10
```

**See the API call estimate only (no requests, no key needed):**

```bash
python -m leadgen --dry-run
```

**Full run (all five cities):**

```bash
python -m leadgen
```

| Flag | What it does |
|---|---|
| `--city Plano` | Only search this city. Repeat the flag or comma-separate for several (`--city Plano,Frisco`). |
| `--limit 10` | Stop once this many practices are found. For cheap test runs. |
| `--dry-run` | Print the estimate and exit. |
| `--yes` / `-y` | Skip the confirmation prompt (for scheduled runs). |
| `--refresh` | Ignore cached API responses and fetch fresh data. |
| `--verbose` / `-v` | Debug logging. |
| `--config path` | Use a different config file. |

Output goes to `output/`:

- `leads.csv`: ranked leads, excluding chains and closed practices.
- `leads.xlsx`: the same data on a **Leads** sheet, plus an **Excluded** sheet showing why each practice was
  dropped. It has clickable links, filters and frozen headers.
- `run.log`: full log of the run.

---

## 4. What's in the output

| Column | Meaning |
|---|---|
| Score | 0–100 ICP fit (see Scoring). |
| Practice Name, City, Phone, Website, Rating, Review Count, Google Maps Link | From Google Places. |
| Head Dentist | Best guess from the practice website: "Dr." / DDS / DMD mentions, weighted toward founder/owner wording, doctor pages and headings. Falls back to the Google listing name (e.g. "Dr. Raj Patel Dentistry"). |
| Email | Best email for the practice. Emails on the practice's own domain, and ones containing the dentist's name, rank first. |
| Email Source | `website`, `Hunter` or `guessed`. |
| Email Status | `verified on site (<page>)`: published on the practice's own website.<br>`found by Hunter on N web page(s)`: Hunter saw it published somewhere.<br>`pattern guess, unverified (...)`: built from a naming pattern. **Not confirmed**; verify before sending. Shown in red in the xlsx. |
| Est. Practice Age | Combined estimate (see below). The "Age Basis" column shows which signals were used. |
| Review Velocity | New reviews per month. After a second run at least 7 days later it is **tracked** from real snapshots. Before that it shows the lifetime average (reviews ÷ estimated age). |
| Why This Lead | One-line summary: strengths first, then "Watch:" caveats. |

Extra columns: address, practice type, number of dentists found, other dentists and emails found, domain registration
date, website "established" year, oldest review seen, rating change, TX license match, business status and Place ID.

### How practice age is estimated

Google doesn't publish opening dates, so three signals are combined:

1. **Website "established / founded / opened in / serving … since YYYY"** text. Used first when found.
2. **Domain registration date** from RDAP. A new practice usually registers its domain shortly before opening.
   It can mislead when an old domain was reused, or when the "website" is a Facebook or Wix page; those shared hosts
   are skipped.
3. **Oldest Google review date** returned by Places. This is a hard lower bound: the practice existed at least
   that long ago. Places returns only up to 5 reviews (Google picks which), so it can't prove a practice is new.
   It only shows a practice is old.

---

## 5. Scoring

Everything lives in `config.yaml`:

- `icp`: your profile: practice types, target age, minimum rating, review-count range, review velocity that earns
  full points, and maximum number of dentists.
- `weights`: relative importance of each component. They're normalised, so they don't need to add up to 100.

| Component | Full credit when… |
|---|---|
| newness | age ≤ `target_max_age_years`; falls linearly to 0 at `newness_zero_at_years` |
| rating | ≥ `min_rating` (0.7 at the minimum, 1.0 at 5★); drops fast below the minimum |
| review_count | inside `review_count_min`–`review_count_max` |
| review_velocity | ≥ `target_review_velocity` reviews/month |
| practice_type | matches one of `icp.practice_types` (or `[any]`) |
| size | ≤ `max_dentists` dentists found on the website |

Missing data gets partial credit (`unknown_credit`) instead of zero, so a practice with no website isn't buried.

**Exclusions** (score 0, moved to the Excluded sheet):

- permanently closed practices
- names or domains matching `exclude_chains` (Aspen, Heartland, Smile Brands and so on; add your own)
- any name or website domain that shows up at `chain_location_threshold` or more locations. This catches DSOs not on
  the list.

Temporarily closed practices are not excluded, but their score is halved.

---

## 6. Growth tracking

Every run is stored in `data/leads.sqlite`:

- `runs`: one row per run.
- `snapshots`: rating, review count and status per practice, timestamped with when Google returned the data.
- `lead_history`: the full lead record and score for each run.

On later runs the tool compares each practice with an earlier snapshot. It uses the most recent snapshot that is at
least 28 days old, or else the oldest one that is at least 7 days old. From that it computes new reviews per month and
the rating change. Run it every 2–4 weeks (with `--yes` from cron or Task Scheduler) and velocity turns into real
momentum.

Places responses are cached for `cache.places_ttl_days` (7 by default), so a rerun inside that window costs nothing
and doesn't record a duplicate snapshot. Use `--refresh` to force fresh data.

---

## 7. Website crawling

For each practice website the crawler:

- reads `robots.txt` and skips anything it disallows. If `robots.txt` can't be reached because of a server error,
  the site is skipped.
- fetches the homepage, then links that look like doctor, team, about or contact pages, then the paths in
  `crawl.paths`. It fetches at most `max_pages_per_site` pages.
- waits `delay_seconds` between requests to the same site and sends your `user_agent`.
- extracts emails from mailto links, Cloudflare-protected addresses, "name [at] domain [dot] com" text and plain text.
  Junk addresses such as tracking or image-file names are filtered out.

Pages are cached for `cache.website_ttl_days`.

---

## 8. Optional extras

### Hunter.io

Set `HUNTER_API_KEY` in `.env`. Hunter is only called for practices whose website lists no email:

1. **Domain Search**: if it returns a person with the dentist's last name, that email is used.
2. **Email Finder** (with the dentist's name, `hunter.use_email_finder`): used if nothing matched.
3. If Hunter only knows the domain's naming pattern (e.g. `{first}.{last}`), an address is built from it and
   labelled **pattern guess, unverified**.

If the key is rejected or credits run out, Hunter is skipped for the rest of the run with a warning. Responses are
cached for 90 days, so reruns don't spend credits.

### Texas State Board of Dental Examiners (TSBDE) license check

The TSBDE online license lookup is an interactive search form. Sending it automated queries could break the site's
terms of use, so **the tool does not query it**. Instead, you can match against a licensee list offline:

1. Get a dentist licensee file from TSBDE. Check <https://tsbde.texas.gov> for licensee data downloads, or request
   the list as public information.
2. Save it as CSV, set `tsbde.license_csv` in `config.yaml`, and adjust the column names under `tsbde` to match the
   file's headers.
3. The **TX License (TSBDE)** column then shows the license number and date for single-name matches, or
   "multiple matches" / "no match".

Leave `license_csv` empty to skip this step.

---

## 9. Good-citizen notes

- **Google's terms:** the Google Maps Platform terms limit how long you may store Places content other than Place
  IDs. Review them, and shorten `places_ttl_days` or prune old `snapshots` rows if your use requires it:
  <https://cloud.google.com/maps-platform/terms>.
- **Cold email:** follow CAN-SPAM. Identify yourself, include a physical address, honour opt-outs, and verify
  "pattern guess" addresses before you send to them.

---

## 10. Tests

```bash
pytest
```

The end-to-end test runs the whole pipeline against local fake servers that stand in for Places, RDAP and a practice
website. It checks robots.txt handling, pagination, exclusions, caching (a rerun makes no Google calls), `--limit`
and the CSV/XLSX columns. `test_web.py` drives the website the same way: saving settings and keys, estimating,
running a search, viewing and downloading leads, validation, the cross-site POST block and the password.

## Layout

```
dental_leads/
  config.yaml         ICP, weights, cities, queries, crawl + cache settings
  .env.example        copy to .env
  leadgen/
    __main__.py       command line
    pipeline.py       orchestration, estimate/confirm, exclusions, summary
    places.py         Places API (New) Text Search over a grid
    website.py        polite crawler
    extract.py        dentist names, emails, founding year, practice type
    age.py            RDAP + age estimate
    hunter.py         optional Hunter.io
    tsbde.py          optional license CSV matching
    db.py             SQLite run history and growth
    scoring.py        fit score + why-this-lead
    export.py         CSV / XLSX
    net.py            HTTP client: cache, retries with backoff, per-site rate limit
    web/              Flask website: pages, background job runner, settings form
  tests/
```
