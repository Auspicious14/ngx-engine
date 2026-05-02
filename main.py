import os
import asyncio
import httpx
import pdfplumber
import urllib.parse
from datetime import datetime, date
from typing import List, Optional
from sqlalchemy import create_engine, Column, Integer, String, Numeric, BigInteger, Date, text, Index
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

load_dotenv()

# -------------------- DATABASE --------------------

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
    percent_change = Column(Numeric(10, 2), default=0.0)
    volume = Column(BigInteger)
    trade_date = Column(Date, nullable=False)

    __table_args__ = (Index('uix_symbol_date', 'symbol', 'trade_date', unique=True),)

# -------------------- VALIDATION --------------------

class StockSchema(BaseModel):
    symbol: str
    company_name: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    percent_change: float = 0.0
    volume: int
    trade_date: date = datetime.now().date()

    @field_validator('open_price', 'high_price', 'low_price', 'close_price', mode='before')
    @classmethod
    def clean_currency(cls, v):
        if not v or str(v).strip() in ["-", ""]: return 0.0
        if isinstance(v, str):
            cleaned = v.replace('₦', '').replace(',', '').strip()
            try: return float(cleaned)
            except ValueError: return 0.0
        return float(v or 0)

    @field_validator('volume', mode='before')
    @classmethod
    def clean_volume(cls, v):
        if not v or str(v).strip() in ["-", ""]: return 0
        if isinstance(v, str):
            cleaned = v.replace(',', '').strip()
            try: return int(float(cleaned))
            except ValueError: return 0
        return int(v or 0)

# -------------------- TELEGRAM NOTIFIER --------------------

class TelegramNotifier:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    async def send(self, message: str):
        if not self.token or not self.chat_id: return
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(self.url, json={
                "chat_id": self.chat_id, 
                "text": message, 
                "parse_mode": "Markdown"
            })

# -------------------- ENGINE --------------------

class NGXEngine:
    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL")
        if self.db_url and self.db_url.startswith("postgres://"):
            self.db_url = self.db_url.replace("postgres://", "postgresql://", 1)
        
        self.engine = create_engine(self.db_url, pool_pre_ping=True)
        self.Session = sessionmaker(bind=self.engine)
        self.notifier = TelegramNotifier()
        Base.metadata.create_all(self.engine)

    async def download_report(self, target_date: Optional[date] = None):
        d = target_date or datetime.now().date()
        date_str = d.strftime("%d-%m-%Y")
        url = f"https://doclib.ngxgroup.com/DownloadsContent/Daily%20Official%20List%20-%20Equities%20for%20{date_str}.pdf"
        
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            try:
                res = await client.get(url)
                if res.status_code == 200:
                    path = f"ngx_{date_str}.pdf"
                    with open(path, "wb") as f: f.write(res.content)
                    return path
            except Exception: return None
        return None

    def parse_pdf(self, path: str, trade_date: date) -> List[StockSchema]:
        data = []
        try:
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if not table: continue
                    header_idx = -1
                    for i, row in enumerate(table[:5]):
                        if row and any("Symbol" in str(cell) for cell in row if cell):
                            header_idx = i
                            break
                    if header_idx == -1: continue

                    for row in table[header_idx + 1:]:
                        if not row or len(row) < 12 or not row[0]: continue
                        symbol = str(row[0]).strip()
                        if not symbol.isupper(): continue
                        
                        close_p = row[5]
                        data.append(StockSchema(
                            symbol=symbol,
                            company_name=row[1],
                            open_price=row[3] or close_p,
                            high_price=close_p, 
                            low_price=close_p,
                            close_price=close_p,
                            volume=row[11],
                            trade_date=trade_date
                        ))
        except Exception: pass
        return data

    def save(self, stocks: List[StockSchema]):
        session = self.Session()
        saved_count = 0
        try:
            for stock in stocks:
                # Find the most recent closing price before this trade_date
                prev = session.query(StockPriceDB.close_price)\
                    .filter(StockPriceDB.symbol == stock.symbol)\
                    .filter(StockPriceDB.trade_date < stock.trade_date)\
                    .order_by(StockPriceDB.trade_date.desc()).first()

                if prev and float(prev[0]) > 0:
                    stock.percent_change = ((stock.close_price - float(prev[0])) / float(prev[0])) * 100

                stmt = text("""
                    INSERT INTO stock_prices (symbol, company_name, open_price, high_price, low_price, close_price, percent_change, volume, trade_date)
                    VALUES (:symbol, :company_name, :open_price, :high_price, :low_price, :close_price, :percent_change, :volume, :trade_date)
                    ON CONFLICT (symbol, trade_date)
                    DO UPDATE SET 
                        close_price = EXCLUDED.close_price,
                        percent_change = EXCLUDED.percent_change,
                        volume = EXCLUDED.volume;
                """)
                session.execute(stmt, stock.model_dump())
                saved_count += 1
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"DB Error: {e}")
        finally:
            session.close()
        return saved_count

    async def execute(self):
        pdf = await self.download_report()
        if not pdf: return
        today = datetime.now().date()
        stocks = self.parse_pdf(pdf, today)
        if not stocks: return
        saved = self.save(stocks)
        
        # Performance Analytics
        gainers = sorted([s for s in stocks if s.percent_change > 0], key=lambda x: x.percent_change, reverse=True)[:3]
        losers = sorted([s for s in stocks if s.percent_change < 0], key=lambda x: x.percent_change)[:3]
        
        msg = f"✅ *NGX Alpha Sync: {today}*\n"
        msg += f"📦 Records: {saved}\n\n"
        msg += "*🚀 Top Gainers:*\n" + ("\n".join([f"• {s.symbol}: +{s.percent_change:.2f}%" for s in gainers]) if gainers else "None") + "\n\n"
        msg += "*🔻 Top Losers:*\n" + ("\n".join([f"• {s.symbol}: {s.percent_change:.2f}%" for s in losers]) if losers else "None")
        
        await self.notifier.send(msg)
        if os.path.exists(pdf): os.remove(pdf)

if __name__ == "__main__":
    engine = NGXEngine()
    asyncio.run(engine.execute())
