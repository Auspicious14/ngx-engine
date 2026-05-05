import os
import asyncio
import httpx
import pdfplumber
import urllib.parse
import bs4
from datetime import datetime, date, timedelta
from typing import List, Optional, Tuple
from sqlalchemy import create_engine, Column, Integer, String, Numeric, BigInteger, Date, Float, text, Index
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

# Load local .env for testing; GitHub Actions will use Secrets
load_dotenv()

# --- DATABASE MODELS ---
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

class EarningsCalendar(Base):
    __tablename__ = 'earnings_calendar'
    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    period = Column(String)           
    expected_date = Column(Date)      
    actual_date = Column(Date)        
    dividend_yield = Column(Float)    

# --- DATA SCHEMAS ---
class StockSchema(BaseModel):
    symbol: str
    company_name: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    percent_change: float = 0.0
    volume: int
    trade_date: date
    # Metadata for alerts
    old_resistance: float = 0.0
    old_support: float = 0.0
    vol_increase: float = 0.0
    is_corporate_action: bool = False

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

# --- NOTIFICATION SERVICES ---
class TelegramNotifier:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    async def send(self, message: str):
        if not self.token or not self.chat_id: return
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                await client.post(self.url, json={"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"})
            except Exception as e: print(f"📡 TG Error: {e}")
            
class WhatsAppNotifier:
    def __init__(self):
        self.id_instance = os.getenv("GREEN_API_ID")
        self.api_token = os.getenv("GREEN_API_TOKEN")
        self.group_id = os.getenv("WHATSAPP_GROUP_ID") 
        self.api_url = os.getenv("WHATSAPP_API_URL", "https://7107.api.greenapi.com")
        self.base_url = f"{self.api_url}/waInstance{self.id_instance}/sendMessage/{self.api_token}"

    async def send(self, message: str):
        if not all([self.id_instance, self.api_token, self.group_id]):
            print("⚠️ WhatsApp configuration missing.")
            return False
            
        payload = {"chatId": self.group_id, "message": message}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(self.base_url, json=payload)
                return response.status_code == 200
            except Exception as e:
                print(f"📡 WhatsApp Error: {e}")
                return False

# --- CORE ENGINE ---
class NGXEngine:
    def __init__(self):
        db_url = os.getenv("DATABASE_URL")
        if db_url and db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        
        self.engine = create_engine(db_url, pool_pre_ping=True)
        self.Session = sessionmaker(bind=self.engine)
        self.wa = WhatsAppNotifier()
        self.tg = TelegramNotifier()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Referer": "https://ngxgroup.com/"
        }
        Base.metadata.create_all(self.engine)

    async def download_report(self, target_date: date) -> Optional[str]:
        """Stage 1: Redirects | Stage 2: Guessing | Stage 3: HTML Scrape"""
        async with httpx.AsyncClient(timeout=40.0, follow_redirects=True, headers=self.headers) as client:
            
            # --- STAGE 1: LATEST REDIRECTS ---
            latest_urls = [
                "https://ngxgroup.com/ngx-download/daily-official-list-equities/",
                "https://ngxgroup.com/ngx-download/market-data-pricelist-2/"
            ]
            for url in latest_urls:
                try:
                    res = await client.get(url)
                    if res.status_code == 200 and b"%PDF" in res.content[:4]:
                        path = f"ngx_latest_{target_date}.pdf"
                        with open(path, "wb") as f: f.write(res.content)
                        return path
                except Exception: continue

            # --- STAGE 2: DATE-STRING GUESSING ---
            delimiters = ["-", " ", ".", ""]
            for sep in delimiters:
                date_str = target_date.strftime(f"%d{sep}%m{sep}%Y")
                encoded = urllib.parse.quote(date_str)
                patterns = [
                    f"Daily%20Official%20List%20-%20Equities%20for%20{encoded}.pdf",
                    f"DAILY%20SUMMARY%20FOR%20{encoded}.pdf",
                    f"Daily%20Summary%20for%20{encoded}.pdf"
                ]
                for p in patterns:
                    url = f"https://doclib.ngxgroup.com/DownloadsContent/{p}"
                    try:
                        res = await client.get(url)
                        if res.status_code == 200 and b"%PDF" in res.content[:4]:
                            path = f"ngx_guess_{target_date}.pdf"
                            with open(path, "wb") as f: f.write(res.content)
                            return path
                    except Exception: continue

            # --- STAGE 3: HTML WEB SCRAPE (RELIABLE FAILOVER) ---
            print("🌐 PDF fallback failed. Attempting HTML scrape...")
            try:
                web_url = "https://ngxgroup.com/exchange/data/equities-price-list/"
                res = await client.get(web_url)
                if res.status_code == 200 and "table" in res.text:
                    path = f"ngx_scrape_{target_date}.html"
                    with open(path, "w", encoding="utf-8") as f: f.write(res.text)
                    return path
            except Exception as e: print(f"🌐 Scrape Error: {e}")

        return None

    def parse_source(self, path: str, trade_date: date) -> List[StockSchema]:
        if path.endswith(".pdf"):
            return self.parse_pdf(path, trade_date)
        elif path.endswith(".html"):
            return self.parse_html(path, trade_date)
        return []

    def parse_pdf(self, path: str, trade_date: date) -> List[StockSchema]:
        data = []
        try:
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if not table: continue
                    header_idx = -1
                    for i, row in enumerate(table[:10]):
                        if row and any(x and "Symbol" in str(x) for x in row):
                            header_idx = i
                            break
                    if header_idx == -1: continue

                    for row in table[header_idx + 1:]:
                        if not row or len(row) < 10 or not row[0]: continue
                        symbol = str(row[0]).strip()
                        if not symbol.isupper() or " " in symbol: continue
                        close_p = row[5]
                        data.append(StockSchema(
                            symbol=symbol, company_name=row[1],
                            open_price=row[3] or close_p, high_price=close_p, 
                            low_price=close_p, close_price=close_p,
                            volume=row[11] if len(row) > 11 else row[-1], 
                            trade_date=trade_date
                        ))
        except Exception as e: print(f"PDF Parse Error: {e}")
        return data

    def parse_html(self, path: str, trade_date: date) -> List[StockSchema]:
        stocks = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                soup = bs4.BeautifulSoup(f.read(), "html.parser")
            table = soup.find("table", {"id": "table_1"}) or soup.find("table")
            if not table: return []

            for row in table.find_all("tr")[1:]:
                cols = row.find_all("td")
                if len(cols) < 10: continue
                symbol = cols[0].get_text(strip=True)
                if not symbol.isupper(): continue
                stocks.append(StockSchema(
                    symbol=symbol, company_name=cols[1].get_text(strip=True),
                    open_price=cols[3].get_text(strip=True),
                    high_price=cols[4].get_text(strip=True),
                    low_price=cols[5].get_text(strip=True),
                    close_price=cols[6].get_text(strip=True),
                    volume=cols[10].get_text(strip=True),
                    trade_date=trade_date
                ))
        except Exception as e: print(f"HTML Parse Error: {e}")
        return stocks

    def save(self, stocks: List[StockSchema]):
        session = self.Session()
        try:
            for stock in stocks:
                prev = session.query(StockPriceDB.close_price).filter(
                    StockPriceDB.symbol == stock.symbol, StockPriceDB.trade_date < stock.trade_date
                ).order_by(StockPriceDB.trade_date.desc()).first()

                if prev and float(prev[0]) > 0:
                    stock.percent_change = ((stock.close_price - float(prev[0])) / float(prev[0])) * 100

                stmt = text("""
                    INSERT INTO stock_prices (symbol, company_name, open_price, high_price, low_price, close_price, percent_change, volume, trade_date)
                    VALUES (:symbol, :company_name, :open_price, :high_price, :low_price, :close_price, :percent_change, :volume, :trade_date)
                    ON CONFLICT (symbol, trade_date) DO UPDATE SET 
                    close_price = EXCLUDED.close_price, percent_change = EXCLUDED.percent_change, volume = EXCLUDED.volume;
                """)
                session.execute(stmt, stock.model_dump(exclude={'old_resistance', 'old_support', 'vol_increase', 'is_corporate_action'}))
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"DB Error: {e}")
        finally: session.close()

    def get_market_alerts(self, stocks: List[StockSchema]):
        session = self.Session()
        breakouts, breakdowns, momentum, volume_spikes = [], [], [], []
        try:
            for stock in stocks:
                stock.is_corporate_action = abs(stock.percent_change) > 10.5
                levels = session.query(text("MAX(high_price)"), text("MIN(low_price)")).from_statement(text("""
                    SELECT high_price, low_price FROM stock_prices 
                    WHERE symbol = :symbol AND trade_date < :today
                    ORDER BY trade_date DESC LIMIT 30
                """)).params(symbol=stock.symbol, today=stock.trade_date).first()

                if levels:
                    res, sup = (float(levels[0]) if levels[0] else 0), (float(levels[1]) if levels[1] else 0)
                    if res > 0 and stock.close_price > res:
                        stock.old_resistance = res
                        breakouts.append(stock)
                    if sup > 0 and stock.close_price < sup and not stock.is_corporate_action:
                        stock.old_support = sup
                        breakdowns.append(stock)

                if stock.percent_change >= 5.0 and not stock.is_corporate_action:
                    momentum.append(stock)

                avg_vol = session.execute(text("""
                    SELECT AVG(volume) FROM (
                        SELECT volume FROM stock_prices 
                        WHERE symbol = :symbol AND trade_date < :today
                        ORDER BY trade_date DESC LIMIT 10
                    ) as subquery
                """), {"symbol": stock.symbol, "today": stock.trade_date}).scalar()

                if avg_vol and stock.volume > (float(avg_vol) * 2):
                    stock.vol_increase = round(stock.volume / float(avg_vol), 1)
                    volume_spikes.append(stock)
            return breakouts, breakdowns, momentum, volume_spikes
        finally: session.close()

    async def send_daily_recap(self, stocks, breakouts, breakdowns, momentum, spikes):
        sorted_stocks = sorted(stocks, key=lambda x: x.percent_change, reverse=True)
        gainers, losers = sorted_stocks[:5], sorted(stocks, key=lambda x: x.percent_change)[:5]
        
        msg = f"🚀 *NGX ALPHA INTELLIGENCE* ({datetime.now().strftime('%d %b %Y')})\n"
        msg += "━━━━━━━━━━━━━━━━\n\n"
        msg += "📈 *TOP GAINERS*\n"
        for s in gainers: msg += f"• *{s.symbol}* (+{s.percent_change:.2f}%)\n"
        
        msg += "\n📉 *TOP LOSERS*\n"
        for s in losers: msg += f"• *{s.symbol}* ({s.percent_change:.2f}%)\n"
        
        if breakouts:
            msg += "\n🔓 *BREAKOUTS*\n"
            for s in breakouts[:3]: msg += f"• *{s.symbol}* (Broke ₦{s.old_resistance})\n"

        if spikes:
            msg += "\n🔊 *VOLUME SPIKES*\n"
            for s in spikes[:3]: msg += f"• *{s.symbol}* ({s.vol_increase}x Vol)\n"

        msg += "\n💡 *TIP:* Breakout + Volume = Entry. 📊"
        await self.tg.send(msg)
        await self.wa.send(msg)

# --- EXECUTION ---
if __name__ == "__main__":
    async def run():
        engine = NGXEngine()
        today = datetime.now().date()
        source_path = await engine.download_report(today)
        
        if not source_path:
            print("🛑 No source found.")
            return

        stocks = engine.parse_source(source_path, today)
        if stocks:
            engine.save(stocks)
            alerts = engine.get_market_alerts(stocks)
            await engine.send_daily_recap(stocks, *alerts)
            if os.path.exists(source_path): os.remove(source_path)
            print(f"✅ Processed {len(stocks)} stocks.")
        else:
            print("🛑 Parsing failed.")

    asyncio.run(run())
