import os
import sqlite3
import re
from pinecone import Pinecone, ServerlessSpec
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

MD_DIR = "data/processed_md"
DB_PATH = "data/brain_metadata.db"

def sanitize_id(text: str) -> str:
    """Strip non-ASCII characters from Pinecone vector IDs."""
    return re.sub(r'[^\x00-\x7F]', '_', text)


def extract_date_from_md(content: str) -> str:
    """Pull date_submitted from the YAML front matter."""
    match = re.search(r'date_submitted:\s*"([^"]+)"', content)
    return match.group(1) if match else "N/A"
    
def get_unvectorized_files():
    """Returns only MD files not yet in Pinecone."""
    if not os.path.exists(MD_DIR):
        print("📂 No processed_md directory yet — nothing to vectorize.")
        return set()
    all_files = set(f for f in os.listdir(MD_DIR) if f.endswith(".md"))
    with sqlite3.connect(DB_PATH) as conn:
        done = set(r[0] for r in conn.execute(
            "SELECT filename FROM vectorized_files"
        ).fetchall())
    return all_files - done

def mark_vectorized(filenames: list):
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO vectorized_files (filename) VALUES (?)",
            [(f,) for f in filenames]
        )

def build_vector_store():
    new_files = get_unvectorized_files()
    if not new_files:
        print("✅ Vector store already up to date.")
        return

    print(f"🧠 Vectorizing {len(new_files)} new filings...")

    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index("ngx-brain")
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)

    with sqlite3.connect(DB_PATH) as conn:
        placeholders = ",".join("?" * len(new_files))
        rows = conn.execute(f"""
            SELECT filename, company, title, category 
            FROM processed_disclosures 
            WHERE filename IN ({placeholders})
        """, list(new_files)).fetchall()
    
    db_meta = {row[0]: row[1:] for row in rows} 

    processed = []
    for filename in new_files:
        path = os.path.join(MD_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        date_submitted = extract_date_from_md(content)

        meta = db_meta.get(filename, ("Unknown", filename, "General_Disclosure"))
        company, title, category = meta

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

        processed.append(filename)
        print(f"  ✅ {filename} → {len(vectors)} chunks")

    mark_vectorized(processed)
    print(f"🏁 Done. {len(processed)} new filings added to Pinecone.")

    mark_vectorized(processed)
    print(f"🏁 Done. {len(processed)} new filings added to Pinecone.")

if __name__ == "__main__":
    build_vector_store()
