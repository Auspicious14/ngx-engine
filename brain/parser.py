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
        """Load ALL metadata from DB into memory once — keyed by filename."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("""
                    SELECT filename, company, title, category, date_submitted
                    FROM processed_disclosures
                    WHERE filename IS NOT NULL
                """).fetchall()
            meta = {}
            for filename, company, title, category, date_submitted in rows:
                meta[filename] = {
                    "company": company or "Unknown",
                    "title": title or "Unknown",
                    "category": category or "General_Disclosure",
                    "date_submitted": date_submitted or "N/A"
                }
            print(f"📋 Loaded {len(meta)} metadata records from DB")
            return meta
        except Exception as e:
            print(f"⚠️ DB load failed: {e}")
            return {}

    def _extract_from_filename(self, filename: str) -> dict:
        """
        Extract best-effort metadata from the filename itself.
        NGX filenames follow patterns like:
        46811_CADBURY_NIGERIA_PLC.-_QUARTER_1_-_FINANCIAL_STATEMENTS_APRIL_2026.pdf
        NAHCO_PLC_-_NOTICE_OF_45TH_ANNUAL_GENERAL_MEETING.pdf
        """
        name = filename.replace(".pdf", "").replace(".md", "")

        # Extract date from filename — pattern: MONTH_YYYY at the end
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

        # Categorize
        upper = clean.upper()
        if any(k in upper for k in ["INSIDER", "DIRECTOR", "DEALING", "DIRECTORSDEALINGS"]):
            category = "Insider_Dealing"
        elif any(k in upper for k in ["FINANCIAL", "RESULT", "AUDITED", "UNAUDITED", "AFS", "QUARTER"]):
            category = "Financial_Result"
        elif "DIVIDEND" in upper:
            category = "Dividend_Announcement"
        else:
            category = "General_Disclosure"

        # Extract company — everything before the first " - " or "_-_"
        company_raw = re.split(r'_-_|-_|_-', clean)[0]
        company = company_raw.replace("_", " ").strip().upper()

        # Title is the full clean name
        title = clean.replace("_", " ").strip().upper()

        return {
            "company": company,
            "title": title,
            "category": category,
            "date_submitted": date_submitted
        }

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
            print("📂 No PDFs found. Scraper needs to run first.")
            return

        print(f"🚀 Processing {len(files)} filings...")
        new_count = 0

        for file in files:
            output_name = file.replace(".pdf", ".md")
            output_path = os.path.join(STRUCTURED_DIR, output_name)

            if os.path.exists(output_path):
                continue

            meta = self._get_metadata(file)
            print(f"🧠 Parsing: {meta['company']} | {meta['date_submitted']} | {file[:50]}")

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

        print(f"✅ Done: {new_count} new filings converted.")


if __name__ == "__main__":
    parser = HybridParser()
    parser.process_all()
