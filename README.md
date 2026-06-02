# Indeed Parser

Scraper for Indeed job listings using Camoufox (anti-detect browser) + Indeed internal GraphQL API.

## Setup

```bash
# Install dependencies
uv sync

# Install Camoufox browser
uv run camoufox fetch

# Copy env file and fill in your values
cp .env.example .env
```

**.env:**
```
INDEED_API_KEY=161092c2017b5bbab13edb12461a62d5a833871e7cad6d9d475304573de67ac8
INDEED_BDT=your_bdt_token
CAPTCHA_API_KEY=                # optional, 2captcha key for auto-solving
```

## Usage

```bash
# Basic — scrape all Python jobs in Warsaw
uv run main.py

# Custom query and location
uv run main.py --query "java developer" --location "Kraków"

# Limit results
uv run main.py --query "python" --limit 50

# With proxy (residential recommended)
uv run main.py --proxy "http://user:pass@host:port"

# Force new login (ignore cached session)
uv run main.py --force-login
```

Results are saved to `data/indeed_YYYY-MM-DDTHH-MM-SS.json`.

## How it works

1. **Session** — loads a cached session from `accounts/` or opens Camoufox to log in via temp email + OTP
2. **Scraping** — paginates through Indeed GraphQL search API, then fetches full details per job
3. **Output** — saves all jobs as a JSON array

## CAPTCHA

- **Without proxy** — CAPTCHAs may appear during login. If no `CAPTCHA_API_KEY` is set, the browser window stays open so you can solve manually.
- **With residential proxy** — CAPTCHAs typically don't appear at all.
- **Auto-solve** — set `CAPTCHA_API_KEY` with a [2captcha.com](https://2captcha.com) key.

## Output format

Each job in the JSON array:

| Field | Description |
|---|---|
| `job_key` | Indeed job ID |
| `title` | Job title |
| `company` | Company name |
| `location` | Location string |
| `description` | Full job description (plain text) |
| `salary` | Formatted salary string |
| `salary_min/max` | Salary range numbers |
| `remote` | Remote/hybrid/onsite label |
| `job_types` | e.g. `["Full-time"]` |
| `benefits` | List of benefit labels |
| `apply_url` | Direct apply link |
| `date_published` | Publication date |
