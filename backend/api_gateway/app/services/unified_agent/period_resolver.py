"""Period phrase resolver for clarification slot filling (ADR P4 v1.3).

Minimal resolver — recognizes common Indonesian period phrases and returns
a normalized period descriptor dict. NOT the full period-extraction pipeline
used by entity_extractor; this is specifically for slot-fill detection.

Returns None if no period phrase is present.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional, Dict, Any


_MONTHS_ID = {
    "januari": 1,
    "februari": 2,
    "maret": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "agustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "desember": 12,
}


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def resolve_period(text: str) -> Optional[Dict[str, Any]]:
    """Return {kind, start_date, end_date, label} or None if no period matched."""
    if not text:
        return None
    s = text.lower()
    today = date.today()

    if re.search(r"\bbulan\s+ini\b", s):
        start, end = _month_bounds(today.year, today.month)
        return {
            "kind": "this_month",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "label": "bulan ini",
        }
    if re.search(r"\bbulan\s+(?:lalu|kemarin)\b", s):
        y, m = (
            (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        )
        start, end = _month_bounds(y, m)
        return {
            "kind": "last_month",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "label": "bulan lalu",
        }
    if re.search(r"\bminggu\s+ini\b", s):
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return {
            "kind": "this_week",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "label": "minggu ini",
        }
    if re.search(r"\bminggu\s+(?:lalu|kemarin)\b", s):
        this_start = today - timedelta(days=today.weekday())
        start = this_start - timedelta(days=7)
        end = this_start - timedelta(days=1)
        return {
            "kind": "last_week",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "label": "minggu lalu",
        }
    if re.search(r"\bhari\s+ini\b", s):
        return {
            "kind": "today",
            "start_date": today.isoformat(),
            "end_date": today.isoformat(),
            "label": "hari ini",
        }
    if re.search(r"\bkemarin\b", s):
        y = today - timedelta(days=1)
        return {
            "kind": "yesterday",
            "start_date": y.isoformat(),
            "end_date": y.isoformat(),
            "label": "kemarin",
        }
    m = re.search(r"\b(\d+)\s+hari\s+(?:terakhir|lalu)\b", s)
    if m:
        days = int(m.group(1))
        start = today - timedelta(days=days)
        return {
            "kind": "last_n_days",
            "start_date": start.isoformat(),
            "end_date": today.isoformat(),
            "label": f"{days} hari terakhir",
        }
    m = re.search(r"\bq([1-4])\b", s)
    if m:
        q = int(m.group(1))
        sm, em = (q - 1) * 3 + 1, q * 3
        start, _ = _month_bounds(today.year, sm)
        _, end = _month_bounds(today.year, em)
        return {
            "kind": "quarter",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "label": f"Q{q} {today.year}",
        }
    for name, num in _MONTHS_ID.items():
        mm = re.search(rf"\b{name}(?:\s+(\d{{4}}))?\b", s)
        if mm:
            year = int(mm.group(1)) if mm.group(1) else today.year
            start, end = _month_bounds(year, num)
            return {
                "kind": "named_month",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "label": f"{name} {year}",
            }
    if re.search(r"\btahun\s+ini\b", s):
        return {
            "kind": "this_year",
            "start_date": date(today.year, 1, 1).isoformat(),
            "end_date": date(today.year, 12, 31).isoformat(),
            "label": "tahun ini",
        }
    if re.search(r"\btahun\s+(?:lalu|kemarin)\b", s):
        y = today.year - 1
        return {
            "kind": "last_year",
            "start_date": date(y, 1, 1).isoformat(),
            "end_date": date(y, 12, 31).isoformat(),
            "label": "tahun lalu",
        }

    return None
