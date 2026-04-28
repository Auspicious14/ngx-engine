import os
import asyncio
import httpx
import pdfplumber
import pandas as pd
import urllib.parse
from datetime import datetime
from typing import List, Optional

# SQLAlchemy 2.0 Imports
from sqlalchemy import create_engine, Column, Integer, String, Numeric, BigInteger, Date, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Pydantic 2.0 Imports
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- DATABASE CONFIGURATION (SQLAlchemy 2.0 Style) ---
class Base(DeclarativeBase):
    pass

class StockPriceDB(Base):
    __tablename__ = 'stock_prices'
    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False)
    company_name = Column(String(255))
    open_price = Column(Numeric(10, 2))
    high_price = Column(Numeric(10, 2))
    low_price = Column(Numeric(10, 2))
    close_price = Column(Numeric(10, 2))
    volume = Column(BigInteger)
    trade_date = Column(Date, default=datetime.now().date())

# --- DATA VALIDATION (Pydantic 2.0 Style) ---
class StockSchema(BaseModel):
    symbol: str
    company_name: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    trade_date: datetime = datetime.now().date()

    @field_validator('open_price', 'high_price', 'low_price', 'close_price', mode='before')
    @classmethod
    def clean_currency(cls, v):
        if isinstance(v, str):
            # Removes ₦, commas, and whitespace
            return float(v.replace('₦', '').replace(',', '').replace(' ', ''))
        return float(v or 0)

    @field_validator('volume', mode='before')
    @classmethod
    def clean_volume(cls, v):
        if isinstance(v, str):
            return int(float(v.replace(',', '').replace(' ', '')))
        return int(v or 0)

# --- TELEGRAM NOTIFIER ---
class TelegramNotifier:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

    async def send(self, message: str):
        if not self.token or not self.chat_id:
            print("Telegram credentials missing. Check your .env or GitHub Secrets.")
            return
        
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id, 
            "text": message, 
            "parse_mode": "Markdown"
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload)
                if response.status_code != 200:
                    print(f"Telegram Error: {response.text}")
            except Exception as e:
                print(f"Failed to send Telegram message: {e}")

# --- NGX INGESTION ENGINE ---
def __init__(self):
        # 1. Get the raw URL
        raw_url = os.getenv("DATABASE_URL")
        if not raw_url:
            raise ValueError("DATABASE_URL not found in environment variables.")

        try:
            # 2. Manually split to avoid the 'Port' ValueError
            # Format expected: postgresql://user:password@host:port/dbname
            prefix, rest = raw_url.split("://")
            user_pass, host_port_db = rest.rsplit("@", 1)
            
            if ":" in user_pass:
                user, password = user_pass.split(":", 1)
                # Encode the password to handle special characters (@, #, &, etc.)
                password = urllib.parse.quote_plus(password)
                
                # Reconstruct the authenticated part
                auth_part = f"{user}:{password}"
            else:
                auth_part = user_pass

            # 3. Handle the host/db part and force SSL
            final_url = f"{prefix}://{auth_part}@{host_port_db}"
            if "sslmode" not in final_url:
                separator = "&" if "?" in final_url else "?"
                final_url += f"{separator}sslmode=require"
            
            self.db_url = final_url
            
        except Exception as e:
            # Fallback to the raw URL if manual parsing fails
            print(f"Manual parse failed, using raw: {e}")
            self.db_url = raw_url

        self.engine = create_engine(self.db_url)
        self.Session = sessionmaker(bind=self.engine)
        self.notifier = TelegramNotifier()
        
        # Initialize tables
        Base.metadata.create_all(self.engine)
    async def download_report(self):
        """Fetches the Daily Official List PDF from NGX DocLib"""
        date_str = datetime.now().strftime("%d%m%Y")
        url = f"https://doclib.ngxgroup.com/DownloadsContent/Daily%20Official%20List%20-%20Equities%20for%20{date_str}.pdf"
        
        print(f"Attempting to download: {url}")
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    filename = f"ngx_{date_str}.pdf"
                    with open(filename, "wb") as f:
                        f.write(response.content)
                    return filename
                return None
            except Exception as e:
                print(f"Download Error: {e}")
                return None

    def parse_pdf(self, path: str) -> List[StockSchema]:
        """Parses the NGX PDF to extract equity data"""
        extracted = []
        try:
            with pdfplumber.open(path) as pdf:
                # Scan the first 5 pages for the 'Equities' price list
                for page in pdf.pages[:5]:
                    tables = page.extract_tables()
                    for table in tables:
                        # Identify the table by checking for 'Symbol' in the header
                        if table and any("Symbol" in str(cell) for cell in table[0]):
                            for row in table[1:]:
                                try:
                                    if not row[0] or len(row) < 7: continue
                                    
                                    stock = StockSchema(
                                        symbol=row[0],
                                        company_name=row[1],
                                        open_price=row[2],
                                        high_price=row[3],
                                        low_price=row[4],
                                        close_price=row[5],
                                        volume=row[6]
                                    )
                                    extracted.append(stock)
                                except Exception:
                                    continue
                            return extracted
        except Exception as e:
            print(f"Parsing error: {e}")
        return extracted

    def save_to_supabase(self, stocks: List[StockSchema]):
        """Upserts data into Supabase (Postgres)"""
        session = self.Session()
        try:
            for stock in stocks:
                # Using PostgreSQL ON CONFLICT for upsert logic
                stmt = text("""
                    INSERT INTO stock_prices (symbol, company_name, open_price, high_price, low_price, close_price, volume, trade_date)
                    VALUES (:symbol, :company_name, :open_price, :high_price, :low_price, :close_price, :volume, :trade_date)
                    ON CONFLICT (symbol, trade_date) DO UPDATE SET
                        close_price = EXCLUDED.close_price,
                        volume = EXCLUDED.volume;
                """)
                session.execute(stmt, stock.model_dump())
            session.commit()
            return len(stocks)
        except Exception as e:
            session.rollback()
            print(f"Database Error: {e}")
            return 0
        finally:
            session.close()

    async def execute(self):
        """The main workflow execution"""
        print(f"[{datetime.now()}] Ingestion started.")
        
        pdf_path = await self.download_report()
        
        if not pdf_path:
            await self.notifier.send("⚠️ *NGX Report Missing*\nThe Daily Official List PDF is not yet available. This is normal if the market just closed.")
            return

        stocks = self.parse_pdf(pdf_path)
        
        if not stocks:
            await self.notifier.send("❌ *Parsing Failed*\nFound the PDF, but couldn't find the Equities table. The format may have changed.")
            return

        saved_count = self.save_to_supabase(stocks)
        
        if saved_count > 0:
            top_mover = max(stocks, key=lambda x: x.volume)
            summary = (
                f"✅ *NGX Data Sync Success*\n"
                f"📅 Date: {datetime.now().strftime('%Y-%m-%d')}\n"
                f"📈 Stocks Tracked: {saved_count}\n\n"
                f"🔥 *Top Volume:* {top_mover.symbol}\n"
                f"📦 Volume: {top_mover.volume:,}\n"
                f"💰 Close: ₦{top_mover.close_price:.2f}"
            )
            await self.notifier.send(summary)
            print(f"Processed {saved_count} stocks.")
        else:
            print("Process complete, but no new records were saved.")

if __name__ == "__main__":
    engine = NGXEngine()
    asyncio.run(engine.execute())
