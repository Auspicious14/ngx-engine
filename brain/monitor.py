import os
import asyncio
from datetime import datetime
from brain.query_engine import AlphaIntelligence

async def run_daily_intelligence_sync():
    brain = AlphaIntelligence()
    today = datetime.now().strftime("%Y-%m-%d")
    
    print(f"🧠 NGX Intelligence Brief: {today}")
    
    # Call Pinecone directly — bypass LLM tool routing
    sections = {
        "Dividend Announcements": brain._search_disclosures(
            query="dividend announcement qualification date closure date",
            category="Dividend_Announcement"
        ),
        "Director Dealings": brain._search_disclosures(
            query="director dealing insider transaction shares bought sold",
            category="Insider_Dealing"
        ),
        "Board Appointments": brain._search_disclosures(
            query="board appointment resignation executive director",
            category="General_Disclosure"
        ),
        "Financial Results": brain._search_disclosures(
            query="financial results earnings revenue profit quarterly annual",
            category="Financial_Result"
        ),
    }
    
    # Build one context block
    context = ""
    for section, data in sections.items():
        context += f"\n\n=== {section} ===\n{data}"
    
    # Single LLM call with all context
    prompt = f"""
    You are an NGX market intelligence assistant.
    Today is {today}.
    
    Below are the ACTUAL NGX corporate disclosure documents retrieved for today.
    Summarize ONLY what is in these documents. Do NOT add information from your training data.
    If a section has no relevant data, say "None found today."
    
    {context}
    
    Write a concise daily brief with 4 sections:
    1. Dividend Announcements
    2. Director Dealings  
    3. Board Changes
    4. Financial Results
    """
    
    from groq import Groq
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1  # low temp = less hallucination
    )
    
    brief = f"🧠 *NGX Intelligence Brief: {today}*\n\n"
    brief += response.choices[0].message.content
    
    # Send as ONE message
    from main import TelegramNotifier, WhatsAppNotifier
    tg = TelegramNotifier()
    wa = WhatsAppNotifier()
    await tg.send(brief)
    await wa.send(brief)
    
    print(brief)

if __name__ == "__main__":
    asyncio.run(run_daily_intelligence_sync())