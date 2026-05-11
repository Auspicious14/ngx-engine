import os
import asyncio
import httpx
import sqlite3
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

# Directories
RAW_PDF_DIR = "data/raw_pdfs"
DB_PATH = "data/brain_metadata.db"
BASE_URL = "https://ngxgroup.com/exchange/data/corporate-disclosures/"

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
        self.headers = {"User-Agent": "Mozilla/5.0"}
        self._init_db()

    def _init_db(self):
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
        if "INSIDER" in text or "DIRECTOR_DEALING" in text:
            return "Insider Dealing"
        if "FINANCIAL" in text or "RESULT" in text or "AUDITED" in text:
            return "Financial Result"
        if "DIVIDEND" in text:
            return "Dividend Announcement"
        return "General Disclosure"

    async def get_latest_items(self) -> List[DisclosureSchema]:
        async with httpx.AsyncClient(headers=self.headers, timeout=30.0) as client:
            res = await client.get(BASE_URL)
            soup = BeautifulSoup(res.text, 'html.parser')
            rows = soup.find('table').find_all('tr')[1:]
            
            items = []
            for row in rows:
                cols = row.find_all('td')
                link_tag = cols[1].find('a')
                if not link_tag: continue

                items.append(DisclosureSchema(
                    company=cols[0].text.strip(),
                    title=link_tag.text.strip(),
                    date_submitted=cols[2].text.strip(),
                    landing_url=link_tag['href']
                ))
            return items

    async def get_pdf_link(self, item: DisclosureSchema) -> Optional[str]:
        """Finds the doclib link and prioritizes High-Value sources."""
        async with httpx.AsyncClient(headers=self.headers, timeout=20.0) as client:
            try:
                res = await client.get(item.landing_url)
                soup = BeautifulSoup(res.text, 'html.parser')
                links = [a['href'] for a in soup.find_all('a', href=True) if '.pdf' in a['href']]
                
                if not links: return None

                # PRIORITY: doclib.ngxgroup.com/Financial_NewsDocs/
                high_value = [l for l in links if "Financial_NewsDocs" in l]
                final_link = high_value[0] if high_value else links[0]
                
                item.category = self._categorize(final_link, item.title)
                return final_link
            except Exception: return None

    async def download(self, item: DisclosureSchema):
        if not item.pdf_url: return
        
        # Check DB
        with sqlite3.connect(DB_PATH) as conn:
            if conn.execute("SELECT 1 FROM processed_disclosures WHERE pdf_url=?", (item.pdf_url,)).fetchone():
                return

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                res = await client.get(item.pdf_url)
                filename = f"{item.category.replace(' ', '_')}_{item.company.replace(' ', '_')}_{datetime.now().strftime('%H%M')}.pdf"
                path = os.path.join(RAW_PDF_DIR, filename)
                
                with open(path, "wb") as f: f.write(res.content)
                
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("INSERT INTO processed_disclosures (company, title, category, pdf_url) VALUES (?,?,?,?)",
                                (item.company, item.title, item.category, item.pdf_url))
                print(f"✅ Downloaded [{item.category}]: {item.company}")
            except Exception as e: print(f"❌ Download Error: {e}")

async def run_scraper():
    scr = DisclosureScraper()
    items = await scr.get_latest_items()
    for item in items:
        link = await scr.get_pdf_link(item)
        if link:
            item.pdf_url = link
            await scr.download(item)

if __name__ == "__main__":
    asyncio.run(run_scraper())
