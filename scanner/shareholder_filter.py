from __future__ import annotations

from io import BytesIO
import re
from typing import Any

import requests
from pypdf import PdfReader

from scanner.market_data import fetch_company_announcements


LBRE_KEYWORDS = (
    "laporan bulanan registrasi pemegang efek",
    "monthly report of securities holders registration",
)


def _unwrap(payload: Any) -> Any:
    current = payload
    for _ in range(3):
        if (
            isinstance(current, dict)
            and "project" in current
            and isinstance(current.get("data"), (dict, list))
        ):
            current = current["data"]
            continue
        break
    return current


def _rows(payload: Any) -> list[dict[str, Any]]:
    payload = _unwrap(payload)
    if isinstance(payload, dict):
        data = payload.get("data", [])
    elif isinstance(payload, list):
        data = payload
    else:
        data = []
    return [x for x in data if isinstance(x, dict)]


def _is_lbre(row: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("title", "subject")
    ).lower()
    return any(keyword in haystack for keyword in LBRE_KEYWORDS)


def _parse_count_token(value: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", value or "")
    if not digits:
        return None
    try:
        count = int(digits)
    except ValueError:
        return None
    return count if count >= 0 else None


def extract_shareholder_count(text: str) -> int | None:
    """Extract current-month shareholder count from an IDX LBRE document."""
    if not text:
        return None

    normalized = (
        text.replace("\u00a0", " ")
        .replace("\r", " ")
        .replace("\n", " ")
    )
    normalized = re.sub(r"\s+", " ", normalized)

    # Common Indonesian form:
    # Jumlah Pemegang Saham ... Bulan Sebelumnya 4.818
    # Bulan Sekarang 4.984 ...
    patterns = (
        r"jumlah\s+pemegang\s+saham.{0,700}?"
        r"bulan\s+sebelumnya\s*[:\-]?\s*([0-9][0-9., ]*)"
        r".{0,300}?bulan\s+(?:sekarang|ini)\s*[:\-]?\s*([0-9][0-9., ]*)",
        r"number\s+of\s+shareholders.{0,700}?"
        r"previous\s+month\s*[:\-]?\s*([0-9][0-9., ]*)"
        r".{0,300}?(?:current|this)\s+month\s*[:\-]?\s*([0-9][0-9., ]*)",
    )

    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            current = _parse_count_token(match.group(2))
            if current is not None:
                return current

    # Some generated IDX PDFs render the table headers first, followed by
    # the three numbers without colons.
    table_patterns = (
        r"jumlah\s+pemegang\s+saham.{0,500}?"
        r"bulan\s+sebelumnya.{0,120}?bulan\s+sekarang.{0,120}?perubahan"
        r".{0,300}?([0-9][0-9.,]*)\s+([0-9][0-9.,]*)\s+[+\-]?[0-9][0-9.,]*",
        r"number\s+of\s+shareholders.{0,500}?"
        r"previous\s+month.{0,120}?(?:current|this)\s+month.{0,120}?change"
        r".{0,300}?([0-9][0-9.,]*)\s+([0-9][0-9.,]*)\s+[+\-]?[0-9][0-9.,]*",
    )

    for pattern in table_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            current = _parse_count_token(match.group(2))
            if current is not None:
                return current

    return None


def _download_pdf_text(url: str, timeout: int = 30) -> str:
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; CuankusMomentum/1.0; "
                "+https://www.idx.co.id/)"
            )
        },
    )
    response.raise_for_status()

    reader = PdfReader(BytesIO(response.content))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)


def fetch_latest_shareholder_count(
    ticker: str,
    announcement_length: int = 50,
) -> dict[str, Any]:
    """Return latest monthly shareholder count from official IDX disclosure PDFs.

    Metadata is obtained through the existing ZAPI IDX company-announcements
    endpoint. The attachment itself is downloaded from the official idx.co.id
    URL returned by IDX/ZAPI.
    """
    payload = fetch_company_announcements(
        ticker,
        length=announcement_length,
    )

    reports = [row for row in _rows(payload) if _is_lbre(row)]
    reports.sort(
        key=lambda row: str(
            row.get("publishedAt")
            or row.get("createdAt")
            or ""
        ),
        reverse=True,
    )

    if not reports:
        return {
            "holder_count": None,
            "report_published_at": None,
            "announcement_id": None,
            "source_url": None,
            "status": "NO_LBRE",
        }

    # Try a few latest reports in case the most recent disclosure has a
    # malformed attachment while the immediately previous one is readable.
    for report in reports[:3]:
        attachments = report.get("attachments") or []
        if not isinstance(attachments, list):
            attachments = []

        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            url = str(attachment.get("url") or "").strip()
            if not url.lower().endswith(".pdf"):
                continue

            try:
                text = _download_pdf_text(url)
                holder_count = extract_shareholder_count(text)
            except Exception as exc:
                print(
                    f"[WARN] shareholder PDF {ticker} gagal "
                    f"({type(exc).__name__}): {exc}"
                )
                continue

            if holder_count is not None:
                return {
                    "holder_count": holder_count,
                    "report_published_at": (
                        report.get("publishedAt")
                        or report.get("createdAt")
                    ),
                    "announcement_id": report.get("id"),
                    "source_url": url,
                    "status": "OK",
                }

    newest = reports[0]
    first_attachment = None
    attachments = newest.get("attachments") or []
    if isinstance(attachments, list):
        for attachment in attachments:
            if isinstance(attachment, dict) and attachment.get("url"):
                first_attachment = str(attachment["url"])
                break

    return {
        "holder_count": None,
        "report_published_at": (
            newest.get("publishedAt")
            or newest.get("createdAt")
        ),
        "announcement_id": newest.get("id"),
        "source_url": first_attachment,
        "status": "PARSE_FAILED",
    }
