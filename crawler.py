import asyncio
import os
import pandas as pd
from datetime import datetime, timedelta
from main import NGXEngine

class AlphaCrawler(NGXEngine):
    async def backfill(self, days: int = 45):
        print(f"🕵️ Alpha Engine: Starting {days}-day historical backfill...")
        
        # 1. Define range (Oldest -> Today)
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)
        
        # 2. Get Business Days (Mon-Fri) only
        business_days = pd.bdate_range(start=start_date, end=end_date)
        
        total_records = 0

        # 3. Iterate FORWARD so each day has access to the day before
        for current_ts in business_days:
            current_date = current_ts.date()
            date_str = current_date.strftime("%d-%m-%Y")
            
            print(f"📡 Downloading report for: {date_str}")
            pdf_path = await self.download_report(target_date=current_date)
            
            if pdf_path:
                stocks = self.parse_pdf(pdf_path, current_date)
                if stocks:
                    saved = self.save(stocks)
                    total_records += saved
                    print(f"   ✅ Saved {saved} stocks.")
                
                if os.path.exists(pdf_path): os.remove(pdf_path)
                
                # Ethical pause
                await asyncio.sleep(1.2)
            else:
                print(f"   ⚠️ No report available for {date_str}")

        report = f"🏁 *Backfill Complete*\nTotal Records: {total_records:,}\nMemory primed for Alpha Engine."
        await self.notifier.send(report)
        print("Done.")

if __name__ == "__main__":
    crawler = AlphaCrawler()
    # Run the backfill (Default 45 days)
    asyncio.run(crawler.backfill(days=45))
