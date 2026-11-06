"""Kas & Bank ringkas (ui-pusat U6 fase 1, BACA saja): GET /api/bank-accounts/summary (B1) + /transactions/recent (B2).
Saldo = jurnal POSTED per coa_id (Law 1/16), kartu kredit dipisah, mutasi lintas akun SEMUA baris POSTED + status, rute di atas /{id}."""
from datetime import date, datetime
from uuid import uuid4

import pytest

from app.routers import bank_accounts as RB
from app.services import kasbank_ringkas as KR

T0 = datetime(2026, 11, 6, 9, 0)


def _akun(tipe, saldo, nama="Kas", jumlah=3, terakhir=date(2026, 11, 5)):
    return {"id": uuid4(), "account_name": nama, "account_type": tipe, "bank_name": None, "account_number": None, "currency": None,
            "saldo": saldo, "mutasi_terakhir": terakhir, "jumlah": jumlah}


def _baris(net, st="RECEIVE_PAYMENT", rev_of=None, rev_by=None, memo="Terima INV-1"):
    return {"id": uuid4(), "journal_id": uuid4(), "journal_number": "JE-1", "journal_date": date(2026, 11, 5), "created_at": T0,
            "source_type": st, "source_id": "s1", "reversal_of_id": rev_of, "reversed_by_id": rev_by, "bank_account_id": uuid4(),
            "account_name": "BCA", "net": net, "keterangan": memo}


# ---------------------------------------------------------------- B1
def test_ringkasan_total_tanpa_kartu_kewajiban_kartu_positif():
    r = KR.ringkasan([_akun("cash", 100000), _akun("bank", -50000.5, "BCA"), _akun("credit_card", -30000, "Kartu")], date(2026, 11, 6))
    assert (r["as_of"], r["total_akun"], r["total_saldo"], r["total_kewajiban_kartu"]) == ("2026-11-06", 3, "49999.50", "30000.00")
    assert [(a["saldo"], a["minus"]) for a in r["akun"]] == [("100000.00", False), ("-50000.50", True), ("-30000.00", True)]
    assert r["akun"][0]["currency"] == "IDR" and r["akun"][0]["mutasi_terakhir_at"] == "2026-11-05" and r["akun"][0]["jumlah_mutasi"] == 3


def test_ringkasan_akun_tanpa_jurnal_nol():
    r = KR.ringkasan([_akun("e_wallet", 0, jumlah=0, terakhir=None)], date(2026, 11, 6))
    assert (r["akun"][0]["saldo"], r["akun"][0]["minus"]) == ("0.00", False)
    assert (r["akun"][0]["mutasi_terakhir_at"], r["akun"][0]["jumlah_mutasi"], r["total_saldo"], r["total_kewajiban_kartu"]) == (None, 0, "0.00", "0.00")


def test_sql_ringkasan_dari_jurnal_posted_per_coa_tenant():
    s = KR.SQL_RINGKASAN
    assert "current_balance" not in s and "opening_balance" not in s
    for k in ("je.status = 'POSTED'", "jl.account_id = ba.coa_id", "je.tenant_id = ba.tenant_id", "ba.tenant_id = $1", "ba.is_active = true"):
        assert k in s, k


# ---------------------------------------------------------------- B2
def test_mutasi_arah_jumlah_status_label():
    m = KR.mutasi([_baris(150000), _baris(-20000.25, "EXPENSE", memo=None), _baris(20000.25, "EXPENSE_REVERSAL", rev_of=uuid4()),
                   _baris(-5000, "BILL_PAYMENT", rev_by=uuid4()), _baris(1, "TAK_DIKENAL"), _baris(0, "REVERSAL"),
                   _baris(5000, "BILL_PAYMENT", rev_of=uuid4())])
    assert [(x["arah"], x["amount"]) for x in m] == [("masuk", "150000.00"), ("keluar", "20000.25"), ("masuk", "20000.25"),
                                                     ("keluar", "5000.00"), ("masuk", "1.00"), ("masuk", "0.00"), ("masuk", "5000.00")]
    assert [x["status"] for x in m] == ["tercatat", "tercatat", "pembatalan", "dibatalkan", "tercatat", "pembatalan", "pembatalan"]
    assert [x["sumber_teks"] for x in m] == ["Terima pembayaran", "Biaya", "Pembatalan biaya", "Bayar tagihan", "Jurnal", "Pembatalan jurnal",
                                          "Pembatalan bayar tagihan"]
    assert m[0]["sumber"] == "receive_payment" and m[0]["description"] == "Terima INV-1" and m[0]["transaction_date"] == "2026-11-05"


def test_sql_mutasi_semua_baris_posted_tanpa_sembunyikan_pembalik():
    s = KR.SQL_MUTASI
    assert "je.tenant_id = $1" in s and "je.status = 'POSTED'" in s and "ba.tenant_id = je.tenant_id" in s and "LIMIT $2" in s
    assert "reversed_by_id IS NULL" not in s and "reversal_of_id IS NULL" not in s  # disembunyikan -> daftar tak cocok dengan saldo
    assert "ORDER BY je.journal_date DESC, je.created_at DESC" in s


# ---------------------------------------------------------------- lewat RUTE (urutan: /summary dan /transactions/recent di atas /{id})
class _C:
    def __init__(self):
        self.panggil = []

    async def fetch(self, sql, *a):
        self.panggil.append((sql, a))
        if sql == KR.SQL_RINGKASAN:
            return [_akun("cash", 1000)]
        if sql == KR.SQL_MUTASI:
            return [_baris(-500)]
        raise AssertionError(sql)


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return _Acq(self.c)


@pytest.fixture
def klien(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    c = _C()

    async def pool():
        return _Pool(c)

    async def hari(conn, tid):
        return date(2026, 11, 6)
    monkeypatch.setattr(RB, "get_pool", pool)
    monkeypatch.setattr(RB, "tanggal_dokumen", hari)
    app = FastAPI()

    @app.middleware("http")
    async def _masuk(request, call_next):
        request.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
        return await call_next(request)
    app.include_router(RB.router, prefix="/api/bank-accounts")
    return TestClient(app), c


def test_rute_summary_bukan_bank_account_id(klien):
    k, c = klien
    r = k.get("/api/bank-accounts/summary")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert (d["total_saldo"], d["akun"][0]["saldo"], d["as_of"]) == ("1000.00", "1000.00", "2026-11-06")
    assert c.panggil == [(KR.SQL_RINGKASAN, ("t1",))]


def test_rute_mutasi_terakhir_limit(klien):
    k, c = klien
    r = k.get("/api/bank-accounts/transactions/recent")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["items"][0]["arah"] == "keluar" and c.panggil[-1] == (KR.SQL_MUTASI, ("t1", 10))
    assert k.get("/api/bank-accounts/transactions/recent?limit=5").status_code == 200 and c.panggil[-1][1] == ("t1", 5)
    assert k.get("/api/bank-accounts/transactions/recent?limit=0").status_code == 422
    assert k.get("/api/bank-accounts/transactions/recent?limit=101").status_code == 422
