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
        # Professional User-Agent for consistent access via GitHub Runners
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        self._init_db()

    def _init_db(self):
        """Initializes the metadata ledger to track unique filings."""
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_disclosures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company TEXT,
                    title TEXT,
                    category TEXT,
                    pdf_url TEXT UNIQUE,
                    filename TEXT UNIQUE,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def _categorize(self, url: str, title: str) -> str:
        """Assigns a category for AI prioritization based on filename and title."""
        text = (url + title).upper()
        if any(k in text for k in ["INSIDER", "DIRECTOR_DEALING"]):
            return "Insider_Dealing"
        if any(k in text for k in ["FINANCIAL", "RESULT", "AUDITED", "UNAUDITED", "AFS"]):
            return "Financial_Result"
        if "DIVIDEND" in text:
            return "Dividend_Announcement"
        return "General_Disclosure"

    async def get_latest_items(self) -> List[DisclosureSchema]:
        """Scrapes the main disclosure table for landing page links."""
        async with httpx.AsyncClient(headers=self.headers, timeout=30.0, follow_redirects=True) as client:
            try:
                res = await client.get(BASE_URL)
                res.raise_for_status()
                print(f"📄 Response length: {len(res.text)}")
                print(f"📄 First 2000 chars:\n{res.text[:2000]}")
                
                soup = BeautifulSoup(res.text, 'html.parser')
                
                table = soup.find('table')
                print(f"🔍 Table found: {table is not None}")
                
                if not table: return []

                rows = table.find_all('tr')[1:] # Skip header
                items = []
                for row in rows:
                    cols = row.find_all('td')
                    link_tag = cols[1].find('a') if len(cols) > 1 else None
                    if not link_tag: continue

                    items.append(DisclosureSchema(
                        company=cols[0].text.strip(),
                        title=link_tag.text.strip(),
                        date_submitted=cols[2].text.strip(),
                        landing_url=link_tag['href']
                    ))
                return items
            except Exception as e:
                print(f"❌ Table Scrape Error: {e}")
                return []

    async def get_pdf_link(self, item: DisclosureSchema) -> Optional[str]:
        """Follows the landing URL to find the actual PDF source link."""
        async with httpx.AsyncClient(headers=self.headers, timeout=20.0, follow_redirects=True) as client:
            try:
                res = await client.get(item.landing_url)
                soup = BeautifulSoup(res.text, 'html.parser')
                links = [a['href'] for a in soup.find_all('a', href=True) if '.pdf' in a['href'].lower()]
                
                if not links: return None

                # Prioritize 'Financial_NewsDocs' as the gold standard source
                high_value = [l for l in links if "Financial_NewsDocs" in l]
                return high_value[0] if high_value else links[0]
            except Exception:
                return None

    async def download(self, item: DisclosureSchema):
        """Downloads unique filings based on their exact URL-defined filename."""
        if not item.pdf_url: return
        
        # EXTRACT UNIQUE FILENAME: This is the ground truth for "uniqueness"
        # Handles cases like Artrol Investment 04.05 vs 05.05 perfectly.
        url_filename = item.pdf_url.split('/')[-1]
        path = os.path.join(RAW_PDF_DIR, url_filename)

        # GITHUB STORAGE CHECK: Skip if the exact file is already in the repo
        if os.path.exists(path):
            print(f"⏭️ Skipping (Already stored): {url_filename}")
            return

        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                res = await client.get(item.pdf_url)
                if res.status_code == 200:
                    with open(path, "wb") as f:
                        f.write(res.content)
                    
                    # Update the local ledger
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute("""
                            INSERT OR IGNORE INTO processed_disclosures (company, title, category, pdf_url, filename) 
                            VALUES (?,?,?,?,?)
                        """, (item.company, item.title, item.category, item.pdf_url, url_filename))
                    
                    print(f"✅ Saved Unique Filing: {url_filename}")
                else:
                    print(f"⚠️ HTTP {res.status_code} for {url_filename}")
            except Exception as e:
                print(f"❌ Download Failed for {url_filename}: {e}")

async def run_scraper():
    print(f"🚀 Evening Sync Started: {datetime.now().strftime('%H:%M:%S')}")
    scr = DisclosureScraper()
    items = await scr.get_latest_items()
    print(f"DEBUG: Found {len(items)} items on the landing page.") # ADD THIS
    # Process sequentially to prevent IP flagging and ensure orderly downloads
    for item in items:
        link = await scr.get_pdf_link(item)
        if link:
            item.pdf_url = link
            item.category = scr._categorize(link, item.title)
            await scr.download(item)
    print("🏁 Evening Sync Finished.")

if __name__ == "__main__":
    asyncio.run(run_scraper())
