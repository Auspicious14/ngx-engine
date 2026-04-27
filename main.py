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
from pydantic import BaseModel, validator
from dotenv import load_dotenv
import urllib.parse

# Load environment variables
load_dotenv()

# --- DATABASE SETUP ---
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

# --- VALIDATION MODELS ---
class StockSchema(BaseModel):
    symbol: str
    company_name: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    trade_date: datetime = datetime.now().date()

    @validator('open_price', 'high_price', 'low_price', 'close_price', pre=True)
    def clean_currency(cls, v):
        if isinstance(v, str):
            return float(v.replace(',', '').replace(' ', ''))
        return v

    @validator('volume', pre=True)
    def clean_volume(cls, v):
        if isinstance(v, str):
            return int(float(v.replace(',', '').replace(' ', '')))
        return v

# --- CORE LOGIC ---
class NGXEngine:
    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL")
        self.engine = create_engine(self.db_url)
        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        
        self.wa_phone = os.getenv("WHATSAPP_PHONE")
        self.wa_key = os.getenv("WHATSAPP_API_KEY")

    async def send_whatsapp(self, message: str):
        if not self.wa_phone or not self.wa_key:
            return
        encoded_msg = urllib.parse.quote(message)
        url = f"https://api.callmebot.com/whatsapp.php?phone={self.wa_phone}&text={encoded_msg}&apikey={self.wa_key}"
        async with httpx.AsyncClient() as client:
            await client.get(url)

    async def download_latest_report(self):
        """Downloads today's PDF from NGX DocLib"""
        # Note: Reports usually drop after 4:30 PM WAT
        date_str = datetime.now().strftime("%d%m%Y") 
        url = f"https://doclib.ngxgroup.com/DownloadsContent/Daily%20Official%20List%20-%20Equities%20for%20{date_str}.pdf"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    path = f"ngx_{date_str}.pdf"
                    with open(path, "wb") as f:
                        f.write(response.content)
                    return path
                return None
            except Exception as e:
                print(f"Download Error: {e}")
                return None

    def parse_report(self, pdf_path: str) -> List[StockSchema]:
        """Extracts equity table data from the PDF"""
        extracted = []
        with pdfplumber.open(pdf_path) as pdf:
            # We iterate pages looking for the 'Price List' table
            for page in pdf.pages[:5]: # Usually in the first few pages
                tables = page.extract_tables()
                for table in tables:
                    # Look for headers that match the NGX format
                    if table and any("Symbol" in str(cell) for cell in table[0]):
                        for row in table[1:]:
                            try:
                                # Ensure row has enough columns
                                if len(row) < 7 or not row[0]: continue
                                
                                item = StockSchema(
                                    symbol=row[0],
                                    company_name=row[1],
                                    open_price=row[2],
                                    high_price=row[3],
                                    low_price=row[4],
                                    close_price=row[5],
                                    volume=row[6]
                                )
                                extracted.append(item)
                            except Exception:
                                continue
                        return extracted
        return extracted

    def save_to_db(self, stocks: List[StockSchema]):
        """Saves or Updates records in Postgres"""
        session = self.Session()
        try:
            for stock in stocks:
                # Check if entry exists for today
                stmt = text("""
                    INSERT INTO stock_prices (symbol, company_name, open_price, high_price, low_price, close_price, volume, trade_date)
                    VALUES (:symbol, :company_name, :open_price, :high_price, :low_price, :close_price, :volume, :trade_date)
                    ON CONFLICT (symbol, trade_date) DO UPDATE SET
                        close_price = EXCLUDED.close_price,
                        volume = EXCLUDED.volume
                """)
                # Using raw SQL for the ON CONFLICT feature which is cleaner in Postgres
                session.execute(stmt, stock.dict())
            session.commit()
            return len(stocks)
        except Exception as e:
            session.rollback()
            print(f"DB Error: {e}")
            return 0
        finally:
            session.close()

    async def start(self):
        print(f"[{datetime.now()}] Starting NGX Ingestion...")
        pdf_path = await self.download_latest_report()
        
        if not pdf_path:
            await self.send_whatsapp("⚠️ NGX Report not ready yet. I'll check again later.")
            return

        stocks = self.parse_report(pdf_path)
        count = self.save_to_db(stocks)
        
        if count > 0:
            # Simple Insight: Find the highest volume stock
            top_mover = max(stocks, key=lambda x: x.volume)
            msg = (f"✅ *NGX Data Sync Success*\n"
                   f"📊 Total Equities: {count}\n"
                   f"🔥 Top Vol: {top_mover.symbol} ({top_mover.volume:,} units)")
            await self.send_whatsapp(msg)
            print(f"Success: {count} stocks processed.")
        else:
            print("No valid stock data found in the report.")

if __name__ == "__main__":
    engine = NGXEngine()
    asyncio.run(engine.start())

