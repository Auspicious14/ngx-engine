import os
import sqlite3
from pinecone import Pinecone, ServerlessSpec
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

MD_DIR = "data/processed_md"
DB_PATH = "data/brain_metadata.db"

def get_unvectorized_files():
    """Returns only MD files not yet in Pinecone."""
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

    # Init Pinecone
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    
    if "ngx-brain" not in pc.list_indexes().names():
        pc.create_index(
            name="ngx-brain",
            dimension=384,  # all-MiniLM-L6-v2 output size
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1")
        )
    index = pc.Index("ngx-brain")

    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)

    processed = []
    for filename in new_files:
        path = os.path.join(MD_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        chunks = splitter.split_text(content)
        vectors = []
        for i, chunk in enumerate(chunks):
            embedding = embeddings.embed_query(chunk)
            vectors.append({
                "id": f"{filename}__chunk{i}",
                "values": embedding,
                "metadata": {
                    "filename": filename,
                    "text": chunk[:1000]  # Pinecone metadata limit
                }
            })

        # Batch upsert (Pinecone limit: 100 vectors per call)
        for i in range(0, len(vectors), 100):
            index.upsert(vectors=vectors[i:i+100])

        processed.append(filename)
        print(f"  ✅ {filename} → {len(vectors)} chunks")

    mark_vectorized(processed)
    print(f"🏁 Done. {len(processed)} new filings added to Pinecone.")

if __name__ == "__main__":
    build_vector_store()
