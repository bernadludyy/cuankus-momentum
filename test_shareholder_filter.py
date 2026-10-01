from __future__ import annotations

from scanner.shareholder_filter import fetch_latest_shareholder_count


def main() -> None:
    tickers = ("AYLS", "BKSL")

    for ticker in tickers:
        metrics = fetch_latest_shareholder_count(
            ticker,
            announcement_length=50,
        )
        holder_count = metrics.get("holder_count")

        print(
            f"{ticker}: holders={holder_count} "
            f"status={metrics.get('status')} "
            f"published={metrics.get('report_published_at')} "
            f"announcement={metrics.get('announcement_id')}"
        )

        if holder_count is None:
            raise RuntimeError(
                f"Shareholder count untuk {ticker} tidak berhasil diparse"
            )

    print("Shareholder filter smoke test passed.")


if __name__ == "__main__":
    main()
