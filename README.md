# Job Monitor Bot

Automated job-search bot that searches Google (via [Serper.dev](https://serper.dev)) across job-board domains and sends Telegram notifications for new remote postings matching your keywords. Runs on GitHub Actions with zero infrastructure costs.

## Features

- **Google search via Serper.dev** — Real Google results across job-board domains using a single API key (no Google Cloud project or Custom Search Engine to set up)
- **Combined-site queries** — One query per keyword spans all boards via `(site:a OR site:b …)`, so every run covers everything
- **Location filtering** — Remote-first, with a visa/relocation exception for onsite/hybrid roles
- **Duplicate detection** — Tracks seen jobs to avoid repeat notifications
- **Telegram notifications** — Get instant alerts on your phone, plus a daily heartbeat and failure alerts
- **GitHub Actions** — Runs automatically on a schedule (every 4 hours by default)

## Quick Start

### 1. Fork this repository

### 2. Get a Serper API key

Sign up at [serper.dev](https://serper.dev) (free tier, ~2,500 one-time credits) and copy your API key. No Google Cloud project or Programmable Search Engine is needed — Serper queries Google directly.

### 3. Set up GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret               | Required | Description                                                          |
| -------------------- | -------- | -------------------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN` | ✅       | Your Telegram bot token from [@BotFather](https://t.me/BotFather)    |
| `TELEGRAM_CHAT_ID`   | ✅       | Your Telegram chat ID (use [@userinfobot](https://t.me/userinfobot)) |
| `SERPER_API_KEY`     | ✅       | API key from [serper.dev](https://serper.dev)                        |

Keywords and target job boards are configured in `google_search_sites.yaml` (not a secret).

### 4. Enable GitHub Actions

The workflow runs automatically every 4 hours. You can also trigger it manually from the **Actions** tab.

## Local Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN="your_token"
export TELEGRAM_CHAT_ID="your_chat_id"
export SERPER_API_KEY="your_serper_key"

# Dry run: search but don't send notifications or update seen_jobs.json
python job_monitor.py --dry-run
```

## Configuration

### `google_search_sites.yaml` — what to search for

```yaml
settings:
  enabled: true
  max_results_per_query: 10
  serper_time_filter: "qdr:d" # past day; qdr:h/d/w/m/y or "" for none
  serper_country: "us"
  query_negative_terms: ["onsite", "hybrid"]

# Each keyword becomes ONE combined query across all sites below.
keywords:
  - '"React Native"'
  - '("iOS Developer" OR "iOS Engineer" OR Swift)'
  - '("Mobile Developer" OR "Mobile Engineer")'

sites:
  - domain: "greenhouse.io"
    name: "Greenhouse"
  - domain: "lever.co"
    name: "Lever"
```

Searches/day = `#keywords × runs/day`. At 3 keywords and 6 runs/day that's ~18 — comfortably within Serper's free tier (~2,500 credits ≈ 20 weeks).

### `sites_config.yaml` — filtering, alerts, runtime tuning

Holds the `location_filter` (remote policy + an always-on `exclude_location_terms` list that drops results whose title/snippet mentions a given country/city as a whole word — e.g. India), `notifications` (heartbeat + failure alerts), and `request` (HTTP timeouts/retries, seen-jobs retention) sections.

## CLI Options

| Flag        | Description                                                          |
| ----------- | -------------------------------------------------------------------- |
| `--dry-run` | Search but don't send notifications or update `seen_jobs.json`       |

## How It Works

1. **Load seen jobs** from `seen_jobs.json`
2. **Search** — one combined `(site:… OR …)` Google query per keyword via Serper
3. **Filter** by location policy (remote-first, with a visa/relocation exception)
4. **Deduplicate** using job IDs (skips already-seen and in-run duplicates)
5. **Send Telegram notification** with retries
6. **Persist seen jobs** only after a successful notification, saved to the GitHub Actions cache (restored on the next run)

## Adding Job Boards

Add a domain to the `sites:` list in `google_search_sites.yaml`:

```yaml
sites:
  - domain: "newsite.com"
    name: "New Site"
```

It's automatically folded into each keyword's combined query — no other setup needed.

## Cadence & Latency

Because every run covers all keywords and boards, your worst-case alert latency equals the schedule interval. Change the `cron` in `.github/workflows/job-monitor.yml`:

```yaml
- cron: "0 */4 * * *" # every 4 hours (default)
```

More frequent = fresher alerts but more Serper credits; less frequent stretches the free tier.

## Troubleshooting

### Jobs not showing up?

- Run `python job_monitor.py --dry-run` locally with `SERPER_API_KEY` set.
- Check the GitHub Actions logs — the `google_last_error` line in **Operational Metrics** reports the exact Serper error, and failures are also pushed to Telegram.
- Broaden or adjust the `keywords` in `google_search_sites.yaml`.

### Out of Serper credits?

You'll get a Telegram failure alert (rate-limit/quota). Lower the cron frequency, trim keywords, or upgrade your Serper plan.

### Duplicate notifications?

- Dedup state lives in the Actions cache (`seen-jobs-*`). If the cache is evicted (rare — it's accessed every run), you may get a one-time repeat batch. Check the "Restore/Save seen jobs" steps in the Actions logs.

## License

MIT
