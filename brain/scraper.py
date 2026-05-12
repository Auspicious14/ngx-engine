import os
import asyncio
import httpx
import sqlite3
import re
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

# Configuration for GitHub Storage
RAW_PDF_DIR = "data/raw_pdfs"
DB_PATH = "data/brain_metadata.db"
BASE_URL = "https://ngxgroup.com/exchange/data/corporate-disclosures/"

# Ensure directory exists for Git to track
os.makedirs(RAW_PDF_DIR, exist_ok=True)

class DisclosureSchema(BaseModel):
    company: str
    title: str
    date_submitted: str
    landing_url: str
    pdf_url: Optional[str] = None
    category: str = "General"

class DisclosureScraper:
    def __init__(self):
        # Professional User-Agent to avoid blocks on GitHub runners
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        self._init_db()

    def _init_db(self):
        """Initializes tracking database. While GitHub resets the environment, 
        this helps if you ever run it locally or use a persistent cache."""
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_disclosures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company TEXT,
                    title TEXT,
                    category TEXT,
                    pdf_url TEXT UNIQUE,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def _categorize(self, url: str, title: str) -> str:
        """Tags the disclosure based on URL and Title keywords."""
        text = (url + title).upper()
        if any(k in text for k in ["INSIDER", "DIRECTOR_DEALING"]):
            return "Insider_Dealing"
        if any(k in text for k in ["FINANCIAL", "RESULT", "AUDITED", "UNAUDITED", "AFS"]):
            return "Financial_Result"
        if "DIVIDEND" in text:
            return "Dividend_Announcement"
        return "General_Disclosure"

    def _generate_filename(self, item: DisclosureSchema) -> str:
        """Creates a clean, Git-safe filename."""
        # Remove non-alphanumeric chars to prevent Git commit errors
        clean_company = re.sub(r'[^a-zA-Z0-9]', '_', item.company)
        # Use the unique ID from the doclib URL to prevent overwriting
        url_id = item.pdf_url.split('/')[-1] if item.pdf_url else "unknown"
        return f"{item.category}_{clean_company}_{url_id}"

    async def get_latest_items(self) -> List[DisclosureSchema]:
        """Fetches the main table from the NGX Disclosure page."""
        async with httpx.AsyncClient(headers=self.headers, timeout=30.0, follow_redirects=True) as client:
            try:
                res = await client.get(BASE_URL)
                res.raise_for_status()
                soup = BeautifulSoup(res.text, 'html.parser')
                
                rows = soup.find('table').find_all('tr')[1:] # Skip header
                items = []
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) < 2: continue
                    
                    link_tag = cols[1].find('a')
                    if not link_tag: continue

                    items.append(DisclosureSchema(
                        company=cols[0].text.strip(),
                        title=link_tag.text.strip(),
                        date_submitted=cols[2].text.strip(),
                        landing_url=link_tag['href']
                    ))
                return items
            except Exception as e:
                print(f"❌ Error fetching NGX table: {e}")
                return []

    async def get_pdf_link(self, item: DisclosureSchema) -> Optional[str]:
        """Navigates to the individual disclosure page to grab the doclib URL."""
        async with httpx.AsyncClient(headers=self.headers, timeout=20.0, follow_redirects=True) as client:
            try:
                res = await client.get(item.landing_url)
                soup = BeautifulSoup(res.text, 'html.parser')
                links = [a['href'] for a in soup.find_all('a', href=True) if '.pdf' in a['href'].lower()]
                
                if not links: return None

                # Prioritize the High-Value 'Financial_NewsDocs' path
                high_value = [l for l in links if "Financial_NewsDocs" in l]
                return high_value[0] if high_value else links[0]
            except Exception:
                return None

    async def download(self, item: DisclosureSchema):
        """Downloads the PDF ONLY if it doesn't already exist in the Git folder."""
        if not item.pdf_url: return
        
        filename = self._generate_filename(item)
        path = os.path.join(RAW_PDF_DIR, filename)

        # CHECK GITHUB STORAGE: If the file is already in the repo, skip it!
        if os.path.exists(path):
            print(f"⏭️ Skipping (Already in Git): {item.company}")
            return

        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                res = await client.get(item.pdf_url)
                if res.status_code == 200:
                    with open(path, "wb") as f:
                        f.write(res.content)
                    
                    # Log in DB for local tracking
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute("INSERT OR IGNORE INTO processed_disclosures (company, title, category, pdf_url) VALUES (?,?,?,?)",
                                    (item.company, item.title, item.category, item.pdf_url))
                    
                    print(f"✅ Downloaded: {filename}")
                else:
                    print(f"⚠️ HTTP {res.status_code} for {item.company}")
            except Exception as e:
                print(f"❌ Download Error: {e}")

async def run_scraper():
    print(f"🕒 Sync Started: {datetime.now().strftime('%H:%M')}")
    scr = DisclosureScraper()
    items = await scr.get_latest_items()
    
    # Process sequentially to respect server limits and avoid IP bans
    for item in items:
        link = await scr.get_pdf_link(item)
        if link:
            item.pdf_url = link
            item.category = scr._categorize(link, item.title)
            await scr.download(item)
    print("🏁 Sync Finished.")

if __name__ == "__main__":
    asyncio.run(run_scraper())
