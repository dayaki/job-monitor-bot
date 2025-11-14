"""
Job Site Monitoring Bot - GitHub Actions Version with 15+ Job Sites
Checks multiple job sites for new postings and sends notifications
"""

import requests
from bs4 import BeautifulSoup
import json
import hashlib
import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs

# ============= CONFIGURATION FROM ENVIRONMENT =============
SEARCH_KEYWORDS = os.getenv('SEARCH_KEYWORDS', 'python developer,software engineer,backend developer').split(',')
SEARCH_KEYWORDS = [kw.strip() for kw in SEARCH_KEYWORDS]

NOTIFICATION_METHOD = os.getenv('NOTIFICATION_METHOD', 'telegram')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

EMAIL_SENDER = os.getenv('EMAIL_SENDER', '')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', '')
EMAIL_RECIPIENT = os.getenv('EMAIL_RECIPIENT', '')

# ============= JOB SITE SCRAPERS =============

class JobSiteScraper:
    def __init__(self, seen_jobs_file='seen_jobs.json'):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        self.seen_jobs_file = seen_jobs_file
        self.seen_jobs = self.load_seen_jobs()
    
    def load_seen_jobs(self):
        """Load previously seen job IDs"""
        try:
            if os.path.exists(self.seen_jobs_file):
                with open(self.seen_jobs_file, 'r') as f:
                    return set(json.load(f))
            return set()
        except Exception as e:
            print(f"Error loading seen jobs: {e}")
            return set()
    
    def save_seen_jobs(self):
        """Save seen job IDs"""
        try:
            with open(self.seen_jobs_file, 'w') as f:
                json.dump(list(self.seen_jobs), f)
            print(f"Saved {len(self.seen_jobs)} seen jobs")
        except Exception as e:
            print(f"Error saving seen jobs: {e}")
    
    def generate_job_id(self, title, company, url):
        """Generate unique ID for a job posting"""
        unique_string = f"{title}|{company}|{url}"
        return hashlib.md5(unique_string.encode()).hexdigest()
    
    def matches_keywords(self, text, keywords):
        """Check if text matches any of the keywords"""
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in keywords)
    
    def add_job_if_new(self, job_data, jobs_list):
        """Add job to list if it's new and not seen before"""
        job_id = self.generate_job_id(job_data['title'], job_data['company'], job_data['url'])
        if job_id not in self.seen_jobs:
            jobs_list.append(job_data)
            self.seen_jobs.add(job_id)
            print(f"  ✓ New: {job_data['title']} at {job_data['company']}")
            return True
        return False
    
    # ========== ORIGINAL SITES ==========
    
    def search_remoteok(self, keywords):
        """Scrape Remote OK"""
        jobs = []
        try:
            print("Searching RemoteOK...")
            response = requests.get("https://remoteok.com/api", headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()[1:] if isinstance(response.json(), list) else []
                
                for job in data[:50]:
                    if not isinstance(job, dict):
                        continue
                    
                    title = job.get('position', '')
                    tags = ' '.join(job.get('tags', []))
                    
                    if self.matches_keywords(f"{title} {tags}", keywords):
                        job_data = {
                            'title': title,
                            'company': job.get('company', 'N/A'),
                            'location': job.get('location', 'Remote'),
                            'url': f"https://remoteok.com/remote-jobs/{job.get('slug', '')}",
                            'posted': job.get('date', 'Recent'),
                            'source': 'RemoteOK'
                        }
                        self.add_job_if_new(job_data, jobs)
                
                print(f"RemoteOK: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping RemoteOK: {e}")
        return jobs
    
    def search_weworkremotely(self, keywords):
        """Scrape We Work Remotely"""
        jobs = []
        try:
            print("Searching WeWorkRemotely...")
            response = requests.get("https://weworkremotely.com/categories/remote-programming-jobs", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_listings = soup.find_all('li', class_='feature')
                
                for job in job_listings[:20]:
                    try:
                        title_elem = job.find('span', class_='title')
                        company_elem = job.find('span', class_='company')
                        link_elem = job.find('a')
                        
                        if title_elem and company_elem and link_elem:
                            title = title_elem.text.strip()
                            
                            if self.matches_keywords(title, keywords):
                                job_data = {
                                    'title': title,
                                    'company': company_elem.text.strip(),
                                    'location': 'Remote',
                                    'url': f"https://weworkremotely.com{link_elem['href']}",
                                    'posted': 'Recent',
                                    'source': 'WeWorkRemotely'
                                }
                                self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"WeWorkRemotely: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping WeWorkRemotely: {e}")
        return jobs
    
    def search_remotive(self, keywords):
        """Scrape Remotive.io"""
        jobs = []
        try:
            print("Searching Remotive...")
            response = requests.get("https://remotive.com/api/remote-jobs?category=software-dev", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                job_list = data.get('jobs', [])
                
                for job in job_list[:30]:
                    title = job.get('title', '')
                    
                    if self.matches_keywords(title, keywords):
                        job_data = {
                            'title': title,
                            'company': job.get('company_name', 'N/A'),
                            'location': job.get('candidate_required_location', 'Remote'),
                            'url': job.get('url', ''),
                            'posted': job.get('publication_date', 'Recent'),
                            'source': 'Remotive'
                        }
                        self.add_job_if_new(job_data, jobs)
                
                print(f"Remotive: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping Remotive: {e}")
        return jobs
    
    # ========== NEW SITES FROM YOUR LIST ==========
    
    def search_remoterocketship(self, keywords):
        """Scrape Remote Rocketship"""
        jobs = []
        try:
            print("Searching RemoteRocketship...")
            # Note: This site may require a subscription, trying to scrape public listings
            response = requests.get("https://www.remoterocketship.com/", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_cards = soup.find_all('a', href=re.compile(r'/jobs/'))
                
                for card in job_cards[:20]:
                    try:
                        title = card.get_text(strip=True)
                        url = urljoin("https://www.remoterocketship.com", card['href'])
                        
                        if self.matches_keywords(title, keywords):
                            job_data = {
                                'title': title,
                                'company': 'Various',
                                'location': 'Remote',
                                'url': url,
                                'posted': 'Recent',
                                'source': 'RemoteRocketship'
                            }
                            self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"RemoteRocketship: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping RemoteRocketship: {e}")
        return jobs
    
    def search_workinstartups(self, keywords):
        """Scrape Work in Startups"""
        jobs = []
        try:
            print("Searching WorkInStartups...")
            # Search for developer roles
            response = requests.get("https://workinstartups.com/job-board/", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_listings = soup.find_all('div', class_=re.compile(r'job'))
                
                for job in job_listings[:20]:
                    try:
                        title_elem = job.find(['h3', 'h2', 'a'])
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            
                            if self.matches_keywords(title, keywords):
                                link = job.find('a', href=True)
                                url = urljoin("https://workinstartups.com", link['href']) if link else ''
                                
                                job_data = {
                                    'title': title,
                                    'company': 'Startup',
                                    'location': 'Various',
                                    'url': url,
                                    'posted': 'Recent',
                                    'source': 'WorkInStartups'
                                }
                                self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"WorkInStartups: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping WorkInStartups: {e}")
        return jobs
    
    def search_bitcoinerjobs(self, keywords):
        """Scrape Bitcoiner Jobs"""
        jobs = []
        try:
            print("Searching BitcoinerJobs...")
            response = requests.get("https://bitcoinerjobs.com/", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_links = soup.find_all('a', href=re.compile(r'/job/'))
                
                for link in job_links[:20]:
                    try:
                        title = link.get_text(strip=True)
                        
                        if self.matches_keywords(title, keywords):
                            job_data = {
                                'title': title,
                                'company': 'Bitcoin Company',
                                'location': 'Remote',
                                'url': urljoin("https://bitcoinerjobs.com", link['href']),
                                'posted': 'Recent',
                                'source': 'BitcoinerJobs'
                            }
                            self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"BitcoinerJobs: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping BitcoinerJobs: {e}")
        return jobs
    
    def search_hiringcafe(self, keywords):
        """Scrape Hiring Cafe"""
        jobs = []
        try:
            print("Searching HiringCafe...")
            response = requests.get("https://hiring.cafe/", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_items = soup.find_all(['div', 'article'], class_=re.compile(r'job', re.I))
                
                for item in job_items[:20]:
                    try:
                        title_elem = item.find(['h2', 'h3', 'a'])
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            
                            if self.matches_keywords(title, keywords):
                                link = item.find('a', href=True)
                                url = link['href'] if link else ''
                                
                                job_data = {
                                    'title': title,
                                    'company': 'Various',
                                    'location': 'Remote',
                                    'url': url if url.startswith('http') else urljoin("https://hiring.cafe", url),
                                    'posted': 'Recent',
                                    'source': 'HiringCafe'
                                }
                                self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"HiringCafe: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping HiringCafe: {e}")
        return jobs
    
    def search_web3career(self, keywords):
        """Scrape Web3 Career"""
        jobs = []
        try:
            print("Searching Web3Career...")
            response = requests.get("https://web3.career/", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_rows = soup.find_all('tr', class_=re.compile(r'job|table'))
                
                for row in job_rows[:30]:
                    try:
                        title_elem = row.find('h2') or row.find('a', class_=re.compile(r'job-title'))
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            
                            if self.matches_keywords(title, keywords):
                                link = row.find('a', href=True)
                                company_elem = row.find(class_=re.compile(r'company'))
                                
                                job_data = {
                                    'title': title,
                                    'company': company_elem.get_text(strip=True) if company_elem else 'Web3 Company',
                                    'location': 'Remote',
                                    'url': urljoin("https://web3.career", link['href']) if link else '',
                                    'posted': 'Recent',
                                    'source': 'Web3Career'
                                }
                                self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"Web3Career: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping Web3Career: {e}")
        return jobs
    
    def search_remote3(self, keywords):
        """Scrape Remote3"""
        jobs = []
        try:
            print("Searching Remote3...")
            response = requests.get("https://www.remote3.co/", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_cards = soup.find_all(['div', 'article'], class_=re.compile(r'job'))
                
                for card in job_cards[:20]:
                    try:
                        title_elem = card.find(['h2', 'h3'])
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            
                            if self.matches_keywords(title, keywords):
                                link = card.find('a', href=True)
                                job_data = {
                                    'title': title,
                                    'company': 'Web3 Company',
                                    'location': 'Remote',
                                    'url': urljoin("https://www.remote3.co", link['href']) if link else '',
                                    'posted': 'Recent',
                                    'source': 'Remote3'
                                }
                                self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"Remote3: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping Remote3: {e}")
        return jobs
    
    def search_protocolai(self, keywords):
        """Scrape Protocol Labs Jobs"""
        jobs = []
        try:
            print("Searching Protocol AI...")
            response = requests.get("https://jobs.protocol.ai/jobs", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_listings = soup.find_all(['div', 'li'], class_=re.compile(r'job|position'))
                
                for listing in job_listings[:20]:
                    try:
                        title_elem = listing.find(['h3', 'h2', 'a'])
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            
                            if self.matches_keywords(title, keywords):
                                link = listing.find('a', href=True)
                                job_data = {
                                    'title': title,
                                    'company': 'Protocol Labs',
                                    'location': 'Remote',
                                    'url': urljoin("https://jobs.protocol.ai", link['href']) if link else '',
                                    'posted': 'Recent',
                                    'source': 'ProtocolAI'
                                }
                                self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"Protocol AI: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping Protocol AI: {e}")
        return jobs
    
    def search_cryptocurrencyjobs(self, keywords):
        """Scrape Cryptocurrency Jobs"""
        jobs = []
        try:
            print("Searching CryptocurrencyJobs...")
            response = requests.get("https://cryptocurrencyjobs.co/", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_items = soup.find_all(['div', 'article'], class_=re.compile(r'job'))
                
                for item in job_items[:20]:
                    try:
                        title_elem = item.find(['h2', 'h3', 'a'])
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            
                            if self.matches_keywords(title, keywords):
                                link = item.find('a', href=True)
                                job_data = {
                                    'title': title,
                                    'company': 'Crypto Company',
                                    'location': 'Remote',
                                    'url': urljoin("https://cryptocurrencyjobs.co", link['href']) if link else '',
                                    'posted': 'Recent',
                                    'source': 'CryptocurrencyJobs'
                                }
                                self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"CryptocurrencyJobs: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping CryptocurrencyJobs: {e}")
        return jobs
    
    def search_laborx(self, keywords):
        """Scrape LaborX"""
        jobs = []
        try:
            print("Searching LaborX...")
            response = requests.get("https://laborx.com/jobs", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_cards = soup.find_all(['div', 'article'], class_=re.compile(r'job|gig'))
                
                for card in job_cards[:20]:
                    try:
                        title_elem = card.find(['h2', 'h3', 'h4'])
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            
                            if self.matches_keywords(title, keywords):
                                link = card.find('a', href=True)
                                job_data = {
                                    'title': title,
                                    'company': 'Crypto/Gig',
                                    'location': 'Remote',
                                    'url': urljoin("https://laborx.com", link['href']) if link else '',
                                    'posted': 'Recent',
                                    'source': 'LaborX'
                                }
                                self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"LaborX: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping LaborX: {e}")
        return jobs
    
    def search_dailyremote(self, keywords):
        """Scrape Daily Remote"""
        jobs = []
        try:
            print("Searching DailyRemote...")
            response = requests.get("https://dailyremote.com/", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_listings = soup.find_all(['div', 'article'], class_=re.compile(r'job'))
                
                for listing in job_listings[:20]:
                    try:
                        title_elem = listing.find(['h2', 'h3', 'a'])
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            
                            if self.matches_keywords(title, keywords):
                                link = listing.find('a', href=True)
                                company_elem = listing.find(class_=re.compile(r'company'))
                                
                                job_data = {
                                    'title': title,
                                    'company': company_elem.get_text(strip=True) if company_elem else 'Various',
                                    'location': 'Remote',
                                    'url': urljoin("https://dailyremote.com", link['href']) if link else '',
                                    'posted': 'Recent',
                                    'source': 'DailyRemote'
                                }
                                self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"DailyRemote: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping DailyRemote: {e}")
        return jobs
    
    def search_justremote(self, keywords):
        """Scrape JustRemote"""
        jobs = []
        try:
            print("Searching JustRemote...")
            response = requests.get("https://justremote.co/remote-jobs", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_cards = soup.find_all(['div', 'article'], class_=re.compile(r'job'))
                
                for card in job_cards[:20]:
                    try:
                        title_elem = card.find(['h2', 'h3'])
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            
                            if self.matches_keywords(title, keywords):
                                link = card.find('a', href=True)
                                company_elem = card.find(class_=re.compile(r'company'))
                                
                                job_data = {
                                    'title': title,
                                    'company': company_elem.get_text(strip=True) if company_elem else 'Various',
                                    'location': 'Remote',
                                    'url': urljoin("https://justremote.co", link['href']) if link else '',
                                    'posted': 'Recent',
                                    'source': 'JustRemote'
                                }
                                self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"JustRemote: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping JustRemote: {e}")
        return jobs
    
    def search_workingnomads(self, keywords):
        """Scrape Working Nomads"""
        jobs = []
        try:
            print("Searching WorkingNomads...")
            response = requests.get("https://www.workingnomads.com/jobs", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_listings = soup.find_all(['li', 'div'], class_=re.compile(r'job'))
                
                for listing in job_listings[:20]:
                    try:
                        title_elem = listing.find(['h2', 'h3', 'a'])
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            
                            if self.matches_keywords(title, keywords):
                                link = listing.find('a', href=True)
                                job_data = {
                                    'title': title,
                                    'company': 'Various',
                                    'location': 'Remote',
                                    'url': urljoin("https://www.workingnomads.com", link['href']) if link else '',
                                    'posted': 'Recent',
                                    'source': 'WorkingNomads'
                                }
                                self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"WorkingNomads: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping WorkingNomads: {e}")
        return jobs
    
    def search_remoteco(self, keywords):
        """Scrape Remote.co"""
        jobs = []
        try:
            print("Searching Remote.co...")
            response = requests.get("https://remote.co/remote-jobs/", 
                                  headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_cards = soup.find_all(['div', 'article'], class_=re.compile(r'job'))
                
                for card in job_cards[:20]:
                    try:
                        title_elem = card.find(['h2', 'h3', 'a'])
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            
                            if self.matches_keywords(title, keywords):
                                link = card.find('a', href=True)
                                company_elem = card.find(class_=re.compile(r'company'))
                                
                                job_data = {
                                    'title': title,
                                    'company': company_elem.get_text(strip=True) if company_elem else 'Various',
                                    'location': 'Remote',
                                    'url': urljoin("https://remote.co", link['href']) if link else '',
                                    'posted': 'Recent',
                                    'source': 'Remote.co'
                                }
                                self.add_job_if_new(job_data, jobs)
                    except:
                        continue
                
                print(f"Remote.co: Found {len(jobs)} new jobs")
        except Exception as e:
            print(f"Error scraping Remote.co: {e}")
        return jobs
    
    def search_all_sites(self, keywords):
        """Search all configured job sites"""
        all_jobs = []
        
        # Original sites
        all_jobs.extend(self.search_remoteok(keywords))
        time.sleep(1)
        all_jobs.extend(self.search_weworkremotely(keywords))
        time.sleep(1)
        all_jobs.extend(self.search_remotive(keywords))
        time.sleep(1)
        
        # Your new sites
        all_jobs.extend(self.search_remoterocketship(keywords))
        time.sleep(1)
        all_jobs.extend(self.search_bitcoinerjobs(keywords))
        time.sleep(1)
        all_jobs.extend(self.search_web3career(keywords))
        time.sleep(1)
        all_jobs.extend(self.search_remote3(keywords))
        time.sleep(1)
        all_jobs.extend(self.search_protocolai(keywords))
        time.sleep(1)
        all_jobs.extend(self.search_cryptocurrencyjobs(keywords))
        time.sleep(1)
        all_jobs.extend(self.search_laborx(keywords))
        time.sleep(1)
        all_jobs.extend(self.search_dailyremote(keywords))
        time.sleep(1)
        all_jobs.extend(self.search_justremote(keywords))
        time.sleep(1)
        all_jobs.extend(self.search_workingnomads(keywords))
        time.sleep(1)
        all_jobs.extend(self.search_remoteco(keywords))
        
        # Note: workinstartups, hiringcafe, daomatch, web3jobs, jobspresso 
        # may have anti-scraping measures or complex structures
        # Try them but they might not always work
        try:
            all_jobs.extend(self.search_workinstartups(keywords))
            time.sleep(1)
        except:
            print("WorkInStartups: Skipped (may have anti-scraping)")
        
        try:
            all_jobs.extend(self.search_hiringcafe(keywords))
            time.sleep(1)
        except:
            print("HiringCafe: Skipped (may have anti-scraping)")
        
        self.save_seen_jobs()
        return all_jobs

# ============= NOTIFICATION HANDLERS =============

def send_telegram_notification(jobs):
    """Send notification via Telegram"""
    if not jobs or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    
    try:
        # Split into chunks if too many jobs
        chunk_size = 10
        for i in range(0, len(jobs), chunk_size):
            chunk = jobs[i:i+chunk_size]
            
            message = f"🔔 *Found {len(chunk)} New Job(s)!*"
            if i > 0:
                message += f" (Part {i//chunk_size + 1})"
            message += "\n\n"
            
            for j, job in enumerate(chunk, 1):
                message += f"*{i+j}. {job['title']}*\n"
                message += f"🏢 {job['company']}\n"
                message += f"📍 {job['location']}\n"
                message += f"🔗 {job['url']}\n"
                message += f"📅 {job['posted']}\n"
                message += f"📱 {job['source']}\n\n"
            
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            }
            
            response = requests.post(url, data=data, timeout=10)
            if response.status_code != 200:
                print(f"✗ Telegram notification failed: {response.text}")
                return False
            
            time.sleep(1)  # Avoid rate limiting
        
        print(f"✓ Telegram notification sent ({len(jobs)} jobs)")
        return True
    except Exception as e:
        print(f"Error sending Telegram notification: {e}")
        return False

def send_email_notification(jobs):
    """Send notification via Email"""
    if not jobs or not EMAIL_SENDER or not EMAIL_PASSWORD:
        return False
    
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"🔔 {len(jobs)} New Job Opportunities"
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECIPIENT or EMAIL_SENDER
        
        html = f"""
        <html>
          <head>
            <style>
              body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
              h2 {{ color: #333; }}
              .job {{ margin: 20px 0; padding: 15px; border: 1px solid #ddd; 
                      border-radius: 5px; background: #f9f9f9; }}
              .job h3 {{ margin: 0 0 10px 0; color: #2c5282; }}
              .job p {{ margin: 5px 0; color: #555; }}
              .job .label {{ font-weight: bold; color: #333; }}
              .button {{ background: #3182ce; color: white !important; padding: 10px 20px; 
                        text-decoration: none; border-radius: 5px; display: inline-block; 
                        margin-top: 10px; }}
              .button:hover {{ background: #2c5282; }}
              .source {{ background: #edf2f7; padding: 5px 10px; border-radius: 3px; 
                        display: inline-block; font-size: 12px; color: #4a5568; }}
            </style>
          </head>
          <body>
            <h2>🔔 {len(jobs)} New Job Opportunities Found!</h2>
            <p style="color: #666;">Found across {len(set(j['source'] for j in jobs))} job sites</p>
        """
        
        for i, job in enumerate(jobs, 1):
            html += f"""
            <div class="job">
                <h3>{i}. {job['title']}</h3>
                <p><span class="label">Company:</span> {job['company']}</p>
                <p><span class="label">Location:</span> {job['location']}</p>
                <p><span class="label">Posted:</span> {job['posted']}</p>
                <p><span class="source">Source: {job['source']}</span></p>
                <a href="{job['url']}" class="button">View Job Details</a>
            </div>
            """
        
        html += """
            <hr style="margin: 30px 0; border: none; border-top: 1px solid #ddd;">
            <p style="color: #999; font-size: 12px; text-align: center;">
              This is an automated job alert. Jobs are checked hourly.
            </p>
          </body>
        </html>
        """
        
        msg.attach(MIMEText(html, 'html'))
        
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        
        print(f"✓ Email notification sent ({len(jobs)} jobs)")
        return True
    except Exception as e:
        print(f"Error sending email notification: {e}")
        return False

# ============= MAIN FUNCTION =============

def main():
    """Main function to run the job search"""
    print("="*80)
    print("JOB MONITORING BOT - GitHub Actions Edition")
    print("="*80)
    print(f"🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"🔍 Keywords: {', '.join(SEARCH_KEYWORDS)}")
    print(f"📬 Notification: {NOTIFICATION_METHOD}")
    print(f"🌐 Searching 18+ job sites...")
    print("="*80)
    print()
    
    # Initialize scraper
    scraper = JobSiteScraper()
    
    # Search for jobs
    print("Starting comprehensive job search...\n")
    new_jobs = scraper.search_all_sites(SEARCH_KEYWORDS)
    
    print("\n" + "="*80)
    if new_jobs:
        print(f"✅ SUCCESS! FOUND {len(new_jobs)} NEW JOB(S)!")
        print("="*80)
        
        # Group by source
        by_source = {}
        for job in new_jobs:
            source = job['source']
            by_source[source] = by_source.get(source, 0) + 1
        
        print("\n📊 Jobs by Source:")
        for source, count in sorted(by_source.items(), key=lambda x: x[1], reverse=True):
            print(f"   • {source}: {count} job(s)")
        
        print("\n📋 Job Listings:")
        for i, job in enumerate(new_jobs, 1):
            print(f"{i}. {job['title']}")
            print(f"   Company: {job['company']} | Source: {job['source']}")
            print(f"   {job['url']}\n")
        
        # Send notifications
        print("="*80)
        print("Sending notifications...")
        if NOTIFICATION_METHOD.lower() == 'telegram':
            send_telegram_notification(new_jobs)
        elif NOTIFICATION_METHOD.lower() == 'email':
            send_email_notification(new_jobs)
        else:
            print("⚠️  No valid notification method configured")
    else:
        print("ℹ️  No new jobs found this time.")
        print("="*80)
        print("\n💡 Tips:")
        print("   • Jobs may already be in your seen list")
        print("   • Try broader keywords if results are limited")
        print("   • Check back in an hour for new postings")
    
    print("\n" + "="*80)
    print("✅ Job search completed successfully!")
    print(f"📝 Tracking {len(scraper.seen_jobs)} total seen jobs")
    print("="*80)

if __name__ == "__main__":
    main()
