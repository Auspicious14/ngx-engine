# import os
# import json
# import asyncio
# import httpx
# import pdfplumber
# import urllib.parse
# import bs4
# from datetime import datetime, date, timedelta
# from typing import List, Optional
# from sqlalchemy import create_engine, Column, Integer, String, Numeric, BigInteger, Date, Float, text, Index
# from sqlalchemy.orm import DeclarativeBase, sessionmaker
# from pydantic import BaseModel, field_validator
# from dotenv import load_dotenv

# load_dotenv()

# # ---------------------------------------------------------------------------
# # HELPERS — called before any httpx.AsyncClient is constructed
# # ---------------------------------------------------------------------------

# def _clean_env(key: str) -> Optional[str]:
#     """
#     Read an environment variable and strip ALL whitespace / non-printable
#     characters that GitHub Actions sometimes appends to secret values.
#     Returns None if the result is empty.
#     """
#     val = (os.getenv(key) or "").strip()
#     val = "".join(c for c in val if c.isprintable())
#     return val or None


# def _get_proxy() -> Optional[str]:
#     """
#     Return a clean proxy URL, or None.
#     Only accepts values that start with a real URL scheme.
#     """
#     raw = (
#         _clean_env("NG_PROXY_URL")
#         or _clean_env("HTTP_PROXY")
#         or _clean_env("HTTPS_PROXY")
#     )
#     if raw and raw.startswith(("http://", "https://", "socks5://")):
#         return raw
#     return None


# def _make_client(
#     *,
#     proxy: Optional[str] = None,
#     extra_headers: Optional[dict] = None,
#     timeout: float = 40.0,
#     base_headers: Optional[dict] = None,
# ) -> httpx.AsyncClient:
#     """
#     Central factory for ALL httpx.AsyncClient instances.

#     trust_env=False  — prevents httpx from reading HTTP_PROXY / HTTPS_PROXY
#                        from the OS environment on its own. Without this, any
#                        client (including Telegram/WhatsApp) picks up the proxy
#                        env var and crashes on its embedded newline.
#     proxy=           — httpx >= 0.28 spelling; None is safe.
#     """
#     headers = {**(base_headers or {}), **(extra_headers or {})}
#     return httpx.AsyncClient(
#         timeout=timeout,
#         follow_redirects=True,
#         headers=headers or None,
#         proxy=proxy,
#         trust_env=False,   # ← key fix: no env-var bleed-in
#     )


# # ---------------------------------------------------------------------------
# # DATABASE MODELS
# # ---------------------------------------------------------------------------

# class Base(DeclarativeBase):
#     pass


# class StockPriceDB(Base):
#     __tablename__ = "stock_prices"
#     id             = Column(Integer, primary_key=True)
#     symbol         = Column(String(20), nullable=False)
#     company_name   = Column(String(255))
#     open_price     = Column(Numeric(10, 2))
#     high_price     = Column(Numeric(10, 2))
#     low_price      = Column(Numeric(10, 2))
#     close_price    = Column(Numeric(10, 2))
#     percent_change = Column(Numeric(10, 2), default=0.0)
#     volume         = Column(BigInteger)
#     trade_date     = Column(Date, nullable=False)

#     __table_args__ = (Index("uix_symbol_date", "symbol", "trade_date", unique=True),)


# class EarningsCalendar(Base):
#     __tablename__ = "earnings_calendar"
#     id             = Column(Integer, primary_key=True)
#     symbol         = Column(String, nullable=False)
#     period         = Column(String)
#     expected_date  = Column(Date)
#     actual_date    = Column(Date)
#     dividend_yield = Column(Float)


# # ---------------------------------------------------------------------------
# # DATA SCHEMAS
# # ---------------------------------------------------------------------------

# class StockSchema(BaseModel):
#     symbol:         str
#     company_name:   str
#     open_price:     float
#     high_price:     float
#     low_price:      float
#     close_price:    float
#     percent_change: float = 0.0
#     volume:         int
#     trade_date:     date
#     # Alert metadata — not persisted
#     old_resistance:      float = 0.0
#     old_support:         float = 0.0
#     vol_increase:        float = 0.0
#     is_corporate_action: bool  = False

#     @field_validator("open_price", "high_price", "low_price", "close_price", mode="before")
#     @classmethod
#     def clean_currency(cls, v):
#         if not v or str(v).strip() in ("-", "", "nil", "N/A"):
#             return 0.0
#         if isinstance(v, str):
#             try:
#                 return float(v.replace("₦", "").replace(",", "").strip())
#             except ValueError:
#                 return 0.0
#         try:
#             return float(v)
#         except (TypeError, ValueError):
#             return 0.0

#     @field_validator("volume", mode="before")
#     @classmethod
#     def clean_volume(cls, v):
#         if not v or str(v).strip() in ("-", "", "nil", "N/A"):
#             return 0
#         if isinstance(v, str):
#             try:
#                 return int(float(v.replace(",", "").strip()))
#             except ValueError:
#                 return 0
#         try:
#             return int(v)
#         except (TypeError, ValueError):
#             return 0


# # ---------------------------------------------------------------------------
# # NOTIFICATION SERVICES
# # Uses _make_client(proxy=None) so no proxy and no env bleed-in.
# # ---------------------------------------------------------------------------

# class TelegramNotifier:
#     def __init__(self):
#         self.token   = _clean_env("TELEGRAM_TOKEN")
#         self.chat_id = _clean_env("TELEGRAM_CHAT_ID")

#     async def send(self, message: str):
#         if not self.token or not self.chat_id:
#             print("⚠️  Telegram config missing — skipping.")
#             return
#         url = f"https://api.telegram.org/bot{self.token}/sendMessage"
#         async with _make_client(timeout=15.0) as client:   # proxy=None, trust_env=False
#             try:
#                 r = await client.post(
#                     url,
#                     json={"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"},
#                 )
#                 print("✅ Telegram sent." if r.status_code == 200
#                       else f"⚠️  Telegram {r.status_code}: {r.text[:120]}")
#             except Exception as e:
#                 print(f"📡 Telegram Error: {e}")


# class WhatsAppNotifier:
#     def __init__(self):
#         self.id_instance = _clean_env("GREEN_API_ID")
#         self.api_token   = _clean_env("GREEN_API_TOKEN")
#         self.group_id    = _clean_env("WHATSAPP_GROUP_ID")
#         api_url          = _clean_env("WHATSAPP_API_URL") or "https://7107.api.greenapi.com"
#         self.endpoint    = (
#             f"{api_url}/waInstance{self.id_instance}"
#             f"/sendMessage/{self.api_token}"
#         )

#     async def send(self, message: str):
#         if not all([self.id_instance, self.api_token, self.group_id]):
#             print("⚠️  WhatsApp config missing — skipping.")
#             return False
#         async with _make_client(timeout=30.0) as client:   # proxy=None, trust_env=False
#             try:
#                 r = await client.post(
#                     self.endpoint,
#                     json={"chatId": self.group_id, "message": message},
#                 )
#                 ok = r.status_code == 200
#                 print("✅ WhatsApp sent." if ok
#                       else f"⚠️  WhatsApp {r.status_code}: {r.text[:120]}")
#                 return ok
#             except Exception as e:
#                 print(f"📡 WhatsApp Error: {e}")
#                 return False


# # ---------------------------------------------------------------------------
# # CORE ENGINE
# # ---------------------------------------------------------------------------

# class NGXEngine:
#     """
#     Download → Parse → Save → Alert pipeline for NGX daily equity data.

#     Download stages (in order):
#       1 — NGX direct redirect download URLs
#       2 — doclib date-pattern PDF guessing (last 7 trading days × all known patterns)
#       3 — DataTables / AJAX JSON endpoint
#       4 — Full HTML scrape with cookie handshake
#       5 — Stooq CSV per-ticker fallback (never geo-blocked)

#     Geo-block mitigation:
#       Set NG_PROXY_URL (e.g. http://user:pass@host:port) for a Nigerian proxy.
#       Only NGX requests are routed through it; Telegram/WhatsApp are not.
#     """

#     BASE_HEADERS = {
#         "User-Agent": (
#             "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
#             "AppleWebKit/537.36 (KHTML, like Gecko) "
#             "Chrome/122.0.0.0 Safari/537.36"
#         ),
#         "Accept": (
#             "text/html,application/xhtml+xml,application/xml;"
#             "q=0.9,image/avif,image/webp,*/*;q=0.8"
#         ),
#         "Accept-Language": "en-US,en;q=0.5",
#         "Referer": "https://ngxgroup.com/",
#     }

#     def __init__(self):
#         db_url = _clean_env("DATABASE_URL") or ""
#         if not db_url:
#             raise RuntimeError("DATABASE_URL is not set.")
#         if db_url.startswith("postgres://"):
#             db_url = db_url.replace("postgres://", "postgresql://", 1)

#         self.engine  = create_engine(db_url, pool_pre_ping=True)
#         self.Session = sessionmaker(bind=self.engine)
#         self.wa      = WhatsAppNotifier()
#         self.tg      = TelegramNotifier()
#         self._proxy  = _get_proxy()

#         if self._proxy:
#             print(f"🌍 Proxy active (NGX requests only): {self._proxy}")
#         else:
#             print("ℹ️  No proxy — connecting directly.")

#         Base.metadata.create_all(self.engine)

#     def _ngx_client(self, extra_headers: Optional[dict] = None) -> httpx.AsyncClient:
#         """Client for NGX requests — carries proxy + NGX headers."""
#         return _make_client(
#             proxy=self._proxy,
#             base_headers=self.BASE_HEADERS,
#             extra_headers=extra_headers,
#         )

#     # ------------------------------------------------------------------
#     # UTILITIES
#     # ------------------------------------------------------------------

#     @staticmethod
#     def _is_pdf(content: bytes) -> bool:
#         return content[:4] == b"%PDF"

#     @staticmethod
#     def _trading_days_back(start: date, n: int = 7) -> List[date]:
#         """Return up to n weekdays going back from start (inclusive)."""
#         days, cursor = [], start
#         while len(days) < n:
#             if cursor.weekday() < 5:
#                 days.append(cursor)
#             cursor -= timedelta(days=1)
#         return days

#     # ------------------------------------------------------------------
#     # STAGE 1 — Direct redirect URLs
#     # ------------------------------------------------------------------

#     async def _stage1_redirect(self, client: httpx.AsyncClient, target_date: date) -> Optional[str]:
#         for url in [
#             "https://ngxgroup.com/ngx-download/daily-official-list-equities/",
#             "https://ngxgroup.com/ngx-download/market-data-pricelist-2/",
#         ]:
#             try:
#                 res = await client.get(url)
#                 if res.status_code == 200 and self._is_pdf(res.content):
#                     path = f"ngx_s1_{target_date}.pdf"
#                     open(path, "wb").write(res.content)
#                     print(f"✅ Stage 1: {url}")
#                     return path
#                 print(f"Stage 1 miss ({res.status_code}): {url}")
#             except Exception as e:
#                 print(f"Stage 1 error: {e}")
#         return None

#     # ------------------------------------------------------------------
#     # STAGE 2 — doclib date-pattern PDF guessing
#     # Covers all delimiter / ordering / template variants NGX has ever used.
#     # ------------------------------------------------------------------

#     async def _stage2_pdf_guess(self, client: httpx.AsyncClient, target_date: date) -> Optional[str]:
#         candidate_dates = self._trading_days_back(target_date, n=7)
#         delimiters = ["-", " ", ".", "/", ""]
#         templates = [
#             "Daily%20Official%20List%20-%20Equities%20for%20{date}.pdf",
#             "Daily%20Official%20List-Equities%20for%20{date}.pdf",
#             "DAILY%20OFFICIAL%20LIST%20-%20EQUITIES%20FOR%20{date}.pdf",
#             "Daily%20Official%20List%20{date}.pdf",
#             "DAILY%20SUMMARY%20FOR%20{date}.pdf",
#             "Daily%20Summary%20for%20{date}.pdf",
#             "Equities%20Price%20List%20{date}.pdf",
#             "NGX%20Daily%20Official%20List%20{date}.pdf",
#             "equities-price-list-{date}.pdf",
#         ]
#         base = "https://doclib.ngxgroup.com/DownloadsContent/"

#         for d in candidate_dates:
#             # Three date orderings: DD-MM-YYYY, MM-DD-YYYY, YYYY-MM-DD
#             date_fmts = [
#                 d.strftime("%d{s}%m{s}%Y"),
#                 d.strftime("%m{s}%d{s}%Y"),
#                 d.strftime("%Y{s}%m{s}%d"),
#             ]
#             for fmt in date_fmts:
#                 for sep in delimiters:
#                     date_str = fmt.replace("{s}", sep)
#                     encoded  = urllib.parse.quote(date_str)
#                     for tmpl in templates:
#                         url = base + tmpl.format(date=encoded)
#                         try:
#                             res = await client.get(url)
#                             if res.status_code == 200 and self._is_pdf(res.content):
#                                 path = f"ngx_s2_{d}.pdf"
#                                 open(path, "wb").write(res.content)
#                                 print(f"✅ Stage 2 ({d}): {url}")
#                                 return path
#                         except Exception:
#                             continue

#         print("Stage 2: no PDF found.")
#         return None

#     # ------------------------------------------------------------------
#     # STAGE 3 — DataTables / AJAX JSON
#     # ------------------------------------------------------------------

#     async def _stage3_ajax(self, client: httpx.AsyncClient, target_date: date) -> Optional[str]:
#         ajax_headers = {
#             **self.BASE_HEADERS,
#             "X-Requested-With": "XMLHttpRequest",
#             "Accept": "application/json, text/javascript, */*; q=0.01",
#         }
#         endpoints = [
#             "https://ngxgroup.com/exchange/data/equities-price-list/?draw=1&start=0&length=1000",
#             "https://ngxgroup.com/wp-json/ngx/v1/equities",
#             "https://ngxgroup.com/wp-json/ngx/v1/market-data",
#             "https://doclib.ngxgroup.com/REST/api/operations/getequitiesprices",
#             "https://doclib.ngxgroup.com/REST/api/operations/getsecurities",
#         ]
#         for url in endpoints:
#             try:
#                 res = await client.get(url, headers=ajax_headers)
#                 if res.status_code != 200:
#                     continue
#                 ct   = res.headers.get("content-type", "")
#                 body = res.text.strip()
#                 if "json" not in ct and not body.startswith(("{", "[")):
#                     continue
#                 data = res.json()
#                 rows = data.get("data") or data.get("aaData") or data.get("securities") or []
#                 if len(rows) > 5:
#                     path = f"ngx_s3_{target_date}.json"
#                     json.dump({"data": rows}, open(path, "w", encoding="utf-8"))
#                     print(f"✅ Stage 3 AJAX: {url} ({len(rows)} rows)")
#                     return path
#             except Exception as e:
#                 print(f"Stage 3 error [{url}]: {e}")
#         print("Stage 3: no AJAX endpoint returned data.")
#         return None

#     # ------------------------------------------------------------------
#     # STAGE 4 — HTML scrape with cookie handshake
#     # ------------------------------------------------------------------

#     async def _stage4_html_scrape(self, client: httpx.AsyncClient, target_date: date) -> Optional[str]:
#         cookies = {}
#         try:
#             warmup = await client.get("https://ngxgroup.com/")
#             cookies = dict(warmup.cookies)
#             print(f"Stage 4 warm-up: {warmup.status_code}")
#             await asyncio.sleep(1.5)
#         except Exception as e:
#             print(f"Stage 4 warm-up error: {e}")

#         for url in [
#             "https://ngxgroup.com/exchange/data/equities-price-list/",
#             "https://ngxgroup.com/ir/equities-market/",
#             "https://www.ngxgroup.com/exchange/data/equities-price-list/",
#         ]:
#             try:
#                 res = await client.get(url, cookies=cookies)
#                 print(f"Stage 4 [{res.status_code}] {len(res.text)}b: {url}")
#                 if res.status_code != 200:
#                     continue
#                 body = res.text
#                 if "cf-browser-verification" in body or "checking your browser" in body.lower():
#                     print("Stage 4: Cloudflare challenge — proxy not bypassing geo-block.")
#                     continue
#                 soup   = bs4.BeautifulSoup(body, "html.parser")
#                 tables = soup.find_all("table")
#                 if any(len(t.find_all("tr")) > 5 for t in tables):
#                     path = f"ngx_s4_{target_date}.html"
#                     open(path, "w", encoding="utf-8").write(body)
#                     print(f"✅ Stage 4 HTML: {url}")
#                     return path
#                 print("Stage 4: table present but <5 rows (JS-rendered).")
#             except Exception as e:
#                 print(f"Stage 4 error [{url}]: {e}")

#         print("Stage 4: no usable HTML.")
#         return None

#     # ------------------------------------------------------------------
#     # STAGE 5 — Stooq CSV fallback (never geo-blocked)
#     # ------------------------------------------------------------------

#     async def _stage5_stooq_csv(self, client: httpx.AsyncClient, target_date: date) -> Optional[str]:
#         TOP_NGX = [
#             "DANGCEM", "MTNN", "AIRTELAFRI", "GTCO", "ZENITHBANK",
#             "ACCESSCORP", "FBNH", "UBA", "STANBIC", "TRANSCORP",
#             "SEPLAT", "OANDO", "NESTLE", "UNILEVER", "NB",
#             "BUACEMENT", "WAPCO", "FLOURMILL", "PRESCO", "OKOMUOIL",
#             "FIDELITYBK", "FCMB", "STERLING", "JAIZBANK", "TRIDENT",
#             "CHAMS", "CAVERTON", "CONOIL", "TOTALENERGIES", "ETERNA",
#         ]
#         candidate_dates = self._trading_days_back(target_date, n=5)
#         print(f"Stage 5: Stooq CSV — {len(TOP_NGX)} tickers, up to {len(candidate_dates)} dates…")

#         for d in candidate_dates:
#             rows = []
#             for symbol in TOP_NGX:
#                 url = (
#                     f"https://stooq.com/q/d/l/?s={symbol.lower()}.ng"
#                     f"&d1={d.strftime('%Y%m%d')}&d2={d.strftime('%Y%m%d')}&i=d"
#                 )
#                 try:
#                     res = await client.get(url)
#                     if res.status_code == 200 and "Date" in res.text:
#                         lines = res.text.strip().splitlines()
#                         if len(lines) >= 2:
#                             parts = lines[1].split(",")
#                             if len(parts) >= 5 and parts[4].replace(".", "").isdigit():
#                                 rows.append({
#                                     "symbol":       symbol,
#                                     "company_name": symbol,
#                                     "open":         parts[1],
#                                     "high":         parts[2],
#                                     "low":          parts[3],
#                                     "close":        parts[4],
#                                     "volume":       parts[5] if len(parts) > 5 else "0",
#                                 })
#                 except Exception:
#                     continue

#             if rows:
#                 path = f"ngx_s5_{d}.json"
#                 json.dump({"stooq": rows, "trade_date": str(d)}, open(path, "w"))
#                 print(f"✅ Stage 5 Stooq: {len(rows)} tickers for {d}.")
#                 return path

#         print("Stage 5: Stooq returned no data.")
#         return None

#     # ------------------------------------------------------------------
#     # DOWNLOAD ORCHESTRATOR
#     # ------------------------------------------------------------------

#     async def download_report(self, target_date: date) -> Optional[str]:
#         async with self._ngx_client() as client:
#             for label, fn in [
#                 ("1 (redirect)",    self._stage1_redirect),
#                 ("2 (PDF guess)",   self._stage2_pdf_guess),
#                 ("3 (AJAX JSON)",   self._stage3_ajax),
#                 ("4 (HTML scrape)", self._stage4_html_scrape),
#                 ("5 (Stooq CSV)",   self._stage5_stooq_csv),
#             ]:
#                 print(f"\n── Stage {label} ──")
#                 try:
#                     path = await fn(client, target_date)
#                     if path:
#                         return path
#                 except Exception as e:
#                     print(f"Stage {label} unhandled error: {e}")

#         print("\n🛑 All download stages exhausted.")
#         return None

#     # ------------------------------------------------------------------
#     # PARSERS
#     # ------------------------------------------------------------------

#     def parse_source(self, path: str, trade_date: date) -> List[StockSchema]:
#         if path.endswith(".pdf"):
#             return self._parse_pdf(path, trade_date)
#         elif path.endswith(".html"):
#             return self._parse_html(path, trade_date)
#         elif path.endswith(".json"):
#             return self._parse_json(path, trade_date)
#         print(f"⚠️  Unknown format: {path}")
#         return []

#     def _parse_pdf(self, path: str, trade_date: date) -> List[StockSchema]:
#         data = []
#         try:
#             with pdfplumber.open(path) as pdf:
#                 print(f"PDF: {len(pdf.pages)} pages")
#                 for page in pdf.pages:
#                     table = page.extract_table()
#                     if not table:
#                         continue
#                     header_idx = -1
#                     for i, row in enumerate(table[:10]):
#                         if row and any("Symbol" in str(x) for x in row if x):
#                             header_idx = i
#                             break
#                     if header_idx == -1:
#                         continue
#                     for row in table[header_idx + 1:]:
#                         if not row or len(row) < 10 or not row[0]:
#                             continue
#                         symbol = str(row[0]).strip()
#                         if not symbol.isupper() or " " in symbol:
#                             continue
#                         close_p = row[5]
#                         try:
#                             data.append(StockSchema(
#                                 symbol=symbol,
#                                 company_name=str(row[1] or "").strip(),
#                                 open_price=row[3] or close_p,
#                                 high_price=row[4] or close_p,
#                                 low_price=row[5]  or close_p,
#                                 close_price=close_p,
#                                 volume=row[11] if len(row) > 11 else row[-1],
#                                 trade_date=trade_date,
#                             ))
#                         except Exception:
#                             continue
#         except Exception as e:
#             print(f"PDF Parse Error: {e}")
#         print(f"PDF parsed: {len(data)} stocks.")
#         return data

#     def _parse_html(self, path: str, trade_date: date) -> List[StockSchema]:
#         stocks = []
#         try:
#             with open(path, "r", encoding="utf-8") as f:
#                 soup = bs4.BeautifulSoup(f.read(), "html.parser")

#             table = (
#                 soup.find("table", {"id": "table_1"})
#                 or soup.find("table", {"id": "DataTables_Table_0"})
#                 or soup.find("table", class_=lambda c: c and "dataTable" in c)
#                 or soup.find("table")
#             )
#             if not table:
#                 print("HTML Parse: no table.")
#                 return []

#             rows = table.find_all("tr")
#             print(f"HTML Parse: {len(rows)} rows.")

#             col_map = {}
#             for row in rows[:5]:
#                 headers = [th.get_text(strip=True).lower() for th in row.find_all(["th", "td"])]
#                 if any(h in ("symbol", "ticker") for h in headers):
#                     for i, h in enumerate(headers):
#                         if h in ("symbol", "ticker"):
#                             col_map["symbol"] = i
#                         elif any(k in h for k in ("company", "name", "security")):
#                             col_map["company"] = i
#                         elif h == "open":
#                             col_map["open"] = i
#                         elif h == "high":
#                             col_map["high"] = i
#                         elif h == "low":
#                             col_map["low"] = i
#                         elif any(k in h for k in ("close", "last", "price")):
#                             col_map["close"] = i
#                         elif "vol" in h:
#                             col_map["volume"] = i
#                     break

#             if not col_map:
#                 col_map = {"symbol": 0, "company": 1, "open": 3, "high": 4, "low": 5, "close": 6, "volume": 10}
#                 print("HTML Parse: positional defaults.")

#             for row in rows[1:]:
#                 cols = row.find_all("td")
#                 if not cols or len(cols) < max(col_map.values()) + 1:
#                     continue

#                 def get(key, fb=0):
#                     idx = col_map.get(key, fb)
#                     return cols[idx].get_text(strip=True) if idx < len(cols) else ""

#                 symbol = get("symbol")
#                 if not symbol or not any(c.isalpha() for c in symbol) or symbol[0].islower():
#                     continue
#                 close_raw = get("close", 6)
#                 if not close_raw or close_raw == "-":
#                     continue
#                 try:
#                     stocks.append(StockSchema(
#                         symbol=symbol,
#                         company_name=get("company", 1) or symbol,
#                         open_price=get("open", 3)   or close_raw,
#                         high_price=get("high", 4)   or close_raw,
#                         low_price=get("low", 5)     or close_raw,
#                         close_price=close_raw,
#                         volume=get("volume", 10)    or "0",
#                         trade_date=trade_date,
#                     ))
#                 except Exception:
#                     continue
#         except Exception as e:
#             print(f"HTML Parse Error: {e}")
#         print(f"HTML parsed: {len(stocks)} stocks.")
#         return stocks

#     def _parse_json(self, path: str, trade_date: date) -> List[StockSchema]:
#         """
#         Handles:
#           - DataTables AJAX array rows:  {"data": [[col0, col1, ...], ...]}
#           - DataTables AJAX object rows: {"data": [{"symbol": ..., ...}, ...]}
#           - Stooq fallback:              {"stooq": [...], "trade_date": "YYYY-MM-DD"}
#         """
#         stocks = []
#         try:
#             with open(path, "r", encoding="utf-8") as f:
#                 payload = json.load(f)

#             # Use embedded trade_date if present (Stooq may have an earlier date)
#             if "trade_date" in payload:
#                 try:
#                     trade_date = date.fromisoformat(payload["trade_date"])
#                 except Exception:
#                     pass

#             # --- Stooq shape ---
#             if "stooq" in payload:
#                 for row in payload["stooq"]:
#                     try:
#                         stocks.append(StockSchema(
#                             symbol=row["symbol"],
#                             company_name=row.get("company_name", row["symbol"]),
#                             open_price=row.get("open", 0),
#                             high_price=row.get("high", 0),
#                             low_price=row.get("low", 0),
#                             close_price=row.get("close", 0),
#                             volume=row.get("volume", 0),
#                             trade_date=trade_date,
#                         ))
#                     except Exception:
#                         continue
#                 print(f"JSON (Stooq) parsed: {len(stocks)} stocks.")
#                 return stocks

#             # --- DataTables / AJAX shape ---
#             rows = (
#                 payload.get("data")
#                 or payload.get("aaData")
#                 or payload.get("securities")
#                 or []
#             )
#             if not rows:
#                 print("JSON Parse: no rows.")
#                 return []

#             sample = rows[0]

#             if isinstance(sample, dict):
#                 for row in rows:
#                     try:
#                         symbol = (
#                             row.get("symbol") or row.get("Symbol")
#                             or row.get("ticker") or ""
#                         ).strip()
#                         if not symbol:
#                             continue
#                         close_p = (
#                             row.get("close_price") or row.get("ClosingPrice")
#                             or row.get("close")    or row.get("last_price") or 0
#                         )
#                         stocks.append(StockSchema(
#                             symbol=symbol,
#                             company_name=row.get("company_name") or row.get("CompanyName") or symbol,
#                             open_price=row.get("open_price")  or row.get("OpeningPrice") or close_p,
#                             high_price=row.get("high_price")  or row.get("HighPrice")    or close_p,
#                             low_price=row.get("low_price")    or row.get("LowPrice")     or close_p,
#                             close_price=close_p,
#                             volume=row.get("volume") or row.get("Volume") or 0,
#                             trade_date=trade_date,
#                         ))
#                     except Exception:
#                         continue

#             elif isinstance(sample, list):
#                 for row in rows:
#                     if len(row) < 7 or not row[0]:
#                         continue
#                     symbol = str(row[0]).strip()
#                     if not symbol.isupper():
#                         continue
#                     close_p = row[6] if len(row) > 6 else row[4]
#                     try:
#                         stocks.append(StockSchema(
#                             symbol=symbol,
#                             company_name=str(row[1] or "").strip(),
#                             open_price=row[3] or close_p,
#                             high_price=row[4] or close_p,
#                             low_price=row[5]  or close_p,
#                             close_price=close_p,
#                             volume=row[11] if len(row) > 11 else row[-1],
#                             trade_date=trade_date,
#                         ))
#                     except Exception:
#                         continue

#         except Exception as e:
#             print(f"JSON Parse Error: {e}")

#         print(f"JSON parsed: {len(stocks)} stocks.")
#         return stocks

#     # ------------------------------------------------------------------
#     # PERSISTENCE
#     # ------------------------------------------------------------------

#     def save(self, stocks: List[StockSchema]):
#         session = self.Session()
#         saved = 0
#         try:
#             for stock in stocks:
#                 prev = (
#                     session.query(StockPriceDB.close_price)
#                     .filter(
#                         StockPriceDB.symbol == stock.symbol,
#                         StockPriceDB.trade_date < stock.trade_date,
#                     )
#                     .order_by(StockPriceDB.trade_date.desc())
#                     .first()
#                 )
#                 if prev and float(prev[0]) > 0:
#                     stock.percent_change = round(
#                         ((stock.close_price - float(prev[0])) / float(prev[0])) * 100, 2
#                     )
#                 session.execute(text("""
#                     INSERT INTO stock_prices
#                         (symbol, company_name, open_price, high_price, low_price,
#                          close_price, percent_change, volume, trade_date)
#                     VALUES
#                         (:symbol, :company_name, :open_price, :high_price, :low_price,
#                          :close_price, :percent_change, :volume, :trade_date)
#                     ON CONFLICT (symbol, trade_date) DO UPDATE SET
#                         company_name   = EXCLUDED.company_name,
#                         open_price     = EXCLUDED.open_price,
#                         high_price     = EXCLUDED.high_price,
#                         low_price      = EXCLUDED.low_price,
#                         close_price    = EXCLUDED.close_price,
#                         percent_change = EXCLUDED.percent_change,
#                         volume         = EXCLUDED.volume;
#                 """), stock.model_dump(
#                     exclude={"old_resistance", "old_support", "vol_increase", "is_corporate_action"}
#                 ))
#                 saved += 1
#             session.commit()
#             print(f"💾 Saved {saved} stocks.")
#         except Exception as e:
#             session.rollback()
#             print(f"DB Error: {e}")
#         finally:
#             session.close()

#     # ------------------------------------------------------------------
#     # MARKET ALERTS
#     # ------------------------------------------------------------------

#     def get_market_alerts(self, stocks: List[StockSchema]):
#         session = self.Session()
#         breakouts, breakdowns, momentum, volume_spikes = [], [], [], []
#         try:
#             for stock in stocks:
#                 stock.is_corporate_action = abs(stock.percent_change) > 10.5

#                 levels = session.execute(text("""
#                     SELECT MAX(high_price), MIN(low_price)
#                     FROM stock_prices
#                     WHERE symbol = :symbol AND trade_date < :today
#                     ORDER BY trade_date DESC LIMIT 30
#                 """), {"symbol": stock.symbol, "today": stock.trade_date}).first()

#                 if levels and levels[0] and levels[1]:
#                     res, sup = float(levels[0]), float(levels[1])
#                     if res > 0 and stock.close_price > res:
#                         stock.old_resistance = res
#                         breakouts.append(stock)
#                     if sup > 0 and stock.close_price < sup and not stock.is_corporate_action:
#                         stock.old_support = sup
#                         breakdowns.append(stock)

#                 if stock.percent_change >= 5.0 and not stock.is_corporate_action:
#                     momentum.append(stock)

#                 avg_vol = session.execute(text("""
#                     SELECT AVG(volume) FROM (
#                         SELECT volume FROM stock_prices
#                         WHERE symbol = :symbol AND trade_date < :today
#                         ORDER BY trade_date DESC LIMIT 10
#                     ) sub
#                 """), {"symbol": stock.symbol, "today": stock.trade_date}).scalar()

#                 if avg_vol and stock.volume > float(avg_vol) * 2:
#                     stock.vol_increase = round(stock.volume / float(avg_vol), 1)
#                     volume_spikes.append(stock)

#             return breakouts, breakdowns, momentum, volume_spikes
#         except Exception as e:
#             print(f"Alert Engine Error: {e}")
#             return [], [], [], []
#         finally:
#             session.close()

#     # ------------------------------------------------------------------
#     # NOTIFICATIONS
#     # ------------------------------------------------------------------

#     async def send_daily_recap(
#         self,
#         stocks:     List[StockSchema],
#         breakouts:  List[StockSchema],
#         breakdowns: List[StockSchema],
#         momentum:   List[StockSchema],
#         spikes:     List[StockSchema],
#     ):
#         if not stocks:
#             return

#         sorted_s = sorted(stocks, key=lambda x: x.percent_change, reverse=True)
#         gainers  = [s for s in sorted_s if s.percent_change > 0][:5]
#         losers   = sorted(
#             [s for s in stocks if s.percent_change < 0],
#             key=lambda x: x.percent_change
#         )[:5]
#         adv  = len([s for s in stocks if s.percent_change > 0])
#         dec  = len([s for s in stocks if s.percent_change < 0])
#         unch = len(stocks) - adv - dec

#         msg  = f"🚀 *NGX ALPHA INTELLIGENCE* — {datetime.now().strftime('%d %b %Y')}\n"
#         msg += "━━━━━━━━━━━━━━━━\n\n"
#         msg += f"📊 *BREADTH*: {adv}↑  {dec}↓  {unch}→  ({len(stocks)} stocks)\n\n"

#         if gainers:
#             msg += "📈 *TOP GAINERS*\n"
#             for s in gainers:
#                 msg += f"• *{s.symbol}* +{s.percent_change:.2f}% @ ₦{s.close_price:.2f}\n"
#         if losers:
#             msg += "\n📉 *TOP LOSERS*\n"
#             for s in losers:
#                 msg += f"• *{s.symbol}* {s.percent_change:.2f}% @ ₦{s.close_price:.2f}\n"
#         if breakouts:
#             msg += "\n🔓 *RESISTANCE BREAKOUTS*\n"
#             for s in breakouts[:3]:
#                 msg += f"• *{s.symbol}* ₦{s.close_price:.2f} > ₦{s.old_resistance:.2f}\n"
#         if breakdowns:
#             msg += "\n🔻 *SUPPORT BREAKDOWNS*\n"
#             for s in breakdowns[:3]:
#                 msg += f"• *{s.symbol}* ₦{s.close_price:.2f} < ₦{s.old_support:.2f}\n"
#         if momentum:
#             msg += "\n⚡ *MOMENTUM (≥5%)*\n"
#             for s in momentum[:3]:
#                 msg += f"• *{s.symbol}* +{s.percent_change:.2f}%\n"
#         if spikes:
#             msg += "\n🔊 *VOLUME SPIKES*\n"
#             for s in spikes[:3]:
#                 msg += f"• *{s.symbol}* {s.vol_increase}× avg vol\n"

#         msg += f"\n📊 *{len(stocks)} stocks* processed today.\n"
#         msg += "💡 *TIP:* Breakout + Volume = Entry signal. DYOR."

#         await asyncio.gather(self.tg.send(msg), self.wa.send(msg))

#     async def _schedule_morning_retry(target_date: date):
#         """
#         Dispatch a GitHub Actions workflow_dispatch event to retry
#         fetching target_date's data the following morning.
#         """
#         token = _clean_env("NGX_GITHUB_TOKEN")
#         repo  = _clean_env("NGX_GITHUB_REPO")   # e.g. "auspicious/ngx-engine"
        
#         if not token or not repo:
#             print("⚠️  GITHUB_TOKEN or GITHUB_REPO not set — cannot schedule retry.")
#             return
    
#         url = f"https://api.github.com/repos/{repo}/actions/workflows/sync.yml/dispatches"
#         payload = {
#             "ref": "main",
#             "inputs": {
#                 "target_date": str(target_date),
#                 "is_retry": "true"
#             }
#         }
#         # Use standard client factory
#         async with _make_client(timeout=15.0) as client:
#             try:
#                 r = await client.post(
#                     url,
#                     json=payload,
#                     headers={
#                         "Authorization": f"Bearer {token}",
#                         "Accept": "application/vnd.github+json",
#                     }
#                 )
#                 if r.status_code == 204:
#                     print(f"📅 Morning retry scheduled for {target_date}.")
#                 else:
#                     print(f"⚠️  Retry dispatch failed: {r.status_code} {r.text[:120]}")
#             except Exception as e:
#                 print(f"Retry dispatch error: {e}")

   
# # ---------------------------------------------------------------------------
# # ENTRY POINT
# # ---------------------------------------------------------------------------

# async def run():
#     engine = NGXEngine()
    
#     # Support manual/retry dispatch with a specific date via environment variable
#     input_date = _clean_env("INPUT_TARGET_DATE")
#     is_retry   = _clean_env("INPUT_IS_RETRY") == "true"
#     today      = date.fromisoformat(input_date) if input_date else datetime.now().date()

#     if today.weekday() >= 5:
#         print(f"📅 {today} is a weekend — NGX closed.")
#         return

#     print(f"\n📊 NGX sync starting for {today} (Retry: {is_retry})…\n")
#     source_path = await engine.download_report(today)

#     if not source_path:
#         # If this wasn't already a morning retry, schedule one
#         if not is_retry:
#             msg = f"⏳ NGX data not yet available for {today} — retry scheduled for tomorrow morning."
#             print(msg)
#             await asyncio.gather(engine.tg.send(msg), engine.wa.send(msg))
#             await _schedule_morning_retry(today)
#         else:
#             msg = f"🛑 NGX SYNC FAILED ({today}) — exhausted all stages including morning retry."
#             print(msg)
#             await asyncio.gather(engine.tg.send(msg), engine.wa.send(msg))
#         return

#     print(f"\n📂 Source: {source_path}")
#     stocks = engine.parse_source(source_path, today)

#     # ... (rest of cleanup and processing same as source 2)
#     try:
#         if os.path.exists(source_path):
#             os.remove(source_path)
#     except Exception:
#         pass

#     if not stocks:
#         msg = f"🛑 NGX PARSE FAILED ({today}) — downloaded but 0 stocks parsed."
#         print(msg)
#         await asyncio.gather(engine.tg.send(msg), engine.wa.send(msg))
#         return

#     engine.save(stocks)
#     breakouts, breakdowns, momentum_movers, spikes = engine.get_market_alerts(stocks)
#     await engine.send_daily_recap(stocks, breakouts, breakdowns, momentum_movers, spikes)
#     print(f"\n✅ Done — {len(stocks)} stocks processed.")


# if __name__ == "__main__":
#     asyncio.run(run()) 




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
    # Temporary fields for alert logic
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
        
    async def send_daily_recap(self, stocks, breakouts, breakdowns, momentum, spikes):
        upcoming_earn, recently_reported = self.get_earnings_watch()
        gainers, losers = self.get_top_performers(stocks)
        today_str = datetime.now().strftime("%d %b %Y")
        has_anomaly = False
        
        msg = f"🚀 *NGX ALPHA INTELLIGENCE* ({today_str})\n"
        msg += "━━━━━━━━━━━━━━━━\n\n"

        # --- SECTION 0: TOP GAINERS (Monospace Table) ---
        msg += "📈 *TOP 5 GAINERS*\n"
        for s in gainers:
            msg += f"`{s.symbol:<10} ₦{s.close_price:>7.2f}  (+{s.percent_change:>5.2f}%)`\n"
        
        # --- SECTION 1: TOP LOSERS (Monospace Table) ---
        msg += "\n📉 *TOP 5 LOSERS*\n"
        for s in losers:
            change_val = s.percent_change
            change_str = f"({change_val:>6.2f}%)"
            
            if abs(change_val) > 10.5:
                change_str += " 🔸"
                has_anomaly = True
                
            msg += f"`{s.symbol:<10} ₦{s.close_price:>7.2f}  {change_str}`\n"
            
        if has_anomaly:
            msg += "\n*🔸 Note:* Extreme moves (>10%) are usually Dividend Mark-downs, not market sell-offs.\n"
        
        msg += "━━━━━━━━━━━━━━━━\n\n"

        # --- SECTION 2: ALERTS ---
        if breakouts:
            msg += "🔓 *RESISTANCE BREAKOUTS*\n"
            for s in breakouts[:3]:
                msg += f"• *{s.symbol}*: ₦{s.close_price} (Broke ₦{s.old_resistance})\n"
            msg += "\n"

        if breakdowns:
            msg += "⚠️ *SUPPORT BREAKDOWNS*\n"
            for s in breakdowns[:3]:
                msg += f"• *{s.symbol}*: ₦{s.close_price} (Below ₦{s.old_support})\n"
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
        months_to_try = [target_date.month]
        if target_date.month == 4: months_to_try.append(2)
            
        candidate_strings = []
        for m in months_to_try:
            try:
                candidate_date = target_date.replace(month=m)
                for sep in delimiters:
                    fmt = f"%d{sep}%m{sep}%Y"
                    candidate_strings.append(candidate_date.strftime(fmt))
            except ValueError: continue

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
                session.execute(stmt, stock.model_dump(exclude={'old_resistance', 'old_support', 'vol_increase', 'is_corporate_action'}))
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

# --- MAIN ---
if __name__ == "__main__":
    async def run_daily_sync():
        engine = NGXEngine()
        today = datetime.now().date()
        pdf = await engine.download_report(today)
        if not pdf: return
        stocks = engine.parse_pdf(pdf, today)
        if not stocks: return
        engine.save(stocks)
        breakouts, breakdowns, momentum, spikes = engine.get_market_alerts(stocks)        
        await engine.send_daily_recap(stocks, breakouts, breakdowns, momentum, spikes)
        if os.path.exists(pdf): os.remove(pdf)

    asyncio.run(run_daily_sync())
