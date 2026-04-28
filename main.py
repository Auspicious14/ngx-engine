import os
import asyncio
import httpx
import pdfplumber
import pandas as pd
from datetime import datetime
from typing import List, Optional
from sqlalchemy import create_engine, Column, Integer, String, Numeric, BigInteger, Date, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- DATABASE CONFIGURATION ---
Base = declarative_base()

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

# --- DATA VALIDATION (PYDANTIC) ---
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
            # Removes currency symbols, commas, and whitespace
            return float(v.replace(',', '').replace(' ', '').replace('₦', ''))
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
            print("Telegram credentials missing.")
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}
        async with httpx.AsyncClient() as client:
            try:
                await client.post(url, json=payload)
            except Exception as e:
                print(f"Telegram Notification Failed: {e}")

# --- NGX INGESTION ENGINE ---
class NGXEngine:
    def __init__(self):
        # Supabase usually requires sslmode=require
        db_url = os.getenv("DATABASE_URL")
        if "sslmode" not in db_url:
            db_url += "?sslmode=require"
            
        self.engine = create_engine(db_url)
        self.Session = sessionmaker(bind=self.engine)
        self.notifier = TelegramNotifier()
        Base.metadata.create_all(self.engine)

    async def download_report(self):
        """Fetches the Daily Official List PDF from NGX"""
        date_str = datetime.now().strftime("%d%m%Y")
        url = f"https://doclib.ngxgroup.com/DownloadsContent/Daily%20Official%20List%20-%20Equities%20for%20{date_str}.pdf"
        
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
        extracted = []
        with pdfplumber.open(path) as pdf:
            # We check the first 5 pages for the 'Equities' price list table
            for page in pdf.pages[:5]:
                tables = page.extract_tables()
                for table in tables:
                    # Logic: Look for the table that has 'Symbol' in the first row
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
        return extracted

    def save_to_supabase(self, stocks: List[StockSchema]):
        session = self.Session()
        try:
            for stock in stocks:
                # PostgreSQL Upsert (ON CONFLICT)
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
        print(f"🚀 Execution started at {datetime.now()}")
        path = await self.download_report()
        
        if not path:
            await self.notifier.send("⚠️ *NGX Report Missing*\nThe Daily Official List PDF is not yet available on the NGX server.")
            return

        stocks = self.parse_pdf(path)
        if not stocks:
            await self.notifier.send("❌ *Parsing Error*\nFound the PDF but could not extract the Equities table.")
            return

        saved_count = self.save_to_supabase(stocks)
        
        if saved_count > 0:
            top_mover = max(stocks, key=lambda x: x.volume)
            summary = (
                f"✅ *NGX Ingestion Successful*\n"
                f"📅 Date: {datetime.now().strftime('%Y-%m-%d')}\n"
                f"📈 Stocks Tracked: {saved_count}\n\n"
                f"🔥 *Top Volume:* {top_mover.symbol}\n"
                f"📦 Volume: {top_mover.volume:,}\n"
                f"💰 Close: ₦{top_mover.close_price:.2f}"
            )
            await self.notifier.send(summary)
            print(f"Ingested {saved_count} stocks successfully.")

if __name__ == "__main__":
    engine = NGXEngine()
    asyncio.run(engine.execute())
