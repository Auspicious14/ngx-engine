import pdfplumber
import pandas as pd
import os

class TableParser:
    def __init__(self, output_dir="data/structured_data"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def extract_tables_to_markdown(self, pdf_path: str):
        """Extracts tables from a PDF and converts them to Markdown strings."""
        markdown_output = []
        filename = os.path.basename(pdf_path)

        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                for table_idx, table in enumerate(tables):
                    if not table or not any(table): continue
                    
                    # Clean the table: Remove None values and strip whitespace
                    df = pd.DataFrame(table)
                    df = df.map(lambda x: str(x).strip() if x is not None else "")
                    
                    # Use the first row as header if it looks like one
                    new_header = df.iloc[0]
                    df = df[1:]
                    df.columns = new_header
                    
                    # Convert to Markdown
                    md_table = df.to_markdown(index=False)
                    markdown_output.append(f"### Table {table_idx+1} from Page {i+1}\n\n{md_table}")

        return "\n\n".join(markdown_output)

    def save_structured_text(self, pdf_path: str, content: str):
        """Saves the markdown content to a text file for the Vector DB later."""
        base_name = os.path.basename(pdf_path).replace(".pdf", ".md")
        save_path = os.path.join(self.output_dir, base_name)
        
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(content)
        return save_path

# Example Usage
if __name__ == "__main__":
    parser = TableParser()
    # Path to one of the PDFs downloaded by your scraper
    sample_pdf = "data/raw_pdfs/247_NGX_INSIDER_DEALING_DISCLOSURE_FORM.pdf"
    
    if os.path.exists(sample_pdf):
        print(f"📄 Parsing: {sample_pdf}")
        markdown_data = parser.extract_tables_to_markdown(sample_pdf)
        saved_file = parser.save_structured_text(sample_pdf, markdown_data)
        print(f"✅ Structured data saved to: {saved_file}")
