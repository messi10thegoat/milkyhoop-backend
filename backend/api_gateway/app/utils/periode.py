"""Periode laporan dari path `/{periode}` — SATU parser ketat (tiket #10b-3a (c)+(e), 25 Sep 2026).

Dulu ada dua salinan yang saling berbeda:
- routers/reports.py: input tak dikenal ("bulan-ini", "2026-9", "abc") DIAM-DIAM jadi "bulan ini" —
  dihitung dari datetime.now() UTC, jadi 00:00–07:00 WIB tanggal 1 = bulan LALU. Laporan bulan yang
  salah tampil tanpa tanda apa pun.
- routers/financial_reports_journal.py: input tak dikenal -> ValueError -> 500.
Kini: YYYY-MM / YYYY-Qn / YYYY, plus kata kunci EKSPLISIT "bulan-ini" / "bulan-lalu" / "tahun-ini"
(putusan MASTER 25 Sep: "laba-rugi/bulan-ini" pernah dipanggil 21 Sep dan pemakaian bot tak terukur
karena bot memanggil lewat localhost) yang dihitung dari TANGGAL BISNIS tenant, bukan jam UTC.
Selain itu 400 dengan pesan yang menyebut format sah.
"""
import re
from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import HTTPException

_BULAN = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_KUARTAL = re.compile(r"^(\d{4})-Q([1-4])$")
_TAHUN = re.compile(r"^(\d{4})$")
KATA_KUNCI = ("bulan-ini", "bulan-lalu", "tahun-ini")

PESAN = ("Periode tidak dikenal: {!r}. Gunakan YYYY-MM (2026-09), YYYY-Qn (2026-Q3), YYYY (2026), "
         "atau bulan-ini / bulan-lalu / tahun-ini.")


def _bulan(y: int, mo: int) -> tuple:
    return datetime(y, mo, 1), datetime(y, mo, monthrange(y, mo)[1], 23, 59, 59)


def parse_periode(periode: str, hari_ini: Optional[date] = None) -> tuple:
    """-> (start_date, end_date) datetime (akhir = 23:59:59 hari terakhir). Tak dikenal -> 400.
    Kata kunci butuh `hari_ini` (tanggal bisnis tenant) — tanpa itu dianggap tak dikenal, supaya tak
    ada jalur yang diam-diam memakai jam dinding."""
    p = (periode or "").strip().lower()
    if p in KATA_KUNCI and hari_ini is not None:
        if p == "bulan-ini":
            return _bulan(hari_ini.year, hari_ini.month)
        if p == "bulan-lalu":
            y, mo = (hari_ini.year - 1, 12) if hari_ini.month == 1 else (hari_ini.year, hari_ini.month - 1)
            return _bulan(y, mo)
        return datetime(hari_ini.year, 1, 1), datetime(hari_ini.year, 12, 31, 23, 59, 59)
    m = _BULAN.match(p)
    if m:
        return _bulan(int(m.group(1)), int(m.group(2)))
    m = _KUARTAL.match(p.upper())
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        awal, akhir = (q - 1) * 3 + 1, q * 3
        return datetime(y, awal, 1), datetime(y, akhir, monthrange(y, akhir)[1], 23, 59, 59)
    m = _TAHUN.match(p)
    if m:
        y = int(m.group(1))
        return datetime(y, 1, 1), datetime(y, 12, 31, 23, 59, 59)
    raise HTTPException(status_code=400, detail=PESAN.format(periode))


async def periode_laporan(periode: str, tenant_id: str, conn=None, sekarang: Optional[datetime] = None) -> tuple:
    """Dipakai handler. DB disentuh HANYA untuk kata kunci (zona tenant); input rusak = 400 tanpa DB.
    `sekarang` (UTC) untuk tes jam suntik."""
    from .tanggal_tenant import tanggal_pada, zona_tenant

    if (periode or "").strip().lower() not in KATA_KUNCI:
        return parse_periode(periode)
    instan = sekarang or datetime.now(timezone.utc)
    if conn is not None:
        return parse_periode(periode, tanggal_pada(instan, await zona_tenant(conn, tenant_id)))
    from ..services.db_pool import get_db_pool

    pool = await get_db_pool()
    async with pool.acquire() as c:
        return parse_periode(periode, tanggal_pada(instan, await zona_tenant(c, tenant_id)))
