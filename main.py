import os
import asyncio
import httpx
import pdfplumber
import urllib.parse
from datetime import datetime
from typing import List
from sqlalchemy import create_engine, Column, Integer, String, Numeric, BigInteger, Date, text
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
    volume = Column(BigInteger)
    trade_date = Column(Date, default=datetime.now().date())

# -------------------- VALIDATION --------------------

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
        if not v or str(v).strip() in ["-", ""]:
            return 0.0
        if isinstance(v, str):
            # Handles Naira symbol, commas, and whitespace
            cleaned = v.replace('₦', '').replace(',', '').strip()
            try:
                return float(cleaned)
            except ValueError:
                return 0.0
        return float(v or 0)

    @field_validator('volume', mode='before')
    @classmethod
    def clean_volume(cls, v):
        if not v or str(v).strip() in ["-", ""]:
            return 0
        if isinstance(v, str):
            cleaned = v.replace(',', '').strip()
            try:
                return int(float(cleaned))
            except ValueError:
                return 0
        return int(v or 0)

# -------------------- PRODUCTION TELEGRAM NOTIFIER --------------------

class TelegramNotifier:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if not self.token or not self.chat_id:
            raise ValueError("❌ TELEGRAM_TOKEN or TELEGRAM_CHAT_ID missing in GitHub Secrets")

        self.url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    async def send(self, message: str, retries: int = 3):
        for attempt in range(1, retries + 1):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(
                        self.url,
                        json={
                            "chat_id": self.chat_id,
                            "text": message,
                            "parse_mode": "Markdown"
                        }
                    )

                if response.status_code == 200:
                    print(f"✅ Telegram sent (attempt {attempt})")
                    return

                if attempt == 1:
                    print("⚠️ Markdown failed, retrying without parse_mode...")
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        response = await client.post(
                            self.url,
                            json={
                                "chat_id": self.chat_id,
                                "text": message
                            }
                        )
                    if response.status_code == 200:
                        print("✅ Telegram sent (fallback mode)")
                        return

                print(f"⚠️ Telegram error: {response.status_code} | {response.text}")

            except Exception as e:
                print(f"⚠️ Telegram exception (attempt {attempt}): {e}")

            await asyncio.sleep(2 ** attempt)

# -------------------- ENGINE --------------------

class NGXEngine:
    def __init__(self):
        raw_url = os.getenv("DATABASE_URL")
        if not raw_url:
            raise ValueError("DATABASE_URL missing")

        try:
            prefix, rest = raw_url.split("://")
            user_pass, host_port_db = rest.rsplit("@", 1)

            if ":" in user_pass:
                user, password = user_pass.split(":", 1)
                password = urllib.parse.quote_plus(password)
                auth = f"{user}:{password}"
            else:
                auth = user_pass

            final_url = f"{prefix}://{auth}@{host_port_db}"

            if "sslmode" not in final_url:
                separator = "&" if "?" in final_url else "?"
                final_url += f"{separator}sslmode=require&connect_timeout=10"

            self.db_url = final_url
        except Exception:
            self.db_url = raw_url

        self.engine = create_engine(self.db_url, pool_pre_ping=True)
        self.Session = sessionmaker(bind=self.engine)
        self.notifier = TelegramNotifier()

        Base.metadata.create_all(self.engine)

    async def download_report(self):
        # Updated to the dashed format based on your discovery
        date_str = datetime.now().strftime("%d-%m-%Y")
        url = f"https://doclib.ngxgroup.com/DownloadsContent/Daily%20Official%20List%20-%20Equities%20for%20{date_str}.pdf"

        print(f"📥 Checking: {url}")

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            try:
                res = await client.get(url)
                if res.status_code == 200:
                    file = f"ngx_equities_{date_str}.pdf"
                    with open(file, "wb") as f:
                        f.write(res.content)
                    return file
            except Exception as e:
                print(f"Download error: {e}")

        return None

    def parse_pdf(self, path: str) -> List[StockSchema]:
        data = []
        try:
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if not table:
                        continue

                    # The header logic: Find the row containing 'Symbol'
                    header_idx = -1
                    for i, row in enumerate(table[:5]):
                        if row and any("Symbol" in str(cell) for cell in row if cell):
                            header_idx = i
                            break
                    
                    if header_idx == -1:
                        continue

                    # Start parsing after the header row
                    for row in table[header_idx + 1:]:
                        try:
                            # Skip empty rows or rows that aren't stock data
                            if not row or len(row) < 12 or not row[0]:
                                continue
                            
                            # Based on your image:
                            # 0: Symbol, 1: Name, 3: Open, 5: Close, 11: Qty
                            
                            symbol = str(row[0]).strip()
                            # Basic validation to ensure it's a stock symbol (usually all caps)
                            if not symbol.isupper():
                                continue

                            close_p = row[5]
                            open_p = row[3] if row[3] and str(row[3]).strip() not in ["-", ""] else close_p

                            data.append(StockSchema(
                                symbol=symbol,
                                company_name=row[1],
                                open_price=open_p,
                                high_price=close_p, # Use close as fallback for High/Low if not explicit
                                low_price=close_p,
                                close_price=close_p,
                                volume=row[11], # 'Qty' is index 11
                                trade_date=datetime.now()
                            ))
                        except Exception:
                            continue
            return data
        except Exception as e:
            print(f"Parse error: {e}")
            return data

    def save(self, stocks: List[StockSchema]):
        session = self.Session()
        try:
            for stock in stocks:
                session.execute(text("""
                    INSERT INTO stock_prices (symbol, company_name, open_price, high_price, low_price, close_price, volume, trade_date)
                    VALUES (:symbol, :company_name, :open_price, :high_price, :low_price, :close_price, :volume, :trade_date)
                    ON CONFLICT (symbol, trade_date)
                    DO UPDATE SET 
                        close_price = EXCLUDED.close_price, 
                        volume = EXCLUDED.volume,
                        open_price = EXCLUDED.open_price;
                """), stock.model_dump())

            session.commit()
            return len(stocks)
        except Exception as e:
            session.rollback()
            print(f"DB error: {e}")
            return 0
        finally:
            session.close()

    async def execute(self):
        print(f"🚀 Job Started: {datetime.now()}")
        try:
            # pdf = await self.download_report()
            pdf = "ngx_equities_01-05-2026.pdf"

            if not pdf:
                await self.notifier.send("⚠️ *NGX Data Alert*\nToday's Equities Report is not available yet.")
                return

            stocks = self.parse_pdf(pdf)

            if not stocks:
                await self.notifier.send("❌ *Parsing Error*\nFound the PDF but couldn't extract stock data.")
                return

            saved = self.save(stocks)

            if saved > 0:
                top = max(stocks, key=lambda x: x.volume)
                await self.notifier.send(
                    f"✅ *NGX Sync Success*\n"
                    f"📊 Stocks Updated: {saved}\n"
                    f"🔥 *Top Volume:* {top.symbol} ({top.volume:,})"
                )
            else:
                await self.notifier.send("ℹ️ No new trading data was found in the report.")

        except Exception as e:
            await self.notifier.send(f"💥 *System Error*\n`{str(e)}`")
            raise

if __name__ == "__main__":
    engine = NGXEngine()
    asyncio.run(engine.execute())
