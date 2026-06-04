"""
Projection Engine — Deterministic gross-profit projection.
PROJECTION_ENGINE_MARKER (do not remove — used by deploy verification grep)

Answers what-if / projection questions about gross profit ("laba kotor") by:
  1. Pulling the last 2 COMPLETE calendar months of P&L (laba-rugi) data.
  2. Aggregating revenue + COGS into a journal-derived gross margin.
  3. Parsing the user's scenario percent (naik/turun N%).
  4. Projecting next-month gross profit at the SAME gross-margin ratio.

NO LLM polish — the markdown response is rendered entirely in code so the
numbers cannot be hallucinated (Iron Law 0/3.1/9). All math uses Decimal
(Iron Law 25). Data source is ONLY the journal-derived /api/reports/laba-rugi
endpoint (Iron Law 1/16). READ-ONLY: no journals written, no advisory lock.
"""
import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Optional

import httpx

logger = logging.getLogger("unified_chat")

_BASE_URL = "http://localhost:8000"

# Indonesian month names (1-indexed)
_BULAN_ID = [
    "",
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
]


@dataclass
class _MonthPL:
    periode: str  # "2026-05"
    year: int
    month: int
    revenue: Decimal
    cogs: Decimal
    gross: Decimal


def _q0(val: Decimal) -> Decimal:
    """Quantize to whole rupiah (no fractional cents in display)."""
    return val.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _q2(val: Decimal) -> Decimal:
    """Quantize to 2 decimals (for percent display)."""
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _to_decimal(val) -> Decimal:
    """Safely coerce an API numeric (int/float/str) to Decimal."""
    if val is None:
        return Decimal("0")
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _fmt_rp(val: Decimal) -> str:
    """Format a Decimal as Indonesian Rupiah, thousand sep '.'."""
    n = _q0(val)
    neg = n < 0
    s = "{:,}".format(abs(int(n))).replace(",", ".")
    return ("-Rp " if neg else "Rp ") + s


def _fmt_pct(val: Decimal) -> str:
    """Format a Decimal percent, thousand/decimal Indonesian style."""
    p = _q2(val)
    s = "{:,.2f}".format(p)
    # en (1,234.56) -> id (1.234,56)
    s = s.replace(",", "#").replace(".", ",").replace("#", ".")
    return s + "%"


def _last_two_complete_months(
    today: Optional[date] = None,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """
    Return ((y1, m1), (y2, m2)) = the two COMPLETE calendar months before the
    current month, oldest first. e.g. today=2026-06-04 -> ((2026,4),(2026,5)).
    """
    if today is None:
        today = date.today()
    y, m = today.year, today.month
    # most recent complete month = previous month
    m2 = m - 1
    y2 = y
    if m2 == 0:
        m2 = 12
        y2 -= 1
    m1 = m2 - 1
    y1 = y2
    if m1 == 0:
        m1 = 12
        y1 -= 1
    return (y1, m1), (y2, m2)


def _parse_scenario_pct(user_text: str) -> tuple[Decimal, bool, str]:
    """
    Parse the scenario percent from the user message.

    Returns (pct, found, direction_label):
      pct   = signed Decimal percent change (e.g. +100, -25). Default 0 if none.
      found = True if a percent magnitude was parsed.
      direction_label = "naik" / "turun" / "tetap" for narration.

    Sign: naik/tambah/bertambah/meningkat/+ => positive.
          turun/kurang/berkurang/menurun/-  => negative.
    """
    t = (user_text or "").lower()

    # magnitude: digits before % or "persen" (allow comma/dot decimals)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:%|persen|perc?ent)", t)
    if not m:
        return Decimal("0"), False, "tetap"

    raw = m.group(1).replace(",", ".")
    try:
        magnitude = Decimal(raw)
    except (InvalidOperation, ValueError):
        return Decimal("0"), False, "tetap"

    down = bool(re.search(r"\b(turun|berkurang|menurun|kurang|drop|anjlok)\b", t))
    up = bool(re.search(r"\b(naik|bertambah|meningkat|tambah|nambah|melonjak)\b", t))

    if down and not up:
        return -magnitude, True, "turun"
    # default to "naik" when an increase verb is present OR ambiguous (scenario
    # phrasing like "jika omzet ... 100 persen" implies an increase)
    return magnitude, True, "naik"


async def _fetch_laba_rugi(
    client: httpx.AsyncClient, periode: str, headers: dict
) -> Optional[dict]:
    """GET /api/reports/laba-rugi/{periode} (accrual). Returns dict or None."""
    try:
        resp = await client.get(
            _BASE_URL + "/api/reports/laba-rugi/" + periode,
            params={"basis": "accrual"},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("[PROJECTION_ENGINE] laba-rugi %s failed: %s", periode, e)
        return None


def _extract_pl(data: dict, year: int, month: int, periode: str) -> _MonthPL:
    """Pull revenue / cogs / gross from a laba-rugi response (handles {data:{}})."""
    src = data
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        src = data["data"]
    revenue = _to_decimal(src.get("pendapatan_penjualan"))
    cogs = _to_decimal(src.get("hpp"))
    gross = _to_decimal(src.get("laba_kotor"))
    # If gross missing/zero but revenue & cogs present, derive it.
    if gross == 0 and (revenue != 0 or cogs != 0):
        gross = revenue - cogs
    return _MonthPL(
        periode=periode,
        year=year,
        month=month,
        revenue=revenue,
        cogs=cogs,
        gross=gross,
    )


async def execute_gross_profit_projection(
    user_text: str,
    auth_token: str,
    tenant_id: str,
    today: Optional[date] = None,
) -> dict:
    """
    Compute a deterministic gross-profit projection and render Bahasa Indonesia
    markdown. Returns {"type": "text", "text": "..."} on success or
    {"type": "error", "message": "..."} on failure.

    READ-ONLY. Zero LLM. All math in Decimal.
    """
    (y1, m1), (y2, m2) = _last_two_complete_months(today)
    p1 = f"{y1:04d}-{m1:02d}"
    p2 = f"{y2:04d}-{m2:02d}"

    headers = {
        "Authorization": "Bearer " + (auth_token or ""),
        "Content-Type": "application/json",
        "X-Tenant-ID": tenant_id,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            d1 = await _fetch_laba_rugi(client, p1, headers)
            d2 = await _fetch_laba_rugi(client, p2, headers)
    except Exception as e:
        return {
            "type": "error",
            "message": "Gagal mengambil data laba rugi: " + str(e)[:120],
        }

    if d1 is None and d2 is None:
        return {
            "type": "error",
            "message": (
                "Maaf, saya belum bisa mengambil data laba rugi 2 bulan terakhir "
                f"({_BULAN_ID[m1]} & {_BULAN_ID[m2]} {y2}). Coba lagi sebentar."
            ),
        }

    months = []
    if d1 is not None:
        months.append(_extract_pl(d1, y1, m1, p1))
    if d2 is not None:
        months.append(_extract_pl(d2, y2, m2, p2))

    total_revenue = sum((mo.revenue for mo in months), Decimal("0"))
    total_cogs = sum((mo.cogs for mo in months), Decimal("0"))
    gross_profit = total_revenue - total_cogs

    # Guard divide-by-zero — no sales data
    if total_revenue <= 0:
        return {
            "type": "text",
            "text": (
                f"📊 **Proyeksi Laba Kotor**\n\n"
                f"Belum ada data penjualan pada 2 bulan terakhir "
                f"({_BULAN_ID[m1]}–{_BULAN_ID[m2]} {y2}), jadi saya belum bisa "
                f"membuat proyeksi laba kotor. Setelah ada transaksi penjualan, "
                f"saya bisa hitung estimasinya."
            ),
        }

    gross_margin_pct = (gross_profit / total_revenue) * Decimal("100")

    # n = number of months we actually have data for (1 or 2)
    n = Decimal(len(months))
    avg_monthly_revenue = total_revenue / n

    pct, pct_found, direction = _parse_scenario_pct(user_text)
    multiplier = Decimal("1") + (pct / Decimal("100"))
    projected_next_month_revenue = avg_monthly_revenue * multiplier
    projected_gross_profit = projected_next_month_revenue * (
        gross_margin_pct / Decimal("100")
    )

    # ── Render deterministic markdown ──
    if len(months) == 2:
        basis_line = (
            f"Berdasarkan data **{_BULAN_ID[months[0].month]}–{_BULAN_ID[months[1].month]} "
            f"{months[1].year}** (2 bulan terakhir)"
        )
    else:
        mo = months[0]
        basis_line = (
            f"Berdasarkan data **{_BULAN_ID[mo.month]} {mo.year}** "
            f"(hanya 1 bulan tersedia dari 2 bulan terakhir)"
        )

    if pct_found:
        sign = "+" if pct >= 0 else "-"
        skenario_label = f"Omzet {direction} {sign}{_fmt_pct(abs(pct)).rstrip('%')}%"
    else:
        skenario_label = "Tetap (tidak ada persentase disebut, asumsi 0%)"

    lines = []
    lines.append("📊 **Proyeksi Laba Kotor Bulan Depan**")
    lines.append("")
    lines.append(basis_line + ":")
    lines.append("")
    lines.append("| Metrik | Nilai |")
    lines.append("|---|---:|")
    lines.append(f"| Total omzet ({len(months)} bln) | {_fmt_rp(total_revenue)} |")
    lines.append(f"| Total HPP ({len(months)} bln) | {_fmt_rp(total_cogs)} |")
    lines.append(f"| Laba kotor ({len(months)} bln) | {_fmt_rp(gross_profit)} |")
    lines.append(f"| Rata-rata omzet / bulan | {_fmt_rp(avg_monthly_revenue)} |")
    lines.append(f"| Margin kotor | {_fmt_pct(gross_margin_pct)} |")
    lines.append(f"| Skenario | {skenario_label} |")
    lines.append(
        f"| Proyeksi omzet bulan depan | {_fmt_rp(projected_next_month_revenue)} |"
    )
    lines.append(f"| **Proyeksi laba kotor** | **{_fmt_rp(projected_gross_profit)}** |")
    lines.append("")
    if not pct_found:
        lines.append(
            "_Catatan: tidak ada persentase perubahan yang terbaca, jadi proyeksi "
            "memakai omzet rata-rata apa adanya (perubahan 0%)._"
        )
        lines.append("")
    lines.append(
        f"⚠️ Estimasi. Asumsi: rasio margin kotor tetap (~{_fmt_pct(gross_margin_pct)}). "
        "Angka aktual bisa berbeda jika struktur biaya berubah."
    )

    text = "\n".join(lines)
    logger.warning(
        "[PROJECTION_ENGINE] p1=%s p2=%s rev=%s cogs=%s margin=%s pct=%s proj_gp=%s",
        p1,
        p2,
        total_revenue,
        total_cogs,
        gross_margin_pct,
        pct,
        _q0(projected_gross_profit),
    )

    return {
        "type": "text",
        "text": text,
        # structured fields for session state / telemetry / tests
        "periode_1": p1,
        "periode_2": p2,
        "total_revenue": str(_q0(total_revenue)),
        "total_cogs": str(_q0(total_cogs)),
        "gross_profit": str(_q0(gross_profit)),
        "gross_margin_pct": str(_q2(gross_margin_pct)),
        "scenario_pct": str(pct),
        "projected_next_month_revenue": str(_q0(projected_next_month_revenue)),
        "projected_gross_profit": str(_q0(projected_gross_profit)),
    }
