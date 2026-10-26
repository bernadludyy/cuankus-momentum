"""Permanent IDX history warehouse on Neon; independent of the ephemeral API cache.

Data saved per ticker/trading date, never conflating today's partial candle with
a completed daily bar. Existing API cache remains the low-latency path.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from .postgres_backend import configured_database_url
from .universe_intelligence import _items, _history, history_features, institutional_flow

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:
    psycopg = None
    Jsonb = None

WIB = timezone(timedelta(hours=7))
_CODE = re.compile(r"^[A-Z0-9]{1,15}$")
_TRADE_ID_FIELDS = (
    "id", "tradeId", "tradeID", "tradeNo", "tradeNumber",
    "sequence", "seq", "matchId", "matchID",
)


def _num(row: dict, *names: str) -> float | None:
    for key in names:
        val = row.get(key)
        if val is None or val == "" or isinstance(val, bool):
            continue
        try:
            n = float(str(val).replace(",", ""))
            if n == n and abs(n) < 1e25:
                return n
        except (ValueError, TypeError):
            continue
    return None


def _session_date(row: dict, *, history: bool) -> date | None:
    stamp = str(row.get("date") or row.get("Date") or row.get("TradingDate") or "")[:10]
    try:
        return date.fromisoformat(stamp)
    except ValueError:
        return None


def _trade_clock(value: Any) -> time | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "T" in text:
        text = text.rsplit("T", 1)[-1]
    text = text.removesuffix("Z").split("+", 1)[0]
    for pattern in ("%H:%M:%S", "%H:%M:%S.%f"):
        try:
            return datetime.strptime(text, pattern).time()
        except ValueError:
            continue
    return None


def _trade_signature(item: dict[str, Any]) -> str:
    for key in _TRADE_ID_FIELDS:
        value = item.get(key)
        if value not in (None, ""):
            return f"{key}:{value}"
    normalized = json.dumps(
        item,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def prepare_volume_by_price_trades(
    ticker: str,
    payload: Any,
    *,
    minimum_lot: int,
    asof: datetime | None = None,
) -> list[dict[str, Any]]:
    """Normalize one existing running-trades response for idempotent storage."""
    ticker = ticker.strip().upper()
    if not _CODE.fullmatch(ticker):
        raise ValueError("Invalid IDX ticker")

    now = (asof or datetime.now(WIB)).astimezone(WIB)
    minimum_lot = max(1, int(minimum_lot))
    occurrences: Counter[str] = Counter()
    prepared: list[dict[str, Any]] = []

    for item in _items(payload):
        clock = _trade_clock(item.get("time") or item.get("tradeTime"))
        if clock is None:
            continue
        trade_at = datetime.combine(now.date(), clock, tzinfo=WIB)
        if trade_at > now + timedelta(minutes=2):
            trade_at -= timedelta(days=1)

        price = _num(item, "price", "Price", "tradePrice", "last", "Last")
        lots = _num(item, "lots", "lot", "Lots", "Lot")
        if price is None or price <= 0 or lots is None or lots < minimum_lot:
            continue

        action = str(item.get("action") or "UNKNOWN").strip().upper()
        if action not in {"BUY", "SELL"}:
            action = "UNKNOWN"

        trade_value = _num(item, "tradeValue", "transactionValue", "value", "Value")
        if trade_value is None or trade_value < 0:
            trade_value = price * lots * 100.0

        base_key = _trade_signature(item)
        occurrences[base_key] += 1
        ordinal = occurrences[base_key]
        trade_key = hashlib.sha256(
            f"{base_key}:{ordinal}".encode("utf-8")
        ).hexdigest()

        prepared.append({
            "trade_date": trade_at.date().isoformat(),
            "ticker": ticker,
            "minimum_lot": minimum_lot,
            "trade_key": trade_key,
            "trade_time": trade_at.isoformat(timespec="seconds"),
            "price": price,
            "action": action,
            "lots": lots,
            "trade_value": trade_value,
        })
    return prepared


class MarketArchive:
    """Narrow PostgreSQL read/write contract shared by Bot 4 and Bot 1/2."""

    def __init__(self, database_url: str | None = None, service: str = "market-archive"):
        self.url = database_url if database_url is not None else configured_database_url()
        self.service = service

    @property
    def ready(self) -> bool:
        return bool(self.url and psycopg is not None and Jsonb is not None)

    def _connect(self):
        if not self.ready:
            raise RuntimeError("Neon MarketArchive requires PROJECT_FABRIC_DATABASE_URL and psycopg")
        return psycopg.connect(self.url, connect_timeout=6, autocommit=True)

    def migrate(self) -> None:
        with self._connect() as cn:
            with cn.cursor() as c:
                c.execute("""CREATE TABLE IF NOT EXISTS project_fabric.stock_summary_daily (
                    trade_date DATE NOT NULL, ticker TEXT NOT NULL,
                    open_price NUMERIC, high_price NUMERIC, low_price NUMERIC,
                    close_price NUMERIC, volume_shares NUMERIC, turnover_idr NUMERIC,
                    frequency BIGINT, foreign_buy_value_idr NUMERIC,
                    foreign_sell_value_idr NUMERIC, raw_json JSONB NOT NULL,
                    source_service TEXT NOT NULL,
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY(trade_date,ticker))""")
                c.execute("""CREATE TABLE IF NOT EXISTS project_fabric.stock_history_daily (
                    trade_date DATE NOT NULL, ticker TEXT NOT NULL,
                    open_price NUMERIC NOT NULL, high_price NUMERIC NOT NULL,
                    low_price NUMERIC NOT NULL, close_price NUMERIC NOT NULL,
                    volume_shares NUMERIC NOT NULL, turnover_idr NUMERIC,
                    frequency BIGINT, foreign_buy_value_idr NUMERIC,
                    foreign_sell_value_idr NUMERIC, raw_json JSONB NOT NULL,
                    source_service TEXT NOT NULL,
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY(trade_date,ticker))""")
                c.execute("""CREATE TABLE IF NOT EXISTS project_fabric.universe_scores (
                    trade_date DATE NOT NULL, ticker TEXT NOT NULL,
                    schema_version INTEGER NOT NULL, scores_json JSONB NOT NULL,
                    available_factors INTEGER NOT NULL DEFAULT 0,
                    history_last_date DATE,
                    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    source_service TEXT NOT NULL,
                    PRIMARY KEY(trade_date,ticker,schema_version))""")
                c.execute("""CREATE TABLE IF NOT EXISTS project_fabric.volume_by_price_daily (
                    trade_date DATE NOT NULL,
                    ticker TEXT NOT NULL,
                    price NUMERIC NOT NULL CHECK (price > 0),
                    minimum_lot INTEGER NOT NULL CHECK (minimum_lot >= 1),
                    buy_lots NUMERIC NOT NULL DEFAULT 0 CHECK (buy_lots >= 0),
                    sell_lots NUMERIC NOT NULL DEFAULT 0 CHECK (sell_lots >= 0),
                    unknown_lots NUMERIC NOT NULL DEFAULT 0 CHECK (unknown_lots >= 0),
                    buy_value_idr NUMERIC NOT NULL DEFAULT 0 CHECK (buy_value_idr >= 0),
                    sell_value_idr NUMERIC NOT NULL DEFAULT 0 CHECK (sell_value_idr >= 0),
                    unknown_value_idr NUMERIC NOT NULL DEFAULT 0 CHECK (unknown_value_idr >= 0),
                    trade_count BIGINT NOT NULL DEFAULT 0 CHECK (trade_count >= 0),
                    first_trade_time TIMESTAMPTZ NOT NULL,
                    last_trade_time TIMESTAMPTZ NOT NULL,
                    source_service TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY(trade_date,ticker,price,minimum_lot))""")
                c.execute("""CREATE INDEX IF NOT EXISTS idx_volume_by_price_ticker_day
                    ON project_fabric.volume_by_price_daily
                    (ticker,trade_date DESC,minimum_lot,price)""")
                c.execute("""CREATE TABLE IF NOT EXISTS project_fabric.volume_by_price_seen_trades (
                    trade_date DATE NOT NULL,
                    ticker TEXT NOT NULL,
                    minimum_lot INTEGER NOT NULL CHECK (minimum_lot >= 1),
                    trade_key TEXT NOT NULL,
                    trade_time TIMESTAMPTZ NOT NULL,
                    price NUMERIC NOT NULL CHECK (price > 0),
                    action TEXT NOT NULL CHECK (action IN ('BUY','SELL','UNKNOWN')),
                    lots NUMERIC NOT NULL CHECK (lots >= 0),
                    trade_value_idr NUMERIC NOT NULL CHECK (trade_value_idr >= 0),
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY(trade_date,ticker,minimum_lot,trade_key))""")
                c.execute("""CREATE INDEX IF NOT EXISTS idx_volume_by_price_seen_captured
                    ON project_fabric.volume_by_price_seen_trades (captured_at)""")

    def save_volume_by_price(
        self,
        ticker: str,
        payload: Any,
        *,
        minimum_lot: int,
        asof: datetime | None = None,
        aggregate_retention_days: int = 120,
        fingerprint_retention_days: int = 2,
    ) -> dict[str, int]:
        """Aggregate an already-fetched running-trades payload without extra API calls."""
        now = (asof or datetime.now(WIB)).astimezone(WIB)
        prepared = prepare_volume_by_price_trades(
            ticker,
            payload,
            minimum_lot=minimum_lot,
            asof=now,
        )
        result = {
            "received_trades": len(_items(payload)),
            "prepared_trades": len(prepared),
            "new_trades": 0,
            "updated_price_levels": 0,
        }
        if not prepared:
            return result

        with self._connect() as cn:
            with cn.cursor() as c:
                with cn.transaction():
                    c.execute(
                        """DELETE FROM project_fabric.volume_by_price_seen_trades
                           WHERE trade_date < %s""",
                        (now.date() - timedelta(days=max(1, fingerprint_retention_days)),),
                    )
                    c.execute(
                        """DELETE FROM project_fabric.volume_by_price_daily
                           WHERE trade_date < %s""",
                        (now.date() - timedelta(days=max(1, aggregate_retention_days)),),
                    )
                    c.execute(
                        """WITH incoming AS (
                            SELECT
                                x.trade_date::date AS trade_date,
                                x.ticker,
                                x.minimum_lot,
                                x.trade_key,
                                x.trade_time::timestamptz AS trade_time,
                                x.price::numeric AS price,
                                x.action,
                                x.lots::numeric AS lots,
                                x.trade_value::numeric AS trade_value_idr
                            FROM jsonb_to_recordset(%s::jsonb) AS x(
                                trade_date text, ticker text, minimum_lot integer,
                                trade_key text, trade_time text, price double precision,
                                action text, lots double precision,
                                trade_value double precision)
                        ), inserted AS (
                            INSERT INTO project_fabric.volume_by_price_seen_trades
                                (trade_date,ticker,minimum_lot,trade_key,trade_time,
                                 price,action,lots,trade_value_idr)
                            SELECT trade_date,ticker,minimum_lot,trade_key,trade_time,
                                   price,action,lots,trade_value_idr
                            FROM incoming
                            ON CONFLICT DO NOTHING
                            RETURNING trade_date,ticker,minimum_lot,trade_time,price,
                                      action,lots,trade_value_idr
                        ), grouped AS (
                            SELECT trade_date,ticker,price,minimum_lot,
                                COALESCE(sum(lots) FILTER (WHERE action='BUY'),0) AS buy_lots,
                                COALESCE(sum(lots) FILTER (WHERE action='SELL'),0) AS sell_lots,
                                COALESCE(sum(lots) FILTER (WHERE action='UNKNOWN'),0) AS unknown_lots,
                                COALESCE(sum(trade_value_idr) FILTER (WHERE action='BUY'),0) AS buy_value,
                                COALESCE(sum(trade_value_idr) FILTER (WHERE action='SELL'),0) AS sell_value,
                                COALESCE(sum(trade_value_idr) FILTER (WHERE action='UNKNOWN'),0) AS unknown_value,
                                count(*) AS trade_count,
                                min(trade_time) AS first_trade_time,
                                max(trade_time) AS last_trade_time
                            FROM inserted
                            GROUP BY trade_date,ticker,price,minimum_lot
                        ), upserted AS (
                            INSERT INTO project_fabric.volume_by_price_daily
                                (trade_date,ticker,price,minimum_lot,buy_lots,sell_lots,
                                 unknown_lots,buy_value_idr,sell_value_idr,
                                 unknown_value_idr,trade_count,first_trade_time,
                                 last_trade_time,source_service)
                            SELECT trade_date,ticker,price,minimum_lot,buy_lots,sell_lots,
                                   unknown_lots,buy_value,sell_value,unknown_value,
                                   trade_count,first_trade_time,last_trade_time,%s
                            FROM grouped
                            ON CONFLICT(trade_date,ticker,price,minimum_lot) DO UPDATE SET
                                buy_lots=project_fabric.volume_by_price_daily.buy_lots+EXCLUDED.buy_lots,
                                sell_lots=project_fabric.volume_by_price_daily.sell_lots+EXCLUDED.sell_lots,
                                unknown_lots=project_fabric.volume_by_price_daily.unknown_lots+EXCLUDED.unknown_lots,
                                buy_value_idr=project_fabric.volume_by_price_daily.buy_value_idr+EXCLUDED.buy_value_idr,
                                sell_value_idr=project_fabric.volume_by_price_daily.sell_value_idr+EXCLUDED.sell_value_idr,
                                unknown_value_idr=project_fabric.volume_by_price_daily.unknown_value_idr+EXCLUDED.unknown_value_idr,
                                trade_count=project_fabric.volume_by_price_daily.trade_count+EXCLUDED.trade_count,
                                first_trade_time=LEAST(project_fabric.volume_by_price_daily.first_trade_time,EXCLUDED.first_trade_time),
                                last_trade_time=GREATEST(project_fabric.volume_by_price_daily.last_trade_time,EXCLUDED.last_trade_time),
                                source_service=EXCLUDED.source_service,
                                updated_at=now()
                            RETURNING 1
                        )
                        SELECT (SELECT count(*) FROM inserted),
                               (SELECT count(*) FROM upserted)""",
                        (Jsonb(prepared), self.service),
                    )
                    counts = c.fetchone() or (0, 0)
        result["new_trades"] = int(counts[0])
        result["updated_price_levels"] = int(counts[1])
        return result

    def volume_by_price_rows(
        self,
        ticker: str,
        *,
        trade_date: date,
        minimum_lot: int,
        low_price: float | None = None,
        high_price: float | None = None,
    ) -> list[dict[str, Any]]:
        ticker = ticker.strip().upper()
        if not _CODE.fullmatch(ticker):
            raise ValueError("Invalid IDX ticker")
        low = 0.0 if low_price is None else float(low_price)
        high = 1e25 if high_price is None else float(high_price)
        if low < 0 or high < low:
            raise ValueError("Invalid price range")
        with self._connect() as cn:
            with cn.cursor() as c:
                c.execute(
                    """SELECT price,buy_lots,sell_lots,unknown_lots,
                              buy_value_idr,sell_value_idr,unknown_value_idr,
                              trade_count,first_trade_time,last_trade_time,updated_at
                       FROM project_fabric.volume_by_price_daily
                       WHERE trade_date=%s AND ticker=%s AND minimum_lot=%s
                         AND price BETWEEN %s AND %s
                       ORDER BY price""",
                    (trade_date, ticker, max(1, int(minimum_lot)), low, high),
                )
                rows = c.fetchall()
        keys = (
            "price", "buy_lots", "sell_lots", "unknown_lots",
            "buy_value_idr", "sell_value_idr", "unknown_value_idr",
            "trade_count", "first_trade_time", "last_trade_time", "updated_at",
        )
        return [dict(zip(keys, row)) for row in rows]

    def save_summary(self, payload: Any, *, completed_through: str | None = None,
                     asof: datetime | None = None) -> int:
        """Only archive completed dates. During today's session keep live in cache."""
        now = (asof or datetime.now(WIB)).astimezone(WIB)
        today = now.date()
        eod = date.fromisoformat(completed_through) if completed_through else None
        if eod and (eod > today or (eod == today and (now.hour, now.minute) < (17, 0))):
            raise ValueError("Completed EOD session is not available before 17:00 WIB")
        prepared = []
        for row in _items(payload):
            ticker = str(row.get("StockCode") or row.get("stockCode") or row.get("ticker") or row.get("code") or "").strip().upper()
            stamp = _session_date(row, history=False)
            if not _CODE.fullmatch(ticker) or not stamp:
                if eod:
                    raise ValueError("Invalid ticker/date in completed EOD snapshot")
                continue
            if eod and stamp != eod:
                raise ValueError("Source date does not match completed EOD session")
            if not eod and stamp >= today:
                continue
            prepared.append((stamp, ticker,
                _num(row, "OpenPrice", "Open", "open"),
                _num(row, "High", "HighPrice", "high"),
                _num(row, "Low", "LowPrice", "low"),
                _num(row, "Close", "close"),
                _num(row, "Volume", "volume"),
                _num(row, "Value", "value"),
                _num(row, "Frequency", "frequency"),
                _num(row, "foreignBuyValue", "ForeignBuyValue"),
                _num(row, "foreignSellValue", "ForeignSellValue"),
                Jsonb(row), self.service))
        if not prepared:
            return 0
        # Cheap daily idempotency, avoiding 900 duplicate inserts every 5 min.
        with self._connect() as cn:
            with cn.cursor() as c:
                # Do not skip partially populated sessions.
                latest_day = max(x[0] for x in prepared)
                c.execute("SELECT count(*) FROM project_fabric.stock_summary_daily WHERE trade_date=%s",
                          (latest_day,))
                if not eod and int(c.fetchone()[0]) >= len({x[1] for x in prepared if x[0] == latest_day}):
                    return 0
                conflict = "DO NOTHING"
                if eod:
                    # Only explicit validated post-close EOD can replace a same-day row.
                    conflict = """DO UPDATE SET
                        open_price=EXCLUDED.open_price, high_price=EXCLUDED.high_price,
                        low_price=EXCLUDED.low_price, close_price=EXCLUDED.close_price,
                        volume_shares=EXCLUDED.volume_shares, turnover_idr=EXCLUDED.turnover_idr,
                        frequency=EXCLUDED.frequency,
                        foreign_buy_value_idr=EXCLUDED.foreign_buy_value_idr,
                        foreign_sell_value_idr=EXCLUDED.foreign_sell_value_idr,
                        raw_json=EXCLUDED.raw_json, source_service=EXCLUDED.source_service,
                        captured_at=now()"""
                with cn.transaction():
                    c.executemany("""INSERT INTO project_fabric.stock_summary_daily
                    (trade_date,ticker,open_price,high_price,low_price,close_price,
                     volume_shares,turnover_idr,frequency,foreign_buy_value_idr,
                     foreign_sell_value_idr,raw_json,source_service)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(trade_date,ticker) """ + conflict, prepared)
        return len(prepared)

    def save_history(self, ticker: str, payload: Any) -> int:
        ticker = ticker.strip().upper()
        if not _CODE.fullmatch(ticker):
            raise ValueError("Invalid IDX ticker")
        # _history filters out today's incomplete daily candle and invalid OHLCV.
        rows = _history(payload)
        if not rows:
            return 0
        raw_by_date = {
            str(row.get("date") or row.get("Date") or "")[:10]: row
            for row in _items(payload)
        }
        prepared = []
        for row in rows:
            raw = raw_by_date.get(row["date"], row)
            prepared.append((
                date.fromisoformat(row["date"]),ticker,
                row["open"],row["high"],row["low"],row["close"],row["volume"],
                _num(raw,"value","Value"),_num(raw,"frequency","Frequency"),
                _num(raw,"foreignBuyValue","ForeignBuyValue"),
                _num(raw,"foreignSellValue","ForeignSellValue"),
                Jsonb(raw),self.service,
            ))
        with self._connect() as cn:
            with cn.cursor() as c:
                c.executemany("""INSERT INTO project_fabric.stock_history_daily
                   (trade_date,ticker,open_price,high_price,low_price,close_price,
                    volume_shares,turnover_idr,frequency,foreign_buy_value_idr,
                    foreign_sell_value_idr,raw_json,source_service)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(trade_date,ticker) DO NOTHING""", prepared)
        return len(prepared)

    def summary_row(self, ticker: str) -> tuple[dict | None, date | None]:
        with self._connect() as cn:
            with cn.cursor() as c:
                c.execute("""SELECT raw_json, trade_date FROM project_fabric.stock_summary_daily
                    WHERE ticker=%s ORDER BY trade_date DESC LIMIT 1""",(ticker,))
                row = c.fetchone()
        return (row[0],row[1]) if row else (None,None)

    def history_rows(self, ticker: str, limit: int = 120) -> list[dict]:
        with self._connect() as cn:
            with cn.cursor() as c:
                c.execute("""SELECT raw_json FROM project_fabric.stock_history_daily
                  WHERE ticker=%s AND trade_date < (now() AT TIME ZONE 'Asia/Jakarta')::date
                  ORDER BY trade_date DESC LIMIT %s""",(ticker,min(500,max(1,limit))))
                rows=c.fetchall()
        return [r[0] for r in reversed(rows)]

    def latest_history_date(self, ticker: str) -> date | None:
        with self._connect() as cn:
            with cn.cursor() as c:
                c.execute("SELECT max(trade_date) FROM project_fabric.stock_history_daily WHERE ticker=%s",
                          (ticker,))
                return c.fetchone()[0]

    def compute_score(self, ticker: str) -> dict | None:
        ticker = ticker.strip().upper()
        history = self.history_rows(ticker)
        if len(history) < 30:
            return None
        features = history_features(ticker, {"items": history})
        if features["history_sessions"] < 30:
            return None
        # The history endpoint supplies real foreign BUY/SELL monetary values.
        # Prefer those to any approximate summary shares*close result.
        flow = institutional_flow(history[-1])
        summary, summary_date = self.summary_row(ticker)
        if flow["score"] is None and summary:
            flow = institutional_flow(summary)
        features["institutional_flow"] = flow
        scores = {k:features[k] for k in ("accumulation_persistence",
                  "breakout_readiness","institutional_flow","distribution_risk","entry_quality")}
        available = sum(1 for s in scores.values() if s.get("score") is not None)
        hist_date = date.fromisoformat(features["history_last_date"])
        trade_date = max(filter(None,(hist_date,summary_date)))
        with self._connect() as cn:
            with cn.cursor() as c:
                c.execute("""INSERT INTO project_fabric.universe_scores
                    (trade_date,ticker,schema_version,scores_json,available_factors,
                     history_last_date,source_service)
                    VALUES (%s,%s,1,%s,%s,%s,%s)
                    ON CONFLICT(trade_date,ticker,schema_version) DO UPDATE SET
                       scores_json=EXCLUDED.scores_json,
                       available_factors=EXCLUDED.available_factors,
                       history_last_date=EXCLUDED.history_last_date,
                       computed_at=now(),source_service=EXCLUDED.source_service""",
                    (trade_date,ticker,Jsonb(scores),available,hist_date,self.service))
        return {"ticker":ticker,"scores":scores,"history_last_date":str(hist_date),
                "market_date":str(trade_date),"available_factors":available}

    def latest_score(self, ticker: str) -> dict | None:
        ticker = ticker.strip().upper()
        with self._connect() as cn:
            with cn.cursor() as c:
                c.execute("""SELECT scores_json,history_last_date,trade_date,computed_at
                  FROM project_fabric.universe_scores WHERE ticker=%s AND schema_version=1
                  ORDER BY trade_date DESC LIMIT 1""",(ticker,))
                row=c.fetchone()
        if not row:
            return None
        scores,hist_date,market_date,computed_at=row
        return {"ticker":ticker,"scores":scores,
                "history_last_date":hist_date.isoformat() if hist_date else None,
                "summary_observed_at": market_date.isoformat(),
                "market_date":market_date.isoformat(),
                "computed_at":computed_at.isoformat(),
                "source":"neon-market-warehouse","archived":True,
                "available":any(isinstance(v,dict) and v.get("score") is not None
                                for v in scores.values())}

    def ticker_count(self) -> int:
        with self._connect() as cn:
            with cn.cursor() as c:
                c.execute("SELECT count(distinct ticker) FROM project_fabric.stock_history_daily")
                return int(c.fetchone()[0])

    def rebuild_available(self, maximum_tickers: int = 250) -> dict:
        with self._connect() as cn:
            with cn.cursor() as c:
                c.execute("""SELECT ticker FROM project_fabric.stock_history_daily
                    GROUP BY ticker HAVING count(*) >= 30
                    ORDER BY ticker LIMIT %s""",(maximum_tickers,))
                tickers=[r[0] for r in c.fetchall()]
        success=0
        failed=[]
        for ticker in tickers:
            try:
                if self.compute_score(ticker):
                    success+=1
            except Exception:
                failed.append(ticker)
        return {"eligible_tickers":len(tickers),"saved_scores":success,
                "failed_tickers":failed}

