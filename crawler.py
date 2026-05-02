import asyncio
import os
import pandas as pd
from datetime import datetime, timedelta
from main import NGXEngine

class AlphaCrawler(NGXEngine):
    def __init__(self):
        super().__init__()
        # NGX 2026 Market Holidays
        self.holidays = [
            "01-01-2026", "20-03-2026", "23-03-2026", 
            "03-04-2026", "06-04-2026", "01-05-2026", 
            "27-05-2026", "28-05-2026"
        ]

    async def backfill(self, days: int = 45):
        print(f"🕵️ Alpha Engine: Starting {days}-day historical backfill...")
        
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)
        
        # Get Business Days (Mon-Fri)
        business_days = pd.bdate_range(start=start_date, end=end_date)
        total_records = 0

        # Iterating FORWARD (Oldest to Newest) is critical for percent_change math
        for current_ts in business_days:
            current_date = current_ts.date()
            date_str = current_date.strftime("%d-%m-%Y")
            
            if date_str in self.holidays:
                print(f"🌴 Skipping holiday: {date_str}")
                continue

            print(f"📡 Processing: {date_str}")
            pdf_path = await self.download_report(target_date=current_date)
            
            if pdf_path:
                stocks = self.parse_pdf(pdf_path, current_date)
                if stocks:
                    saved = self.save(stocks)
                    total_records += saved
                    print(f"   ✅ Saved {saved} stocks.")
                
                if os.path.exists(pdf_path): os.remove(pdf_path)
                await asyncio.sleep(1.5) # Anti-ban delay
            else:
                print(f"   ⚠️ No report available for {date_str}")

        msg = f"🏁 *Backfill Complete*\nTotal Records: {total_records:,}\nMarket trends calculated."
        await self.notifier.send(msg)

if __name__ == "__main__":
    crawler = AlphaCrawler()
    asyncio.run(crawler.backfill(days=45))
