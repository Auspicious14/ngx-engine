import pdfplumber
import os
import sqlite3
import re
import pandas as pd
from datetime import datetime

RAW_DIR = "data/raw_pdfs"
STRUCTURED_DIR = "data/processed_md"
DB_PATH = "data/brain_metadata.db"

os.makedirs(STRUCTURED_DIR, exist_ok=True)

class HybridParser:
    def __init__(self):
        self.db_path = DB_PATH
        self.db_meta = self._load_db_metadata()

    def _load_db_metadata(self) -> dict:
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Ensure column exists
                cols = [r[1] for r in conn.execute("PRAGMA table_info(processed_disclosures)").fetchall()]
                if "date_submitted" not in cols:
                    conn.execute("ALTER TABLE processed_disclosures ADD COLUMN date_submitted TEXT")
                    conn.commit()
    
                rows = conn.execute("""
                    SELECT filename, company, title, category, date_submitted
                    FROM processed_disclosures
                    WHERE filename IS NOT NULL
                """).fetchall()
    
                meta = {}
                missing_dates = []
                for filename, company, title, category, date_submitted in rows:
                    # Extract date from filename if DB has none
                    if not date_submitted or date_submitted == "N/A":
                        date_submitted = self._extract_date_from_filename(filename)
                        missing_dates.append((date_submitted, filename))
    
                    meta[filename] = {
                        "company": company or "Unknown",
                        "title": title or "Unknown",
                        "category": category or "General_Disclosure",
                        "date_submitted": date_submitted
                    }
    
                # Backfill dates into DB for next run
                if missing_dates:
                    conn.executemany(
                        "UPDATE processed_disclosures SET date_submitted = ? WHERE filename = ?",
                        missing_dates
                    )
                    conn.commit()
                    print(f"📅 Backfilled {len(missing_dates)} missing dates from filenames")
    
            print(f"📋 Loaded {len(meta)} metadata records from DB")
            return meta
        except Exception as e:
            print(f"⚠️ DB load failed: {e}")
            return {}

    def _extract_from_filename(self, filename: str) -> dict:
        name = filename.replace(".pdf", "").replace(".md", "")
    
        # Extract date from filename
        months = ["JANUARY","FEBRUARY","MARCH","APRIL","MAY","JUNE",
                  "JULY","AUGUST","SEPTEMBER","OCTOBER","NOVEMBER","DECEMBER"]
        date_submitted = "N/A"
        for i, month in enumerate(months):
            pattern = rf'{month}[_\s](\d{{4}})'
            match = re.search(pattern, name.upper())
            if match:
                date_submitted = f"{match.group(1)}-{str(i+1).zfill(2)}-01"
                break
    
        # Strip leading numeric ID (e.g. "46811_")
        clean = re.sub(r'^\d+_', '', name)
    
        # Categorize from full name
        upper = clean.upper()
        if any(k in upper for k in ["INSIDER", "DIRECTOR", "DEALING", "DIRECTORSDEALINGS"]):
            category = "Insider_Dealing"
        elif any(k in upper for k in ["FINANCIAL", "RESULT", "AUDITED", "UNAUDITED", "AFS", "QUARTER"]):
            category = "Financial_Result"
        elif "DIVIDEND" in upper:
            category = "Dividend_Announcement"
        else:
            category = "General_Disclosure"
    
        # Extract company — stop at known suffix patterns
        # NGX filenames: COMPANY_NAME-TITLE_CORPORATE_ACTIONS_MONTH_YEAR
        # or:            COMPANY_NAME-TITLE_FINANCIAL_STATEMENTS_MONTH_YEAR
        stop_patterns = [
            r'[-_]CORPORATE[_\s]ACTIONS',
            r'[-_]FINANCIAL[_\s]STATEMENTS',
            r'[-_]FINANCIAL[_\s]RESULTS',
            r'[-_]PRESS[_\s]RELEASE',
            r'[-_]NOTICES?[_\s]OF',
            r'[-_]NOTICE[_\s]OF',
            r'[-_]BOARD[_\s]MEETING',
            r'[-_]ANNUAL[_\s]GENERAL',
            r'[-_]DIRECTOR',
            r'[-_]DIVIDEND',
            r'[-_]QUARTER',
            r'[-_]AUDITED',
            r'[-_]UNAUDITED',
            r'[-_]AGM',
            r'[-_]EGM',
            r'[-_]AFS',
        ]
    
        company_raw = clean
        earliest_stop = len(clean)
    
        for pattern in stop_patterns:
            match = re.search(pattern, clean.upper())
            if match and match.start() < earliest_stop:
                earliest_stop = match.start()
    
        company_raw = clean[:earliest_stop]
        company_raw = re.sub(r'[-_\s]+$', '', company_raw)
        
        company = company_raw.replace("_", " ").strip().upper()
        parts = re.split(r'\.\-|\-', company_raw, maxsplit=1)
        company = parts[0].replace("_", " ").strip().upper()
        # Title is the full clean name
        title = clean.replace("_", " ").strip().upper()
    
        return {
            "company": company,
            "title": title,
            "category": category,
            "date_submitted": date_submitted
        }
    def _extract_date_from_filename(self, filename: str) -> str:
        """Extract date from NGX filename pattern: MONTH_YYYY"""
        months = ["JANUARY","FEBRUARY","MARCH","APRIL","MAY","JUNE",
                  "JULY","AUGUST","SEPTEMBER","OCTOBER","NOVEMBER","DECEMBER"]
        name = filename.upper()
        for i, month in enumerate(months):
            match = re.search(rf'{month}[_\s](\d{{4}})', name)
            if match:
                return f"{match.group(1)}-{str(i+1).zfill(2)}-01"
        return "N/A"
    
    def _get_metadata(self, filename: str) -> dict:
        """DB first, filename fallback."""
        # Try DB lookup
        meta = self.db_meta.get(filename)
        if meta and meta["company"] not in ("Unknown", "", None):
            return meta

        # Fallback: extract from filename
        print(f"  ⚠️ No DB record for {filename[:50]} — extracting from filename")
        return self._extract_from_filename(filename)

    def _table_to_markdown(self, table):
        if not table or not any(table): return ""
        try:
            df = pd.DataFrame(table)
            df = df.map(lambda x: str(x).replace("\n", " ").strip() if x is not None else "")
            if not df.empty:
                return df.to_markdown(index=False)
        except Exception:
            pass
        return ""

    def extract_content(self, pdf_path: str):
        full_md = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        full_md.append(text)
                    tables = page.extract_tables()
                    for table in tables:
                        md_table = self._table_to_markdown(table)
                        if md_table:
                            full_md.append(f"\n{md_table}\n")
            return "\n\n".join(full_md)
        except Exception as e:
            print(f"⚠️ Error reading {os.path.basename(pdf_path)}: {e}")
            return None

    def process_all(self):
        files = [f for f in os.listdir(RAW_DIR) if f.endswith(".pdf")]
    
        if not files:
            print("📂 No PDFs found.")
            return
    
        # Only process files not yet converted
        pending = [f for f in files if not os.path.exists(
            os.path.join(STRUCTURED_DIR, f.replace(".pdf", ".md"))
        )]
    
        print(f"🚀 Processing {len(pending)} new filings ({len(files)} total)...")
        new_count = 0
    
        for i, file in enumerate(pending):
            output_name = file.replace(".pdf", ".md")
            output_path = os.path.join(STRUCTURED_DIR, output_name)
    
            meta = self._get_metadata(file)
            print(f"🧠 [{i+1}/{len(pending)}] {meta['company']} | {meta['date_submitted']} | {file[:50]}")
    
            content = self.extract_content(os.path.join(RAW_DIR, file))
    
            if content:
                header = (
                    f"---\n"
                    f"company: \"{meta['company']}\"\n"
                    f"title: \"{meta['title']}\"\n"
                    f"category: \"{meta['category']}\"\n"
                    f"date_submitted: \"{meta['date_submitted']}\"\n"
                    f"source_file: \"{file}\"\n"
                    f"parsed_at: \"{datetime.now().isoformat()}\"\n"
                    f"---\n\n"
                )
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(header + content)
                new_count += 1
    
            # Force garbage collection every 50 files to free memory
            if i % 50 == 0:
                import gc
                gc.collect()
                print(f"  🧹 Memory freed at batch {i}")
    
        print(f"✅ Done: {new_count} new filings converted.")

    
if __name__ == "__main__":
    parser = HybridParser()
    parser.process_all()
