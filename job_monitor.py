"""
Job Site Monitoring Bot - Async Version with 20+ Job Sites
Checks multiple job sites for new postings and sends Telegram notifications
Features: Async scraping, retry logic, YAML config, structured logging
"""

import argparse
import asyncio
import aiohttp
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, quote_plus

import yaml
from bs4 import BeautifulSoup

# ============= LOGGING SETUP =============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('job_monitor')

# ============= CONFIGURATION FROM ENVIRONMENT =============
SEARCH_KEYWORDS = os.getenv('SEARCH_KEYWORDS', 'react,react native,mobile').split(',')
SEARCH_KEYWORDS = [kw.strip().lower() for kw in SEARCH_KEYWORDS]

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

ADZUNA_APP_ID = os.getenv('ADZUNA_APP_ID', '')
ADZUNA_APP_KEY = os.getenv('ADZUNA_APP_KEY', '')

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', '')
GOOGLE_CSE_ID = os.getenv('GOOGLE_CSE_ID', '')

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
TIMEOUT = REQUEST_CONFIG.get('timeout', 15)
MAX_RETRIES = REQUEST_CONFIG.get('max_retries', 3)
RETRY_BASE_DELAY = REQUEST_CONFIG.get('retry_base_delay', 1.0)
RETRY_MAX_DELAY = REQUEST_CONFIG.get('retry_max_delay', 10.0)
CONCURRENT_LIMIT = REQUEST_CONFIG.get('concurrent_limit', 10)

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
    
    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT)
            self._session = aiohttp.ClientSession(headers=self.headers, timeout=timeout)
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def fetch(self, url: str, return_json: bool = False) -> Optional[str | dict]:
        async with self._semaphore:
            session = await self.get_session()
            last_error = None
            
            for attempt in range(MAX_RETRIES):
                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            if return_json:
                                text = await response.text()
                                if not text or not text.strip():
                                    logger.warning(f"Empty response from {url}")
                                    return None
                                try:
                                    return json.loads(text)
                                except json.JSONDecodeError as e:
                                    logger.warning(f"Invalid JSON response from {url}: {e}")
                                    logger.debug(f"Response content: {text[:500]}")
                                    return None
                            else:
                                return await response.text()
                        elif response.status == 429:
                            delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                            logger.warning(f"Rate limited on {url}, waiting {delay}s")
                            await asyncio.sleep(delay)
                        elif response.status in (502, 503, 504):
                            delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                            logger.warning(f"HTTP {response.status} for {url}, retrying in {delay}s (attempt {attempt + 1}/{MAX_RETRIES})")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            logger.warning(f"HTTP {response.status} for {url}")
                            return None
                except asyncio.TimeoutError:
                    last_error = "timeout"
                    logger.warning(f"Timeout fetching {url} (attempt {attempt + 1}/{MAX_RETRIES})")
                except aiohttp.ClientError as e:
                    last_error = str(e)
                    logger.warning(f"Client error: {e} (attempt {attempt + 1}/{MAX_RETRIES})")
                except Exception as e:
                    logger.error(f"Unexpected error fetching {url}: {e}")
                    return None
                
                if attempt < MAX_RETRIES - 1:
                    delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                    await asyncio.sleep(delay)
            
            logger.error(f"Failed to fetch {url} after {MAX_RETRIES} attempts: {last_error}")
            return None

http_client = AsyncHTTPClient()

# ============= JOB SITE SCRAPER =============
class JobSiteScraper:
    def __init__(self, seen_jobs_file: str = 'seen_jobs.json'):
        self.seen_jobs_file = seen_jobs_file
        self.seen_jobs = self.load_seen_jobs()
    
    def load_seen_jobs(self) -> set:
        try:
            if os.path.exists(self.seen_jobs_file):
                with open(self.seen_jobs_file, 'r') as f:
                    return set(json.load(f))
            return set()
        except Exception as e:
            logger.error(f"Error loading seen jobs: {e}")
            return set()
    
    def save_seen_jobs(self):
        try:
            with open(self.seen_jobs_file, 'w') as f:
                json.dump(list(self.seen_jobs), f)
            logger.info(f"Saved {len(self.seen_jobs)} seen jobs")
        except Exception as e:
            logger.error(f"Error saving seen jobs: {e}")
    
    def generate_job_id(self, title: str, company: str, url: str) -> str:
        unique_string = f"{title}|{company}|{url}".lower()
        return hashlib.md5(unique_string.encode()).hexdigest()
    
    def matches_keywords(self, job: dict) -> bool:
        searchable = f"{job.get('title', '')} {job.get('company', '')} {job.get('description', '')}".lower()
        return any(kw in searchable for kw in SEARCH_KEYWORDS)
    
    def is_new_job(self, job_id: str) -> bool:
        return job_id not in self.seen_jobs
    
    def mark_as_seen(self, job_id: str):
        self.seen_jobs.add(job_id)
    
    def parse_html(self, html: str) -> BeautifulSoup:
        try:
            return BeautifulSoup(html, 'lxml')
        except Exception:
            return BeautifulSoup(html, 'html.parser')

    # ============= API SCRAPERS (Custom - unique response structures) =============
    async def scrape_remoteok_api(self) -> list[dict]:
        jobs = []
        site_name = "RemoteOK-API"
        try:
            data = await http_client.fetch("https://remoteok.com/api", return_json=True)
            if not data or not isinstance(data, list):
                health_tracker.record_failure(site_name, "Invalid response")
                return jobs
            
            for item in data[1:51]:
                if not isinstance(item, dict):
                    continue
                title = item.get('position', '')
                company = item.get('company', '')
                url = item.get('url', '')
                
                if not title or not url:
                    continue
                
                job = {'title': title, 'company': company, 'url': url, 'source': site_name, 'description': item.get('description', '')}
                job_id = self.generate_job_id(title, company, url)
                if self.is_new_job(job_id) and self.matches_keywords(job):
                    job['id'] = job_id
                    jobs.append(job)
                    self.mark_as_seen(job_id)
            
            health_tracker.record_success(site_name, len(jobs))
            logger.info(f"{site_name}: Found {len(jobs)} new matching jobs")
        except Exception as e:
            health_tracker.record_failure(site_name, str(e))
            logger.error(f"{site_name} error: {e}")
        return jobs

    async def scrape_remotive_api(self) -> list[dict]:
        jobs = []
        site_name = "Remotive"
        try:
            data = await http_client.fetch("https://remotive.com/api/remote-jobs?category=software-dev", return_json=True)
            if not data or 'jobs' not in data:
                health_tracker.record_failure(site_name, "Invalid response")
                return jobs
            
            for item in data['jobs'][:30]:
                title = item.get('title', '')
                company = item.get('company_name', '')
                url = item.get('url', '')
                
                if not title or not url:
                    continue
                
                job = {'title': title, 'company': company, 'url': url, 'source': site_name, 'description': item.get('description', '')}
                job_id = self.generate_job_id(title, company, url)
                if self.is_new_job(job_id) and self.matches_keywords(job):
                    job['id'] = job_id
                    jobs.append(job)
                    self.mark_as_seen(job_id)
            
            health_tracker.record_success(site_name, len(jobs))
            logger.info(f"{site_name}: Found {len(jobs)} new matching jobs")
        except Exception as e:
            health_tracker.record_failure(site_name, str(e))
            logger.error(f"{site_name} error: {e}")
        return jobs

    async def scrape_adzuna(self) -> list[dict]:
        jobs = []
        site_name = "Adzuna"
        
        if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
            logger.debug(f"{site_name}: Skipped (no credentials)")
            return jobs
        
        try:
            adzuna_config = CONFIG.get('adzuna', {})
            countries = adzuna_config.get('countries', ['us'])
            results_per_page = adzuna_config.get('results_per_page', 20)
            
            for country in countries:
                for keyword in SEARCH_KEYWORDS[:3]:
                    encoded_keyword = quote_plus(keyword)
                    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1?app_id={ADZUNA_APP_ID}&app_key={ADZUNA_APP_KEY}&results_per_page={results_per_page}&what={encoded_keyword}"
                    
                    data = await http_client.fetch(url, return_json=True)
                    
                    if data is None:
                        logger.warning(f"{site_name}: No response for keyword '{keyword}' in {country}")
                        continue
                    
                    if isinstance(data, str):
                        logger.warning(f"{site_name}: Got HTML instead of JSON - check API credentials")
                        health_tracker.record_failure(site_name, "Invalid API credentials or rate limited")
                        return jobs
                    
                    if 'error' in data:
                        error_msg = data.get('error', {}).get('message', str(data.get('error')))
                        logger.warning(f"{site_name}: API error - {error_msg}")
                        continue
                    
                    if 'results' not in data:
                        logger.debug(f"{site_name}: No results for keyword '{keyword}'")
                        continue
                    
                    for item in data['results']:
                        title = item.get('title', '')
                        company = item.get('company', {}).get('display_name', '')
                        job_url = item.get('redirect_url', '')
                        adzuna_id = item.get('id', '')
                        
                        if not title or not job_url:
                            continue
                        
                        job = {'title': title, 'company': company, 'url': job_url, 'source': site_name}
                        job_id = f"adzuna_{adzuna_id}" if adzuna_id else self.generate_job_id(title, company, job_url)
                        if self.is_new_job(job_id) and self.matches_keywords(job):
                            job['id'] = job_id
                            jobs.append(job)
                            self.mark_as_seen(job_id)
            
            health_tracker.record_success(site_name, len(jobs))
            logger.info(f"{site_name}: Found {len(jobs)} new matching jobs")
        except Exception as e:
            health_tracker.record_failure(site_name, str(e))
            logger.error(f"{site_name} error: {e}")
        return jobs

    # ============= GOOGLE CUSTOM SEARCH API =============
    async def scrape_google_search(self) -> list[dict]:
        """Search for jobs using Google Custom Search API."""
        jobs = []
        site_name = "GoogleSearch"
        
        if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
            logger.debug(f"{site_name}: Skipped (no credentials)")
            return jobs
        
        google_config = load_google_search_config()
        settings = google_config.get('settings', {})
        
        if not settings.get('enabled', False):
            logger.debug(f"{site_name}: Disabled in config")
            return jobs
        
        keywords = google_config.get('keywords', [])
        sites = google_config.get('sites', [])
        max_results = settings.get('max_results_per_query', 10)
        date_restrict = settings.get('date_restrict', 'w1')
        
        if not keywords or not sites:
            logger.warning(f"{site_name}: No keywords or sites configured")
            return jobs
        
        try:
            total_queries = 0
            
            for site_entry in sites:
                domain = site_entry.get('domain', '')
                source_name = site_entry.get('name', domain)
                
                if not domain:
                    continue
                
                for keyword in keywords:
                    query = f'{keyword} site:{domain} remote'
                    
                    url = (
                        f"https://www.googleapis.com/customsearch/v1"
                        f"?key={GOOGLE_API_KEY}"
                        f"&cx={GOOGLE_CSE_ID}"
                        f"&q={query}"
                        f"&num={max_results}"
                        f"&dateRestrict={date_restrict}"
                    )
                    
                    data = await http_client.fetch(url, return_json=True)
                    total_queries += 1
                    
                    if not data:
                        continue
                    
                    if 'error' in data:
                        error_msg = data['error'].get('message', 'Unknown error')
                        logger.warning(f"{site_name}: API error - {error_msg}")
                        continue
                    
                    items = data.get('items', [])
                    
                    for item in items:
                        title = item.get('title', '')
                        job_url = item.get('link', '')
                        snippet = item.get('snippet', '')
                        
                        if not title or not job_url:
                            continue
                        
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
                            'source': f"Google-{source_name}",
                            'description': snippet
                        }
                        job_id = self.generate_job_id(title, company, job_url)
                        
                        if self.is_new_job(job_id) and self.matches_keywords(job):
                            job['id'] = job_id
                            jobs.append(job)
                            self.mark_as_seen(job_id)
                    
                    await asyncio.sleep(0.1)
            
            health_tracker.record_success(site_name, len(jobs))
            logger.info(f"{site_name}: Found {len(jobs)} new jobs from {total_queries} queries")
        except Exception as e:
            health_tracker.record_failure(site_name, str(e))
            logger.error(f"{site_name} error: {e}")
        
        return jobs

    # ============= GENERIC HTML SCRAPER (Config-driven) =============
    def _find_element(self, container, selector: str, fallback_selector: str = None):
        """Find element using CSS selector with fallback support."""
        if selector == "self":
            return container
        
        elem = container.select_one(selector)
        if not elem and fallback_selector:
            elem = container.select_one(fallback_selector)
        return elem
    
    def _extract_text(self, elem) -> str:
        """Extract text from element safely."""
        if elem is None:
            return ''
        return elem.get_text(strip=True)
    
    def _extract_url(self, elem, base_url: str) -> str:
        """Extract URL from element safely."""
        if elem is None:
            return ''
        href = elem.get('href', '')
        if href:
            return urljoin(base_url, href)
        return ''

    async def scrape_html_site(self, site_key: str, site_config: dict) -> list[dict]:
        """Generic HTML scraper that uses YAML config for selectors."""
        jobs = []
        site_name = site_config.get('name', site_key)
        url = site_config.get('url', '')
        max_jobs = site_config.get('max_jobs', 20)
        selectors = site_config.get('selectors', {})
        fallback_selectors = site_config.get('fallback_selectors', {})
        
        if not url:
            health_tracker.record_failure(site_name, "No URL configured")
            return jobs
        
        try:
            html = await http_client.fetch(url)
            if not html:
                health_tracker.record_failure(site_name, "Failed to fetch")
                return jobs
            
            soup = self.parse_html(html)
            base_url = url.rsplit('/', 1)[0] if '/' in url else url
            
            job_selector = selectors.get('job_container', '')
            fallback_job_selector = fallback_selectors.get('job_container', '')
            
            job_containers = soup.select(job_selector)[:max_jobs] if job_selector else []
            if not job_containers and fallback_job_selector:
                job_containers = soup.select(fallback_job_selector)[:max_jobs]
            
            if not job_containers:
                health_tracker.record_failure(site_name, "No job containers found")
                return jobs
            
            seen_urls = set()
            
            for container in job_containers:
                title_selector = selectors.get('title', '')
                fallback_title = fallback_selectors.get('title', '')
                title_elem = self._find_element(container, title_selector, fallback_title)
                
                if title_selector == "self":
                    title = self._extract_text(container)
                    job_url = self._extract_url(container, base_url)
                else:
                    title = self._extract_text(title_elem)
                    
                    link_selector = selectors.get('link', 'a')
                    fallback_link = fallback_selectors.get('link', '')
                    link_elem = self._find_element(container, link_selector, fallback_link)
                    job_url = self._extract_url(link_elem, base_url)
                
                if not title or len(title) < 3 or not job_url:
                    continue
                
                if job_url in seen_urls:
                    continue
                seen_urls.add(job_url)
                
                company_selector = selectors.get('company', '')
                fallback_company = fallback_selectors.get('company', '')
                company_elem = self._find_element(container, company_selector, fallback_company) if company_selector else None
                company = self._extract_text(company_elem)
                
                job = {'title': title, 'company': company, 'url': job_url, 'source': site_name}
                job_id = self.generate_job_id(title, company, job_url)
                
                if self.is_new_job(job_id) and self.matches_keywords(job):
                    job['id'] = job_id
                    jobs.append(job)
                    self.mark_as_seen(job_id)
            
            health_tracker.record_success(site_name, len(jobs))
            logger.info(f"{site_name}: Found {len(jobs)} new matching jobs")
        except Exception as e:
            health_tracker.record_failure(site_name, str(e))
            logger.error(f"{site_name} error: {e}")
        
        return jobs

    async def scrape_all_html_sites(self) -> list[list[dict]]:
        """Scrape all HTML sites from YAML config concurrently."""
        sites_config = CONFIG.get('sites', {})
        tasks = []
        
        for site_key, site_config in sites_config.items():
            if not site_config.get('enabled', True):
                continue
            if site_config.get('type') != 'html':
                continue
            
            tasks.append(self.scrape_html_site(site_key, site_config))
        
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def scrape_all_sites(self) -> list[dict]:
        logger.info(f"Starting concurrent scrape with keywords: {SEARCH_KEYWORDS}")
        
        api_tasks = [
            self.scrape_remoteok_api(),
            self.scrape_remotive_api(),
            self.scrape_adzuna(),
            self.scrape_google_search(),
        ]
        
        html_task = self.scrape_all_html_sites()
        
        all_results = await asyncio.gather(*api_tasks, html_task, return_exceptions=True)
        
        all_jobs = []
        for result in all_results:
            if isinstance(result, Exception):
                logger.error(f"Task failed: {result}")
            elif isinstance(result, list):
                for item in result:
                    if isinstance(item, Exception):
                        logger.error(f"HTML scraper failed: {item}")
                    elif isinstance(item, list):
                        all_jobs.extend(item)
                    elif isinstance(item, dict):
                        all_jobs.append(item)
        
        logger.info(f"Total new matching jobs: {len(all_jobs)}")
        logger.info(health_tracker.get_summary())
        return all_jobs

# ============= TELEGRAM NOTIFICATION =============
async def send_telegram_notification(jobs: list[dict]) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured")
        return False
    
    if not jobs:
        logger.info("No new jobs to notify")
        return True
    
    try:
        session = await http_client.get_session()
        
        header = f"🔔 *{len(jobs)} New Job(s) Found!*\n"
        header += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        header += f"🔍 Keywords: {', '.join(SEARCH_KEYWORDS[:3])}\n"
        header += "─" * 30 + "\n\n"
        
        messages = [header]
        current_message = header
        
        for i, job in enumerate(jobs, 1):
            title = job.get('title', 'Unknown')[:100]
            company = job.get('company', 'Unknown')[:50]
            url = job.get('url', '')
            source = job.get('source', 'Unknown')
            
            job_text = f"*{i}. {title}*\n"
            job_text += f"🏢 {company}\n" if company else ""
            job_text += f"🌐 {source}\n"
            job_text += f"🔗 [Apply Here]({url})\n\n"
            
            if len(current_message) + len(job_text) > 4000:
                messages.append(current_message)
                current_message = job_text
            else:
                current_message += job_text
        
        if current_message and current_message != header:
            messages.append(current_message)
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        
        for msg in messages:
            payload = {
                'chat_id': TELEGRAM_CHAT_ID,
                'text': msg,
                'parse_mode': 'Markdown',
                'disable_web_page_preview': True
            }
            
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Telegram API error: {error_text}")
                    return False
            
            await asyncio.sleep(0.5)
        
        logger.info(f"Successfully sent {len(messages)} Telegram message(s)")
        return True
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")
        return False

# ============= CLI ARGUMENT PARSING =============
def parse_args():
    parser = argparse.ArgumentParser(
        description='Job Monitor Bot - Scrapes job sites and sends Telegram notifications'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Test mode: scrape sites but skip Telegram notifications and seen_jobs.json updates. Prints detailed report of working/failed sites.'
    )
    parser.add_argument(
        '--google-only',
        action='store_true',
        help='Only run Google Custom Search scraper (useful for testing Google API setup)'
    )
    parser.add_argument(
        '--adzuna-only',
        action='store_true',
        help='Only run Adzuna API scraper (useful for testing Adzuna API setup)'
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
async def main(dry_run: bool = False, google_only: bool = False, adzuna_only: bool = False):
    logger.info("=" * 50)
    logger.info("Job Monitor Bot Starting")
    if dry_run:
        logger.info("🧪 DRY RUN MODE - No notifications, no seen_jobs.json updates")
    if google_only:
        logger.info("🔍 GOOGLE ONLY MODE - Only running Google Custom Search")
    if adzuna_only:
        logger.info("💼 ADZUNA ONLY MODE - Only running Adzuna API")
    logger.info(f"Search keywords: {SEARCH_KEYWORDS}")
    logger.info(f"Concurrent limit: {CONCURRENT_LIMIT}")
    logger.info("=" * 50)
    
    scraper = JobSiteScraper()
    
    try:
        start_time = datetime.now()
        if google_only:
            new_jobs = await scraper.scrape_google_search()
        elif adzuna_only:
            new_jobs = await scraper.scrape_adzuna()
        else:
            new_jobs = await scraper.scrape_all_sites()
        elapsed = (datetime.now() - start_time).total_seconds()
        
        logger.info(f"Scraping completed in {elapsed:.2f} seconds")
        
        if dry_run:
            print_dry_run_report(new_jobs)
        else:
            if new_jobs:
                logger.info(f"Found {len(new_jobs)} new matching jobs")
                await send_telegram_notification(new_jobs)
            else:
                logger.info("No new matching jobs found")
            
            scraper.save_seen_jobs()
        
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise
    finally:
        await http_client.close()
    
    logger.info("Job Monitor Bot Finished")

if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(dry_run=args.dry_run, google_only=args.google_only, adzuna_only=args.adzuna_only))
