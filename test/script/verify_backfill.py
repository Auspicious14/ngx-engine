import sqlite3

DB_PATH = "data/brain_metadata.db"

with sqlite3.connect(DB_PATH) as conn:
    # Total records
    total = conn.execute("SELECT COUNT(*) FROM processed_disclosures").fetchone()[0]
    
    # How many still Unknown
    unknown = conn.execute(
        "SELECT COUNT(*) FROM processed_disclosures WHERE company = 'Unknown'"
    ).fetchone()[0]
    
    # Category breakdown
    categories = conn.execute("""
        SELECT category, COUNT(*) as count 
        FROM processed_disclosures 
        GROUP BY category 
        ORDER BY count DESC
    """).fetchall()
    
    # Sample of real records
    sample = conn.execute("""
        SELECT company, title, category 
        FROM processed_disclosures 
        WHERE company != 'Unknown' 
        LIMIT 5
    """).fetchall()

print(f"✅ Total records: {total}")
print(f"⚠️  Unknown company: {unknown}")
print(f"\n📊 Category breakdown:")
for cat, count in categories:
    print(f"   {cat}: {count}")
print(f"\n🔍 Sample records:")
for row in sample:
    print(f"   {row[0]} | {row[2]}")
    print(f"   → {row[1][:60]}...")
