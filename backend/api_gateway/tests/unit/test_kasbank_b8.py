"""B8 Kas & Bank tulis (putusan pemilik LANGSUNG 6 Nov 2026): kas minus DITOLAK (KAS_MINUS) di tx manual, posting transfer, dan biaya;
hitung fisik = jumlah fisik -> server hitung selisih; X-Idempotency-Key opsional; */preview = penentu SAMA, NOL tulisan."""
import asyncio
from datetime import date
from decimal import Decimal as D
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.routers import bank_accounts as RB
from app.routers import bank_transfers as RT
from app.routers import expenses as RE
from app.schemas.bank_accounts import AdjustBalanceRequest, CreateManualTransactionRequest
from app.schemas.bank_transfers import CreateBankTransferRequest
from app.services import idem_buat
from app.services import kasbank_tulis as KT
from app.services.galat_berkode import GalatBerkode

T = "t1"
HARI = date(2026, 11, 6)
KAS, BANK, LAWAN, ADJ_UNTUNG, ADJ_RUGI, FEE = (uuid4() for _ in range(6))


def _akun(tipe="cash", saldo=100000, coa=None, nama="Kas Kecil", aktif=True):
    return {"id": uuid4(), "account_name": nama, "account_type": tipe, "coa_id": coa or uuid4(), "is_active": aktif, "saldo": D(str(saldo))}


class _Tx:
    def __init__(self, c, readonly):
        self.c, self.readonly = c, readonly

    async def __aenter__(self):
        self.c.readonly.append(self.readonly)
        return self

    async def __aexit__(self, *a):
        return False


class _C:
    """Tiruan asyncpg: rekening per id/coa, akun lawan, periode, baris transfer; mencatat execute/fetchval (tulisan)."""

    def __init__(self, akun=(), lawan_tipe="EQUITY", periode=None, transfer=None):
        self.akun = {a["id"]: a for a in akun}
        self.lawan_tipe, self.periode, self.transfer = lawan_tipe, periode, transfer
        self.tulis, self.akun_args, self.readonly = [], [], []

    def transaction(self, readonly=False):
        return _Tx(self, readonly)

    async def fetchrow(self, sql, *a):
        if sql == KT.SQL_AKUN:
            self.akun_args.append(a)
            return self.akun.get(a[0])
        if sql == KT.SQL_AKUN_COA:
            return next((x for x in self.akun.values() if x["coa_id"] == a[0]), None)
        if sql == KT.SQL_TRANSFER_POSTING:
            return self.transfer
        if "FROM chart_of_accounts WHERE id" in sql:
            return {"id": a[0], "account_type": self.lawan_tipe} if a[0] == LAWAN or any(x["coa_id"] == a[0] for x in self.akun.values()) else None
        if "FROM fiscal_periods" in sql:
            return self.periode
        if "FOR UPDATE" in sql:
            return {"id": a[0]}
        raise AssertionError(sql)

    async def fetch(self, sql, *a):
        if "account_roles" in sql:
            return []
        if "SELECT id, name AS account_name FROM chart_of_accounts" in sql:  # kolom CoA = name (nyata kaos 6 Nov)
            return [{"id": i, "account_name": f"akun-{str(i)[:4]}"} for i in a[1]]
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        self.tulis.append((sql, a))
        if "SELECT from_bank_id" in sql:
            return self.transfer["from_bank_id"] if self.transfer else None
        if "SELECT coa_id FROM bank_accounts" in sql:
            return next((x["coa_id"] for x in self.akun.values() if x["id"] == a[0]), None)
        if "RETURNING id" in sql:
            return uuid4()
        if "COALESCE(SUM" in sql:
            return D(0)
        return None

    async def execute(self, sql, *a):
        self.tulis.append((sql, a))

    def baris_jurnal(self):
        return [a[3:7] if len(a) == 7 else a for s, a in self.tulis if "INSERT INTO journal_lines" in s]


@pytest.fixture(autouse=True)
def peran(monkeypatch):
    async def r(conn, tid, role):
        return {"BANK_ADJUSTMENT_GAIN": ADJ_UNTUNG, "BANK_ADJUSTMENT_LOSS": ADJ_RUGI, "BANK_FEE": FEE}[role.name if hasattr(role, "name") else str(role)]
    monkeypatch.setattr(KT, "resolve_account_id_by_role", r)


def _tx(arah="out", jumlah=150000):
    return CreateManualTransactionRequest(direction=arah, amount=jumlah, transaction_date=HARI, description="Beli galon", contra_account_id=str(LAWAN))


def run(c):
    return asyncio.run(c)


# ---------------------------------------------------------------- tx manual
def test_kas_minus_ditolak_pratinjau_blok_tulis_galat_berkode():
    a = _akun("cash", 100000)
    r = run(KT.rencana_tx_manual(_C([a]), T, a["id"], _tx(), True))
    assert [b["code"] for b in r["blocks"]] == ["KAS_MINUS"]
    assert "sekarang Rp 100.000" in r["blocks"][0]["message"] and "hitung fisik" in r["blocks"][0]["message"]
    with pytest.raises(GalatBerkode) as e:
        run(KT.rencana_tx_manual(_C([a]), T, a["id"], _tx(), False))
    assert (e.value.status_code, e.value.code) == (400, "KAS_MINUS")


def test_kas_kecil_juga_kas_bank_boleh_minus():
    k = _akun("petty_cash", 100000)
    assert [b["code"] for b in run(KT.rencana_tx_manual(_C([k]), T, k["id"], _tx(), True))["blocks"]] == ["KAS_MINUS"]
    b = _akun("bank", 100000, nama="BCA")
    r = run(KT.rencana_tx_manual(_C([b]), T, b["id"], _tx(), True))
    assert r["blocks"] == [] and r["saldo"][0]["sesudah"] == D("-50000")


def test_kas_tepat_nol_boleh_dan_uang_masuk_tak_dicek():
    a = _akun("cash", 150000)
    assert run(KT.rencana_tx_manual(_C([a]), T, a["id"], _tx(), True))["blocks"] == []
    a0 = _akun("cash", 0)
    assert run(KT.rencana_tx_manual(_C([a0]), T, a0["id"], _tx("in"), True))["blocks"] == []


def test_baris_jurnal_arah_masuk_keluar():
    a = _akun("bank", 10 ** 9, coa=BANK)
    masuk = run(KT.rencana_tx_manual(_C([a]), T, a["id"], _tx("in", 5000), True))["baris"]
    keluar = run(KT.rencana_tx_manual(_C([a]), T, a["id"], _tx("out", 5000), True))["baris"]
    assert [(x[0], x[1], x[2]) for x in masuk] == [(BANK, D(5000), D(0)), (LAWAN, D(0), D(5000))]
    assert [(x[0], x[1], x[2]) for x in keluar] == [(LAWAN, D(5000), D(0)), (BANK, D(0), D(5000))]


def test_blok_lama_tetap_urut_piutang_akun_sama_periode_rekening():
    a = _akun("bank", 10 ** 9, coa=BANK)
    r = run(KT.rencana_tx_manual(_C([a], lawan_tipe="RECEIVABLE"), T, a["id"], _tx(), True))
    assert [b["code"] for b in r["blocks"]] == ["AKUN_LAWAN_PIUTANG_HUTANG"]
    sama = CreateManualTransactionRequest(direction="in", amount=1, transaction_date=HARI, description="x", contra_account_id=str(BANK))
    assert [b["code"] for b in run(KT.rencana_tx_manual(_C([a]), T, a["id"], sama, True))["blocks"]] == ["AKUN_LAWAN_SAMA"]
    tutup = {"id": uuid4(), "period_name": "Okt 2026", "status": "CLOSED"}
    assert [b["code"] for b in run(KT.rencana_tx_manual(_C([a], periode=tutup), T, a["id"], _tx(), True))["blocks"]] == ["PERIODE_TERTUTUP"]
    r = run(KT.rencana_tx_manual(_C([]), T, uuid4(), _tx(), True))
    assert [(b["code"], b["status"]) for b in r["blocks"]] == [("REKENING_TIDAK_DITEMUKAN", 404)]


# ---------------------------------------------------------------- hitung fisik
def test_hitung_fisik_jumlah_fisik_selisih_dihitung_server_saldo_per_tanggal():
    a = _akun("cash", 100000, coa=KAS)
    c = _C([a])
    r = run(KT.rencana_hitung_fisik(c, T, a["id"], AdjustBalanceRequest(adjustment_date=HARI, jumlah_fisik=80000, reason="hitung laci"), True))
    assert r["blocks"] == [] and r["selisih"] == D(-20000) and c.akun_args[0][2] == HARI
    assert [(x[0], x[1], x[2]) for x in r["baris"]] == [(ADJ_RUGI, D(20000), D(0)), (KAS, D(0), D(20000))]
    assert r["saldo"][0]["sesudah"] == D(80000)
    lebih = run(KT.rencana_hitung_fisik(_C([a]), T, a["id"], AdjustBalanceRequest(adjustment_date=HARI, jumlah_fisik=130000, reason="x"), True))
    assert [(x[0], x[1], x[2]) for x in lebih["baris"]] == [(KAS, D(30000), D(0)), (ADJ_UNTUNG, D(0), D(30000))]


def test_hitung_fisik_sama_saldo_selisih_nol_dan_bentuk_lama_semua_tanggal():
    a = _akun("cash", 100000)
    r = run(KT.rencana_hitung_fisik(_C([a]), T, a["id"], AdjustBalanceRequest(adjustment_date=HARI, jumlah_fisik=100000, reason="x"), True))
    assert [b["code"] for b in r["blocks"]] == ["SELISIH_NOL"]
    c = _C([a])
    lama = run(KT.rencana_hitung_fisik(c, T, a["id"], AdjustBalanceRequest(adjustment_date=HARI, adjustment_amount=-5000, reason="x"), True))
    assert lama["selisih"] == D(-5000) and c.akun_args[0][2] is None
    minus = run(KT.rencana_hitung_fisik(_C([a]), T, a["id"], AdjustBalanceRequest(adjustment_date=HARI, adjustment_amount=-150000, reason="x"), True))
    assert [b["code"] for b in minus["blocks"]] == ["SALDO_MINUS"]


def test_skema_hitung_fisik_satu_dari_dua():
    for x in ({}, {"jumlah_fisik": 1, "adjustment_amount": 1}):
        with pytest.raises(ValidationError):
            AdjustBalanceRequest(adjustment_date=HARI, reason="x", **x)
    with pytest.raises(ValidationError):
        AdjustBalanceRequest(adjustment_date=HARI, reason="x", jumlah_fisik=-1)


# ---------------------------------------------------------------- transfer
def _trf(asal, tujuan, jumlah=60000, biaya=0, status="draft"):
    return {"status": status, "amount": jumlah, "fee_amount": biaya, "total_amount": jumlah + biaya, "transfer_date": HARI, "transfer_number": "TRF-1",
            "from_bank_id": asal["id"], "from_name": asal["account_name"], "from_type": asal["account_type"], "from_balance": asal["saldo"],
            "from_coa_id": asal["coa_id"], "to_bank_id": tujuan["id"], "to_name": tujuan["account_name"], "to_coa_id": tujuan["coa_id"], "to_balance": tujuan["saldo"]}


def test_posting_transfer_kas_minus_bank_saldo_tidak_cukup_biaya_status():
    kas, bank, tujuan = _akun("cash", 50000), _akun("bank", 50000, nama="BCA"), _akun("bank", 0, nama="Mandiri")
    kode = lambda t: [b["code"] for b in run(KT.rencana_posting_transfer_id(_C(transfer=t), T, uuid4()))["blocks"]]  # noqa: E731
    assert kode(_trf(kas, tujuan)) == ["KAS_MINUS"]
    assert kode(_trf(bank, tujuan)) == ["SALDO_TIDAK_CUKUP"]
    assert kode(_trf(bank, tujuan, 10000, status="posted")) == ["TRANSFER_BUKAN_DRAF"]
    r = run(KT.rencana_posting_transfer_id(_C(transfer=_trf(bank, tujuan, 10000, 2500)), T, uuid4()))
    assert r["blocks"] == [] and [(x[0], x[1], x[2]) for x in r["baris"]] == [
        (tujuan["coa_id"], D(10000), D(0)), (FEE, D(2500), D(0)), (bank["coa_id"], D(0), D(12500))]
    assert [(s["sesudah"]) for s in r["saldo"]] == [D(37500), D(10000)]


def test_buat_transfer_tanpa_auto_post_tak_cek_saldo_dengan_auto_post_cek():
    kas, tujuan = _akun("cash", 50000), _akun("bank", 0, nama="Mandiri")
    body = dict(from_bank_id=str(kas["id"]), to_bank_id=str(tujuan["id"]), amount=60000, transfer_date=HARI)
    r = run(KT.rencana_buat_transfer(_C([kas, tujuan]), T, CreateBankTransferRequest(**body), True))
    assert r["blocks"] == [] and len(r["baris"]) == 2
    r = run(KT.rencana_buat_transfer(_C([kas, tujuan]), T, CreateBankTransferRequest(**body, auto_post=True), True))
    assert [b["code"] for b in r["blocks"]] == ["KAS_MINUS"]
    with pytest.raises(GalatBerkode):
        run(KT.rencana_buat_transfer(_C([kas, tujuan]), T, CreateBankTransferRequest(**body, auto_post=True), False))


def test_post_transfer_tulis_memakai_baris_penentu_dan_menolak_sebelum_jurnal():
    bank, tujuan = _akun("bank", 10 ** 6, nama="BCA"), _akun("bank", 0, nama="Mandiri")
    t = _trf(bank, tujuan, 10000, 2500)
    c = _C(transfer=t)
    run(RT._post_transfer(c, {"tenant_id": T, "user_id": uuid4()}, uuid4()))
    assert [tuple(x[:3]) for x in c.baris_jurnal()] == [(x[0], x[1], x[2]) for x in run(KT.rencana_posting_transfer_id(_C(transfer=t), T, uuid4()))["baris"]]
    kas = _akun("cash", 5000)
    c = _C(transfer=_trf(kas, tujuan, 10000))
    with pytest.raises(GalatBerkode):
        run(RT._post_transfer(c, {"tenant_id": T, "user_id": uuid4()}, uuid4()))
    assert not any("INSERT INTO journal_entries" in s for s, _ in c.tulis)


# ---------------------------------------------------------------- biaya (kasus TESTER EXP-2611-0003: Kas Kecil 1,5 jt, biaya 2 jt)
def test_biaya_dari_kas_kecil_melebihi_saldo_kas_minus():
    kk = _akun("petty_cash", 1500000, coa=KAS)
    c = _C([kk])
    with pytest.raises(GalatBerkode) as e:
        run(RE.create_expense_journal(c, T, uuid4(), "EXP-2611-0003", HARI, False, LAWAN, None, D(2000000), D(0), D(0), KAS, None))
    assert e.value.code == "KAS_MINUS" and "sekarang Rp 1.500.000" in e.value.detail
    assert not any("INSERT INTO journal_lines" in s and str(KAS) in a for s, a in c.tulis)  # baris kredit kas tak pernah ditulis
    bca = _akun("bank", 0, coa=BANK, nama="BCA")
    run(RE.create_expense_journal(_C([bca]), T, uuid4(), "EXP-1", HARI, False, LAWAN, None, D(2000000), D(0), D(0), BANK, None))  # bank boleh minus


def test_tinjau_biaya_posted_blok_kas_minus(monkeypatch):
    kk = _akun("petty_cash", 1500000, coa=KAS)
    c = _C([kk])

    async def pool():
        return _Pool(c)
    monkeypatch.setattr(RE, "get_pool", pool)
    from app.schemas.expenses import CreateExpenseRequest
    body = CreateExpenseRequest.model_construct(is_itemized=False, line_items=None, amount=2000000, tax_rate=D(0), pph_rate=D(0),
                                                status="posted", paid_through_id=kk["id"])
    out = run(RE.calculate_expense_totals(_Req(), body))
    assert [b["code"] for b in out["blocks"]] == ["KAS_MINUS"] and c.readonly == [True]
    body = CreateExpenseRequest.model_construct(**{**body.__dict__, "status": "draft"})
    assert run(RE.calculate_expense_totals(_Req(), body))["blocks"] == []


# ---------------------------------------------------------------- lewat RUTE: pratinjau NOL tulisan, tulis == pratinjau, idempotensi
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


class _Req:
    def __init__(self):
        class S:
            user = {"tenant_id": T, "user_id": "00000000-0000-0000-0000-000000000001"}
        self.state, self.headers = S(), {}


def _klien(c, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    async def pool():
        return _Pool(c)
    monkeypatch.setattr(RB, "get_pool", pool)
    monkeypatch.setattr(RT, "get_pool", pool)

    async def prasyarat(pool, tid):
        return None
    monkeypatch.setattr(RT, "_ensure_bank_transfers_role_preconditions", prasyarat)
    app = FastAPI()

    @app.middleware("http")
    async def _masuk(request, call_next):
        request.state.user = {"tenant_id": T, "user_id": "00000000-0000-0000-0000-000000000001"}
        return await call_next(request)
    from fastapi.responses import JSONResponse
    from app.services.galat_berkode import GalatBerkode

    @app.exception_handler(GalatBerkode)
    async def _gb(request, exc):  # sama dengan app/main.py
        return JSONResponse(status_code=exc.status_code, content=exc.badan(), headers=exc.headers)

    app.include_router(RB.router, prefix="/api/bank-accounts")
    app.include_router(RT.router, prefix="/api/bank-transfers")
    return TestClient(app, raise_server_exceptions=False)


def _json_tx(arah="out", jumlah=150000):
    return {"direction": arah, "amount": jumlah, "transaction_date": "2026-11-06", "description": "Beli galon", "contra_account_id": str(LAWAN)}


def test_rute_pratinjau_tx_readonly_nol_tulisan_lalu_tulis_baris_sama(monkeypatch):
    a = _akun("bank", 10 ** 6, coa=BANK, nama="BCA")
    c = _C([a])
    k = _klien(c, monkeypatch)
    r = k.post(f"/api/bank-accounts/{a['id']}/transactions/preview", json=_json_tx())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["ok"] is True and c.readonly == [True] and c.tulis == []
    assert [(j["account_id"], j["debit"], j["credit"]) for j in d["jurnal"]] == [(str(LAWAN), "150000.00", "0.00"), (str(BANK), "0.00", "150000.00")]
    assert d["saldo"][0]["sesudah"] == "850000.00" and d["saldo"][0]["minus"] is False
    w = k.post(f"/api/bank-accounts/{a['id']}/transactions", json=_json_tx())
    assert w.status_code == 200, w.text
    assert [(str(x[0]), f"{D(x[1]):.2f}", f"{D(x[2]):.2f}") for x in c.baris_jurnal()] == [(j["account_id"], j["debit"], j["credit"]) for j in d["jurnal"]]


def test_rute_tulis_kas_minus_400_berkode_tanpa_jurnal(monkeypatch):
    a = _akun("cash", 100000)
    c = _C([a])
    r = _klien(c, monkeypatch).post(f"/api/bank-accounts/{a['id']}/transactions", json=_json_tx())
    assert r.status_code == 400 and r.json()["code"] == "KAS_MINUS"
    assert not any("INSERT INTO journal_entries" in s for s, _ in c.tulis)


def test_rute_idempotensi_tap_kedua_jawaban_tersimpan_tanpa_jurnal(monkeypatch):
    a = _akun("bank", 10 ** 6, coa=BANK, nama="BCA")
    c = _C([a])
    dipanggil = []

    async def mulai_aksi(conn, ctx, kunci, prefix, doc_id, isi, response=None):
        dipanggil.append((kunci, prefix, str(doc_id)))
        return ("kp", "sd", {"success": True, "data": {"id": "lama"}}) if kunci == "k-2" else ("kp", "sd", None)
    monkeypatch.setattr(idem_buat, "mulai_aksi", mulai_aksi)
    k = _klien(c, monkeypatch)
    r = k.post(f"/api/bank-accounts/{a['id']}/transactions", json=_json_tx(), headers={"X-Idempotency-Key": "k-2"})
    assert r.json() == {"success": True, "data": {"id": "lama"}} and not any("journal" in s for s, _ in c.tulis)
    assert dipanggil == [("k-2", "BANK_TX_MANUAL", str(a["id"]))]
    for jalur, prefix in ((f"/api/bank-accounts/{a['id']}/adjust", "BANK_ADJUST"),):
        k.post(jalur, json={"adjustment_date": "2026-11-06", "jumlah_fisik": 1, "reason": "x"}, headers={"X-Idempotency-Key": "k-2"})
        assert dipanggil[-1][:2] == ("k-2", prefix)
    k.post(f"/api/bank-transfers/{uuid4()}/post", headers={"X-Idempotency-Key": "k-2"})
    assert dipanggil[-1][:2] == ("k-2", "BANK_TRANSFER_POST")


def test_rute_pratinjau_transfer_dan_hitung_fisik_terdaftar(monkeypatch):
    kas, tujuan = _akun("cash", 50000, coa=KAS), _akun("bank", 0, nama="Mandiri")
    c = _C([kas, tujuan])
    k = _klien(c, monkeypatch)
    r = k.post("/api/bank-transfers/preview", json={"from_bank_id": str(kas["id"]), "to_bank_id": str(tujuan["id"]), "amount": 60000,
                                                    "transfer_date": "2026-11-06", "auto_post": True})
    assert r.status_code == 200 and [b["code"] for b in r.json()["data"]["blocks"]] == ["KAS_MINUS"] and c.tulis == []
    r = k.post(f"/api/bank-accounts/{kas['id']}/adjust/preview", json={"adjustment_date": "2026-11-06", "jumlah_fisik": 45000, "reason": "laci"})
    assert r.status_code == 200 and r.json()["data"]["selisih"] == "-5000.00" and c.tulis == []
