"""MUTASI piutang/hutang per kontak (9 Nov 2026; kontrak backend/docs/KONTRAK-kontak-mutasi-20261109.md).

Paritas angka diuji di SALINAN (scripts/ops/uji-salinan-mutasi.sh: saldo_akhir == saldo detail untuk setiap kontak kaos/nyawit). Di sini:
penyusun saldo berjalan, saldo_awal/dari/sampai, halaman, teks server, omitted, galat rute, dan cermin cabang penentu (setiap cabang
compute_*_outstanding punya padanan; tenant eksplisit)."""
import datetime as dt
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import customers as cu
from app.routers import vendors as ve
from app.services import kontak_mutasi as KM

TENANT = "kaos-biru-konveksi"
ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def b(urut, tgl, jno, debit=0, kredit=0, st="INVOICE", tipe="sales_invoice", dno="INV-1", ket=None, did=None):
    return {"urut": urut, "st": st, "tgl": dt.date(2026, 11, tgl), "jid": uuid.uuid4(), "jno": jno, "tipe": tipe, "did": did or uuid.uuid4(),
            "dno": dno, "ket": ket, "debit": Decimal(str(debit)), "kredit": Decimal(str(kredit))}


BARIS = [b(2, 5, "RCV-2", kredit=500, st="RECEIVE_PAYMENT", tipe="receive_payment", dno="RCV-2", ket="INV-1"),
         b(1, 3, "INV-1", debit=1500), b(1, 1, "INV-0", debit=250, dno="INV-0"), b(6, 5, "CN-1", kredit=100.5, st="CREDIT_NOTE",
                                                                                    tipe="credit_note", dno="CN-1")]


def test_susun_urut_dan_saldo_berjalan_pelanggan():
    r = KM.susun(BARIS, "customer", None, 0, 50)
    assert [x["jno"] for x in r["baris"]] == ["INV-0", "INV-1", "CN-1", "RCV-2"]  # tanggal, lalu nomor jurnal
    assert [x["saldo"] for x in r["baris"]] == [Decimal("250"), Decimal("1750"), Decimal("1649.5"), Decimal("1149.5")]
    assert r["saldo_awal"] == 0 and r["saldo_akhir"] == Decimal("1149.5") and r["total"] == 4
    assert r["total_debit"] == Decimal("1750") and r["total_kredit"] == Decimal("600.5")


def test_susun_vendor_kredit_menambah():
    r = KM.susun([b(1, 1, "BILL-1", kredit=900, st="BILL"), b(2, 2, "PAY-1", debit=400, st="BILL_PAYMENT")], "vendor", None, 0, 50)
    assert [x["saldo"] for x in r["baris"]] == [Decimal("900"), Decimal("500")] and r["saldo_akhir"] == Decimal("500")


def test_susun_dari_dan_halaman():
    r = KM.susun(BARIS, "customer", dt.date(2026, 11, 3), 0, 50)
    assert r["saldo_awal"] == Decimal("250") and r["total"] == 3 and r["baris"][0]["saldo"] == Decimal("1750")
    assert r["saldo_awal"] + sum(x["debit"] - x["kredit"] for x in r["baris"]) == r["saldo_akhir"]
    p2 = KM.susun(BARIS, "customer", None, 2, 2)
    assert [x["jno"] for x in p2["baris"]] == ["CN-1", "RCV-2"] and p2["baris"][0]["saldo"] == Decimal("1649.5")  # meneruskan saldo
    assert p2["total"] == 4


def test_teks_server():
    assert KM.jenis_text(1, "INVOICE") == "Faktur penjualan"
    assert KM.jenis_text(9, "RECEIVE_PAYMENT") == "Pembayaran masuk (dibatalkan)"
    assert KM.jenis_text(10, "BILL") == "Pembatalan faktur pembelian"
    assert KM.keterangan(2, "INV-1") == "untuk INV-1" and KM.keterangan(10, "JV-9") == "membatalkan JV-9" and KM.keterangan(1, None) is None
    assert KM.modul_dok("deposit_application", "vendor") == "vendor_deposit" and KM.modul_dok("vendor_credit", "vendor") == "debit_note"
    assert KM.modul_dok("opening_balance", "vendor") is None


@pytest.mark.parametrize("sql,fungsi", [(KM.SQL_AR, "ar"), (KM.SQL_AP, "ap")])
def test_cermin_cabang_penentu(sql, fungsi):
    """Setiap cabang compute_*_outstanding punya padanan (sumber + sisi akun); jurnal POSTED tak dibalik; tenant $1 eksplisit."""
    harap = {"ar": ["'INVOICE'", "receive_payment_allocations", "'CREDIT_NOTE'", "'DEPOSIT_APPLICATION'", "'RECEIVE_PAYMENT_UNAPPLY'",
                    "'CREDIT_NOTE_REFUND'", "original_invoice_id IS NULL", "status NOT IN ('draft', 'void')", "cda.status = 'active'"],
             "ap": ["'BILL'", "bill_payment_allocations", "'VENDOR_CREDIT'", "'DEPOSIT_APPLICATION'", "'VENDOR_CREDIT_REFUND'",
                    "original_bill_id IS NULL", "'VENDOR_OPENING_BALANCE'", "NOT EXISTS (SELECT 1 FROM bill_payment_allocations",
                    "status_v2 NOT IN ('draft', 'void')"]}[fungsi]
    for h in harap:
        assert h in sql, h
    assert sql.count(KM.HIDUP) >= 6
    assert ("RECEIVABLE" if fungsi == "ar" else "PAYABLE") in sql


def test_batal_berpasangan():
    for j in KM.JENIS.values():
        assert "reversed_by_id IS NOT NULL" in j["batal"] and "rv.id = je.reversed_by_id AND rv.tenant_id = $1" in j["batal"]


# ---------------------------------------------------------------- mutasi() + rute


class Conn:
    def __init__(self, pihak=True, baris=None):
        self.pihak, self.baris, self.log = pihak, baris or [], []

    async def fetchrow(self, sql, *a):
        self.log.append((sql, a))
        return {"id": ID, "nama": "Nutrindo"} if self.pihak else None

    async def fetch(self, sql, *a):
        self.log.append((sql, a))
        if "FROM journal_entries WHERE tenant_id = $1 AND id = ANY" in sql:  # temuan B: waktu buat jurnal (urutan dalam tanggal)
            return []
        return self.baris


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def pasang(monkeypatch, mod, conn, izin=True):
    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq(conn))
    monkeypatch.setattr(mod, "get_pool", _pool)

    async def _boleh(request, modul):
        return izin if isinstance(izin, bool) else modul in izin
    monkeypatch.setattr("app.services.dashboard_izin.boleh_baca", _boleh)


def req():
    return SimpleNamespace(headers={}, state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": "22222222-2222-2222-2222-222222222222"}))


@pytest.mark.asyncio
async def test_rute_pelanggan_bentuk_kontrak(monkeypatch):
    conn = Conn(baris=list(BARIS))
    pasang(monkeypatch, cu, conn)
    d = (await cu.get_customer_mutasi(req(), ID, dari=None, sampai=None, tanpa_batal=True, skip=0, limit=50))["data"]
    assert set(d) == {"pihak", "akun_text", "dari", "sampai", "tanpa_batal", "saldo_awal", "total_debit", "total_kredit", "saldo_akhir",
                      "total", "skip", "limit", "baris", "omitted"}
    assert d["pihak"] == {"id": str(ID), "nama": "Nutrindo", "jenis": "customer"} and d["akun_text"] == "Piutang usaha"
    assert d["saldo_akhir"] == 1149.5 and d["saldo_awal"] == 0 and d["omitted"] == []
    x = d["baris"][3]
    assert set(x) == {"id", "tanggal", "jurnal", "dokumen", "jenis_text", "keterangan", "debit", "kredit", "saldo", "batal"}
    assert x["jenis_text"] == "Pembayaran masuk" and x["keterangan"] == "untuk INV-1" and x["dokumen"]["tipe"] == "receive_payment"
    assert x["debit"] == 0 and x["kredit"] == 500 and x["saldo"] == 1149.5 and x["batal"] is False and x["tanggal"] == "2026-11-05"
    sql, a = conn.log[1]
    assert a == (TENANT, ID) and "reversed_by_id IS NOT NULL" not in sql  # tanpa_batal=True: tanpa jalur batal


@pytest.mark.asyncio
async def test_rute_tanpa_batal_false_dan_sampai(monkeypatch):
    conn = Conn(baris=list(BARIS))
    pasang(monkeypatch, cu, conn)
    d = (await cu.get_customer_mutasi(req(), ID, dari=None, sampai=dt.date(2026, 11, 3), tanpa_batal=False, skip=0, limit=50))["data"]
    assert "reversed_by_id IS NOT NULL" in conn.log[1][0] and d["total"] == 2 and d["sampai"] == "2026-11-03"


@pytest.mark.asyncio
async def test_omitted_tanpa_izin_baca_dokumen(monkeypatch):
    pasang(monkeypatch, cu, Conn(baris=list(BARIS)), izin={"sales_invoice"})
    d = (await cu.get_customer_mutasi(req(), ID, dari=None, sampai=None, tanpa_batal=True, skip=0, limit=50))["data"]
    assert sorted(d["omitted"]) == ["credit_note", "receive_payment"]
    rcv = d["baris"][3]
    assert rcv["dokumen"]["nomor"] is None and rcv["keterangan"] is None and rcv["kredit"] == 500  # angka tetap
    assert d["baris"][0]["dokumen"]["nomor"] == "INV-0"


@pytest.mark.asyncio
async def test_rute_galat(monkeypatch):
    pasang(monkeypatch, ve, Conn(pihak=False))
    with pytest.raises(HTTPException) as e:
        await ve.get_vendor_mutasi(req(), ID, dari=None, sampai=None, tanpa_batal=True, skip=0, limit=50)
    assert e.value.status_code == 404 and e.value.detail == "Vendor tidak ditemukan."
    with pytest.raises(HTTPException) as e:
        await cu.get_customer_mutasi(req(), ID, dari=dt.date(2026, 11, 5), sampai=dt.date(2026, 11, 1), tanpa_batal=True, skip=0, limit=50)
    assert e.value.status_code == 400 and e.value.detail == "Tanggal awal tidak boleh setelah tanggal akhir."


@pytest.mark.parametrize("jalur,harap", [(f"/api/customers/{ID}/mutasi", ("customer", "R")), (f"/api/vendors/{ID}/mutasi", ("supplier", "R"))])
def test_izin_baca(jalur, harap):
    from app.middleware.permission_middleware import PermissionMiddleware
    assert PermissionMiddleware(app=None)._find_permission(jalur, "GET") == harap
