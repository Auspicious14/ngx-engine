import pdfplumber
import os
import sqlite3
import pandas as pd
from datetime import datetime

# Configuration - Aligning with your Scraper
RAW_DIR = "data/raw_pdfs"
STRUCTURED_DIR = "data/processed_md"
DB_PATH = "data/brain_metadata.db"

os.makedirs(STRUCTURED_DIR, exist_ok=True)

class HybridParser:
    def __init__(self):
        self.db_path = DB_PATH
        # Fallback categories if DB lookup fails
        self.categories = {
            "INSIDER": ["INSIDER", "DIRECTOR", "DEALING", "TRADE"],
            "FINANCIAL": ["AUDITED", "UNAUDITED", "FINANCIAL", "RESULTS", "Q1", "Q2", "Q3", "AFS"],
            "CORPORATE": ["DIVIDEND", "AGM", "NOTICE", "PRESS RELEASE", "APPOINTMENT"]
        }

    def _get_db_metadata(self, filename: str):
        """Retrieves the high-quality metadata from our scraper's database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT company, title, category, date_submitted 
                    FROM processed_disclosures 
                    WHERE filename = ?
                """, (filename,))
                return cursor.fetchone()
        except Exception:
            return None

    def _classify_fallback(self, filename: str) -> str:
        """Backup classification if the database is missing or empty."""
        name = filename.upper()
        if any(k in name for k in self.categories["INSIDER"]): return "INSIDER_TRADING"
        if any(k in name for k in self.categories["FINANCIAL"]): return "FINANCIAL_REPORT"
        if any(k in name for k in self.categories["CORPORATE"]): return "CORPORATE_ACTION"
        return "GENERAL_DISCLOSURE"

    def _table_to_markdown(self, table):
        """Converts a pdfplumber table into a clean Markdown table."""
        if not table or not any(table): return ""
        try:
            df = pd.DataFrame(table)
            # Clean data: remove newlines and None values
            df = df.map(lambda x: str(x).replace("\n", " ").strip() if x is not None else "")
            if not df.empty:
                return df.to_markdown(index=False)
        except Exception:
            pass
        return ""

    def extract_content(self, pdf_path: str):
        """Sequential extraction of text and tables to maintain context."""
        full_md = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    # 1. Extract Text
                    text = page.extract_text()
                    if text:
                        full_md.append(text)
                    
                    # 2. Extract Tables (Crucial for Financial Results)
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
        """Batch processes new PDFs into structured Markdown."""
        files = [f for f in os.listdir(RAW_DIR) if f.endswith(".pdf")]
        
        if not files:
            print(f"📂 No PDFs found. Scraper needs to run first.")
            return

        print(f"🚀 Processing {len(files)} filings...")
        new_count = 0
        
        for file in files:
            output_name = file.replace(".pdf", ".md")
            output_path = os.path.join(STRUCTURED_DIR, output_name)
            
            # IDEMPOTENCY: Skip files already converted to MD
            if os.path.exists(output_path):
                continue

            # Metadata Acquisition
            db_data = self._get_db_metadata(file)
            if db_data:
                company, title, category, date = db_data
            else:
                company, title, date = "Unknown", "Unknown", "N/A"
                category = self._classify_fallback(file)

            print(f"🧠 Parsing: {company} | {file}")
            
            content = self.extract_content(os.path.join(RAW_DIR, file))
            
            if content:
                # RAG-Ready YAML Header
                header = (
                    f"---\n"
                    f"company: \"{company}\"\n"
                    f"title: \"{title}\"\n"
                    f"category: \"{category}\"\n"
                    f"date_submitted: \"{date}\"\n"
                    f"source_file: \"{file}\"\n"
                    f"parsed_at: \"{datetime.now().isoformat()}\"\n"
                    f"---\n\n"
                )
                
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(header + content)
                new_count += 1

        print(f"✅ Success: {new_count} new filings converted for the Vector DB.")

if __name__ == "__main__":
    parser = HybridParser()
    parser.process_all()
