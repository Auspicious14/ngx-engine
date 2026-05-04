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

class EarningsCalendar(Base):
    __tablename__ = 'earnings_calendar'
    
    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    period = Column(String)           # e.g., "Q2 2026" or "FY 2025"
    expected_date = Column(Date)      # NGX Deadline
    actual_date = Column(Date)        # When it was actually released
    dividend_yield = Column(Float)    # Optional: If they announce a dividend
    
class TelegramNotifier:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    async def send(self, message: str):
        if not self.token or not self.chat_id: return
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(self.url, json={"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"})
            
class WhatsAppNotifier:
    def __init__(self):
        self.api_key = os.getenv("WHATSAPP_API_KEY")
        self.phone_number = os.getenv("WHATSAPP_PHONE") # Your instance/sender ID
        self.group_id = os.getenv("WHATSAPP_GROUP_ID")
        # Update this URL based on your specific provider (e.g., UltraMsg, Whapi)
        self.base_url = "https://api.ultramsg.com/instanceXXXX/messages/chat" 

    async def send(self, message: str):
        if not self.api_key or not self.group_id:
            return
            
        payload = {
            "token": self.api_key,
            "to": self.group_id,
            "body": message
        }
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                # Most gateways use a simple POST request
                response = await client.post(self.base_url, data=payload)
                return response.status_code == 200
            except Exception as e:
                print(f"WhatsApp Error: {e}")
                return False
                
class NGXEngine:
    def __init__(self):
        db_url = os.getenv("DATABASE_URL")
        if db_url and db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        
        self.engine = create_engine(db_url, pool_pre_ping=True)
        self.Session = sessionmaker(bind=self.engine)
        self.notifier = TelegramNotifier()
        Base.metadata.create_all(self.engine)

    def get_market_alerts(self, stocks: List[StockSchema]):
        session = self.Session()
        breakouts = []       # Price > 30-day Resistance
        breakdowns = []      # Price < 30-day Support
        momentum = []        # Price Gain > 5%
        volume_spikes = []   # Volume > 2x 10-day Average
        
        try:
            for stock in stocks:
                # 1. TECHNICAL LEVELS (Resistance & Support)
                levels = self.detect_levels(stock.symbol, stock.trade_date)
                if levels:
                    res = float(levels[0]) if levels[0] else 0
                    sup = float(levels[1]) if levels[1] else 0

                    # Resistance Breakout (The Ceiling)
                    if res > 0 and stock.close_price > res:
                        stock.old_resistance = res
                        breakouts.append(stock)
                    
                    # Support Breakdown (The Floor)
                    if sup > 0 and stock.close_price < sup:
                        stock.old_support = sup
                        breakdowns.append(stock)

                # 2. PRICE MOMENTUM (The 5% Rule)
                if stock.percent_change >= 5.0:
                    momentum.append(stock)

                # 3. VOLUME SPIKES (The 'Smart Money' Tracker)
                # Compares today's volume to the average of the last 10 days
                avg_vol = session.query(text("AVG(volume)")).from_statement(text("""
                    SELECT volume FROM stock_prices 
                    WHERE symbol = :symbol AND trade_date < :today
                    ORDER BY trade_date DESC LIMIT 10
                """)).params(symbol=stock.symbol, today=stock.trade_date).scalar()

                if avg_vol and stock.volume > (float(avg_vol) * 2):
                    stock.vol_increase = round(stock.volume / float(avg_vol), 1)
                    volume_spikes.append(stock)

            return breakouts, breakdowns, momentum, volume_spikes
        finally:
            session.close()

    def get_earnings_watch(self):
        session = self.Session()
        today = datetime.now().date()
        two_weeks_out = today + timedelta(days=14)
        
        # Look for stocks that haven't reported yet but are nearing a deadline
        # OR stocks that just reported in the last 3 days (to explain price jumps)
        upcoming = session.query(EarningsCalendar).filter(
            EarningsCalendar.actual_date == None,
            EarningsCalendar.expected_date <= two_weeks_out
        ).all()
        
        just_reported = session.query(EarningsCalendar).filter(
            EarningsCalendar.actual_date >= (today - timedelta(days=3))
        ).all()
        
        return upcoming, just_reported
            
    async def send_daily_recap(self, stocks, breakouts, breakdowns, momentum, spikes):
        today_str = datetime.now().strftime("%d %b %Y")
        msg = f"🚀 *NGX ALPHA INTELLIGENCE* ({today_str})\n"
        msg += "━━━━━━━━━━━━━━━━\n\n"

        # --- SECTION 1: BREAKOUTS ---
        if breakouts:
            msg += "🔓 *RESISTANCE BREAKOUTS*\n"
            msg += "_WHY: These stocks broke their 'ceiling'. It means buyers are finally stronger than sellers, often leading to a new rally._\n"
            for s in breakouts[:3]:
                msg += f"• *{s.symbol}*: ₦{s.close_price} (Broke ₦{s.old_resistance})\n"
            msg += "\n"

        # --- SECTION 2: BREAKDOWNS ---
        if breakdowns:
            msg += "⚠️ *SUPPORT BREAKDOWNS*\n"
            msg += "_WHY: The 'floor' collapsed. This usually happens on bad news or when big investors are exiting. Be very careful here!_\n"
            for s in breakdowns[:3]:
                msg += f"• *{s.symbol}*: ₦{s.close_price} (Below ₦{s.old_support})\n"
            msg += "\n"

        # --- SECTION 3: MOMENTUM ---
        if momentum:
            msg += "🔥 *HIGH MOMENTUM (5%+)*\n"
            msg += "_WHY: These stocks are moving fast. Watch for news like earnings or dividend announcements that might be driving this jump._\n"
            for s in momentum[:3]:
                msg += f"• *{s.symbol}*: +{s.percent_change:.2f}%\n"
            msg += "\n"

        # --- SECTION 4: VOLUME ---
        if spikes:
            msg += "🔊 *UNUSUAL VOLUME*\n"
            msg += "_WHY: High volume means 'Smart Money' (Institutional banks) is active. It confirms that the price move is backed by real money._\n"
            for s in spikes[:3]:
                msg += f"• *{s.symbol}*: {s.vol_increase}x Normal Vol\n"
            msg += "\n"
            
        if upcoming_earn or recently_reported:
            msg += "📝 *EARNINGS & DIVIDEND WATCH*\n"
            msg += "_WHY: Financial results are the biggest drivers of price. 'Just Reported' explains today's move, while 'Upcoming' warns of future volatility._\n\n"
            
            for e in recently_reported:
                msg += f"✅ *{e.symbol}*: Just Released {e.period} results! Check the PDF for dividend news.\n"
            
            for e in upcoming_earn:
                days_left = (e.expected_date - datetime.now().date()).days
                msg += f"⏳ *{e.symbol}*: {e.period} results due in {days_left} days. Expect price swings.\n"
            msg += "\n"
            
        # --- FINAL TRADER TIP ---
        msg += "💡 *TRADER'S TIP*\n"
        if upcoming_earn:
            msg += "Be careful buying stocks in the 'Earnings Watch' list today. A bad report can break even the strongest support level! 📉"
        else:
            msg += "The 'Perfect Trade' is a **Breakout** + **High Volume** + **5% Gain**. When all three hit at once, it’s a high-probability signal! 📊"
        # Send triggers
        await self.notifier.send(msg)
        wa = WhatsAppNotifier()
        await wa.send(msg)
        
    async def download_report(self, target_date: date):
        # 1. Define possible delimiters used by NGX staff
        delimiters = ["-", " ", "", "."]
        
        # 2. Build a list of candidate date strings
        # We start with the correct month
        months_to_try = [target_date.month]
        if target_date.month == 4: # Specific fix for the April/February mixup
            months_to_try.append(2)
            
        candidate_strings = []
        for m in months_to_try:
            # Check if the day exists in the target month (e.g., skip Feb 29/30/31)
            try:
                # This creates a date object for the candidate month/day/year
                candidate_date = target_date.replace(month=m)
                
                for sep in delimiters:
                    # Generates formats like "15-04-2026", "15 04 2026", etc.
                    fmt = f"%d{sep}%m{sep}%Y"
                    candidate_strings.append(candidate_date.strftime(fmt))
            except ValueError:
                # If target_date is April 29, and we try month=2, it raises ValueError.
                # We catch it here and simply move to the next month/delimiter.
                continue

        # 3. Execution loop
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for attempt_str in candidate_strings:
                encoded_date = urllib.parse.quote(attempt_str)
                url = f"https://doclib.ngxgroup.com/DownloadsContent/Daily%20Official%20List%20-%20Equities%20for%20{encoded_date}.pdf"
                
                try:
                    res = await client.get(url)
                    if res.status_code == 200:
                        # Always save locally with a consistent, standard name
                        path = f"ngx_{target_date.strftime('%Y-%m-%d')}.pdf"
                        with open(path, "wb") as f:
                            f.write(res.content)
                        
                        actual_date_str = target_date.strftime("%d-%m-%Y")
                        if attempt_str != actual_date_str:
                            print(f"   🎯 Recovery: Found {actual_date_str} hidden as '{attempt_str}'")
                        return path
                except Exception:
                    continue
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
                            symbol=symbol, company_name=row[1],
                            open_price=row[3] or close_p, high_price=close_p, 
                            low_price=close_p, close_price=close_p,
                            volume=row[11], trade_date=trade_date
                        ))
        except Exception as e: print(f"Parse Error: {e}")
        return data

    def save(self, stocks: List[StockSchema]):
        session = self.Session()
        saved_count = 0
        try:
            for stock in stocks:
                # Calculate percent change against previous day in DB
                prev = session.query(StockPriceDB.close_price).filter(
                    StockPriceDB.symbol == stock.symbol,
                    StockPriceDB.trade_date < stock.trade_date
                ).order_by(StockPriceDB.trade_date.desc()).first()

                if prev and float(prev[0]) > 0:
                    stock.percent_change = ((stock.close_price - float(prev[0])) / float(prev[0])) * 100

                stmt = text("""
                    INSERT INTO stock_prices (symbol, company_name, open_price, high_price, low_price, close_price, percent_change, volume, trade_date)
                    VALUES (:symbol, :company_name, :open_price, :high_price, :low_price, :close_price, :percent_change, :volume, :trade_date)
                    ON CONFLICT (symbol, trade_date) DO UPDATE SET 
                    close_price = EXCLUDED.close_price, percent_change = EXCLUDED.percent_change, volume = EXCLUDED.volume;
                """)
                session.execute(stmt, stock.model_dump())
                saved_count += 1
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"DB Error: {e}")
        finally: session.close()
        return saved_count
        
    def detect_levels(self, symbol: str, trade_date: date):
        session = self.Session()
        try:
            # We look at the last 30 trading days to find the 'range'
            stats = session.query(
                text("MAX(high_price) as resistance"),
                text("MIN(low_price) as support")
            ).from_statement(text("""
                SELECT high_price, low_price FROM stock_prices 
                WHERE symbol = :symbol AND trade_date < :today
                ORDER BY trade_date DESC LIMIT 30
            """)).params(symbol=symbol, today=trade_date).first()
            
            return stats # Returns (resistance, support)
        finally:
            session.close()

if __name__ == "__main__":
    async def run_daily_sync():
        engine = NGXEngine()
        today = datetime.now().date()
        
        # 1. Download and Parse
        pdf = await engine.download_report(today)
        if not pdf:
            print("No report found today.")
            return
            
        stocks = engine.parse_pdf(pdf, today)
        if not stocks: return
        
        # 2. Save to DB (this also calculates percent_change)
        engine.save(stocks)
        
        # 3. Generate Alerts
        breakouts, spikes = engine.get_market_alerts(stocks)
        
        # 4. Send the Telegram Recap
        await engine.send_daily_recap(stocks, breakouts, spikes)
        
        # Cleanup
        if os.path.exists(pdf): os.remove(pdf)

    asyncio.run(run_daily_sync())
    # engine = NGXEngine()
    # Logic for daily run omitted here for brevity; focus is on the engine logic.
