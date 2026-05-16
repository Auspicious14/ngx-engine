import asyncio
import httpx
import sqlite3
import os
import re
import sys

DB_PATH = "data/brain_metadata.db"
RAW_DIR = "data/raw_pdfs"

# Add project root to path so brain.parser is importable
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

    # Build lookup: filename → metadata from API
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
            "date_submitted": item.get("Modified", "")[:10],
        }

    disk_files = [f for f in os.listdir(RAW_DIR) if f.endswith(".pdf")]
    print(f"📂 {len(disk_files)} PDFs on disk, {len(api_lookup)} in API")

    # --- Pass 1: Insert files found in API ---
    inserted_from_api = 0
    with sqlite3.connect(DB_PATH) as conn:
        # Ensure date_submitted column exists
        try:
            conn.execute("ALTER TABLE processed_disclosures ADD COLUMN date_submitted TEXT")
        except Exception:
            pass  # Column already exists

        for filename in disk_files:
            meta = api_lookup.get(filename)
            if not meta:
                continue
            conn.execute("""
                INSERT OR IGNORE INTO processed_disclosures 
                (company, title, category, pdf_url, filename, date_submitted)
                VALUES (?,?,?,?,?,?)
            """, (
                meta["company"], meta["title"], meta["category"],
                meta["pdf_url"], filename, meta["date_submitted"]
            ))
            inserted_from_api += 1
        conn.commit()

    print(f"✅ Pass 1: {inserted_from_api} records inserted from API")

    # --- Pass 2: Filename extraction for files not in API ---
    from brain.parser import HybridParser
    p = HybridParser.__new__(HybridParser)  # skip __init__ to avoid DB load

    missing = [f for f in disk_files if f not in api_lookup]
    print(f"⚠️  {len(missing)} files not in API — extracting from filename")

    inserted_from_filename = 0
    with sqlite3.connect(DB_PATH) as conn:
        for filename in missing:
            meta = p._extract_from_filename(filename)
            conn.execute("""
                INSERT INTO processed_disclosures 
                (company, title, category, pdf_url, filename, date_submitted)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(filename) DO UPDATE SET
                    company = excluded.company,
                    title = excluded.title,
                    category = excluded.category,
                    date_submitted = excluded.date_submitted
            """, (...))
            inserted_from_filename += 1
        conn.commit()

    print(f"✅ Pass 2: {inserted_from_filename} records filled from filename extraction")

    # --- Verify ---
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM processed_disclosures").fetchone()[0]
        unknown = conn.execute(
            "SELECT COUNT(*) FROM processed_disclosures WHERE company = 'Unknown'"
        ).fetchone()[0]
        sample = conn.execute("""
            SELECT company, date_submitted, filename 
            FROM processed_disclosures 
            WHERE company != 'Unknown' 
            LIMIT 5
        """).fetchall()

    print(f"\n📊 Total records: {total} | Still Unknown: {unknown}")
    print("🔍 Sample:")
    for row in sample:
        print(f"   {row[1]} | {row[0]} | {row[2][:50]}")

if __name__ == "__main__":
    asyncio.run(backfill())
