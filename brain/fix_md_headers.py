# brain/fix_md_headers.py
import os
import re
import sqlite3

MD_DIR = "data/processed_md"
DB_PATH = "data/brain_metadata.db"

def fix_headers():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT filename, company, title, category, date_submitted
            FROM processed_disclosures
            WHERE filename IS NOT NULL
        """).fetchall()
    
    db_meta = {}
    for filename, company, title, category, date_submitted in rows:
        md_name = filename.replace(".pdf", ".md")
        db_meta[md_name] = {
            "company": company or "Unknown",
            "title": title or "Unknown", 
            "category": category or "General_Disclosure",
            "date_submitted": date_submitted or "N/A"
        }

    md_files = [f for f in os.listdir(MD_DIR) if f.endswith(".md")]
    print(f"🔧 Fixing headers for {len(md_files)} MD files...")

    fixed = 0
    for md_file in md_files:
        path = os.path.join(MD_DIR, md_file)
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Get metadata from DB or extract from filename
        meta = db_meta.get(md_file)
        if not meta:
            continue

        # Replace YAML front matter only — leave PDF content untouched
        new_header = (
            f"---\n"
            f"company: \"{meta['company']}\"\n"
            f"title: \"{meta['title']}\"\n"
            f"category: \"{meta['category']}\"\n"
            f"date_submitted: \"{meta['date_submitted']}\"\n"
            f"source_file: \"{md_file.replace('.md', '.pdf')}\"\n"
            f"---\n"
        )

        # Replace everything between first --- and second ---
        new_content = re.sub(
            r'^---\n.*?---\n',
            new_header,
            content,
            count=1,
            flags=re.DOTALL
        )

        if new_content != content:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
            fixed += 1

    print(f"✅ Fixed {fixed} headers. {len(md_files) - fixed} already correct.")

if __name__ == "__main__":
    fix_headers()
