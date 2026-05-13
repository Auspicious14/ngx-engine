# brain/backfill_metadata.py
import asyncio
import httpx
import sqlite3
import os
import re

DB_PATH = "data/brain_metadata.db"
RAW_DIR = "data/raw_pdfs"

def _categorize(url: str, title: str) -> str:
    text = (url + title).upper()
    if any(k in text for k in ["INSIDER", "DIRECTOR_DEALING", "DIRECTORSDEALINGS"]):
        return "Insider_Dealing"
    if any(k in text for k in ["FINANCIAL", "RESULT", "AUDITED", "UNAUDITED", "AFS"]):
        return "Financial_Result"
    if "DIVIDEND" in text:
        return "Dividend_Announcement"
    return "General_Disclosure"

async def backfill():
    api_url = (
        "https://doclib.ngxgroup.com/_api/Web/Lists/GetByTitle('XFinancial_News')/items/"
        "?$select=URL,Modified,Created,CompanyName,CompanySymbol,Type_of_Submission"
        "&$orderby=Created%20desc"
        "&$filter=Modified%20ge%20'2019-01-31T23:00:00.000Z'"
        "&$Top=1000"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json;odata=verbose"
    }

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        res = await client.get(api_url)
        results = res.json()["d"]["results"]

    # Build a lookup: filename → metadata
    api_lookup = {}
    for item in results:
        pdf_url = item.get("URL", {}).get("Url", "")
        if not pdf_url or ".pdf" not in pdf_url.lower():
            continue
        filename = pdf_url.split("/")[-1]
        api_lookup[filename] = {
            "company": item.get("CompanyName", "Unknown"),
            "title": item.get("URL", {}).get("Description", "Unknown").upper(),
            "category": _categorize(pdf_url, item.get("URL", {}).get("Description", "")),
            "pdf_url": pdf_url,
        }

    # Get all existing PDF filenames on disk
    disk_files = [f for f in os.listdir(RAW_DIR) if f.endswith(".pdf")]
    print(f"📂 {len(disk_files)} PDFs on disk, {len(api_lookup)} in API")

    inserted = 0
    with sqlite3.connect(DB_PATH) as conn:
        for filename in disk_files:
            meta = api_lookup.get(filename)
            if not meta:
                # API doesn't have it (older than 2019 or missing) — use filename fallback
                meta = {
                    "company": "Unknown",
                    "title": filename.replace("_", " ").replace(".pdf", ""),
                    "category": _categorize(filename, filename),
                    "pdf_url": f"https://doclib.ngxgroup.com/Financial_NewsDocs/{filename}"
                }
            
            conn.execute("""
                INSERT OR IGNORE INTO processed_disclosures 
                (company, title, category, pdf_url, filename)
                VALUES (?,?,?,?,?)
            """, (meta["company"], meta["title"], meta["category"], meta["pdf_url"], filename))
            inserted += 1

    print(f"✅ Backfilled {inserted} records into processed_disclosures")

if __name__ == "__main__":
    asyncio.run(backfill())
