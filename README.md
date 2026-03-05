# Job Monitor Bot

Automated job search bot that scrapes 20+ job sites and sends Telegram notifications for new postings matching your keywords. Runs on GitHub Actions with zero infrastructure costs.

## Features

- **Multi-source scraping** — Searches Google Custom Search and 15+ HTML job boards
- **Title-only keyword filtering** — Matches keywords against job titles only
- **Duplicate detection** — Tracks seen jobs to avoid repeat notifications
- **Telegram notifications** — Get instant alerts on your phone
- **GitHub Actions** — Runs automatically on a schedule (hourly, daily, etc.)
- **Async & fast** — Concurrent scraping with retry logic and rate limiting

## Quick Start

### 1. Fork this repository

### 2. Set up GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret               | Required | Description                                                                 |
| -------------------- | -------- | --------------------------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN` | ✅       | Your Telegram bot token from [@BotFather](https://t.me/BotFather)           |
| `TELEGRAM_CHAT_ID`   | ✅       | Your Telegram chat ID (use [@userinfobot](https://t.me/userinfobot))        |
| `SEARCH_KEYWORDS`    | ✅       | Comma-separated keywords, e.g., `react,react native,mobile`                 |
| `GOOGLE_API_KEY`     | Optional | Google Custom Search API key                                                |
| `GOOGLE_CSE_ID`      | Optional | Google Custom Search Engine ID                                              |

### 3. Enable GitHub Actions

The workflow runs automatically every hour. You can also trigger it manually from the **Actions** tab.

## Local Development

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export TELEGRAM_BOT_TOKEN="your_token"
export TELEGRAM_CHAT_ID="your_chat_id"
export SEARCH_KEYWORDS="react,react native,mobile"

# Run with dry-run (no notifications, no seen_jobs updates)
python job_monitor.py --dry-run

# Test specific scrapers
python job_monitor.py --google-only --dry-run
```

## Configuration

### `sites_config.yaml`

Configure HTML job sites and runtime request tuning:

```yaml
sites:
  weworkremotely:
    name: "WeWorkRemotely"
    url: "https://weworkremotely.com/remote-jobs/search?term=developer"
    type: "html"
    enabled: true
    max_jobs: 20
    selectors:
      job_container: ".jobs article"
      title: ".title"
      company: ".company"
      link: "a"

request:
  timeout: 15
  max_retries: 3
  retry_base_delay: 1.0
  retry_max_delay: 10.0
  concurrent_limit: 10
  per_domain_min_interval: 0.2
  cache_ttl_seconds: 900
  seen_jobs_ttl_days: 90
  telegram_max_retries: 3
```

### `google_search_sites.yaml`

Configure Google Custom Search queries:

```yaml
settings:
  enabled: true
  max_results_per_query: 10
  date_restrict: "d1" # Last 24 hours (code enforces max d2)
  max_queries_per_run: 3
  min_seconds_between_queries: 2.0
  google_max_retries_per_query: 1
  google_stop_on_rate_limit: true
  google_schedule_interval_hours: 3
  google_query_negative_terms: ["onsite", "hybrid"]

keywords:
  - '"React Native"'
  - '"Mobile Developer"'

sites:
  - domain: "greenhouse.io"
    name: "Greenhouse"
  - domain: "lever.co"
    name: "Lever"
```

## CLI Options

| Flag            | Description                                                             |
| --------------- | ----------------------------------------------------------------------- |
| `--dry-run`     | Test mode: scrape but don't send notifications or update seen_jobs.json |
| `--google-only` | Only run Google Custom Search scraper                                   |

## How It Works

1. **Load seen jobs** from `seen_jobs.json`
2. **Scrape all sources** concurrently (APIs + HTML sites)
3. **Filter jobs** by title keywords and location policy (remote-first, with visa/relocation exception)
4. **Deduplicate** using job IDs (skips already-seen jobs and in-run duplicates)
5. **Send Telegram notification** with retries
6. **Persist seen jobs only after successful notification**

## Adding New Job Sites

### HTML Sites

Add an entry to `sites_config.yaml`:

```yaml
sites:
  my_new_site:
    name: "My New Site"
    url: "https://example.com/jobs"
    type: "html"
    enabled: true
    selectors:
      job_container: ".job-card"
      title: "h2"
      company: ".company-name"
      link: "a"
```

### Google Search Sites

Add to `google_search_sites.yaml`:

```yaml
sites:
  - domain: "newsite.com"
    name: "New Site"
```

## Troubleshooting

### Jobs not showing up?

- Check `--dry-run` output for failed sites
- Verify CSS selectors match the site's HTML structure
- Ensure API credentials are correct

### Duplicate notifications?

- Make sure `seen_jobs.json` is being committed back to the repo
- Check GitHub Actions logs for save errors

### Rate limited?

- Reduce `concurrent_limit` in `sites_config.yaml`
- Increase `retry_base_delay` for slower retries

## License

MIT
