"""laporan-fix-1 (neraca): ekuitas = ekuitas buku + SEMUA laba s.d. as_of -- Laba Ditahan Tahun Sebelumnya (sebelum 1 Jan tahun as_of,
belum ditutup) + Laba Tahun Berjalan (1 Jan..as_of). Neraca SELALU akrual (putusan pemilik 10 Nov 2026); period_start/basis diterima tapi
tak dipakai (dulu period_start tengah tahun membuang laba sebelumnya -> neraca tak balance, gap TESTER nyawit 1.200).
Paritas nyata di salinan DB = scripts/ops/uji-salinan-laporan-psak.sh."""
import asyncio
from decimal import Decimal as D

import pytest

from app.services.report_engine import balance_sheet as BS
from app.services.report_engine import validation as VAL

PANGGIL = []


@pytest.fixture
def palsu(monkeypatch):
    PANGGIL.clear()

    async def b(conn, tid, fp, start_date=None, end_date=None):
        if fp.get("is_cash") is True or fp == {"account_types": BS.ASSET_TYPES}:
            return D(3000)                                  # aset = kas 3000
        if fp == {"account_type": "EQUITY"} or fp.get("psak_sub_category") == "PAID_IN_CAPITAL":
            return D(-1300)                                 # modal 1300 (kredit = negatif mentah)
        return D(0)

    async def bd(conn, tid, fp, start_date=None, end_date=None):
        return []

    async def laba(conn, tid, start, end, basis="accrual"):
        PANGGIL.append((start, end, basis))
        return {"net": D(1200) if start == "1900-01-01" else D(500)}   # sebelum 2026 = 1200, 2026 s.d. as_of = 500
    monkeypatch.setattr(BS, "compute_balance", b)
    monkeypatch.setattr(BS, "compute_balance_detail", bd)
    monkeypatch.setattr(BS, "hitung_laba_rugi", laba)


def neraca(*a, **k):
    return asyncio.run(BS.generate_balance_sheet(None, "t", *a, **k))


def test_laba_sebelumnya_masuk_ekuitas_dan_neraca_balance(palsu):
    r = neraca("2026-10-31")
    assert PANGGIL == [("2026-01-01", "2026-10-31", "accrual"), ("1900-01-01", "2025-12-31", "accrual")]
    ek = r["ekuitas"]
    assert (ek["laba_periode"], ek["laba_tahun_berjalan"], ek["laba_ditahan_lalu"], ek["tahun_mulai"]) == (500.0, 500.0, 1200.0, "2026-01-01")
    assert ek["total"] == 3000.0 and r["total_liabilitas_ekuitas"] == 3000.0 and r["_balanced"] is True
    baris = {x["kunci"]: x for x in r["baris"]}
    assert (baris["laba_ditahan_lalu"]["label"], baris["laba_ditahan_lalu"]["nilai"]) == ("Laba Ditahan Tahun Sebelumnya", "1200.00")
    assert (baris["laba_periode"]["label"], baris["laba_periode"]["nilai"]) == ("Laba Tahun Berjalan", "500.00")
    assert baris["total_ekuitas"]["nilai"] == "3000.00"


def test_period_start_tengah_tahun_dan_basis_kas_tak_mengubah_neraca(palsu):
    dasar = neraca("2026-10-31")
    PANGGIL.clear()
    r = neraca("2026-10-31", "2026-07-01", basis="cash")
    assert PANGGIL == [("2026-01-01", "2026-10-31", "accrual"), ("1900-01-01", "2025-12-31", "accrual")]
    assert r["_balanced"] is True and r["ekuitas"] == dasar["ekuitas"] and r["basis_applied"] == "accrual"
    assert r["basis_text"] == "Basis Akrual"


def test_baris_laba_lalu_hilang_bila_nol(palsu, monkeypatch):
    async def laba(conn, tid, start, end, basis="accrual"):
        return {"net": D(0) if start == "1900-01-01" else D(500)}
    monkeypatch.setattr(BS, "hitung_laba_rugi", laba)
    r = neraca("2026-10-31")
    assert "laba_ditahan_lalu" not in {x["kunci"] for x in r["baris"]} and r["ekuitas"]["total"] == 1800.0


def test_validasi_laba_hanya_dibanding_bila_sebanding():
    bs = {"_balanced": True, "ekuitas": {"laba_periode": 500.0, "tahun_mulai": "2026-01-01"}, "aset_lancar": {"kas_setara_kas": {"total": 0}}}
    cf = {"_reconciled": True, "kas_akhir": 0}
    pl = lambda **k: {"laba_bersih": 300.0, "basis_applied": "accrual", "period": {"start": "2026-01-01"}, **k}
    gal = lambda p: [e for e in asyncio.run(VAL.validate_reports(bs, p, cf)) if "LABA" in e]
    assert gal(pl()) != []                                              # akrual 1 Jan dan beda -> galat sungguhan
    assert gal(pl(basis_applied="cash")) == []                          # basis kas = definisi lain, bukan galat
    assert gal(pl(period={"start": "2026-07-01"})) == []                # tengah tahun = bukan laba tahun berjalan
    assert gal(pl(laba_bersih=500.0)) == []
