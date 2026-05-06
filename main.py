import os
import json
import asyncio
import httpx
import pdfplumber
import urllib.parse
import bs4
from datetime import datetime, date, timedelta
from typing import List, Optional
from sqlalchemy import create_engine, Column, Integer, String, Numeric, BigInteger, Date, Float, text, Index
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# DATABASE MODELS
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class StockPriceDB(Base):
    __tablename__ = "stock_prices"
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

    __table_args__ = (Index("uix_symbol_date", "symbol", "trade_date", unique=True),)


class EarningsCalendar(Base):
    __tablename__ = "earnings_calendar"
    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    period = Column(String)
    expected_date = Column(Date)
    actual_date = Column(Date)
    dividend_yield = Column(Float)


# ---------------------------------------------------------------------------
# DATA SCHEMAS
# ---------------------------------------------------------------------------

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
    # Alert metadata (not persisted)
    old_resistance: float = 0.0
    old_support: float = 0.0
    vol_increase: float = 0.0
    is_corporate_action: bool = False

    @field_validator("open_price", "high_price", "low_price", "close_price", mode="before")
    @classmethod
    def clean_currency(cls, v):
        if not v or str(v).strip() in ["-", "", "nil", "N/A"]:
            return 0.0
        if isinstance(v, str):
            cleaned = v.replace("₦", "").replace(",", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return 0.0
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    @field_validator("volume", mode="before")
    @classmethod
    def clean_volume(cls, v):
        if not v or str(v).strip() in ["-", "", "nil", "N/A"]:
            return 0
        if isinstance(v, str):
            cleaned = v.replace(",", "").strip()
            try:
                return int(float(cleaned))
            except ValueError:
                return 0
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0


# ---------------------------------------------------------------------------
# NOTIFICATION SERVICES
# ---------------------------------------------------------------------------

class TelegramNotifier:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    async def send(self, message: str):
        if not self.token or not self.chat_id:
            print("⚠️  Telegram config missing — skipping.")
            return
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                r = await client.post(
                    self.url,
                    json={"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"},
                )
                print("✅ Telegram sent." if r.status_code == 200 else f"⚠️  Telegram {r.status_code}: {r.text[:100]}")
            except Exception as e:
                print(f"📡 Telegram Error: {e}")


class WhatsAppNotifier:
    def __init__(self):
        self.id_instance = os.getenv("GREEN_API_ID")
        self.api_token = os.getenv("GREEN_API_TOKEN")
        self.group_id = os.getenv("WHATSAPP_GROUP_ID")
        self.api_url = os.getenv("WHATSAPP_API_URL", "https://7107.api.greenapi.com")
        self.base_url = (
            f"{self.api_url}/waInstance{self.id_instance}/sendMessage/{self.api_token}"
        )

    async def send(self, message: str):
        if not all([self.id_instance, self.api_token, self.group_id]):
            print("⚠️  WhatsApp config missing — skipping.")
            return False
        payload = {"chatId": self.group_id, "message": message}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                r = await client.post(self.base_url, json=payload)
                ok = r.status_code == 200
                print("✅ WhatsApp sent." if ok else f"⚠️  WhatsApp {r.status_code}: {r.text[:100]}")
                return ok
            except Exception as e:
                print(f"📡 WhatsApp Error: {e}")
                return False


# ---------------------------------------------------------------------------
# CORE ENGINE
# ---------------------------------------------------------------------------

class NGXEngine:
    """
    Download → Parse → Save → Alert pipeline for NGX daily equity data.

    Download strategy (in order):
      Stage 1 — NGX direct download redirect URLs
      Stage 2 — doclib date-pattern PDF guessing (tries last 5 trading days)
      Stage 3 — DataTables / AJAX JSON endpoint
      Stage 4 — Full HTML scrape with cookie handshake
      Stage 5 — Stooq CSV (international fallback, no geo-block)

    Geo-block mitigation:
      Set NG_PROXY_URL (or HTTP_PROXY / HTTPS_PROXY) to a Nigerian proxy URL.
      The engine strips whitespace/newlines before using it, so GitHub Actions
      secret formatting never causes an InvalidURL crash.
    """

    BASE_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://ngxgroup.com/",
    }

    def __init__(self):
        db_url = os.getenv("DATABASE_URL", "")
        if not db_url:
            raise RuntimeError("DATABASE_URL environment variable is not set.")
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)

        self.engine = create_engine(db_url, pool_pre_ping=True)
        self.Session = sessionmaker(bind=self.engine)
        self.wa = WhatsAppNotifier()
        self.tg = TelegramNotifier()

        # Read proxy once at init — strip ALL whitespace/newlines that GitHub
        # Actions sometimes appends to secret values.
        raw_proxy = (
            os.getenv("NG_PROXY_URL")
            or os.getenv("HTTP_PROXY")
            or os.getenv("HTTPS_PROXY")
            or ""
        ).strip()
        # Guard: only use if it looks like a valid URL scheme
        self._proxy: Optional[str] = raw_proxy if raw_proxy.startswith(("http://", "https://", "socks5://")) else None

        if self._proxy:
            print(f"🌍 Proxy active: {self._proxy}")
        else:
            print("ℹ️  No proxy configured — connecting directly.")

        Base.metadata.create_all(self.engine)

    # ------------------------------------------------------------------
    # CLIENT FACTORY
    # ------------------------------------------------------------------

    def _client(self, extra_headers: Optional[dict] = None, timeout: float = 40.0) -> httpx.AsyncClient:
        """
        Return a configured AsyncClient.
        proxy= is the correct kwarg for httpx ≥ 0.28 (replaces the old proxies= dict).
        Passing None is safe — httpx ignores it.
        """
        headers = {**self.BASE_HEADERS, **(extra_headers or {})}
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
            proxy=self._proxy,  # None when not configured — httpx handles it gracefully
        )

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _is_pdf(content: bytes) -> bool:
        return content[:4] == b"%PDF"

    @staticmethod
    def _trading_days_back(start: date, n: int = 5) -> List[date]:
        """Return up to n past trading days (Mon–Fri) starting from start (inclusive)."""
        days, cursor = [], start
        while len(days) < n:
            if cursor.weekday() < 5:
                days.append(cursor)
            cursor -= timedelta(days=1)
        return days

    # ------------------------------------------------------------------
    # STAGE 1 — Direct redirect URLs
    # ------------------------------------------------------------------

    async def _stage1_redirect(self, client: httpx.AsyncClient, target_date: date) -> Optional[str]:
        urls = [
            "https://ngxgroup.com/ngx-download/daily-official-list-equities/",
            "https://ngxgroup.com/ngx-download/market-data-pricelist-2/",
        ]
        for url in urls:
            try:
                res = await client.get(url)
                if res.status_code == 200 and self._is_pdf(res.content):
                    path = f"ngx_stage1_{target_date}.pdf"
                    with open(path, "wb") as f:
                        f.write(res.content)
                    print(f"✅ Stage 1 hit: {url}")
                    return path
                print(f"Stage 1 miss ({res.status_code}): {url}")
            except Exception as e:
                print(f"Stage 1 error: {e}")
        return None

    # ------------------------------------------------------------------
    # STAGE 2 — doclib date-pattern PDF guessing
    # ------------------------------------------------------------------

    async def _stage2_pdf_guess(self, client: httpx.AsyncClient, target_date: date) -> Optional[str]:
        candidate_dates = self._trading_days_back(target_date, n=5)
        delimiters = ["-", " ", ".", ""]
        templates = [
            "Daily%20Official%20List%20-%20Equities%20for%20{date}.pdf",
            "DAILY%20OFFICIAL%20LIST%20-%20EQUITIES%20FOR%20{date}.pdf",
            "DAILY%20SUMMARY%20FOR%20{date}.pdf",
            "Daily%20Summary%20for%20{date}.pdf",
            "Equities{date}.pdf",
        ]
        for d in candidate_dates:
            for sep in delimiters:
                date_str = d.strftime(f"%d{sep}%m{sep}%Y")
                encoded = urllib.parse.quote(date_str)
                for tmpl in templates:
                    url = f"https://doclib.ngxgroup.com/DownloadsContent/{tmpl.format(date=encoded)}"
                    try:
                        res = await client.get(url)
                        if res.status_code == 200 and self._is_pdf(res.content):
                            path = f"ngx_stage2_{d}.pdf"
                            with open(path, "wb") as f:
                                f.write(res.content)
                            print(f"✅ Stage 2 hit ({d}): {url}")
                            return path
                    except Exception:
                        continue
        print("Stage 2: no PDF match found.")
        return None

    # ------------------------------------------------------------------
    # STAGE 3 — DataTables / AJAX JSON endpoint
    # ------------------------------------------------------------------

    async def _stage3_ajax(self, client: httpx.AsyncClient, target_date: date) -> Optional[str]:
        ajax_headers = {
            **self.BASE_HEADERS,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        endpoints = [
            "https://ngxgroup.com/exchange/data/equities-price-list/?draw=1&columns[0][data]=symbol&start=0&length=1000",
            "https://ngxgroup.com/exchange/data/equities-price-list/?draw=1&start=0&length=500",
            "https://ngxgroup.com/wp-json/ngx/v1/equities",
            "https://ngxgroup.com/wp-json/ngx/v1/market-data",
            "https://doclib.ngxgroup.com/REST/api/operations/getequitiesprices",
            "https://doclib.ngxgroup.com/REST/api/operations/getsecurities",
        ]
        for url in endpoints:
            try:
                res = await client.get(url, headers=ajax_headers)
                if res.status_code != 200:
                    continue
                ct = res.headers.get("content-type", "")
                if "json" not in ct and not res.text.strip().startswith("{"):
                    continue
                data = res.json()
                rows = data.get("data") or data.get("aaData") or []
                if rows:
                    path = f"ngx_stage3_{target_date}.json"
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump({"data": rows}, f)
                    print(f"✅ Stage 3 AJAX hit: {url} ({len(rows)} rows)")
                    return path
            except Exception as e:
                print(f"Stage 3 error [{url}]: {e}")
        print("Stage 3: no AJAX endpoint returned data.")
        return None

    # ------------------------------------------------------------------
    # STAGE 4 — HTML scrape with cookie handshake
    # ------------------------------------------------------------------

    async def _stage4_html_scrape(self, client: httpx.AsyncClient, target_date: date) -> Optional[str]:
        scrape_urls = [
            "https://ngxgroup.com/exchange/data/equities-price-list/",
            "https://ngxgroup.com/ir/equities-market/",
            "https://www.ngxgroup.com/exchange/data/equities-price-list/",
        ]
        # Warm-up to collect session cookies / Cloudflare clearance
        cookies = {}
        try:
            warmup = await client.get("https://ngxgroup.com/")
            cookies = dict(warmup.cookies)
            print(f"Stage 4 warm-up: {warmup.status_code}, cookies: {list(cookies.keys())}")
            await asyncio.sleep(1.5)
        except Exception as e:
            print(f"Stage 4 warm-up error: {e}")

        for url in scrape_urls:
            try:
                res = await client.get(url, cookies=cookies)
                print(f"Stage 4 [{res.status_code}]: {url} ({len(res.text)} bytes)")

                if res.status_code != 200:
                    continue

                body = res.text

                # Reject Cloudflare challenge pages
                if "cf-browser-verification" in body or "checking your browser" in body.lower():
                    print("Stage 4: Cloudflare challenge — geo-block still active.")
                    continue

                if "<table" not in body.lower():
                    print("Stage 4: no <table> in response.")
                    continue

                soup = bs4.BeautifulSoup(body, "html.parser")
                tables = soup.find_all("table")
                has_data = any(len(t.find_all("tr")) > 5 for t in tables)
                if not has_data:
                    print("Stage 4: table found but <5 rows (probably JS-rendered).")
                    continue

                path = f"ngx_stage4_{target_date}.html"
                with open(path, "w", encoding="utf-8") as f:
                    f.write(body)
                print(f"✅ Stage 4 HTML hit: {url}")
                return path

            except Exception as e:
                print(f"Stage 4 error [{url}]: {e}")

        print("Stage 4: no usable HTML found.")
        return None

    # ------------------------------------------------------------------
    # STAGE 5 — Stooq CSV fallback (no geo-block)
    # ------------------------------------------------------------------

    async def _stage5_stooq_csv(self, client: httpx.AsyncClient, target_date: date) -> Optional[str]:
        TOP_NGX = [
            "DANGCEM", "MTNN", "AIRTELAFRI", "GTCO", "ZENITHBANK",
            "ACCESSCORP", "FBNH", "UBA", "STANBIC", "TRANSCORP",
            "SEPLAT", "OANDO", "NESTLE", "UNILEVER", "NB",
            "BUACEMENT", "WAPCO", "FLOURMILL", "PRESCO", "OKOMUOIL",
            "FIDELITYBK", "FCMB", "STERLING", "JAIZBANK", "TRIDENT",
            "CHAMS", "CAVERTON", "CONOIL", "TOTALENERGIES", "ETERNA",
        ]
        rows = []
        print(f"Stage 5: Stooq CSV fallback — {len(TOP_NGX)} tickers…")
        for symbol in TOP_NGX:
            url = (
                f"https://stooq.com/q/d/l/?s={symbol.lower()}.ng"
                f"&d1={target_date.strftime('%Y%m%d')}"
                f"&d2={target_date.strftime('%Y%m%d')}&i=d"
            )
            try:
                res = await client.get(url)
                if res.status_code == 200 and "Date" in res.text:
                    lines = res.text.strip().splitlines()
                    if len(lines) >= 2:
                        parts = lines[1].split(",")
                        if len(parts) >= 5:
                            rows.append({
                                "symbol": symbol,
                                "company_name": symbol,
                                "open": parts[1],
                                "high": parts[2],
                                "low": parts[3],
                                "close": parts[4],
                                "volume": parts[5] if len(parts) > 5 else "0",
                            })
            except Exception:
                continue

        if rows:
            path = f"ngx_stage5_{target_date}.json"
            with open(path, "w") as f:
                json.dump({"stooq": rows}, f)
            print(f"✅ Stage 5 Stooq: {len(rows)} tickers retrieved.")
            return path

        print("Stage 5: Stooq returned no data.")
        return None

    # ------------------------------------------------------------------
    # DOWNLOAD ORCHESTRATOR
    # ------------------------------------------------------------------

    async def download_report(self, target_date: date) -> Optional[str]:
        async with self._client() as client:
            for stage, fn in [
                ("1 (redirect)",    lambda: self._stage1_redirect(client, target_date)),
                ("2 (PDF guess)",   lambda: self._stage2_pdf_guess(client, target_date)),
                ("3 (AJAX JSON)",   lambda: self._stage3_ajax(client, target_date)),
                ("4 (HTML scrape)", lambda: self._stage4_html_scrape(client, target_date)),
                ("5 (Stooq CSV)",   lambda: self._stage5_stooq_csv(client, target_date)),
            ]:
                print(f"\n── Stage {stage} ──")
                path = await fn()
                if path:
                    return path

        print("\n🛑 All download stages exhausted.")
        return None

    # ------------------------------------------------------------------
    # PARSERS
    # ------------------------------------------------------------------

    def parse_source(self, path: str, trade_date: date) -> List[StockSchema]:
        if path.endswith(".pdf"):
            return self._parse_pdf(path, trade_date)
        elif path.endswith(".html"):
            return self._parse_html(path, trade_date)
        elif path.endswith(".json"):
            return self._parse_json(path, trade_date)
        print(f"⚠️  Unknown source format: {path}")
        return []

    def _parse_pdf(self, path: str, trade_date: date) -> List[StockSchema]:
        data = []
        try:
            with pdfplumber.open(path) as pdf:
                print(f"PDF: {len(pdf.pages)} pages")
                for page in pdf.pages:
                    table = page.extract_table()
                    if not table:
                        continue

                    header_idx = -1
                    for i, row in enumerate(table[:10]):
                        if row and any("Symbol" in str(x) for x in row if x):
                            header_idx = i
                            break
                    if header_idx == -1:
                        continue

                    for row in table[header_idx + 1:]:
                        if not row or len(row) < 10 or not row[0]:
                            continue
                        symbol = str(row[0]).strip()
                        if not symbol.isupper() or " " in symbol:
                            continue
                        close_p = row[5]
                        try:
                            data.append(StockSchema(
                                symbol=symbol,
                                company_name=str(row[1] or "").strip(),
                                open_price=row[3] or close_p,
                                high_price=row[4] or close_p,
                                low_price=row[5] or close_p,
                                close_price=close_p,
                                volume=row[11] if len(row) > 11 else row[-1],
                                trade_date=trade_date,
                            ))
                        except Exception:
                            continue
        except Exception as e:
            print(f"PDF Parse Error: {e}")
        print(f"PDF parsed: {len(data)} stocks.")
        return data

    def _parse_html(self, path: str, trade_date: date) -> List[StockSchema]:
        stocks = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                soup = bs4.BeautifulSoup(f.read(), "html.parser")

            table = (
                soup.find("table", {"id": "table_1"})
                or soup.find("table", {"id": "DataTables_Table_0"})
                or soup.find("table", class_=lambda c: c and "dataTable" in c)
                or soup.find("table")
            )
            if not table:
                print("HTML Parse: no table found.")
                return []

            rows = table.find_all("tr")
            print(f"HTML Parse: {len(rows)} rows.")

            # Auto-detect column positions from header row
            col_map = {}
            for row in rows[:5]:
                headers = [th.get_text(strip=True).lower() for th in row.find_all(["th", "td"])]
                if any(h in ("symbol", "ticker") for h in headers):
                    for i, h in enumerate(headers):
                        if h in ("symbol", "ticker"):
                            col_map["symbol"] = i
                        elif any(k in h for k in ("company", "name", "security")):
                            col_map["company"] = i
                        elif h == "open":
                            col_map["open"] = i
                        elif h == "high":
                            col_map["high"] = i
                        elif h == "low":
                            col_map["low"] = i
                        elif any(k in h for k in ("close", "last", "price")):
                            col_map["close"] = i
                        elif "vol" in h:
                            col_map["volume"] = i
                    break

            # Positional defaults if header detection failed
            if not col_map:
                col_map = {"symbol": 0, "company": 1, "open": 3, "high": 4, "low": 5, "close": 6, "volume": 10}
                print("HTML Parse: using positional column defaults.")

            for row in rows[1:]:
                cols = row.find_all("td")
                if not cols or len(cols) < max(col_map.values()) + 1:
                    continue

                def get(key, default=0):
                    idx = col_map.get(key, default)
                    return cols[idx].get_text(strip=True) if idx < len(cols) else ""

                symbol = get("symbol", 0)
                if not symbol or not any(c.isalpha() for c in symbol) or symbol[0].islower():
                    continue

                close_raw = get("close", 6)
                if not close_raw or close_raw == "-":
                    continue

                try:
                    stocks.append(StockSchema(
                        symbol=symbol,
                        company_name=get("company", 1) or symbol,
                        open_price=get("open", 3) or close_raw,
                        high_price=get("high", 4) or close_raw,
                        low_price=get("low", 5) or close_raw,
                        close_price=close_raw,
                        volume=get("volume", 10) or "0",
                        trade_date=trade_date,
                    ))
                except Exception:
                    continue

        except Exception as e:
            print(f"HTML Parse Error: {e}")
        print(f"HTML parsed: {len(stocks)} stocks.")
        return stocks

    def _parse_json(self, path: str, trade_date: date) -> List[StockSchema]:
        """
        Handles:
          - DataTables AJAX: {"data": [[col0, col1, ...], ...]}
          - DataTables AJAX: {"data": [{"symbol": ..., ...}, ...]}
          - Stooq fallback:  {"stooq": [{"symbol":…, "open":…, …}, …]}
        """
        stocks = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            # --- Stooq shape ---
            if "stooq" in payload:
                for row in payload["stooq"]:
                    try:
                        stocks.append(StockSchema(
                            symbol=row["symbol"],
                            company_name=row.get("company_name", row["symbol"]),
                            open_price=row.get("open", 0),
                            high_price=row.get("high", 0),
                            low_price=row.get("low", 0),
                            close_price=row.get("close", 0),
                            volume=row.get("volume", 0),
                            trade_date=trade_date,
                        ))
                    except Exception:
                        continue
                print(f"JSON (Stooq) parsed: {len(stocks)} stocks.")
                return stocks

            # --- DataTables AJAX shape ---
            rows = payload.get("data") or payload.get("aaData") or []
            if not rows:
                print("JSON Parse: no rows found.")
                return []

            sample = rows[0]

            if isinstance(sample, dict):
                for row in rows:
                    try:
                        symbol = (
                            row.get("symbol") or row.get("Symbol")
                            or row.get("ticker") or ""
                        ).strip()
                        if not symbol:
                            continue
                        close_p = (
                            row.get("close_price") or row.get("ClosingPrice")
                            or row.get("close") or row.get("last_price") or 0
                        )
                        stocks.append(StockSchema(
                            symbol=symbol,
                            company_name=row.get("company_name") or row.get("CompanyName") or symbol,
                            open_price=row.get("open_price") or row.get("OpeningPrice") or close_p,
                            high_price=row.get("high_price") or row.get("HighPrice") or close_p,
                            low_price=row.get("low_price") or row.get("LowPrice") or close_p,
                            close_price=close_p,
                            volume=row.get("volume") or row.get("Volume") or 0,
                            trade_date=trade_date,
                        ))
                    except Exception:
                        continue

            elif isinstance(sample, list):
                for row in rows:
                    if len(row) < 10 or not row[0]:
                        continue
                    symbol = str(row[0]).strip()
                    if not symbol.isupper():
                        continue
                    close_p = row[6] if len(row) > 6 else row[4]
                    try:
                        stocks.append(StockSchema(
                            symbol=symbol,
                            company_name=str(row[1] or "").strip(),
                            open_price=row[3] or close_p,
                            high_price=row[4] or close_p,
                            low_price=row[5] or close_p,
                            close_price=close_p,
                            volume=row[11] if len(row) > 11 else row[-1],
                            trade_date=trade_date,
                        ))
                    except Exception:
                        continue

        except Exception as e:
            print(f"JSON Parse Error: {e}")

        print(f"JSON parsed: {len(stocks)} stocks.")
        return stocks

    # ------------------------------------------------------------------
    # PERSISTENCE
    # ------------------------------------------------------------------

    def save(self, stocks: List[StockSchema]):
        session = self.Session()
        saved = 0
        try:
            for stock in stocks:
                prev = (
                    session.query(StockPriceDB.close_price)
                    .filter(
                        StockPriceDB.symbol == stock.symbol,
                        StockPriceDB.trade_date < stock.trade_date,
                    )
                    .order_by(StockPriceDB.trade_date.desc())
                    .first()
                )
                if prev and float(prev[0]) > 0:
                    stock.percent_change = round(
                        ((stock.close_price - float(prev[0])) / float(prev[0])) * 100, 2
                    )

                stmt = text("""
                    INSERT INTO stock_prices
                        (symbol, company_name, open_price, high_price, low_price,
                         close_price, percent_change, volume, trade_date)
                    VALUES
                        (:symbol, :company_name, :open_price, :high_price, :low_price,
                         :close_price, :percent_change, :volume, :trade_date)
                    ON CONFLICT (symbol, trade_date) DO UPDATE SET
                        company_name   = EXCLUDED.company_name,
                        open_price     = EXCLUDED.open_price,
                        high_price     = EXCLUDED.high_price,
                        low_price      = EXCLUDED.low_price,
                        close_price    = EXCLUDED.close_price,
                        percent_change = EXCLUDED.percent_change,
                        volume         = EXCLUDED.volume;
                """)
                session.execute(
                    stmt,
                    stock.model_dump(
                        exclude={"old_resistance", "old_support", "vol_increase", "is_corporate_action"}
                    ),
                )
                saved += 1

            session.commit()
            print(f"💾 Saved {saved} stocks to DB.")
        except Exception as e:
            session.rollback()
            print(f"DB Error: {e}")
        finally:
            session.close()

    # ------------------------------------------------------------------
    # MARKET ALERTS
    # ------------------------------------------------------------------

    def get_market_alerts(self, stocks: List[StockSchema]):
        session = self.Session()
        breakouts, breakdowns, momentum, volume_spikes = [], [], [], []
        try:
            for stock in stocks:
                stock.is_corporate_action = abs(stock.percent_change) > 10.5

                levels = session.execute(
                    text("""
                        SELECT MAX(high_price), MIN(low_price)
                        FROM stock_prices
                        WHERE symbol = :symbol
                          AND trade_date < :today
                        ORDER BY trade_date DESC
                        LIMIT 30
                    """),
                    {"symbol": stock.symbol, "today": stock.trade_date},
                ).first()

                if levels and levels[0] and levels[1]:
                    res = float(levels[0])
                    sup = float(levels[1])
                    if res > 0 and stock.close_price > res:
                        stock.old_resistance = res
                        breakouts.append(stock)
                    if sup > 0 and stock.close_price < sup and not stock.is_corporate_action:
                        stock.old_support = sup
                        breakdowns.append(stock)

                if stock.percent_change >= 5.0 and not stock.is_corporate_action:
                    momentum.append(stock)

                avg_vol = session.execute(
                    text("""
                        SELECT AVG(volume) FROM (
                            SELECT volume FROM stock_prices
                            WHERE symbol = :symbol AND trade_date < :today
                            ORDER BY trade_date DESC LIMIT 10
                        ) sub
                    """),
                    {"symbol": stock.symbol, "today": stock.trade_date},
                ).scalar()

                if avg_vol and stock.volume > float(avg_vol) * 2:
                    stock.vol_increase = round(stock.volume / float(avg_vol), 1)
                    volume_spikes.append(stock)

            return breakouts, breakdowns, momentum, volume_spikes
        except Exception as e:
            print(f"Alert Engine Error: {e}")
            return [], [], [], []
        finally:
            session.close()

    # ------------------------------------------------------------------
    # NOTIFICATIONS
    # ------------------------------------------------------------------

    async def send_daily_recap(
        self,
        stocks: List[StockSchema],
        breakouts: List[StockSchema],
        breakdowns: List[StockSchema],
        momentum: List[StockSchema],
        spikes: List[StockSchema],
    ):
        if not stocks:
            return

        sorted_stocks = sorted(stocks, key=lambda x: x.percent_change, reverse=True)
        gainers = [s for s in sorted_stocks if s.percent_change > 0][:5]
        losers = sorted([s for s in stocks if s.percent_change < 0], key=lambda x: x.percent_change)[:5]

        adv  = len([s for s in stocks if s.percent_change > 0])
        dec  = len([s for s in stocks if s.percent_change < 0])
        unch = len(stocks) - adv - dec

        msg  = f"🚀 *NGX ALPHA INTELLIGENCE* — {datetime.now().strftime('%d %b %Y')}\n"
        msg += "━━━━━━━━━━━━━━━━\n\n"
        msg += f"📊 *BREADTH*: {adv}↑  {dec}↓  {unch}→  ({len(stocks)} stocks)\n\n"

        if gainers:
            msg += "📈 *TOP GAINERS*\n"
            for s in gainers:
                msg += f"• *{s.symbol}* +{s.percent_change:.2f}% @ ₦{s.close_price:.2f}\n"

        if losers:
            msg += "\n📉 *TOP LOSERS*\n"
            for s in losers:
                msg += f"• *{s.symbol}* {s.percent_change:.2f}% @ ₦{s.close_price:.2f}\n"

        if breakouts:
            msg += "\n🔓 *RESISTANCE BREAKOUTS*\n"
            for s in breakouts[:3]:
                msg += f"• *{s.symbol}* ₦{s.close_price:.2f} > ₦{s.old_resistance:.2f}\n"

        if breakdowns:
            msg += "\n🔻 *SUPPORT BREAKDOWNS*\n"
            for s in breakdowns[:3]:
                msg += f"• *{s.symbol}* ₦{s.close_price:.2f} < ₦{s.old_support:.2f}\n"

        if momentum:
            msg += "\n⚡ *MOMENTUM (≥5%)*\n"
            for s in momentum[:3]:
                msg += f"• *{s.symbol}* +{s.percent_change:.2f}%\n"

        if spikes:
            msg += "\n🔊 *VOLUME SPIKES*\n"
            for s in spikes[:3]:
                msg += f"• *{s.symbol}* {s.vol_increase}× avg vol\n"

        msg += f"\n📊 *{len(stocks)} stocks* processed today.\n"
        msg += "💡 *TIP:* Breakout + Volume = Entry signal. DYOR."

        await asyncio.gather(self.tg.send(msg), self.wa.send(msg))


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

async def run():
    engine = NGXEngine()
    today = datetime.now().date()

    if today.weekday() >= 5:
        print(f"📅 {today} is a weekend — NGX closed. Exiting.")
        return

    print(f"\n📊 NGX sync starting for {today}…\n")
    source_path = await engine.download_report(today)

    if not source_path:
        msg = f"🛑 NGX SYNC FAILED ({today}) — all download stages exhausted."
        print(msg)
        await asyncio.gather(engine.tg.send(msg), engine.wa.send(msg))
        return

    print(f"\n📂 Source: {source_path}")
    stocks = engine.parse_source(source_path, today)

    # Clean up temp file regardless of parse result
    try:
        if os.path.exists(source_path):
            os.remove(source_path)
    except Exception:
        pass

    if not stocks:
        msg = f"🛑 NGX PARSE FAILED ({today}) — source downloaded but 0 stocks parsed."
        print(msg)
        await asyncio.gather(engine.tg.send(msg), engine.wa.send(msg))
        return

    engine.save(stocks)
    breakouts, breakdowns, momentum_movers, spikes = engine.get_market_alerts(stocks)
    await engine.send_daily_recap(stocks, breakouts, breakdowns, momentum_movers, spikes)
    print(f"\n✅ Done — {len(stocks)} stocks processed.")


if __name__ == "__main__":
    asyncio.run(run())
