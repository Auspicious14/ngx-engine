import os
from brain.query_engine import AlphaIntelligence # Reusing your class
from datetime import datetime

def run_daily_monitor():
    brain = AlphaIntelligence()
    
    # Define the "Critical Triggers" you want to monitor daily
    monitor_queries = [
        "Summarize all new Dividend Announcements including amounts and qualification dates.",
        "List all Director Dealings or Insider trades from today's filings.",
        "Identify any earnings forecasts or financial results released today.",
        "Were there any board changes or executive appointments announced?"
    ]
    
    report_header = f"# 🚀 NGX Market Intelligence Brief: {datetime.now().strftime('%Y-%m-%d')}\n\n"
    report_body = ""

    for query in monitor_queries:
        print(f"🤖 Monitoring: {query}")
        summary = brain.ask(query)
        report_body += f"## {query}\n{summary}\n\n"

    # Save the briefing to a file that you can view directly on GitHub
    with open("DAILY_MARKET_REPORT.md", "w", encoding="utf-8") as f:
        f.write(report_header + report_body)
    
    print("🏁 Daily Report Generated: DAILY_MARKET_REPORT.md")

if __name__ == "__main__":
    run_daily_monitor()
