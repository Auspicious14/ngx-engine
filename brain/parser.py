import pdfplumber
import os
import re
import pandas as pd

# Configuration
RAW_DIR = "data/raw_pdfs"
STRUCTURED_DIR = "data/structured_data"

os.makedirs(STRUCTURED_DIR, exist_ok=True)

class HybridParser:
    def __init__(self):
        # Classification keywords to help the AI prioritize later
        self.categories = {
            "INSIDER": ["INSIDER", "DIRECTOR", "DEALING", "TRADE"],
            "FINANCIAL": ["AUDITED", "UNAUDITED", "FINANCIAL", "RESULTS", "Q1", "Q2", "Q3", "AFS"],
            "CORPORATE": ["DIVIDEND", "AGM", "NOTICE", "PRESS RELEASE", "APPOINTMENT"]
        }

    def _classify_doc(self, filename: str) -> str:
        name = filename.upper()
        if any(k in name for k in self.categories["INSIDER"]): return "INSIDER_TRADING"
        if any(k in name for k in self.categories["FINANCIAL"]): return "FINANCIAL_REPORT"
        if any(k in name for k in self.categories["CORPORATE"]): return "CORPORATE_ACTION"
        return "GENERAL_DISCLOSURE"

    def _table_to_markdown(self, table):
        """Converts a pdfplumber table (list of lists) into a clean Markdown table."""
        if not table or not any(table): return ""
        
        # Clean the data (remove None, handle multiline cells)
        df = pd.DataFrame(table)
        df = df.map(lambda x: str(x).replace("\n", " ").strip() if x is not None else "")
        
        # Set first row as header if it's not empty
        if not df.empty:
            return df.to_markdown(index=False)
        return ""

    def extract_content(self, pdf_path: str):
        """Extracts text and tables sequentially to preserve the flow of the document."""
        full_md = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    # 1. Extract Text
                    text = page.extract_text()
                    if text:
                        full_md.append(text)
                    
                    # 2. Extract Tables
                    tables = page.extract_tables()
                    for table in tables:
                        md_table = self._table_to_markdown(table)
                        if md_table:
                            full_md.append(f"\n{md_table}\n")
            
            return "\n\n".join(full_md)
        except Exception as e:
            print(f"   ⚠️ Error reading {os.path.basename(pdf_path)}: {e}")
            return None

    def run_batch_process(self, source_folder: str):
        """Scans the folder and processes all PDFs."""
        files = [f for f in os.listdir(source_folder) if f.endswith(".pdf")]
        
        if not files:
            print(f"📂 No PDFs found in {source_folder}. Run the scraper first!")
            return

        print(f"🚀 Starting Hybrid Parsing for {len(files)} files...")
        
        for file in files:
            source_path = os.path.join(source_folder, file)
            output_name = file.replace(".pdf", ".md")
            output_path = os.path.join(STRUCTURED_DIR, output_name)
            
            # Skip if already parsed to save processing time
            if os.path.exists(output_path):
                continue

            doc_type = self._classify_doc(file)
            print(f"🧠 Parsing [{doc_type}]: {file}")
            
            content = self.extract_content(source_path)
            
            if content:
                # Add Metadata Header for the Vector DB
                header = f"---\nSOURCE: {file}\nCATEGORY: {doc_type}\nPARSED_AT: {os.getlogin()}\n---\n\n"
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(header + content)

if __name__ == "__main__":
    # REAL PARAMS: Points to your data directories
    parser = HybridParser()
    parser.run_batch_process(RAW_DIR)
    print("\n✅ Phase 1 Complete: All disclosures are now structured Markdown.")
