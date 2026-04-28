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
            print("Telegram credentials missing.")
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
class NGXEngine:
    def __init__(self):
        raw_url = os.getenv("DATABASE_URL")
        if not raw_url:
            raise ValueError("DATABASE_URL not found.")

        try:
            prefix, rest = raw_url.split("://")
            user_pass, host_port_db = rest.rsplit("@", 1)
            
            if ":" in user_pass:
                user, password = user_pass.split(":", 1)
                password = urllib.parse.quote_plus(password)
                auth_part = f"{user}:{password}"
            else:
                auth_part = user_pass

            # Force IPv4 by using the pooler address if you have it
            final_url = f"{prefix}://{auth_part}@{host_port_db}"
            
            # Ensure SSL and add a connection timeout
            if "sslmode" not in final_url:
                separator = "&" if "?" in final_url else "?"
                final_url += f"{separator}sslmode=require&connect_timeout=10"
            
            self.db_url = final_url
        except Exception:
            self.db_url = raw_url

        # Use 'pool_pre_ping' to handle dropped connections
        self.engine = create_engine(self.db_url, pool_pre_ping=True)
        self.Session = sessionmaker(bind=self.engine)
        self.notifier = TelegramNotifier()
        Base.metadata.create_all(self.engine)
    async def download_report(self):
        date_str = datetime.now().strftime("%d%m%Y")
        url = f"https://doclib.ngxgroup.com/DownloadsContent/Daily%20Official%20List%20-%20Equities%20for%20{date_str}.pdf"
        
        print(f"Attempting download: {url}")
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
        try:
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages[:5]:
                    tables = page.extract_tables()
                    for table in tables:
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
        session = self.Session()
        try:
            for stock in stocks:
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
        print(f"[{datetime.now()}] Ingestion started.")
        pdf_path = await self.download_report()
        
        if not pdf_path:
            await self.notifier.send("⚠️ *NGX Report Missing*\nNot available yet. Market usually uploads after 4:30 PM WAT.")
            return

        stocks = self.parse_pdf(pdf_path)
        if not stocks:
            await self.notifier.send("❌ *Parsing Failed*\nTable not found in PDF.")
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
        else:
            print("No new records saved.")

if __name__ == "__main__":
    engine = NGXEngine()
    asyncio.run(engine.execute())
