import os
import asyncio
import httpx
import sqlite3
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

# Configuration
RAW_PDF_DIR = "data/raw_pdfs"
DB_PATH = "data/brain_metadata.db"
BASE_URL = "https://ngxgroup.com/exchange/data/corporate-disclosures/"

os.makedirs(RAW_PDF_DIR, exist_ok=True)

class DisclosureSchema(BaseModel):
    company: str
    title: str
    date_submitted: str
    download_url: str  # Landing page URL
    pdf_url: Optional[str] = None
    file_path: Optional[str] = None

class DisclosureScraper:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_disclosures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    company TEXT,
                    date_submitted TEXT,
                    pdf_url TEXT UNIQUE,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    async def get_latest_disclosures(self, limit: int = 20) -> List[DisclosureSchema]:
        """Scrapes the main disclosure table for landing page links."""
        async with httpx.AsyncClient(headers=self.headers, timeout=30.0) as client:
            response = await client.get(BASE_URL)
            if response.status_code != 200:
                return []

            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.find('table') # NGX uses a standard table for disclosures
            if not table:
                return []

            disclosures = []
            rows = table.find_all('tr')[1:] # Skip header

            for row in rows[:limit]:
                cols = row.find_all('td')
                if len(cols) < 3: continue
                
                # Check for the link in the "Disclosures" column
                link_tag = cols[1].find('a')
                if not link_tag: continue

                disclosures.append(DisclosureSchema(
                    company=cols[0].text.strip(),
                    title=link_tag.text.strip(),
                    date_submitted=cols[2].text.strip(),
                    download_url=link_tag['href']
                ))
            return disclosures

    async def extract_pdf_link(self, landing_url: str) -> Optional[str]:
        """Navigates to the individual disclosure page to find the actual PDF source."""
        async with httpx.AsyncClient(headers=self.headers, timeout=20.0) as client:
            try:
                res = await client.get(landing_url)
                soup = BeautifulSoup(res.text, 'html.parser')
                # Look for the 'Download' button or direct link to doclib.ngxgroup.com
                download_btn = soup.find('a', string=lambda x: x and 'Download' in x)
                if download_btn:
                    return download_btn['href']
                
                # Fallback: find any link ending in .pdf
                for a in soup.find_all('a', href=True):
                    if a['href'].endswith('.pdf'):
                        return a['href']
            except Exception:
                return None
        return None

    async def download_pdf(self, disclosure: DisclosureSchema):
        """Downloads the PDF and updates the tracking database."""
        if not disclosure.pdf_url:
            return

        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                # Check if already processed
                with sqlite3.connect(DB_PATH) as conn:
                    exists = conn.execute("SELECT 1 FROM processed_disclosures WHERE pdf_url = ?", (disclosure.pdf_url,)).fetchone()
                    if exists: return

                res = await client.get(disclosure.pdf_url)
                if res.status_code == 200:
                    filename = f"{disclosure.company.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
                    path = os.path.join(RAW_PDF_DIR, filename)
                    
                    with open(path, "wb") as f:
                        f.write(res.content)
                    
                    # Update DB
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute("""
                            INSERT INTO processed_disclosures (title, company, date_submitted, pdf_url)
                            VALUES (?, ?, ?, ?)
                        """, (disclosure.title, disclosure.company, disclosure.date_submitted, disclosure.pdf_url))
                    
                    print(f"✅ Downloaded: {disclosure.title} ({disclosure.company})")
            except Exception as e:
                print(f"❌ Error downloading {disclosure.pdf_url}: {e}")

async def run_brain_sync():
    scraper = DisclosureScraper()
    print("🔍 Fetching latest corporate disclosures...")
    items = await scraper.get_latest_disclosures()
    
    for item in items:
        # Step 2: Get actual PDF source
        pdf_link = await scraper.extract_pdf_link(item.download_url)
        if pdf_link:
            item.pdf_url = pdf_link
            # Step 3: Download and Log
            await scraper.download_pdf(item)

if __name__ == "__main__":
    asyncio.run(run_brain_sync())
