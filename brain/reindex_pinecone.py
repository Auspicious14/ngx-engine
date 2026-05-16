import os
import sqlite3
import re
from pinecone import Pinecone
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

MD_DIR = "data/processed_md"
DB_PATH = "data/brain_metadata.db"

def sanitize_id(text: str) -> str:
    return re.sub(r'[^\x00-\x7F]', '_', text)

def extract_date_from_md(content: str) -> str:
    """Pull date_submitted from the YAML front matter."""
    match = re.search(r'date_submitted:\s*"([^"]+)"', content)
    return match.group(1) if match else "N/A"
    
def reindex():
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index("ngx-brain")
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    
    # Pull all metadata from DB
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT filename, company, title, category FROM processed_disclosures"
        ).fetchall()
    
    db_meta = {row[0]: row[1:] for row in rows}
    files = [f for f in os.listdir(MD_DIR) if f.endswith(".md")]
    print(f"🔁 Re-indexing {len(files)} files with full metadata...")

    for filename in files:
        pdf_name = filename.replace(".md", ".pdf")
        meta = db_meta.get(pdf_name, ("Unknown", filename, "General_Disclosure"))
        company, title, category = meta

        path = os.path.join(MD_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            
        date_submitted = extract_date_from_md(content)
        chunks = splitter.split_text(content)
        vectors = []
        for i, chunk in enumerate(chunks):
            embedding = embeddings.embed_query(chunk)
            vectors.append({
                "id": sanitize_id(f"{filename}__chunk{i}"),
                "values": embedding,
                "metadata": {
                    "filename": filename,
                    "company": company,
                    "title": title,
                    "category": category,
                    "date_submitted": date_submitted,
                    "text": chunk[:1000]
                }
            })

        for i in range(0, len(vectors), 100):
            index.upsert(vectors=vectors[i:i+100])

        print(f"  ✅ {company} | {filename[:60]}")

    print("🏁 Re-index complete.")

if __name__ == "__main__":
    reindex()
