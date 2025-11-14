"""
Job Site Monitoring Bot - GitHub Actions Version
Checks multiple job sites for new postings and sends notifications
"""

import requests
from bs4 import BeautifulSoup
import json
import hashlib
import os
from datetime import datetime

# ============= CONFIGURATION FROM ENVIRONMENT =============
# These will be set as GitHub Secrets
SEARCH_KEYWORDS = os.getenv('SEARCH_KEYWORDS', 'python developer,software engineer,backend developer').split(',')
SEARCH_KEYWORDS = [kw.strip() for kw in SEARCH_KEYWORDS]

NOTIFICATION_METHOD = os.getenv('NOTIFICATION_METHOD', 'telegram')  # telegram or email
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8083956471:AAGNkFh_x_YGVJTSTOHExJ4qrzUfSnapYUk')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '1010499402')

EMAIL_SENDER = os.getenv('EMAIL_SENDER', '')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', '')
EMAIL_RECIPIENT = os.getenv('EMAIL_RECIPIENT', '')

# ============= JOB SITE SCRAPERS =============

class JobSiteScraper:
    def __init__(self, seen_jobs_file='seen_jobs.json'):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
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
    
    def search_remoteok(self, keywords):
        """Scrape Remote OK for remote jobs"""
        jobs = []
        try:
            print("Searching RemoteOK...")
            url = "https://remoteok.com/api"
            response = requests.get(url, headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 1:
                    data = data[1:]  # Skip metadata
                
                for job in data[:50]:  # Check recent 50 jobs
                    if not isinstance(job, dict):
                        continue
                    
                    title = job.get('position', '')
                    tags = job.get('tags', [])
                    
                    # Check if job matches keywords
                    search_text = f"{title} {' '.join(tags)}".lower()
                    if any(kw.lower() in search_text for kw in keywords):
                        job_data = {
                            'title': title,
                            'company': job.get('company', 'N/A'),
                            'location': job.get('location', 'Remote'),
                            'url': f"https://remoteok.com/remote-jobs/{job.get('slug', '')}",
                            'posted': job.get('date', 'Recent'),
                            'tags': ', '.join(tags[:5]) if tags else 'N/A',
                            'source': 'RemoteOK'
                        }
                        
                        job_id = self.generate_job_id(
                            job_data['title'], 
                            job_data['company'], 
                            job_data['url']
                        )
                        
                        if job_id not in self.seen_jobs:
                            jobs.append(job_data)
                            self.seen_jobs.add(job_id)
                            print(f"  ✓ New: {job_data['title']} at {job_data['company']}")
                
                print(f"RemoteOK: Found {len(jobs)} new jobs")
            else:
                print(f"RemoteOK returned status {response.status_code}")
        except Exception as e:
            print(f"Error scraping RemoteOK: {e}")
        
        return jobs
    
    def search_weworkremotely(self, keywords):
        """Scrape We Work Remotely"""
        jobs = []
        try:
            print("Searching WeWorkRemotely...")
            url = "https://weworkremotely.com/categories/remote-programming-jobs"
            response = requests.get(url, headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                job_listings = soup.find_all('li', class_='feature')
                
                for job in job_listings[:20]:  # Check recent 20 jobs
                    try:
                        title_elem = job.find('span', class_='title')
                        company_elem = job.find('span', class_='company')
                        link_elem = job.find('a')
                        
                        if title_elem and company_elem and link_elem:
                            title = title_elem.text.strip()
                            company = company_elem.text.strip()
                            
                            # Check if matches keywords
                            if any(kw.lower() in title.lower() for kw in keywords):
                                job_data = {
                                    'title': title,
                                    'company': company,
                                    'location': 'Remote',
                                    'url': f"https://weworkremotely.com{link_elem['href']}",
                                    'posted': 'Recent',
                                    'tags': 'Remote',
                                    'source': 'WeWorkRemotely'
                                }
                                
                                job_id = self.generate_job_id(
                                    job_data['title'],
                                    job_data['company'],
                                    job_data['url']
                                )
                                
                                if job_id not in self.seen_jobs:
                                    jobs.append(job_data)
                                    self.seen_jobs.add(job_id)
                                    print(f"  ✓ New: {job_data['title']} at {job_data['company']}")
                    except Exception as e:
                        continue
                
                print(f"WeWorkRemotely: Found {len(jobs)} new jobs")
            else:
                print(f"WeWorkRemotely returned status {response.status_code}")
        except Exception as e:
            print(f"Error scraping WeWorkRemotely: {e}")
        
        return jobs
    
    def search_remotive(self, keywords):
        """Scrape Remotive.io"""
        jobs = []
        try:
            print("Searching Remotive...")
            url = "https://remotive.com/api/remote-jobs?category=software-dev"
            response = requests.get(url, headers=self.headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                job_list = data.get('jobs', [])
                
                for job in job_list[:30]:  # Check recent 30 jobs
                    title = job.get('title', '')
                    tags = job.get('tags', [])
                    
                    search_text = f"{title} {' '.join(tags)}".lower()
                    if any(kw.lower() in search_text for kw in keywords):
                        job_data = {
                            'title': title,
                            'company': job.get('company_name', 'N/A'),
                            'location': job.get('candidate_required_location', 'Remote'),
                            'url': job.get('url', ''),
                            'posted': job.get('publication_date', 'Recent'),
                            'tags': ', '.join(tags[:5]) if tags else 'N/A',
                            'source': 'Remotive'
                        }
                        
                        job_id = self.generate_job_id(
                            job_data['title'],
                            job_data['company'],
                            job_data['url']
                        )
                        
                        if job_id not in self.seen_jobs:
                            jobs.append(job_data)
                            self.seen_jobs.add(job_id)
                            print(f"  ✓ New: {job_data['title']} at {job_data['company']}")
                
                print(f"Remotive: Found {len(jobs)} new jobs")
            else:
                print(f"Remotive returned status {response.status_code}")
        except Exception as e:
            print(f"Error scraping Remotive: {e}")
        
        return jobs
    
    def search_all_sites(self, keywords):
        """Search all configured job sites"""
        all_jobs = []
        
        all_jobs.extend(self.search_remoteok(keywords))
        all_jobs.extend(self.search_weworkremotely(keywords))
        all_jobs.extend(self.search_remotive(keywords))
        
        self.save_seen_jobs()
        return all_jobs

# ============= NOTIFICATION HANDLERS =============

def send_telegram_notification(jobs):
    """Send notification via Telegram"""
    if not jobs or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    
    try:
        message = f"🔔 *{len(jobs)} New Job Opportunities Found!*\n\n"
        
        for i, job in enumerate(jobs[:10], 1):  # Limit to 10 jobs per message
            message += f"*{i}. {job['title']}*\n"
            message += f"🏢 {job['company']}\n"
            message += f"📍 {job['location']}\n"
            message += f"🏷️ {job['tags']}\n"
            message += f"🔗 {job['url']}\n"
            message += f"📅 {job['posted']}\n"
            message += f"📱 Source: {job['source']}\n\n"
        
        if len(jobs) > 10:
            message += f"\n_...and {len(jobs) - 10} more jobs!_"
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            print(f"✓ Telegram notification sent ({len(jobs)} jobs)")
            return True
        else:
            print(f"✗ Telegram notification failed: {response.text}")
            return False
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
              body {{ font-family: Arial, sans-serif; }}
              .job {{ margin: 20px 0; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }}
              .job h3 {{ margin: 0 0 10px 0; color: #333; }}
              .job p {{ margin: 5px 0; color: #666; }}
              .button {{ background: #007bff; color: white; padding: 10px 20px; 
                        text-decoration: none; border-radius: 5px; display: inline-block; }}
            </style>
          </head>
          <body>
            <h2>🔔 {len(jobs)} New Job Opportunities Found!</h2>
        """
        
        for job in jobs:
            html += f"""
            <div class="job">
                <h3>{job['title']}</h3>
                <p><strong>Company:</strong> {job['company']}</p>
                <p><strong>Location:</strong> {job['location']}</p>
                <p><strong>Tags:</strong> {job['tags']}</p>
                <p><strong>Posted:</strong> {job['posted']}</p>
                <p><strong>Source:</strong> {job['source']}</p>
                <p><a href="{job['url']}" class="button">View Job</a></p>
            </div>
            """
        
        html += """
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
    print("="*60)
    print("JOB MONITORING BOT - GitHub Actions")
    print("="*60)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Keywords: {', '.join(SEARCH_KEYWORDS)}")
    print(f"Notification: {NOTIFICATION_METHOD}")
    print("="*60)
    print()
    
    # Initialize scraper
    scraper = JobSiteScraper()
    
    # Search for jobs
    print("Starting job search...\n")
    new_jobs = scraper.search_all_sites(SEARCH_KEYWORDS)
    
    print("\n" + "="*60)
    if new_jobs:
        print(f"✓ FOUND {len(new_jobs)} NEW JOB(S)!")
        print("="*60)
        
        # Print summary
        for i, job in enumerate(new_jobs, 1):
            print(f"{i}. {job['title']} at {job['company']} ({job['source']})")
        
        # Send notifications
        print("\nSending notifications...")
        if NOTIFICATION_METHOD.lower() == 'telegram':
            send_telegram_notification(new_jobs)
        elif NOTIFICATION_METHOD.lower() == 'email':
            send_email_notification(new_jobs)
        else:
            print("No valid notification method configured")
    else:
        print("No new jobs found this time.")
        print("="*60)
    
    print("\n✓ Job search completed successfully!")

if __name__ == "__main__":
    main()
