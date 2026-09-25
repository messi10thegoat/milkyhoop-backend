"""parse_periode ketat (tiket #10b-3a (c)+(e), 25 Sep 2026).

Dulu reports.parse_periode: input tak dikenal diam-diam "bulan ini" (jam UTC); financial_reports_journal:
ValueError -> 500. Kini satu parser bersama: format sah saja, selain itu 400 — dan 400 itu harus
SAMPAI ke klien (handler meneruskan HTTPException) SEBELUM menyentuh DB.
"""
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import financial_reports_journal as FRJ
from app.routers import reports as R
from app.utils import periode as PER
from app.utils.periode import parse_periode

APP = Path(R.__file__).resolve().parents[1]


@pytest.mark.parametrize("p,awal,akhir", [
    ("2026-09", datetime(2026, 9, 1), datetime(2026, 9, 30, 23, 59, 59)),
    ("2024-02", datetime(2024, 2, 1), datetime(2024, 2, 29, 23, 59, 59)),
    ("2026-Q3", datetime(2026, 7, 1), datetime(2026, 9, 30, 23, 59, 59)),
    ("2026-Q1", datetime(2026, 1, 1), datetime(2026, 3, 31, 23, 59, 59)),
    ("2026", datetime(2026, 1, 1), datetime(2026, 12, 31, 23, 59, 59)),
    (" 2026-12 ", datetime(2026, 12, 1), datetime(2026, 12, 31, 23, 59, 59)),
])
def test_format_sah(p, awal, akhir):
    assert parse_periode(p) == (awal, akhir)


@pytest.mark.parametrize("p", ["bulan-ini", "bulan ini", "2026-9", "2026-13", "2026-00", "2026-Q5", "2026-Q0",
                               "26-09", "2026-09-01", "abc", "", "Q3-2026", "20261"])
def test_tak_dikenal_400_bukan_bulan_ini(p):
    with pytest.raises(HTTPException) as e:
        parse_periode(p)
    assert e.value.status_code == 400 and "YYYY-MM" in e.value.detail


def test_satu_sumber_tanpa_salinan_lokal():
    for mod in (R, FRJ):
        pohon = ast.parse(Path(mod.__file__).read_text())
        assert not any(isinstance(n, ast.FunctionDef) and n.name == "parse_periode" for n in pohon.body), mod.__name__
        assert mod.periode_laporan is PER.periode_laporan
        assert "parse_periode(periode)" not in Path(mod.__file__).read_text()   # semua lewat periode_laporan
    # tak ada lagi fallback "bulan ini" dari jam dinding di jalur periode
    assert "Default to current month" not in Path(R.__file__).read_text()


class PoolMeledak:
    async def __call__(self):
        raise AssertionError("DB disentuh sebelum periode divalidasi")


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "grapgrap-manado", "user_id": None}), headers={})


@pytest.mark.asyncio
@pytest.mark.parametrize("panggil", [
    lambda: R.get_neraca(_req(), "2026-9"),
    lambda: R.get_arus_kas(_req(), "2026-9"),
    lambda: R.get_laba_rugi(_req(), "2026-9", basis=None),
    lambda: R.get_profit_loss_by_basis(_req(), "2026-9", basis=None),
    lambda: R.get_cash_accrual_comparison(_req(), "2026-9"),
    lambda: FRJ.get_neraca_journal(_req(), "2026-9"),
])
async def test_handler_meneruskan_400_sebelum_db(monkeypatch, panggil):
    for mod in (R, FRJ):
        for nama in ("get_pool", "get_db_pool", "get_db_connection"):
            if hasattr(mod, nama):
                monkeypatch.setattr(mod, nama, PoolMeledak())
    with pytest.raises(HTTPException) as e:
        await panggil()
    assert e.value.status_code == 400 and "2026-9" in e.value.detail


# ---------- kata kunci eksplisit (putusan MASTER) — dari TANGGAL BISNIS tenant ----------

@pytest.mark.parametrize("kata,hari,awal,akhir", [
    ("bulan-ini", (2026, 10, 1), datetime(2026, 10, 1), datetime(2026, 10, 31, 23, 59, 59)),
    ("BULAN-INI", (2026, 9, 25), datetime(2026, 9, 1), datetime(2026, 9, 30, 23, 59, 59)),
    ("bulan-lalu", (2026, 1, 15), datetime(2025, 12, 1), datetime(2025, 12, 31, 23, 59, 59)),
    ("bulan-lalu", (2026, 3, 1), datetime(2026, 2, 1), datetime(2026, 2, 28, 23, 59, 59)),
    ("tahun-ini", (2026, 9, 25), datetime(2026, 1, 1), datetime(2026, 12, 31, 23, 59, 59)),
])
def test_kata_kunci_dari_hari_ini(kata, hari, awal, akhir):
    from datetime import date
    assert parse_periode(kata, date(*hari)) == (awal, akhir)


class ConnZona:
    def __init__(self, zona):
        self.zona, self.n = zona, 0

    async def fetchval(self, sql, *a):
        self.n += 1
        assert "timezone" in sql
        return self.zona


@pytest.mark.asyncio
@pytest.mark.parametrize("utc,bulan", [
    (datetime(2026, 9, 30, 17, 30, tzinfo=__import__("datetime").timezone.utc), 10),   # 2026-10-01 00:30 WIB
    (datetime(2026, 9, 30, 16, 59, tzinfo=__import__("datetime").timezone.utc), 9),    # 2026-09-30 23:59 WIB
])
async def test_bulan_ini_wib_bukan_utc(utc, bulan):
    import uuid
    conn = ConnZona("Asia/Jakarta")
    awal, _ = await PER.periode_laporan("bulan-ini", f"uji-{uuid.uuid4()}", conn=conn, sekarang=utc)
    assert (awal.year, awal.month) == (2026, bulan) and conn.n == 1


@pytest.mark.asyncio
async def test_input_rusak_tak_menyentuh_db():
    conn = ConnZona("Asia/Jakarta")
    with pytest.raises(HTTPException) as e:
        await PER.periode_laporan("2026-9", "t", conn=conn)
    assert e.value.status_code == 400 and conn.n == 0
    assert await PER.periode_laporan("2026-09", "t", conn=conn) == parse_periode("2026-09") and conn.n == 0
