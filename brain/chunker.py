import os
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Configuration
MARKDOWN_DIR = "./data/markdown/" # Change this to where your MD files live
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

def load_and_chunk_files(directory_path):
    """Reads Markdown files from a directory and splits them into chunks."""
    
    # 1. Initialize the Splitter
    # We use a chunk size of 1000 characters, with a 200-character overlap
    # to ensure context isn't lost between chunks.
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
        is_separator_regex=False,
    )

    all_chunks = []

    # 2. Iterate through the Markdown files
    for filename in os.listdir(directory_path):
        if filename.endswith(".md"):
            filepath = os.path.join(directory_path, filename)
            
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            # 3. Create the chunks
            # create_documents allows us to attach metadata immediately
            chunks = text_splitter.create_documents(
                texts=[content], 
                metadatas=[{"source": filename}] # This links back to your SQLite .db
            )
            
            all_chunks.extend(chunks)
            print(f"Processed {filename}: Created {len(chunks)} chunks.")

    return all_chunks

if __name__ == "__main__":
    # Ensure the directory exists
    if not os.path.exists(MARKDOWN_DIR):
        print(f"Error: Directory {MARKDOWN_DIR} not found.")
    else:
        print("Starting the chunking process...")
        document_chunks = load_and_chunk_files(MARKDOWN_DIR)
        
        print(f"\nTotal chunks created across all files: {len(document_chunks)}")
        
        # Print a sample chunk to verify
        if document_chunks:
            print("\n--- SAMPLE CHUNK ---")
            print(f"Metadata: {document_chunks[0].metadata}")
            print(f"Content:\n{document_chunks[0].page_content[:300]}...")
