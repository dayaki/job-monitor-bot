"""
Job Monitor Bot - searches Google (via Serper.dev) for new remote job postings
and sends Telegram notifications.
Features: async search, retry logic, YAML config, structured logging, heartbeat alerts
"""

import argparse
import asyncio
import aiohttp
import hashlib
import html
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

# ============= LOGGING SETUP =============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('job_monitor')

# ============= CONFIGURATION FROM ENVIRONMENT =============
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

# Serper.dev — Google search results via a single API key (no Cloud project/CSE).
SERPER_API_KEY = os.getenv('SERPER_API_KEY', '')

# ============= LOAD YAML CONFIG =============
CONFIG_PATH = Path(__file__).parent / 'sites_config.yaml'
GOOGLE_SEARCH_CONFIG_PATH = Path(__file__).parent / 'google_search_sites.yaml'

def load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, 'r') as f:
                return yaml.safe_load(f)
        logger.warning(f"Config file not found at {CONFIG_PATH}, using defaults")
        return {'sites': {}, 'request': {}}
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return {'sites': {}, 'request': {}}

def load_google_search_config() -> dict:
    try:
        if GOOGLE_SEARCH_CONFIG_PATH.exists():
            with open(GOOGLE_SEARCH_CONFIG_PATH, 'r') as f:
                return yaml.safe_load(f)
        logger.warning(f"Google search config not found at {GOOGLE_SEARCH_CONFIG_PATH}")
        return {'settings': {'enabled': False}, 'keywords': [], 'sites': []}
    except Exception as e:
        logger.error(f"Error loading Google search config: {e}")
        return {'settings': {'enabled': False}, 'keywords': [], 'sites': []}

CONFIG = load_config()
REQUEST_CONFIG = CONFIG.get('request', {})
LOCATION_FILTER_CONFIG = CONFIG.get('location_filter', {})
NOTIFICATIONS_CONFIG = CONFIG.get('notifications', {})
TIMEOUT = REQUEST_CONFIG.get('timeout', 15)
MAX_RETRIES = REQUEST_CONFIG.get('max_retries', 3)
RETRY_BASE_DELAY = REQUEST_CONFIG.get('retry_base_delay', 1.0)
RETRY_MAX_DELAY = REQUEST_CONFIG.get('retry_max_delay', 10.0)
CONCURRENT_LIMIT = REQUEST_CONFIG.get('concurrent_limit', 10)
SEEN_JOBS_MAX = REQUEST_CONFIG.get('seen_jobs_max', 5000)
SEEN_JOBS_TTL_DAYS = REQUEST_CONFIG.get('seen_jobs_ttl_days', 90)
TELEGRAM_MAX_RETRIES = REQUEST_CONFIG.get('telegram_max_retries', 3)
TELEGRAM_RETRY_BASE_DELAY = REQUEST_CONFIG.get('telegram_retry_base_delay', 1.0)
TELEGRAM_RETRY_MAX_DELAY = REQUEST_CONFIG.get('telegram_retry_max_delay', 10.0)

SENSITIVE_QUERY_KEYS = {
    'access_token',
    'api_key',
    'authorization',
    'key',
    'password',
    'token',
}


def normalize_job_url(url: str) -> str:
    """Canonicalize a URL for dedup: lowercase scheme/host, drop the fragment, the trailing
    slash, and tracking query params. Two links to the same posting collapse to one string."""
    if not url:
        return ''
    try:
        parts = urlsplit(url.strip())
        netloc = parts.netloc.lower()
        path = parts.path.rstrip('/') or '/'
        clean_query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            lowered = key.lower()
            if lowered.startswith('utm_') or lowered in {'ref', 'source', 'fbclid', 'gclid'}:
                continue
            clean_query.append((key, value))
        return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(clean_query), ''))
    except Exception:
        return url.strip()


def redact_url(url: str) -> str:
    """Mask sensitive query parameters before logging."""
    if not url:
        return ''
    try:
        parts = urlsplit(url)
        redacted_query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in SENSITIVE_QUERY_KEYS:
                redacted_query.append((key, 'REDACTED'))
            else:
                redacted_query.append((key, value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(redacted_query), ''))
    except Exception:
        return url


def coerce_string_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list):
        values = [str(item).strip().lower() for item in value if str(item).strip()]
        return values if values else default
    return default

# ============= SCRAPER HEALTH TRACKING =============
class ScraperHealth:
    def __init__(self):
        self.stats: dict[str, dict] = {}
    
    def record_success(self, site_name: str, job_count: int):
        if site_name not in self.stats:
            self.stats[site_name] = {'success': 0, 'failure': 0, 'jobs_found': 0}
        self.stats[site_name]['success'] += 1
        self.stats[site_name]['jobs_found'] += job_count
    
    def record_failure(self, site_name: str, error: str):
        if site_name not in self.stats:
            self.stats[site_name] = {'success': 0, 'failure': 0, 'jobs_found': 0, 'last_error': ''}
        self.stats[site_name]['failure'] += 1
        self.stats[site_name]['last_error'] = error
    
    def get_summary(self) -> str:
        lines = ["Scraper Health Summary:"]
        for site, stats in sorted(self.stats.items()):
            status = "✓" if stats['success'] > 0 else "✗"
            lines.append(f"  {status} {site}: {stats['jobs_found']} jobs, {stats['failure']} failures")
        return "\n".join(lines)
    
    def get_failed_sites(self) -> list[dict]:
        """Returns list of failed sites with their error reasons."""
        failed = []
        for site, stats in sorted(self.stats.items()):
            if stats['success'] == 0 and stats['failure'] > 0:
                failed.append({
                    'site': site,
                    'error': stats.get('last_error', 'Unknown error'),
                    'failures': stats['failure']
                })
        return failed
    
    def get_working_sites(self) -> list[dict]:
        """Returns list of working sites with job counts."""
        working = []
        for site, stats in sorted(self.stats.items()):
            if stats['success'] > 0:
                working.append({
                    'site': site,
                    'jobs_found': stats['jobs_found']
                })
        return working

health_tracker = ScraperHealth()

# ============= ASYNC HTTP CLIENT WITH RETRY =============
class AsyncHTTPClient:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
        }
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
        self._per_domain_min_interval = REQUEST_CONFIG.get('per_domain_min_interval', 0.2)
        self._domain_last_request: dict[str, float] = {}
        self._domain_backoff_until: dict[str, float] = {}
        self._domain_locks: dict[str, asyncio.Lock] = {}
    
    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT)
            self._session = aiohttp.ClientSession(headers=self.headers, timeout=timeout)
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _apply_domain_throttle(self, domain: str):
        if not domain:
            return
        lock = self._domain_locks.setdefault(domain, asyncio.Lock())
        async with lock:
            now = time.time()
            next_allowed = max(
                self._domain_last_request.get(domain, 0) + self._per_domain_min_interval,
                self._domain_backoff_until.get(domain, 0)
            )
            delay = next_allowed - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._domain_last_request[domain] = time.time()
    
    async def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: Optional[dict[str, str]] = None,
        max_retries_override: Optional[int] = None,
        fail_fast_on_rate_limit: bool = False,
        error_state: Optional[dict[str, Any]] = None,
    ) -> Optional[dict]:
        """POST a JSON body and return the parsed JSON response (or None on failure)."""
        attempts = max(1, int(max_retries_override if max_retries_override is not None else MAX_RETRIES))
        safe_url = redact_url(url)
        merged_headers = dict(self.headers)
        if headers:
            merged_headers.update(headers)
        if error_state is not None:
            error_state.clear()
            error_state.update({'last_error': None, 'status': None, 'rate_limited': False})

        async with self._semaphore:
            session = await self.get_session()
            last_error = None
            domain = urlsplit(url).netloc.lower()

            for attempt in range(attempts):
                try:
                    await self._apply_domain_throttle(domain)
                    async with session.post(url, json=payload, headers=merged_headers) as response:
                        if error_state is not None:
                            error_state.update({'status': response.status})
                        if response.status == 200:
                            text = await response.text()
                            if not text or not text.strip():
                                logger.warning(f"Empty response from {safe_url}")
                                return None
                            try:
                                return json.loads(text)
                            except json.JSONDecodeError as e:
                                logger.warning(f"Invalid JSON response from {safe_url}: {e}")
                                return None
                        elif response.status == 429:
                            last_error = "rate_limited"
                            if error_state is not None:
                                error_state.update({'last_error': last_error, 'rate_limited': True})
                            delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                            retry_after = response.headers.get('Retry-After')
                            if retry_after:
                                try:
                                    delay = max(delay, float(retry_after))
                                except ValueError:
                                    pass
                            if fail_fast_on_rate_limit:
                                logger.warning(f"Rate limited on {safe_url}; fail-fast enabled")
                                return None
                            logger.warning(f"Rate limited on {safe_url}, waiting {delay}s")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            last_error = f"http_{response.status}"
                            body_snippet = ''
                            try:
                                body_text = await response.text()
                                body_snippet = ' '.join((body_text or '').split())[:300]
                            except Exception:
                                pass
                            if error_state is not None:
                                error_state.update({'last_error': last_error, 'body': body_snippet})
                            logger.warning(
                                f"HTTP {response.status} for {safe_url}"
                                + (f": {body_snippet}" if body_snippet else "")
                            )
                            return None
                except asyncio.TimeoutError:
                    last_error = "timeout"
                    if error_state is not None:
                        error_state.update({'last_error': last_error})
                    logger.warning(f"Timeout posting to {safe_url} (attempt {attempt + 1}/{attempts})")
                except aiohttp.ClientError as e:
                    last_error = str(e)
                    if error_state is not None:
                        error_state.update({'last_error': last_error})
                    logger.warning(f"Client error posting to {safe_url}: {e} (attempt {attempt + 1}/{attempts})")
                except Exception as e:
                    logger.error(f"Unexpected error posting to {safe_url}: {e}")
                    return None

                if attempt < attempts - 1:
                    await asyncio.sleep(min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY))

            logger.error(f"Failed to POST {safe_url} after {attempts} attempts: {last_error}")
            if error_state is not None:
                error_state.update({'last_error': last_error})
            return None

http_client = AsyncHTTPClient()

# ============= JOB SITE SCRAPER =============
class JobSiteScraper:
    def __init__(self, seen_jobs_file: str = 'seen_jobs.json'):
        self.seen_jobs_file = seen_jobs_file
        self.seen_jobs = self.load_seen_jobs()
        self.pending_job_ids: set[str] = set()
        self.location_filter_mode = str(
            LOCATION_FILTER_CONFIG.get('location_filter_mode', 'strict_remote_with_exception')
        ).strip().lower()
        self.location_remote_terms = coerce_string_list(
            LOCATION_FILTER_CONFIG.get('location_remote_terms'),
            [
                'remote',
                'work from home',
                'wfh',
                'distributed',
                'anywhere',
            ]
        )
        self.location_hybrid_terms = coerce_string_list(
            LOCATION_FILTER_CONFIG.get('location_hybrid_terms'),
            [
                'hybrid',
                '2 days onsite',
                '3 days onsite',
                'partially remote',
            ]
        )
        self.location_onsite_terms = coerce_string_list(
            LOCATION_FILTER_CONFIG.get('location_onsite_terms'),
            [
                'onsite',
                'on-site',
                'in office',
                'office based',
                'on site',
            ]
        )
        self.location_exception_terms = coerce_string_list(
            LOCATION_FILTER_CONFIG.get('location_exception_terms'),
            [
                'visa sponsorship',
                'sponsorship available',
                'relocation support',
                'willing to relocate',
                'relocation assistance',
            ]
        )
        self.accept_unspecified_location = bool(
            LOCATION_FILTER_CONFIG.get('accept_unspecified_location', False)
        )
        # Always-on exclude list (independent of location_filter_mode): drop results whose
        # title/snippet mentions any of these as a WHOLE WORD. Whole-word matching keeps
        # "india" from nuking "Indiana"/"Indianapolis".
        self.exclude_location_terms = coerce_string_list(
            LOCATION_FILTER_CONFIG.get('exclude_location_terms'), []
        )
        self._exclude_location_patterns = [
            re.compile(rf'(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])')
            for term in self.exclude_location_terms
        ]
        self.metrics = {
            'google_queries_available': 0,
            'google_queries_executed': 0,
            'google_queries_skipped_by_budget': 0,
            'google_rate_limited': 0,
            'google_stopped_early_reason': '',
            'google_last_error': '',
            'jobs_rejected_location': 0,
            'jobs_excluded_location': 0,
            'jobs_accepted_exception': 0,
        }
    
    def load_seen_jobs(self) -> dict[str, float]:
        try:
            if os.path.exists(self.seen_jobs_file):
                with open(self.seen_jobs_file, 'r') as f:
                    payload = json.load(f)
                    now = time.time()
                    if isinstance(payload, list):
                        return {job_id: now for job_id in payload if isinstance(job_id, str)}
                    if isinstance(payload, dict):
                        seen: dict[str, float] = {}
                        for job_id, ts in payload.items():
                            if isinstance(job_id, str):
                                try:
                                    seen[job_id] = float(ts)
                                except (TypeError, ValueError):
                                    seen[job_id] = now
                        return seen
            return {}
        except Exception as e:
            logger.error(f"Error loading seen jobs: {e}")
            return {}
    
    def save_seen_jobs(self):
        try:
            self._prune_seen_jobs()
            with open(self.seen_jobs_file, 'w') as f:
                json.dump(self.seen_jobs, f)
            logger.info(f"Saved {len(self.seen_jobs)} seen jobs")
        except Exception as e:
            logger.error(f"Error saving seen jobs: {e}")

    def _prune_seen_jobs(self):
        if not self.seen_jobs:
            return
        now = time.time()
        ttl_seconds = SEEN_JOBS_TTL_DAYS * 24 * 60 * 60
        self.seen_jobs = {
            job_id: ts
            for job_id, ts in self.seen_jobs.items()
            if now - ts <= ttl_seconds
        }
        if len(self.seen_jobs) > SEEN_JOBS_MAX:
            newest_first = sorted(self.seen_jobs.items(), key=lambda item: item[1], reverse=True)
            self.seen_jobs = dict(newest_first[:SEEN_JOBS_MAX])
    
    def generate_job_id(self, url: str) -> str:
        # Dedup on the normalized URL alone — it uniquely identifies a posting and is
        # immune to Google title/snippet drift and trailing-slash/host-case variants.
        return hashlib.md5(normalize_job_url(url).encode()).hexdigest()
    
    def is_new_job(self, job_id: str) -> bool:
        return job_id not in self.seen_jobs and job_id not in self.pending_job_ids
    
    def queue_job_id(self, job_id: str):
        self.pending_job_ids.add(job_id)

    def mark_jobs_as_seen(self, job_ids: list[str]):
        now = time.time()
        for job_id in job_ids:
            self.seen_jobs[job_id] = now
            self.pending_job_ids.discard(job_id)

    @staticmethod
    def _contains_any_term(text: str, terms: list[str]) -> bool:
        return any(term in text for term in terms)

    def classify_location(self, job: dict) -> dict[str, Any]:
        if self.location_filter_mode != 'strict_remote_with_exception':
            return {'accepted': True, 'accepted_by_exception': False, 'reason': 'filter_disabled'}

        title = str(job.get('title', '')).lower()
        description = str(job.get('description', '')).lower()
        searchable = f"{title} {description}"

        has_remote = self._contains_any_term(searchable, self.location_remote_terms)
        has_hybrid = self._contains_any_term(searchable, self.location_hybrid_terms)
        has_onsite = self._contains_any_term(searchable, self.location_onsite_terms)
        has_exception = self._contains_any_term(searchable, self.location_exception_terms)

        if has_remote:
            return {'accepted': True, 'accepted_by_exception': False, 'reason': 'remote'}
        if (has_hybrid or has_onsite) and has_exception:
            return {
                'accepted': True,
                'accepted_by_exception': True,
                'reason': 'onsite_or_hybrid_with_visa_or_relocation_exception',
            }
        if has_hybrid:
            return {'accepted': False, 'accepted_by_exception': False, 'reason': 'hybrid_without_exception'}
        if has_onsite:
            return {'accepted': False, 'accepted_by_exception': False, 'reason': 'onsite_without_exception'}
        if self.accept_unspecified_location:
            return {'accepted': True, 'accepted_by_exception': False, 'reason': 'location_unspecified_accepted'}
        return {'accepted': False, 'accepted_by_exception': False, 'reason': 'location_unspecified_without_exception'}

    def matched_exclude_term(self, job: dict) -> Optional[str]:
        """Return the first exclude term appearing as a whole word in the title/snippet, else
        None. Applied to every result regardless of location_filter_mode."""
        if not self._exclude_location_patterns:
            return None
        searchable = f"{job.get('title', '')} {job.get('description', '')}".lower()
        for term, pattern in zip(self.exclude_location_terms, self._exclude_location_patterns):
            if pattern.search(searchable):
                return term
        return None

    def location_hint(self, job: dict) -> str:
        """Short location label for the Telegram message, independent of the filter mode
        (which may be off). Best-effort from the title+snippet — open the link to confirm."""
        searchable = f"{job.get('title', '')} {job.get('description', '')}".lower()
        has_remote = self._contains_any_term(searchable, self.location_remote_terms)
        has_hybrid = self._contains_any_term(searchable, self.location_hybrid_terms)
        has_onsite = self._contains_any_term(searchable, self.location_onsite_terms)
        has_exception = self._contains_any_term(searchable, self.location_exception_terms)
        if has_remote:
            label = "Remote"
        elif has_hybrid:
            label = "Hybrid"
        elif has_onsite:
            label = "Onsite"
        else:
            label = "Location unclear"
        if has_exception and label != "Remote":
            label += " · visa/relocation"
        return label

    def log_operational_metrics(self):
        logger.info(
            "Operational Metrics:\n"
            f"  google_queries_available={self.metrics['google_queries_available']}\n"
            f"  google_queries_executed={self.metrics['google_queries_executed']}\n"
            f"  google_queries_skipped_by_budget={self.metrics['google_queries_skipped_by_budget']}\n"
            f"  google_rate_limited={self.metrics['google_rate_limited']}\n"
            f"  google_stopped_early_reason={self.metrics['google_stopped_early_reason'] or 'none'}\n"
            f"  google_last_error={self.metrics['google_last_error'] or 'none'}\n"
            f"  jobs_rejected_location={self.metrics['jobs_rejected_location']}\n"
            f"  jobs_excluded_location={self.metrics['jobs_excluded_location']}\n"
            f"  jobs_accepted_exception={self.metrics['jobs_accepted_exception']}"
        )
    
    def _handle_search_item(
        self,
        raw_title: str,
        raw_link: str,
        snippet: str,
        source_label: str,
        site_name: str,
        jobs: list[dict],
    ):
        """Process one Serper search result into a job, applying the location filter and
        dedup. Mutates `jobs` and metrics in place. The search query is the keyword authority,
        so there is no title/snippet keyword re-check here."""
        title = (raw_title or '').strip()
        job_url = normalize_job_url(raw_link or '')
        if not title or not job_url:
            return

        company = ''
        if ' - ' in title:
            parts = title.rsplit(' - ', 1)
            if len(parts) == 2:
                title, company = parts[0].strip(), parts[1].strip()
        elif ' | ' in title:
            parts = title.rsplit(' | ', 1)
            if len(parts) == 2:
                title, company = parts[0].strip(), parts[1].strip()

        job = {
            'title': title,
            'company': company,
            'url': job_url,
            'source': source_label,
            'description': snippet or '',
        }
        job_id = self.generate_job_id(job_url)

        if not self.is_new_job(job_id):
            return

        excluded_term = self.matched_exclude_term(job)
        if excluded_term:
            self.metrics['jobs_excluded_location'] += 1
            self.queue_job_id(job_id)  # don't re-evaluate/re-count this URL again this run
            logger.info(f"{site_name}: Excluded (matched '{excluded_term}'): {title[:120]}")
            return

        location_result = self.classify_location(job)
        if not location_result['accepted']:
            self.metrics['jobs_rejected_location'] += 1
            logger.info(
                f"{site_name}: Rejected by location filter "
                f"({location_result['reason']}): {title[:120]}"
            )
            return
        if location_result['accepted_by_exception']:
            self.metrics['jobs_accepted_exception'] += 1
        job['id'] = job_id
        job['location_reason'] = location_result['reason']
        job['location_hint'] = self.location_hint(job)
        jobs.append(job)
        self.queue_job_id(job_id)

    # ============= SERPER.DEV (GOOGLE RESULTS VIA SINGLE API KEY) =============
    async def scrape_serper_search(self) -> list[dict]:
        """Search for jobs via Serper.dev (real Google results, one API key, no CSE).

        Reuses the same keyword/site queries, rotation budget, and filtering as the
        Google CSE path; only the transport (POST + X-API-KEY) and freshness param differ.
        """
        jobs = []
        site_name = "SerperSearch"

        if not SERPER_API_KEY:
            logger.debug(f"{site_name}: Skipped (no SERPER_API_KEY)")
            return jobs

        search_config = load_google_search_config()
        settings = search_config.get('settings', {})

        if not settings.get('enabled', False):
            logger.debug(f"{site_name}: Disabled in config")
            return jobs

        keywords = search_config.get('keywords', [])
        sites = search_config.get('sites', [])
        max_results = settings.get('max_results_per_query', 10)
        min_seconds_between_queries = max(0.0, float(settings.get('min_seconds_between_queries', 1.0)))
        max_consecutive_failures = max(1, int(settings.get('max_consecutive_failures', 3)))
        serper_max_retries_per_query = max(1, int(settings.get('serper_max_retries_per_query', 2)))
        serper_stop_on_rate_limit = bool(settings.get('serper_stop_on_rate_limit', True))
        # Serper time filter: qdr:h/d/w/m/y (hour/day/week/month/year). Empty = no filter.
        serper_time_filter = str(settings.get('serper_time_filter', 'qdr:d')).strip()
        # Country bias(es) for Google ranking (gl). One request is sent per (keyword, country),
        # so total searches = keywords × countries. Accepts a list or comma-separated string,
        # and the legacy single 'serper_country'. Empty => one unbiased search.
        raw_countries = settings.get('serper_countries', settings.get('serper_country', ''))
        if isinstance(raw_countries, str):
            countries = [c.strip().lower() for c in raw_countries.split(',') if c.strip()]
        elif isinstance(raw_countries, list):
            countries = [str(c).strip().lower() for c in raw_countries if str(c).strip()]
        else:
            countries = []
        if not countries:
            countries = ['']  # single unbiased query
        negative_terms = coerce_string_list(
            settings.get('query_negative_terms'),
            []
        )
        jitter_max_seconds = max(0.0, min(float(settings.get('query_jitter_max_seconds', 0.4)), 2.0))

        if not keywords or not sites:
            logger.warning(f"{site_name}: No keywords or sites configured")
            return jobs

        # Map result domains back to friendly board names for the alert "source" label.
        domain_to_name = {
            s['domain']: s.get('name', s['domain'])
            for s in sites if s.get('domain')
        }
        domains = list(domain_to_name.keys())

        try:
            # One combined query per keyword across ALL boards via (site:a OR site:b ...),
            # so every run covers every board+keyword — no rotation, latency = cron interval.
            site_clause = "(" + " OR ".join(f"site:{d}" for d in domains) + ")"
            negative_clause = " ".join(
                f"-{term.lstrip('-').strip()}"
                for term in negative_terms
                if term and term.lstrip('-').strip()
            )
            # One query per keyword (no location terms baked in — every matching role is
            # returned regardless of remote/onsite/hybrid; set query_negative_terms to
            # re-exclude). Fan out across countries: one request per (keyword, country).
            query_specs: list[tuple[str, str]] = []
            for keyword in keywords:
                query_parts = [keyword, site_clause]
                if negative_clause:
                    query_parts.append(negative_clause)
                query = " ".join(query_parts)
                for gl in countries:
                    query_specs.append((query, gl))
            self.metrics['google_queries_available'] = len(query_specs)

            total_queries = 0
            consecutive_failures = 0

            def next_query_delay() -> float:
                return min_seconds_between_queries + random.uniform(0.0, jitter_max_seconds)

            for query, gl in query_specs:
                payload: dict[str, Any] = {'q': query, 'num': max_results}
                if serper_time_filter:
                    payload['tbs'] = serper_time_filter
                if gl:
                    payload['gl'] = gl

                error_state: dict[str, Any] = {}
                data = await http_client.post_json(
                    "https://google.serper.dev/search",
                    payload=payload,
                    headers={'X-API-KEY': SERPER_API_KEY},
                    max_retries_override=serper_max_retries_per_query,
                    fail_fast_on_rate_limit=serper_stop_on_rate_limit,
                    error_state=error_state,
                )
                total_queries += 1
                self.metrics['google_queries_executed'] = total_queries

                if error_state.get('rate_limited'):
                    self.metrics['google_rate_limited'] += 1
                    if serper_stop_on_rate_limit:
                        self.metrics['google_stopped_early_reason'] = 'rate_limited'
                        logger.warning(f"{site_name}: Stopping early due to Serper rate-limit response")
                        break

                if not data:
                    status = error_state.get('status')
                    detail = error_state.get('body') or error_state.get('last_error') or 'no response'
                    self.metrics['google_last_error'] = f"HTTP {status}: {detail}"
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        if not self.metrics['google_stopped_early_reason']:
                            self.metrics['google_stopped_early_reason'] = 'consecutive_failures'
                        logger.warning(
                            f"{site_name}: Stopping early after {consecutive_failures} consecutive failed queries"
                        )
                        break
                    await asyncio.sleep(next_query_delay())
                    continue

                consecutive_failures = 0
                for item in data.get('organic', []) or []:
                    link = item.get('link', '')
                    netloc = urlsplit(link).netloc.lower()
                    board = next((name for dom, name in domain_to_name.items() if dom in netloc), 'Search')
                    self._handle_search_item(
                        item.get('title', ''),
                        link,
                        item.get('snippet', ''),
                        f"Serper-{board}",
                        site_name,
                        jobs,
                    )

                await asyncio.sleep(next_query_delay())

            health_tracker.record_success(site_name, len(jobs))
            logger.info(f"{site_name}: Found {len(jobs)} new jobs from {total_queries} queries")
        except Exception as e:
            health_tracker.record_failure(site_name, str(e))
            logger.error(f"{site_name} error: {e}")

        return jobs

# ============= TELEGRAM NOTIFICATION =============
async def _deliver_telegram_message(session, url: str, payload: dict) -> bool:
    """Send a single Telegram message with retry/backoff. Returns True on success."""
    for attempt in range(TELEGRAM_MAX_RETRIES):
        try:
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    return True

                error_text = await response.text()
                retriable = response.status in (429, 500, 502, 503, 504)
                if not retriable:
                    logger.error(f"Telegram API error (status {response.status}): {error_text}")
                    return False

                delay = min(TELEGRAM_RETRY_BASE_DELAY * (2 ** attempt), TELEGRAM_RETRY_MAX_DELAY)
                if response.status == 429:
                    try:
                        error_payload = json.loads(error_text)
                        retry_after = float(error_payload.get('parameters', {}).get('retry_after', 0))
                        delay = max(delay, retry_after)
                    except Exception:
                        pass
                logger.warning(
                    f"Telegram API temporary error (status {response.status}), "
                    f"retrying in {delay}s (attempt {attempt + 1}/{TELEGRAM_MAX_RETRIES})"
                )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            delay = min(TELEGRAM_RETRY_BASE_DELAY * (2 ** attempt), TELEGRAM_RETRY_MAX_DELAY)
            logger.warning(
                f"Telegram delivery failed ({e}), retrying in {delay}s "
                f"(attempt {attempt + 1}/{TELEGRAM_MAX_RETRIES})"
            )
        except Exception as e:
            logger.error(f"Unexpected Telegram error: {e}")
            return False

        if attempt < TELEGRAM_MAX_RETRIES - 1:
            await asyncio.sleep(delay)

    logger.error("Telegram delivery failed after retries")
    return False


async def send_telegram_notification(jobs: list[dict]) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured")
        return False
    
    if not jobs:
        logger.info("No new jobs to notify")
        return True
    
    try:
        session = await http_client.get_session()

        def _escape_text(value: str, max_length: int, fallback: str = 'Unknown') -> str:
            normalized = (value or '').strip()
            if not normalized:
                normalized = fallback
            return html.escape(normalized[:max_length], quote=False)

        header = f"🔔 <b>{len(jobs)} New Job(s) Found!</b>\n"
        header += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        header += "─" * 30 + "\n\n"

        messages = []
        current_message = header

        for i, job in enumerate(jobs, 1):
            title = _escape_text(job.get('title', ''), 100)
            company = _escape_text(job.get('company', ''), 50, fallback='')
            source = _escape_text(job.get('source', ''), 60)
            hint = _escape_text(job.get('location_hint', ''), 60, fallback='')
            snippet = _escape_text(job.get('description', ''), 200, fallback='')
            url = html.escape(job.get('url', ''), quote=True)

            job_text = f"<b>{i}. {title}</b>\n"
            if company:
                job_text += f"🏢 {company}\n"
            if hint:
                job_text += f"📍 {hint}\n"
            if snippet:
                job_text += f"📝 {snippet}\n"
            job_text += f"🌐 {source}\n"
            if url:
                job_text += f"🔗 <a href=\"{url}\">Apply Here</a>\n\n"
            else:
                job_text += "🔗 No URL provided\n\n"

            if len(current_message) + len(job_text) > 4000:
                messages.append(current_message)
                current_message = header + job_text
            else:
                current_message += job_text

        if current_message:
            messages.append(current_message)

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        for msg in messages:
            payload = {
                'chat_id': TELEGRAM_CHAT_ID,
                'text': msg,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }

            if not await _deliver_telegram_message(session, url, payload):
                return False

            await asyncio.sleep(0.5)

        logger.info(f"Successfully sent {len(messages)} Telegram message(s)")
        return True
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")
        return False


async def send_telegram_status(text: str) -> bool:
    """Send a standalone status/heartbeat/alert message (not a job listing)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured; skipping status message")
        return False
    try:
        session = await http_client.get_session()
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
        }
        return await _deliver_telegram_message(session, url, payload)
    except Exception as e:
        logger.error(f"Error sending Telegram status: {e}")
        return False


def detect_run_issues(metrics: dict) -> list[str]:
    """Return human-readable problems for this run (empty list = healthy)."""
    issues: list[str] = []
    for failed in health_tracker.get_failed_sites():
        issues.append(f"{failed['site']} failing: {failed['error']}")
    if metrics.get('google_rate_limited', 0) > 0:
        issues.append(f"Search rate-limited ×{metrics['google_rate_limited']}")
    if metrics.get('google_queries_available', 0) > 0 and metrics.get('google_queries_executed', 0) == 0:
        issues.append("Search ran 0 queries (check API key / quota)")
    reason = metrics.get('google_stopped_early_reason')
    if reason:
        detail = metrics.get('google_last_error', '')
        issues.append(f"Search stopped early: {reason}" + (f" — {detail}" if detail else ""))
    elif metrics.get('google_last_error'):
        issues.append(f"Search error: {metrics['google_last_error']}")
    return issues


def build_status_message(metrics: dict, new_job_count: int, issues: list[str]) -> str:
    """Compose a heartbeat (healthy) or alert (issues present) status message."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    if issues:
        lines = ["⚠️ <b>Job Monitor — Issues Detected</b>", f"📅 {now}", ""]
        for issue in issues:
            lines.append(f"• {html.escape(issue, quote=False)}")
    else:
        lines = ["✅ <b>Job Monitor — Heartbeat</b>", f"📅 {now}"]

    lines.append("")
    lines.append(f"🆕 New jobs this run: {new_job_count}")
    lines.append(
        f"🔎 Search queries: {metrics.get('google_queries_executed', 0)}"
        f"/{metrics.get('google_queries_available', 0)}"
    )
    rejected = metrics.get('jobs_rejected_location', 0)
    if rejected:
        lines.append(f"📍 Rejected by location filter: {rejected}")
    excluded = metrics.get('jobs_excluded_location', 0)
    if excluded:
        lines.append(f"🚫 Excluded (location): {excluded}")
    working = health_tracker.get_working_sites()
    if working:
        summary = ", ".join(f"{w['site']}({w['jobs_found']})" for w in working)
        lines.append(f"🌐 Sources: {html.escape(summary, quote=False)}")
    return "\n".join(lines)

# ============= CLI ARGUMENT PARSING =============
def parse_args():
    parser = argparse.ArgumentParser(
        description='Job Monitor Bot - searches Google via Serper.dev and sends Telegram notifications'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Test mode: search but skip Telegram notifications and seen_jobs.json updates. Prints a report of what was found.'
    )
    return parser.parse_args()

def print_dry_run_report(jobs: list[dict]):
    """Print detailed report for dry-run mode."""
    print("\n" + "=" * 60)
    print("DRY RUN REPORT")
    print("=" * 60)
    
    # Working sites
    working = health_tracker.get_working_sites()
    print(f"\n✅ WORKING SITES ({len(working)}):")
    print("-" * 40)
    if working:
        for site in working:
            print(f"  ✓ {site['site']}: {site['jobs_found']} jobs found")
    else:
        print("  No working sites found")
    
    # Failed sites
    failed = health_tracker.get_failed_sites()
    print(f"\n❌ FAILED SITES ({len(failed)}):")
    print("-" * 40)
    if failed:
        for site in failed:
            print(f"  ✗ {site['site']}")
            print(f"    Reason: {site['error']}")
            print(f"    Failures: {site['failures']}")
            print()
    else:
        print("  All sites working!")
    
    # Jobs found
    print(f"\n📋 JOBS FOUND ({len(jobs)}):")
    print("-" * 40)
    if jobs:
        for i, job in enumerate(jobs[:20], 1):  # Show first 20
            title = job.get('title', 'Unknown')[:60]
            company = job.get('company', 'Unknown')[:30]
            source = job.get('source', 'Unknown')
            print(f"  {i}. [{source}] {title}")
            print(f"     Company: {company}")
        if len(jobs) > 20:
            print(f"\n  ... and {len(jobs) - 20} more jobs")
    else:
        print("  No matching jobs found")
    
    print("\n" + "=" * 60)
    print("END OF DRY RUN REPORT")
    print("=" * 60 + "\n")

# ============= MAIN =============
async def main(dry_run: bool = False):
    logger.info("=" * 50)
    logger.info("Job Monitor Bot Starting")
    if dry_run:
        logger.info("🧪 DRY RUN MODE - No notifications, no seen_jobs.json updates")
    logger.info(f"Concurrent limit: {CONCURRENT_LIMIT}")
    logger.info("=" * 50)

    scraper = JobSiteScraper()

    try:
        start_time = datetime.now()
        new_jobs = await scraper.scrape_serper_search()
        elapsed = (datetime.now() - start_time).total_seconds()
        
        logger.info(f"Scraping completed in {elapsed:.2f} seconds")
        scraper.log_operational_metrics()
        
        if dry_run:
            print_dry_run_report(new_jobs)
        else:
            if new_jobs:
                logger.info(f"Found {len(new_jobs)} new matching jobs")
                notification_sent = await send_telegram_notification(new_jobs)
                if not notification_sent:
                    raise RuntimeError("Notification failed. Keeping jobs unseen for retry on next run.")
                scraper.mark_jobs_as_seen([job.get('id', '') for job in new_jobs if job.get('id')])
            else:
                logger.info("No new matching jobs found")

            # Heartbeat / failure alert: separate from job notifications so silence
            # means "no new jobs", not "the bot broke". Failures never block the run.
            issues = detect_run_issues(scraper.metrics)
            if not SERPER_API_KEY:
                issues.append("SERPER_API_KEY missing — no jobs can be found")
            alert_on_failures = bool(NOTIFICATIONS_CONFIG.get('alert_on_failures', True))
            heartbeat_enabled = bool(NOTIFICATIONS_CONFIG.get('heartbeat_enabled', True))
            heartbeat_hour = int(NOTIFICATIONS_CONFIG.get('heartbeat_hour_utc', 9))
            should_alert = bool(issues) and alert_on_failures
            should_heartbeat = (
                heartbeat_enabled
                and not should_alert
                and not new_jobs
                and datetime.now(timezone.utc).hour == heartbeat_hour
            )
            if should_alert or should_heartbeat:
                status_text = build_status_message(scraper.metrics, len(new_jobs), issues)
                if not await send_telegram_status(status_text):
                    logger.warning("Failed to send status/heartbeat message")

            scraper.save_seen_jobs()
        
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise
    finally:
        await http_client.close()
    
    logger.info("Job Monitor Bot Finished")

if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(dry_run=args.dry_run))
