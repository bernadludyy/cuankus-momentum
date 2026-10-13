"""Durable fundamental warehouse for the full IDX universe.

The archive deliberately keeps both normalized decision fields and the original
provider payloads.  The normalized columns make screeners cheap; the JSONB
payloads let us add new ratios later without spending another ZAPI call.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from .postgres_backend import configured_database_url

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover
    psycopg = None
    Jsonb = None


_CODE = re.compile(r"^[A-Z0-9]{1,15}$")


def parse_number(value: Any) -> float | None:
    """Parse provider values such as Rp127.23T, 13.61x and 20.44%."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number else None
    text = str(value).strip().upper()
    if not text or text in {"-", "N/A", "NONE", "NULL"}:
        return None
    text = (
        text.replace("RP", "")
        .replace("IDR", "")
        .replace("%", "")
        .replace("X", "")
        .replace(",", "")
        .strip()
    )
    match = re.fullmatch(r"([-+]?[0-9]+(?:\.[0-9]+)?)\s*([KMBT]?)", text)
    if not match:
        return None
    multiplier = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
    return float(match.group(1)) * multiplier[match.group(2)]


def unwrap_zapi(payload: Any) -> dict[str, Any]:
    current = payload
    for _ in range(4):
        if isinstance(current, dict) and "project" in current and isinstance(current.get("data"), dict):
            current = current["data"]
            continue
        break
    return current if isinstance(current, dict) else {}


def normalized_fundamental_fields(payload: Any) -> dict[str, Any]:
    data = unwrap_zapi(payload)
    ratios = data.get("ratios") if isinstance(data.get("ratios"), dict) else {}
    overview = data.get("overview") if isinstance(data.get("overview"), dict) else {}
    valuation = ratios.get("valuation") if isinstance(ratios.get("valuation"), dict) else {}
    profitability = ratios.get("profitability") if isinstance(ratios.get("profitability"), dict) else {}
    solvency = ratios.get("solvency") if isinstance(ratios.get("solvency"), dict) else {}
    dividend = ratios.get("dividend") if isinstance(ratios.get("dividend"), dict) else {}
    return {
        "stock_id": parse_number(data.get("stockId")),
        "provider_updated_at": data.get("lastUpdated"),
        "eps": parse_number(overview.get("eps")),
        "bvps": parse_number(overview.get("bvps")),
        "revenue": parse_number(overview.get("revenue")),
        "net_income": parse_number(overview.get("net_income")),
        "gross_profit": parse_number(overview.get("gross_profit")),
        "pe": parse_number(valuation.get("pe")),
        "pb": parse_number(valuation.get("pb")),
        "ps": parse_number(valuation.get("ps")),
        "cps": parse_number(valuation.get("cps")),
        "rps": parse_number(valuation.get("rps")),
        "cfps": parse_number(valuation.get("cfps")),
        "dividend_yield_pct": parse_number(dividend.get("ttm")),
        "payout_ratio_pct": parse_number(dividend.get("payoutRatio")),
        "dividend_per_share": parse_number(dividend.get("dividendPerShare")),
        "current_ratio": parse_number(solvency.get("cr")),
        "debt_equity_pct": parse_number(solvency.get("de")),
        "gross_margin_pct": parse_number(profitability.get("gpm")),
        "net_margin_pct": parse_number(profitability.get("npm")),
        "operating_margin_pct": parse_number(profitability.get("opm")),
        "roa_pct": parse_number(profitability.get("roa")),
        "roe_pct": parse_number(profitability.get("roe")),
        "earnings": data.get("earnings") if isinstance(data.get("earnings"), list) else [],
    }


def statement_period_rows(payload: Any) -> list[dict[str, Any]]:
    """Flatten Pluang chart blocks into one row per ticker period label."""
    data = unwrap_zapi(payload)
    rows: list[dict[str, Any]] = []
    for period_kind, aliases in (("quarterly", ("quarterly",)), ("annually", ("annually", "annual"))):
        block: dict[str, Any] = {}
        for alias in aliases:
            if isinstance(data.get(alias), dict):
                block = data[alias]
                break
        if not block:
            continue
        merged: dict[str, dict[str, Any]] = {}
        mappings = (
            ("incomeStatement", {"revenue": "revenue", "netProfitLoss": "net_profit_loss", "profitMargin": "profit_margin"}),
            ("balanceSheet", {"assets": "assets", "liabilities": "liabilities", "debtToAsset": "debt_to_asset"}),
            ("cashFlow", {"operating": "operating_cash_flow", "investing": "investing_cash_flow", "finance": "financing_cash_flow", "netCF": "net_cash_flow"}),
        )
        for section_name, key_map in mappings:
            section = block.get(section_name)
            chart = section.get("chart") if isinstance(section, dict) else None
            if not isinstance(chart, list):
                continue
            for point in chart:
                if not isinstance(point, dict):
                    continue
                timeframe = str(point.get("timeframe") or "").replace("\n", " ").strip()
                if not timeframe:
                    continue
                target = merged.setdefault(timeframe, {"period_kind": period_kind, "timeframe": timeframe})
                for source_key, dest_key in key_map.items():
                    if source_key in point:
                        target[dest_key] = parse_number(point.get(source_key))
                target.setdefault("raw", {})[section_name] = point
        rows.extend(merged.values())
    return rows


@dataclass(frozen=True)
class UniverseTicker:
    ticker: str
    company_name: str | None
    close_price: float | None
    listed_shares: float | None
    market_cap_idr: float | None


class FundamentalArchive:
    def __init__(self, database_url: str | None = None, service: str = "fundamental-backfill"):
        self.url = database_url if database_url is not None else configured_database_url()
        self.service = service

    @property
    def ready(self) -> bool:
        return bool(self.url and psycopg is not None and Jsonb is not None)

    def _connect(self):
        if not self.ready:
            raise RuntimeError("FundamentalArchive requires PROJECT_FABRIC_DATABASE_URL and psycopg")
        return psycopg.connect(self.url, connect_timeout=10, autocommit=True)

    def migrate(self) -> None:
        statements = (
            "CREATE SCHEMA IF NOT EXISTS project_fabric",
            """CREATE TABLE IF NOT EXISTS project_fabric.fundamental_latest (
                ticker TEXT PRIMARY KEY,
                company_name TEXT, sector TEXT, stock_id BIGINT,
                close_price NUMERIC, listed_shares NUMERIC, market_cap_idr NUMERIC,
                provider_updated_at TEXT,
                eps NUMERIC, bvps NUMERIC, revenue NUMERIC, net_income NUMERIC, gross_profit NUMERIC,
                pe NUMERIC, pb NUMERIC, ps NUMERIC, cps NUMERIC, rps NUMERIC, cfps NUMERIC,
                dividend_yield_pct NUMERIC, payout_ratio_pct NUMERIC, dividend_per_share NUMERIC,
                current_ratio NUMERIC, debt_equity_pct NUMERIC,
                gross_margin_pct NUMERIC, net_margin_pct NUMERIC, operating_margin_pct NUMERIC,
                roa_pct NUMERIC, roe_pct NUMERIC,
                earnings_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                fundamentals_json JSONB, financials_json JSONB, profile_json JSONB,
                endpoint_status JSONB NOT NULL DEFAULT '{}'::jsonb,
                errors_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                source_service TEXT NOT NULL,
                captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )""",
            """CREATE INDEX IF NOT EXISTS idx_fundamental_latest_quality
                ON project_fabric.fundamental_latest (roe_pct DESC, pe ASC)""",
            """CREATE INDEX IF NOT EXISTS idx_fundamental_latest_updated
                ON project_fabric.fundamental_latest (updated_at DESC)""",
            """CREATE TABLE IF NOT EXISTS project_fabric.financial_statement_periods (
                ticker TEXT NOT NULL, period_kind TEXT NOT NULL, timeframe TEXT NOT NULL,
                revenue NUMERIC, net_profit_loss NUMERIC, profit_margin NUMERIC,
                assets NUMERIC, liabilities NUMERIC, debt_to_asset NUMERIC,
                operating_cash_flow NUMERIC, investing_cash_flow NUMERIC,
                financing_cash_flow NUMERIC, net_cash_flow NUMERIC,
                raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                source_service TEXT NOT NULL,
                captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (ticker, period_kind, timeframe)
            )""",
            """CREATE TABLE IF NOT EXISTS project_fabric.fundamental_backfill_runs (
                run_id TEXT PRIMARY KEY, started_at TIMESTAMPTZ NOT NULL,
                finished_at TIMESTAMPTZ, status TEXT NOT NULL,
                requested_tickers INTEGER NOT NULL DEFAULT 0,
                completed_tickers INTEGER NOT NULL DEFAULT 0,
                complete_tickers INTEGER NOT NULL DEFAULT 0,
                partial_tickers INTEGER NOT NULL DEFAULT 0,
                failed_tickers INTEGER NOT NULL DEFAULT 0,
                api_calls INTEGER NOT NULL DEFAULT 0,
                summary_json JSONB NOT NULL DEFAULT '{}'::jsonb
            )""",
        )
        with self._connect() as cn, cn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)

    def universe(self, stale_days: int = 90, force: bool = False) -> list[UniverseTicker]:
        predicate = "TRUE" if force else "(f.ticker IS NULL OR f.updated_at < now() - (%s * interval '1 day') OR COALESCE(f.endpoint_status->>'fundamentals','') <> 'OK' OR COALESCE(f.endpoint_status->>'financials','') <> 'OK' OR COALESCE(f.endpoint_status->>'company-profile','') <> 'OK')"
        params: tuple[Any, ...] = () if force else (max(1, int(stale_days)),)
        sql = f"""WITH latest AS (
                    SELECT max(trade_date) AS trade_date FROM project_fabric.stock_summary_daily
                ), universe AS (
                    SELECT s.ticker,
                           NULLIF(s.raw_json->>'StockName','') AS company_name,
                           s.close_price,
                           NULLIF(s.raw_json->>'ListedShares','')::numeric AS listed_shares
                    FROM project_fabric.stock_summary_daily s
                    JOIN latest l ON l.trade_date=s.trade_date
                )
                SELECT u.ticker, u.company_name, u.close_price, u.listed_shares,
                       CASE WHEN u.close_price > 0 AND u.listed_shares > 0
                            THEN u.close_price*u.listed_shares END AS market_cap_idr
                FROM universe u
                LEFT JOIN project_fabric.fundamental_latest f ON f.ticker=u.ticker
                WHERE {predicate}
                ORDER BY u.ticker"""
        with self._connect() as cn, cn.cursor() as cur:
            cur.execute(sql, params)
            result = cur.fetchall()
        return [UniverseTicker(r[0], r[1], float(r[2]) if r[2] is not None else None,
                               float(r[3]) if r[3] is not None else None,
                               float(r[4]) if r[4] is not None else None) for r in result]

    def upsert_ticker(self, meta: UniverseTicker, payloads: dict[str, Any], errors: dict[str, str]) -> None:
        fundamental = payloads.get("fundamentals")
        profile = unwrap_zapi(payloads.get("company-profile")) if payloads.get("company-profile") else {}
        fields = normalized_fundamental_fields(fundamental) if fundamental else {}
        status = {name: ("OK" if payloads.get(name) is not None else "ERROR")
                  for name in ("fundamentals", "financials", "company-profile")}
        company_name = profile.get("name") or meta.company_name
        sector = profile.get("sector")
        values = (
            meta.ticker, company_name, sector, fields.get("stock_id"),
            meta.close_price, meta.listed_shares, meta.market_cap_idr,
            fields.get("provider_updated_at"), fields.get("eps"), fields.get("bvps"),
            fields.get("revenue"), fields.get("net_income"), fields.get("gross_profit"),
            fields.get("pe"), fields.get("pb"), fields.get("ps"), fields.get("cps"),
            fields.get("rps"), fields.get("cfps"), fields.get("dividend_yield_pct"),
            fields.get("payout_ratio_pct"), fields.get("dividend_per_share"),
            fields.get("current_ratio"), fields.get("debt_equity_pct"),
            fields.get("gross_margin_pct"), fields.get("net_margin_pct"),
            fields.get("operating_margin_pct"), fields.get("roa_pct"), fields.get("roe_pct"),
            Jsonb(fields.get("earnings") or []),
            Jsonb(unwrap_zapi(fundamental)) if fundamental else None,
            Jsonb(unwrap_zapi(payloads["financials"])) if payloads.get("financials") else None,
            Jsonb(profile) if profile else None,
            Jsonb(status), Jsonb(errors), self.service,
        )
        with self._connect() as cn, cn.cursor() as cur:
            cur.execute("""INSERT INTO project_fabric.fundamental_latest (
                ticker,company_name,sector,stock_id,close_price,listed_shares,market_cap_idr,
                provider_updated_at,eps,bvps,revenue,net_income,gross_profit,pe,pb,ps,cps,rps,cfps,
                dividend_yield_pct,payout_ratio_pct,dividend_per_share,current_ratio,debt_equity_pct,
                gross_margin_pct,net_margin_pct,operating_margin_pct,roa_pct,roe_pct,earnings_json,
                fundamentals_json,financials_json,profile_json,endpoint_status,errors_json,source_service)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(ticker) DO UPDATE SET
                  company_name=COALESCE(EXCLUDED.company_name,fundamental_latest.company_name),
                  sector=COALESCE(EXCLUDED.sector,fundamental_latest.sector),
                  stock_id=COALESCE(EXCLUDED.stock_id,fundamental_latest.stock_id),
                  close_price=EXCLUDED.close_price, listed_shares=EXCLUDED.listed_shares,
                  market_cap_idr=EXCLUDED.market_cap_idr,
                  provider_updated_at=COALESCE(EXCLUDED.provider_updated_at,fundamental_latest.provider_updated_at),
                  eps=COALESCE(EXCLUDED.eps,fundamental_latest.eps), bvps=COALESCE(EXCLUDED.bvps,fundamental_latest.bvps),
                  revenue=COALESCE(EXCLUDED.revenue,fundamental_latest.revenue), net_income=COALESCE(EXCLUDED.net_income,fundamental_latest.net_income),
                  gross_profit=COALESCE(EXCLUDED.gross_profit,fundamental_latest.gross_profit),
                  pe=COALESCE(EXCLUDED.pe,fundamental_latest.pe), pb=COALESCE(EXCLUDED.pb,fundamental_latest.pb), ps=COALESCE(EXCLUDED.ps,fundamental_latest.ps),
                  cps=COALESCE(EXCLUDED.cps,fundamental_latest.cps), rps=COALESCE(EXCLUDED.rps,fundamental_latest.rps), cfps=COALESCE(EXCLUDED.cfps,fundamental_latest.cfps),
                  dividend_yield_pct=COALESCE(EXCLUDED.dividend_yield_pct,fundamental_latest.dividend_yield_pct),
                  payout_ratio_pct=COALESCE(EXCLUDED.payout_ratio_pct,fundamental_latest.payout_ratio_pct),
                  dividend_per_share=COALESCE(EXCLUDED.dividend_per_share,fundamental_latest.dividend_per_share),
                  current_ratio=COALESCE(EXCLUDED.current_ratio,fundamental_latest.current_ratio),
                  debt_equity_pct=COALESCE(EXCLUDED.debt_equity_pct,fundamental_latest.debt_equity_pct),
                  gross_margin_pct=COALESCE(EXCLUDED.gross_margin_pct,fundamental_latest.gross_margin_pct),
                  net_margin_pct=COALESCE(EXCLUDED.net_margin_pct,fundamental_latest.net_margin_pct),
                  operating_margin_pct=COALESCE(EXCLUDED.operating_margin_pct,fundamental_latest.operating_margin_pct),
                  roa_pct=COALESCE(EXCLUDED.roa_pct,fundamental_latest.roa_pct), roe_pct=COALESCE(EXCLUDED.roe_pct,fundamental_latest.roe_pct),
                  earnings_json=CASE WHEN EXCLUDED.fundamentals_json IS NULL THEN fundamental_latest.earnings_json ELSE EXCLUDED.earnings_json END,
                  fundamentals_json=COALESCE(EXCLUDED.fundamentals_json,fundamental_latest.fundamentals_json),
                  financials_json=COALESCE(EXCLUDED.financials_json,fundamental_latest.financials_json),
                  profile_json=COALESCE(EXCLUDED.profile_json,fundamental_latest.profile_json),
                  endpoint_status=EXCLUDED.endpoint_status, errors_json=EXCLUDED.errors_json,
                  source_service=EXCLUDED.source_service, captured_at=now(), updated_at=now()""", values)
            financials = payloads.get("financials")
            for row in statement_period_rows(financials) if financials else []:
                cur.execute("""INSERT INTO project_fabric.financial_statement_periods (
                    ticker,period_kind,timeframe,revenue,net_profit_loss,profit_margin,assets,liabilities,
                    debt_to_asset,operating_cash_flow,investing_cash_flow,financing_cash_flow,net_cash_flow,
                    raw_json,source_service)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(ticker,period_kind,timeframe) DO UPDATE SET
                    revenue=EXCLUDED.revenue,net_profit_loss=EXCLUDED.net_profit_loss,
                    profit_margin=EXCLUDED.profit_margin,assets=EXCLUDED.assets,
                    liabilities=EXCLUDED.liabilities,debt_to_asset=EXCLUDED.debt_to_asset,
                    operating_cash_flow=EXCLUDED.operating_cash_flow,
                    investing_cash_flow=EXCLUDED.investing_cash_flow,
                    financing_cash_flow=EXCLUDED.financing_cash_flow,
                    net_cash_flow=EXCLUDED.net_cash_flow,raw_json=EXCLUDED.raw_json,
                    source_service=EXCLUDED.source_service,captured_at=now()""",
                    (meta.ticker,row["period_kind"],row["timeframe"],row.get("revenue"),
                     row.get("net_profit_loss"),row.get("profit_margin"),row.get("assets"),
                     row.get("liabilities"),row.get("debt_to_asset"),row.get("operating_cash_flow"),
                     row.get("investing_cash_flow"),row.get("financing_cash_flow"),
                     row.get("net_cash_flow"),Jsonb(row.get("raw") or {}),self.service))

    def start_run(self, run_id: str, requested: int) -> None:
        with self._connect() as cn, cn.cursor() as cur:
            cur.execute("""INSERT INTO project_fabric.fundamental_backfill_runs
                (run_id,started_at,status,requested_tickers) VALUES (%s,%s,'RUNNING',%s)
                ON CONFLICT(run_id) DO UPDATE SET started_at=EXCLUDED.started_at,status='RUNNING',
                requested_tickers=EXCLUDED.requested_tickers""",
                (run_id, datetime.now(timezone.utc), requested))

    def finish_run(self, run_id: str, *, completed: int, complete: int, partial: int,
                   failed: int, api_calls: int, summary: dict[str, Any], status: str) -> None:
        with self._connect() as cn, cn.cursor() as cur:
            cur.execute("""UPDATE project_fabric.fundamental_backfill_runs SET
                finished_at=now(),status=%s,completed_tickers=%s,complete_tickers=%s,
                partial_tickers=%s,failed_tickers=%s,api_calls=%s,summary_json=%s
                WHERE run_id=%s""",
                (status,completed,complete,partial,failed,api_calls,Jsonb(summary),run_id))

