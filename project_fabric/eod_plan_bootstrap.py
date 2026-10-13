from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .eod_finalizer import (
    EOD_STATUS_ENDPOINT,
    EOD_STATUS_PARAMS,
    finalize_eod,
)
from .postgres_backend import PostgresTTLCache, configured_database_url
from .universe_intelligence import _cache_key


WIB = timezone(timedelta(hours=7))


def main() -> None:
    database_url = configured_database_url()
    if not database_url:
        raise RuntimeError("PROJECT_FABRIC_DATABASE_URL belum dikonfigurasi.")

    cache = PostgresTTLCache(
        database_url,
        namespace="shared-v1",
        service="cuankus-momentum-eod-plan-bootstrap",
        auto_migrate=False,
    )
    status = cache.get(
        _cache_key(EOD_STATUS_ENDPOINT, EOD_STATUS_PARAMS),
        7 * 24 * 3600,
    )
    if not isinstance(status, dict) or not status.get("trade_date"):
        raise RuntimeError("Belum ada EOD Finalizer status yang bisa dibootstrap.")

    trade_date = str(status["trade_date"])[:10]
    asof = datetime.fromisoformat(f"{trade_date}T17:00:00+07:00")
    result = finalize_eod(asof=asof, force=True)
    print(
        "[EOD-PLAN-BOOTSTRAP] "
        f"trade_date={result.get('trade_date')} "
        f"finalized={result.get('finalized', 0)} "
        f"plans={result.get('entry_plan_count', 0)} "
        f"failed={result.get('failed', 0)} "
        f"zapi_calls={result.get('zapi_calls', 0)}"
    )
    if result.get("entry_plan_count", 0) <= 0:
        raise SystemExit("EOD entry plan bootstrap menghasilkan 0 plan.")


if __name__ == "__main__":
    main()
