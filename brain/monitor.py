import os
import asyncio
from brain.query_engine import AlphaIntelligence
from main import TelegramNotifier, WhatsAppNotifier 
from datetime import datetime

async def run_daily_intelligence_sync():
    brain = AlphaIntelligence()
    send_whatsapp_msg = WhatsAppNotifier()
    send_telegram_msg = TelegramNotifier()
    
    intelligence_queries = [
        "List all new Dividend Announcements and qualification dates.",
        "Summarize all Director Dealings or Insider trades from today's filings.",
        "List any Executive or Board appointments/resignations.",
        "Summarize any recently released Financial Results or Earnings Forecasts."
    ]
    
    report_header = f"🧠 *NGX Intelligence Brief: {datetime.now().strftime('%Y-%m-%d')}*\n\n"
    report_body = ""

    for query in intelligence_queries:
        summary = brain.ask(query)
        if "I don't have that specific data yet" not in summary:
            report_body += f"📍 *{query}*\n{summary}\n\n"

    if not report_body:
        final_report = report_header + "No significant corporate disclosures detected today."
    else:
        final_report = report_header + report_body

    print("📡 Pushing Daily Intelligence to WhatsApp and Telegram...")
    await send_telegram_msg.send(final_report)
    await send_whatsapp_msg.send(final_report)
    
    with open("DAILY_MARKET_REPORT.md", "w", encoding="utf-8") as f:
        f.write(final_report)

if __name__ == "__main__":
    asyncio.run(run_daily_intelligence_sync())
