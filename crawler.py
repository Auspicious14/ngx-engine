import os
import asyncio
import httpx
from datetime import datetime, timedelta
import pandas as pd
from main import NGXEngine  # Assumes your previous logic is in main.py

class NGXCrawler(NGXEngine):
    def __init__(self):
        super().__init__()
        # Official NGX 2026 Trading Holidays
        self.holidays = [
            "01-01-2026", # New Year
            "20-03-2026", # Eid-el-Fitr
            "23-03-2026", # Eid-el-Fitr Holiday
            "03-04-2026", # Good Friday
            "06-04-2026", # Easter Monday
            "01-05-2026", # Workers' Day
            "27-05-2026", # Eid-el-Kabir
            "28-05-2026", # Eid-el-Kabir Holiday
        ]

    async def backfill(self, days: int = 60):
        """Iterates backward to fill historical price data."""
        print(f"🕵️ Alpha Engine: Starting backfill for {days} days...")
        
        # Calculate date range (Business days only)
        end_date = datetime.now() - timedelta(days=1)
        start_date = end_date - timedelta(days=days)
        
        # bdate_range automatically skips Saturday/Sunday
        business_days = pd.bdate_range(start=start_date, end=end_date)
        date_strings = business_days.strftime("%d-%m-%Y").tolist()

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for date_str in reversed(date_strings):
                if date_str in self.holidays:
                    print(f"🌴 Skipping {date_str} (Market Holiday)")
                    continue

                url = f"https://doclib.ngxgroup.com/DownloadsContent/Daily%20Official%20List%20-%20Equities%20for%20{date_str}.pdf"
                print(f"📡 Requesting: {date_str}...")

                try:
                    response = await client.get(url)
                    
                    if response.status_code == 200:
                        temp_pdf = f"temp_{date_str}.pdf"
                        with open(temp_pdf, "wb") as f:
                            f.write(response.content)
                        
                        # Use your existing parser
                        stocks = self.parse_pdf(temp_pdf)
                        
                        if stocks:
                            # CRITICAL: Override trade_date with the historical date
                            current_date_obj = datetime.strptime(date_str, "%d-%m-%Y").date()
                            for s in stocks:
                                s.trade_date = current_date_obj
                            
                            saved_count = self.save(stocks)
                            print(f"✅ Success: Saved {saved_count} stocks for {date_str}")
                        
                        os.remove(temp_pdf) # Clean up
                    elif response.status_code == 404:
                        print(f"🛑 404: No report found for {date_str}")
                    else:
                        print(f"⚠️ Unexpected status {response.status_code} for {date_str}")

                except Exception as e:
                    print(f"❌ Error processing {date_str}: {str(e)}")
                
                # Small delay to keep the NGX server happy
                await asyncio.sleep(1.5)

if __name__ == "__main__":
    crawler = NGXCrawler()
    # Run the backfill (adjust 'days' to go further back)
    asyncio.run(crawler.backfill(days=45))
