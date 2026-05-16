import os
import asyncio
import httpx
import pdfplumber
import urllib.parse
from datetime import datetime, date, timedelta
from typing import List, Optional
from sqlalchemy import create_engine, Column, Integer, String, Numeric, BigInteger, Date, Float, text, Index
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv
from brain.query_engine import AlphaIntelligence
import asyncio

# Load local .env for testing; GitHub Actions will use Secrets
load_dotenv()
brain = AlphaIntelligence()

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
    # Analytical fields for logic (Excluded from DB Save)
    old_resistance: float = 0.0
    old_support: float = 0.0
    vol_increase: float = 0.0
    is_corporate_action: bool = False
    target: float = 0.0
    stop_loss: float = 0.0

    @property
    def traded_value(self) -> float:
        """Calculates total Naira value traded for liquidity checks."""
        return self.volume * self.close_price

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
            await client.post(self.url, json={"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"})
            
class WhatsAppNotifier:
    def __init__(self):
        self.id_instance = os.getenv("GREEN_API_ID")
        self.api_token = os.getenv("GREEN_API_TOKEN")
        self.group_id = os.getenv("WHATSAPP_GROUP_ID") 
        self.api_url = os.getenv("WHATSAPP_API_URL", "https://7107.api.greenapi.com")
        self.base_url = f"{self.api_url}/waInstance{self.id_instance}/sendMessage/{self.api_token}"

    async def send(self, message: str):
        if not all([self.id_instance, self.api_token, self.group_id]):
            return False
        payload = {"chatId": self.group_id, "message": message}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(self.base_url, json=payload)
                return response.status_code == 200
            except Exception: return False

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
        Base.metadata.create_all(self.engine)

    def get_market_alerts(self, stocks: List[StockSchema]):
        session = self.Session()
        breakouts, breakdowns, momentum, volume_spikes = [], [], [], []
        
        try:
            for stock in stocks:
                stock.is_corporate_action = abs(stock.percent_change) > 10.5
                
                levels = self.detect_levels(stock.symbol, stock.trade_date)
                if levels:
                    res = float(levels[0]) if levels[0] else 0
                    sup = float(levels[1]) if levels[1] else 0
                    
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
        finally:
            session.close()

    def get_earnings_watch(self):
        session = self.Session()
        today = datetime.now().date()
        try:
            upcoming = session.query(EarningsCalendar).filter(
                EarningsCalendar.actual_date == None,
                EarningsCalendar.expected_date <= (today + timedelta(days=14))
            ).all()
            just_reported = session.query(EarningsCalendar).filter(
                EarningsCalendar.actual_date >= (today - timedelta(days=3))
            ).all()
            return upcoming, just_reported
        finally:
            session.close()

    def get_top_performers(self, stocks: List[StockSchema]):
        sorted_stocks = sorted(stocks, key=lambda x: x.percent_change, reverse=True)
        gainers = sorted_stocks[:5]
        losers_list = [s for s in stocks if s.percent_change < 0]
        losers = sorted(losers_list, key=lambda x: x.percent_change)[:5]
        return gainers, losers
        
    def identify_high_conviction(self, breakouts: List[StockSchema], spikes: List[StockSchema]):
        breakout_symbols = {s.symbol for s in breakouts}
        spike_symbols = {s.symbol for s in spikes}
        perfect_trade_symbols = breakout_symbols.intersection(spike_symbols)
        
        high_conviction = []
        for s in breakouts:
            if s.symbol in perfect_trade_symbols:
                s.target = round(s.close_price * 1.10, 2)
                s.stop_loss = s.old_resistance
                high_conviction.append(s)
        return high_conviction

    def get_periodic_performance(self, label: str, days: int = 14):
        session = self.Session()
        today = datetime.now().date()
        start_date = today - timedelta(days=days)
        try:
            query = text("""
                WITH period_start AS (
                    SELECT DISTINCT ON (symbol) symbol, close_price as old_price
                    FROM stock_prices 
                    WHERE trade_date >= :start_date
                    ORDER BY symbol, trade_date ASC
                ),
                period_end AS (
                    SELECT DISTINCT ON (symbol) symbol, close_price as new_price
                    FROM stock_prices 
                    ORDER BY symbol, trade_date DESC
                )
                SELECT s.symbol, s.old_price, e.new_price
                FROM period_start s
                JOIN period_end e ON s.symbol = e.symbol
            """)
            results = session.execute(query, {"start_date": start_date}).fetchall()
            performance = []
            for r in results:
                change = ((float(r.new_price) - float(r.old_price)) / float(r.old_price)) * 100
                performance.append({
                    "symbol": r.symbol, "change": change, "price": float(r.new_price)
                })
            sorted_perf = sorted(performance, key=lambda x: x['change'], reverse=True)
            return sorted_perf[:5], sorted_perf[-5:]
        finally: session.close()

    async def send_periodic_report(self, label: str, winners: list, losers: list):
        msg = f"📊 *NGX {label} LEADERBOARD*\n"
        msg += "━━━━━━━━━━━━━━━━\n\n"
        msg += "🏆 *TOP PERFORMERS*\n"
        for s in winners:
            msg += f"`{s['symbol']:<10} ₦{s['price']:>7.2f}  (+{s['change']:>5.1f}%)`\n"
        msg += "\n📉 *BIGGEST LAGGARDS*\n"
        for s in reversed(losers):
            msg += f"`{s['symbol']:<10} ₦{s['price']:>7.2f}  ({s['change']:>6.1f}%)`\n"
        msg += "\n━━━━━━━━━━━━━━━━\n"
        msg += "💡 *INSIGHT:* Look for winners that also appeared in daily 'Unusual Volume' alerts."
        await self.tg.send(msg)
        await self.wa.send(msg)
        
    async def send_daily_recap(self, stocks, breakouts, breakdowns, momentum, spikes):
        upcoming_earn, recently_reported = self.get_earnings_watch()
        gainers, losers = self.get_top_performers(stocks)
        high_conviction = self.identify_high_conviction(breakouts, spikes)
        today_str = datetime.now().strftime("%d %b %Y")
        has_anomaly = False
        
        msg = f"🚀 *NGX ALPHA INTELLIGENCE* ({today_str})\n"
        msg += "━━━━━━━━━━━━━━━━\n\n"

        if high_conviction:
            msg += "🎯 *HIGH CONVICTION TRADES*\n"
            msg += "_Breakout confirmed by Volume Spike_\n"
            for s in high_conviction:
                liq = "✅" if s.traded_value >= 1000000 else "⚠️"
                msg += f"• *{s.symbol}* {liq}\n"
                msg += f"  `Entry: ₦{s.close_price:<7} Vol: {s.vol_increase}x`\n"
                msg += f"  `Target: ₦{s.target:<6} Stop: ₦{s.stop_loss:<6}`\n\n"
            msg += "━━━━━━━━━━━━━━━━\n\n"
            
        msg += "📈 *TOP 5 GAINERS*\n"
        for s in gainers:
            liq = "✅" if s.traded_value >= 1000000 else "⚠️"
            msg += f"`{s.symbol:<10} ₦{s.close_price:>7.2f}  (+{s.percent_change:>5.2f}%) {liq}`\n"
        
        msg += "\n📉 *TOP 5 LOSERS*\n"
        for s in losers:
            liq = "✅" if s.traded_value >= 1000000 else "⚠️"
            change_val = s.percent_change
            change_str = f"({change_val:>6.2f}%)"
            if abs(change_val) > 10.5:
                change_str += " 🔸"
                has_anomaly = True
            msg += f"`{s.symbol:<10} ₦{s.close_price:>7.2f}  {change_str} {liq}`\n"
            
        if has_anomaly:
            msg += "\n*🔸 Note:* Extreme moves (>10%) are usually Dividend Mark-downs, not market sell-offs.\n"

        msg += "\n*⚠️ LIQUIDITY ALERT:* ⚠️ traded < ₦1M today. Selling large volumes will cause 'Big War' (Slippage).\n"
        msg += "━━━━━━━━━━━━━━━━\n\n"

        if breakouts:
            msg += "🔓 *RESISTANCE BREAKOUTS*\n"
            for s in breakouts[:3]:
                msg += f"• *{s.symbol}*: ₦{s.close_price} (Broke ₦{s.old_resistance})\n"
            msg += "\n"

        if momentum:
            msg += "🔥 *HIGH MOMENTUM (5%+)*\n"
            for s in momentum[:3]:
                msg += f"• *{s.symbol}*: +{s.percent_change:.2f}%\n"
            msg += "\n"

        if spikes:
            msg += "🔊 *UNUSUAL VOLUME*\n"
            for s in spikes[:3]:
                msg += f"• *{s.symbol}*: {s.vol_increase}x Normal Vol\n"
            msg += "\n"
            
        if upcoming_earn or recently_reported:
            msg += "📝 *EARNINGS WATCH*\n"
            for e in recently_reported:
                msg += f"✅ *{e.symbol}*: Just Released {e.period} results!\n"
            for e in upcoming_earn:
                days_left = (e.expected_date - datetime.now().date()).days
                msg += f"⏳ *{e.symbol}*: Due in {max(0, days_left)} days.\n"
            msg += "\n"
            
        msg += "💡 *TRADER'S TIP*\n"
        msg += "The 'Perfect Trade' is a **Breakout** + **High Volume**. 📊"

        await self.tg.send(msg)
        await self.wa.send(msg)
        
    async def download_report(self, target_date: date):
        delimiters = ["-", " ", "", "."]
        candidate_strings = []
        for sep in delimiters:
            fmt = f"%d{sep}%m{sep}%Y"
            candidate_strings.append(target_date.strftime(fmt))

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for attempt_str in candidate_strings:
                encoded_date = urllib.parse.quote(attempt_str)
                url = f"https://doclib.ngxgroup.com/DownloadsContent/Daily%20Official%20List%20-%20Equities%20for%20{encoded_date}.pdf"
                try:
                    res = await client.get(url)
                    if res.status_code == 200:
                        path = f"ngx_{target_date.strftime('%Y-%m-%d')}.pdf"
                        with open(path, "wb") as f: f.write(res.content)
                        return path
                except Exception: continue
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
                # EXCLUDE ANALYTICAL FIELDS TO PREVENT DB ERROR
                exclude_fields = {'old_resistance', 'old_support', 'vol_increase', 'is_corporate_action', 'target', 'stop_loss'}
                session.execute(stmt, stock.model_dump(exclude=exclude_fields))
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"DB Error: {e}")
        finally: session.close()
        
    def detect_levels(self, symbol: str, trade_date: date):
        session = self.Session()
        try:
            stats = session.query(text("MAX(high_price)"), text("MIN(low_price)")).from_statement(text("""
                SELECT high_price, low_price FROM stock_prices 
                WHERE symbol = :symbol AND trade_date < :today
                ORDER BY trade_date DESC LIMIT 30
            """)).params(symbol=symbol, today=trade_date).first()
            return stats
        finally: session.close()


    async def process_market_event(self, ticker, percentage_change):
        """
        The 'Insight' Loop: Triggered when main.py detects a spike.
        """
        print(f"🚀 Spike Detected: {ticker} moved {percentage_change}%")
        
        research_query = f"Explain any recent filings, director dealings, or news for {ticker} that explain a {percentage_change}% movement."
        
        insight = brain.ask(user_query=research_query, company_filter=ticker)
        
        alert_msg = (
            f"🔔 *MARKET ALERT: {ticker}*\n"
            f"📈 Movement: {percentage_change}%\n\n"
            f"📝 *Intelligence Insight:*\n{insight}"
        )
        
        await self.tg.send(alert_msg) 
        await self.wa.send(alert_msg)


if __name__ == "__main__":
    async def run_daily_sync():
        engine = NGXEngine()
        today = datetime.now().date()
        
        # 1. Download and Parse
        pdf = await engine.download_report(today)
        if not pdf:
            print(f"No report found for {today}")
            return
            
        stocks = engine.parse_pdf(pdf, today)
        if not stocks:
            print(f"Could not parse data from {pdf}")
            return
            
        # 2. Database Save
        engine.save(stocks)
        
        # 3. Market Alerts & Daily Recap
        breakouts, breakdowns, momentum, spikes = engine.get_market_alerts(stocks)        
        await engine.send_daily_recap(stocks, breakouts, breakdowns, momentum, spikes)
        
        # 4. 🧠 INTELLIGENCE LOOP: Trigger research for each spike or breakout
        # We loop through the 'spikes' list identified by the engine
        for stock in spikes:
            # Parameters: ticker (str), percentage_change (float)
            await engine.process_market_event(stock.symbol, stock.percent_change)

        # Optional: Trigger for breakouts too?
        # for stock in breakouts:
        #    await engine.process_market_event(stock.symbol, stock.percent_change)
        
        # 5. Cleanup
        if os.path.exists(pdf): os.remove(pdf)

        # 6. Periodic Leaderboards (Friday check)
        if today.weekday() == 4:
            # Bi-Weekly (Every Friday between 8th-14th and 22nd-28th)
            if today.weekday() == 4 and today.isocalendar()[1] % 2 == 0:
                winners, losers = engine.get_periodic_performance("BI-WEEKLY", 14)
                await engine.send_periodic_report("14-DAY BI-WEEKLY", winners, losers)

            # Monthly (Last Friday of the month)
            next_day = today + timedelta(days=1)
            if next_day.month != today.month:
                winners, losers = engine.get_periodic_performance("MONTHLY", 30)
                await engine.send_periodic_report("30-DAY MONTHLY", winners, losers)

    asyncio.run(run_daily_sync())
