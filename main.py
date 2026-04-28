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
    def clean_currency(cls, v):
        if isinstance(v, str):
            return float(v.replace('₦', '').replace(',', '').strip())
        return float(v or 0)

    @field_validator('volume', mode='before')
    def clean_volume(cls, v):
        if isinstance(v, str):
            return int(float(v.replace(',', '').strip()))
        return int(v or 0)

# -------------------- PRODUCTION TELEGRAM NOTIFIER --------------------

class TelegramNotifier:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if not self.token or not self.chat_id:
            raise ValueError("❌ TELEGRAM_TOKEN or TELEGRAM_CHAT_ID missing")

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

                # Retry without Markdown (common hidden failure)
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

        # HARD FAIL (important for observability)
        raise RuntimeError("❌ Telegram failed after retries")

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
                final_url += "?sslmode=require&connect_timeout=10"

            self.db_url = final_url
        except Exception:
            self.db_url = raw_url

        self.engine = create_engine(self.db_url, pool_pre_ping=True)
        self.Session = sessionmaker(bind=self.engine)
        self.notifier = TelegramNotifier()

        Base.metadata.create_all(self.engine)

    # -------------------- DOWNLOAD --------------------

    async def download_report(self):
        date_str = datetime.now().strftime("%d%m%Y")
        url = f"https://doclib.ngxgroup.com/DownloadsContent/Daily%20Official%20List%20-%20Equities%20for%20{date_str}.pdf"

        print(f"📥 Checking: {url}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.get(url)
                if res.status_code == 200:
                    file = f"ngx_{date_str}.pdf"
                    with open(file, "wb") as f:
                        f.write(res.content)
                    return file
            except Exception as e:
                print("Download error:", e)

        return None

    # -------------------- PARSE --------------------

    def parse_pdf(self, path: str) -> List[StockSchema]:
        data = []

        try:
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages[:5]:
                    tables = page.extract_tables()

                    for table in tables:
                        if table and "Symbol" in str(table[0]):
                            for row in table[1:]:
                                if not row or len(row) < 7:
                                    continue
                                try:
                                    data.append(StockSchema(
                                        symbol=row[0],
                                        company_name=row[1],
                                        open_price=row[2],
                                        high_price=row[3],
                                        low_price=row[4],
                                        close_price=row[5],
                                        volume=row[6]
                                    ))
                                except:
                                    continue
                            return data
        except Exception as e:
            print("Parse error:", e)

        return data

    # -------------------- SAVE --------------------

    def save(self, stocks: List[StockSchema]):
        session = self.Session()
        try:
            for stock in stocks:
                session.execute(text("""
                    INSERT INTO stock_prices (symbol, company_name, open_price, high_price, low_price, close_price, volume, trade_date)
                    VALUES (:symbol, :company_name, :open_price, :high_price, :low_price, :close_price, :volume, :trade_date)
                    ON CONFLICT (symbol, trade_date)
                    DO UPDATE SET close_price = EXCLUDED.close_price, volume = EXCLUDED.volume
                """), stock.model_dump())

            session.commit()
            return len(stocks)

        except Exception as e:
            session.rollback()
            print("DB error:", e)
            return 0
        finally:
            session.close()

    # -------------------- EXECUTE --------------------

    async def execute(self):
        start = datetime.now()

        await self.notifier.send(f"🚀 *NGX Job Started*\n🕒 {start}")

        try:
            pdf = await self.download_report()

            if not pdf:
                await self.notifier.send("⚠️ NGX report not available yet.")
                return

            stocks = self.parse_pdf(pdf)

            if not stocks:
                await self.notifier.send("❌ Parsing failed.")
                return

            saved = self.save(stocks)

            if saved == 0:
                await self.notifier.send("ℹ️ No new records to update.")
                return

            top = max(stocks, key=lambda x: x.volume)

            await self.notifier.send(
                f"✅ *NGX Sync Complete*\n"
                f"📊 Records: {saved}\n"
                f"🔥 Top Volume: {top.symbol} ({top.volume:,})"
            )

        except Exception as e:
            await self.notifier.send(f"💥 *System Error*\n`{str(e)}`")
            raise

        finally:
            end = datetime.now()
            print(f"⏱ Finished in {end - start}")


# -------------------- ENTRY --------------------

if __name__ == "__main__":
    engine = NGXEngine()
    asyncio.run(engine.execute())
