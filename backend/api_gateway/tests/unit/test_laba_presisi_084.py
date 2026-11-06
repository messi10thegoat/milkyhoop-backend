"""BUG-084 (6 Nov 2026): dashboard "LABA BULAN INI Rp 100.306,56" vs L/R Rp 100.307.

Sen lahir di jurnal HPP (WAC, di LUAR pembulatan titik lahir -- putusan pemilik 4 Nov) dan benar di jurnal. Dashboard v2 menampilkan
angka laba rugi pada presisi tenant: bawaan 0 = rupiah penuh, flag uang_bersen = 2 (sen tetap). Dibulatkan dari jumlahnya sendiri
(HALF_UP), bukan selisih komponen bulat. Piutang/hutang TIDAK disentuh (sisa bersen lama harus terbayar persis).
"""
import asyncio
import os
from datetime import date
from decimal import Decimal as D

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import dashboard_v2 as DV  # noqa: E402

T = "pt-uji"


def _jalan(c):
    return asyncio.run(c)


class _C:
    async def fetch(self, sql, *a):
        return []


@pytest.fixture
def presisi(monkeypatch):
    nilai = {"p": 0}

    async def p(conn, tid):
        return nilai["p"]
    monkeypatch.setattr(DV.UP, "presisi_tenant", p)
    return nilai


def test_uang_p_half_up_dan_sen_bila_bersen():
    assert DV.uang_p(D("100306.56"), 0) == "100307.00"
    assert DV.uang_p(D("100306.49"), 0) == "100306.00"
    assert DV.uang_p(D("-0.50"), 0) == "-1.00"
    assert DV.uang_p(D("100306.56"), 2) == "100306.56"


def test_laba_bulan_ini_rupiah_penuh_bawaan_sen_bila_flag(monkeypatch, presisi):
    async def lr(conn, tid, mulai, akhir):  # angka nyata nyawit November (HPP bersen)
        return {"pendapatan": D("8806000.00"), "beban": D("8705693.44"), "laba": D("100306.56"),
                "rinci_beban": [{"account_code": "5-10100", "account_name": "HPP", "balance": D("8705693.44")}]}
    monkeypatch.setattr(DV.DA, "laba_rugi", lr)
    pnl, beban = _jalan(DV.laba_rugi_dan_beban(_C(), T, date(2026, 11, 1), date(2026, 11, 30)))
    assert (pnl["revenue"], pnl["expense"], pnl["profit"]) == ("8806000.00", "8705693.00", "100307.00")
    assert beban["total"] == "8705693.00" and beban["categories"][0]["amount"] == "8705693.00"
    presisi["p"] = 2
    pnl, beban = _jalan(DV.laba_rugi_dan_beban(_C(), T, date(2026, 11, 1), date(2026, 11, 30)))
    assert pnl["profit"] == "100306.56" and beban["categories"][0]["amount"] == "8705693.44"


def test_tren_dan_pembanding_ikut_presisi(monkeypatch, presisi):
    async def bulanan(conn, tid, awal, akhir):
        return {date(2026, 11, 1): {"pendapatan": D("8806000"), "beban": D("8705693.44"), "laba": D("100306.56")}}
    monkeypatch.setattr(DV.DA, "laba_rugi_bulanan", bulanan)
    t = _jalan(DV.tren_12_bulan(_C(), T, date(2026, 11, 6), date(2026, 11, 1)))
    assert t["points"][-1]["profit"] == "100307.00" and t["points"][-1]["expense"] == "8705693.00"

    async def banyak(conn, tid, rentang):
        return [{"revenue": D("8806000"), "expense": D("8705693.44"), "profit": D("100306.56"), "net_cash": D("10.40")},
                {"revenue": D("1000"), "expense": D("0.49"), "profit": D("999.51"), "net_cash": D("0")}][:len(rentang)]
    monkeypatch.setattr(DV, "_metrik_rentang_banyak", banyak)
    b = _jalan(DV.pembanding(_C(), T, "bulan_ini", date(2026, 11, 1), date(2026, 11, 6), date(2026, 11, 6), date(2026, 10, 1)))
    m = b["metrics"]
    assert m["profit"]["current"] == "100307.00" and m["net_cash"]["current"] == "10.00"
    assert m["profit"]["prev_period"] == {"value": "1000.00", "delta": "99307.00", "delta_pct": m["profit"]["prev_period"]["delta_pct"]}


def test_piutang_hutang_tak_dibulatkan():
    """Sisa bersen lama wajib tampil persis (putusan 4 Nov): uang() tanpa presisi tetap dipakai di luar laba rugi."""
    import inspect
    for fn in (DV.piutang, DV.hutang, DV.kas_bank):
        assert "uang_p(" not in inspect.getsource(fn), fn.__name__
