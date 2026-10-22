"""CW penuh Uang Muka Pembelian (22 Okt 2026): pratinjau create/post/apply/unapply/refund/void/delete + history + applicable-bills.
Penentu = services/uang_muka_vendor_rencana, dipakai TULIS (sesudah kunci) dan PRATINJAU (read-only tanpa kunci). Uji: blok per aksi,
semua blok dilaporkan, pratinjau readonly tanpa FOR UPDATE/advisory & nol tulisan, jurnal RENCANA dari peran, tulis memakai penentu."""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal as D

import pytest
from fastapi import HTTPException

from app.routers import vendor_deposits as VD
from app.schemas import vendor_deposits as VS
from app.services import uang_muka_vendor as UMV, uang_muka_vendor_daftar as UMD, uang_muka_vendor_rencana as R
from app.services.role_resolver import AccountRole

T = "kaos-biru-konveksi"
DID, VID, BILL, APP, PO = (uuid.uuid4() for _ in range(5))
AP, UM, KAS, BCA_COA, BCA = (uuid.uuid4() for _ in range(5))
HARI = date(2026, 10, 22)


class _Tr:
    def __init__(self, k, readonly=False): self.k = k; k.readonly.append(readonly)
    async def start(self): pass
    async def rollback(self): self.k.rollback += 1
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Req:
    class state:
        user = {"tenant_id": T, "user_id": str(uuid.uuid4())}


class Kon:
    def __init__(self, status="posted", sisa=D("1000"), sisa_tagihan=D("600"), bill=True, vendor=True, po=None, bank=True,
                 app_status="active", bergantung=(0, 0), periode=None):
        self.vd = {"id": DID, "tenant_id": T, "status": status, "deposit_number": "VD-1", "deposit_date": HARI, "amount": D("1000"),
                   "vendor_id": VID, "bank_account_id": BCA if bank else None, "journal_id": uuid.uuid4() if status != "draft" else None,
                   "reference": None, "purchase_order_id": None}
        self.sisa, self.sisa_t, self.bill, self.vendor, self.po, self.bank = sisa, sisa_tagihan, bill, vendor, po, bank
        self.app_status, self.bergantung, self.periode = app_status, bergantung, periode
        self.q, self.writes, self.readonly, self.rollback = [], [], [], 0

    def transaction(self, readonly=False): return _Tr(self, readonly)

    def _c(self, sql):
        q = " ".join(sql.split()); self.q.append(q)
        if q.startswith(("INSERT", "UPDATE", "DELETE")): self.writes.append(q[:60])
        return q

    async def execute(self, sql, *a):
        self._c(sql); return "OK"

    async def fetchrow(self, sql, *a):
        q = self._c(sql)
        if "FROM vendor_deposits" in q: return self.vd
        if "FROM vendors" in q: return {"id": VID, "name": "CV X"} if self.vendor else None
        if "FROM purchase_orders" in q: return self.po
        if "FROM bank_accounts" in q and "coa_id" in q: return {"id": BCA, "coa_id": BCA_COA} if self.bank else None
        if "FROM bills" in q: return {"id": BILL, "invoice_number": "BILL-1", "status": "posted"} if self.bill else None
        if "FROM vendor_deposit_applications a" in q:
            return {"id": APP, "bill_id": BILL, "amount": D("400"), "journal_id": uuid.uuid4(), "status": self.app_status, "bill_number": "BILL-1"}
        if "FROM journal_entries" in q: return {"id": uuid.uuid4(), "status": "POSTED", "reversed_by_id": None}
        if "fiscal_periods" in q: return self.periode
        if "n_terap" in q: return {"n_terap": self.bergantung[0], "n_refund": self.bergantung[1]}
        if "FROM chart_of_accounts" in q and "account_code" in q:
            return {"account_code": {AP: "2-10100", UM: "1-10550", KAS: "1-10100", BCA_COA: "1-10210"}.get(a[0], "?"), "name": "Akun"}
        if "FROM bank_transactions" in q: return None
        return None

    async def fetch(self, sql, *a):
        q = self._c(sql)
        if "FROM journal_lines" in q:
            return [{"account_id": AP, "debit": D("400"), "credit": D("0"), "memo": None}, {"account_id": UM, "debit": D("0"), "credit": D("400"), "memo": None}]
        if "vd.amount, ag.sisa" in q:  # fakta_vd
            return [{"id": DID, "status": self.vd["status"], "amount": D("1000"), "sisa": self.sisa, "terpakai": D("0"), "dikembalikan": D("0")}]
        return []

    async def fetchval(self, sql, *a):
        q = self._c(sql)
        if "compute_ap_outstanding" in q: return self.sisa_t
        if "SUM(jl.debit - jl.credit)" in q: return self.sisa
        if "FROM bank_accounts" in q and "account_name" in q: return "BCA"
        if "FROM bank_accounts WHERE coa_id" in q: return None
        return None


@pytest.fixture
def kon(monkeypatch):
    async def akun(conn, tid, p): return {AccountRole.AP_TRADE: AP, AccountRole.AP_PREPAID: UM, AccountRole.CASH_GENERAL: KAS}[p]
    monkeypatch.setattr(UMV, "akun_peran", akun)
    async def akun_baca(conn, tid): return UM
    monkeypatch.setattr(UMD, "akun_baca", akun_baca)
    async def hari(conn, tid): return HARI
    monkeypatch.setattr(VD, "tanggal_dokumen", hari)
    async def rekon(conn, tid, did): return None
    import app.services.jaga_rekonsiliasi as JR
    monkeypatch.setattr(JR, "tolak_void_bila_terekonsiliasi", rekon)
    def pasang(k):
        class CM:
            async def __aenter__(s): return k
            async def __aexit__(s, *a): return False
        class P:
            def acquire(s): return CM()
        async def gp(): return P()
        monkeypatch.setattr(VD, "get_pool", gp)
        return k
    return pasang


def run(c): return asyncio.run(c)


def _tanpa_kunci(k):
    assert k.readonly == [True] and k.rollback == 1 and k.writes == [], (k.readonly, k.rollback, k.writes)
    assert not any("FOR UPDATE" in q or "pg_advisory" in q for q in k.q), [q for q in k.q if "FOR UPDATE" in q or "pg_advisory" in q]


# ---------------------------------------------------------------- bentuk + read-only
def test_apply_preview_jurnal_rencana_dan_sisa_tagihan(kon):
    k = kon(Kon())
    d = run(VD.preview_apply_vendor_deposit(_Req(), DID, VS.ApplyDepositRequest(bill_id=BILL, amount="250.50")))["data"]
    _tanpa_kunci(k)
    assert d["action"] == "apply" and d["can_proceed"] and d["preview"] is True and d["blocks"] == []
    j = d["journals"][0]
    assert j["journal_number"] is None and j["source_type"] == "DEPOSIT_APPLICATION"
    assert [(l["account_code"], l["debit"], l["credit"]) for l in j["lines"]] == [("2-10100", 250.5, 0.0), ("1-10550", 0.0, 250.5)]
    assert d["bills"] == [{"bill_id": str(BILL), "bill_number": "BILL-1", "remaining_before": 600.0, "remaining_after": 349.5}]
    assert d["before"]["remaining"] == 1000.0 and d["after"]["remaining"] == 749.5
    assert d["payload"] == {"bill_id": str(BILL), "amount": "250.50", "applied_date": None}


def test_apply_preview_semua_blok(kon):
    kon(Kon(status="draft", bill=False, periode={"status": "CLOSED"}))
    d = run(VD.preview_apply_vendor_deposit(_Req(), DID, VS.ApplyDepositRequest(bill_id=BILL, amount="10")))["data"]
    assert [b["code"] for b in d["blocks"]] == ["BELUM_DIPOSTING", "TAGIHAN_TIDAK_DITEMUKAN", "PERIODE_TERTUTUP"]
    assert not d["can_proceed"]


def test_apply_tulis_menolak_dengan_blok_pertama_pesan_sama(kon):
    kon(Kon(sisa=D("100")))
    p = run(VD.preview_apply_vendor_deposit(_Req(), DID, VS.ApplyDepositRequest(bill_id=BILL, amount="500")))["data"]
    kon(Kon(sisa=D("100")))
    with pytest.raises(HTTPException) as e:
        run(VD.apply_vendor_deposit(_Req(), DID, VS.ApplyDepositRequest(bill_id=BILL, amount="500")))
    assert e.value.status_code == 400 and e.value.detail == p["blocks"][0]["message"] == "Saldo uang muka tidak cukup. Tersedia: 100"


def test_post_preview_bank_cermin(kon):
    k = kon(Kon(status="draft"))
    d = run(VD.preview_post_vendor_deposit(_Req(), DID))["data"]
    _tanpa_kunci(k)
    assert d["can_proceed"] and [(l["account_code"], l["debit"], l["credit"]) for l in d["journals"][0]["lines"]] == [("1-10550", 1000.0, 0.0), ("1-10210", 0.0, 1000.0)]
    assert d["bank_transactions"] == [{"bank_account": "BCA", "amount": -1000.0, "transaction_type": "withdrawal"}]
    assert d["after"]["status"] == "posted"


def test_post_preview_sudah_diposting(kon):
    kon(Kon(status="posted"))
    d = run(VD.preview_post_vendor_deposit(_Req(), DID))["data"]
    assert [b["code"] for b in d["blocks"]] == ["SUDAH_DIPOSTING"] and d["blocks"][0]["message"] == "Uang muka ini sudah diposting."


def test_refund_preview_rekening_wajib(kon):
    kon(Kon())
    d = run(VD.preview_refund_vendor_deposit(_Req(), DID, VS.VendorDepositRefundCreate(refund_date=HARI, amount="10")))["data"]
    assert [b["code"] for b in d["blocks"]] == ["REKENING_WAJIB"] and d["journals"] == []
    kon(Kon())
    d = run(VD.preview_refund_vendor_deposit(_Req(), DID, VS.VendorDepositRefundCreate(refund_date=HARI, amount="10", bank_account_id=BCA)))["data"]
    assert d["can_proceed"] and [(l["account_code"], l["debit"]) for l in d["journals"][0]["lines"]] == [("1-10210", 10.0), ("1-10550", 0.0)]
    assert d["bank_transactions"][0]["transaction_type"] == "deposit"


def test_void_preview_alasan_wajib_dan_masih_dipakai(kon):
    kon(Kon(bergantung=(1, 0)))
    d = run(VD.preview_void_vendor_deposit(_Req(), DID, None))["data"]
    assert [b["code"] for b in d["blocks"]] == ["VOID_REASON_REQUIRED", "UANG_MUKA_MASIH_DIPAKAI"]
    kon(Kon())
    d = run(VD.preview_void_vendor_deposit(_Req(), DID, VS.VoidDepositRequest(reason="salah input")))["data"]
    assert d["can_proceed"] and d["after"]["status"] == "void" and d["payload"] == {"reason": "salah input"}
    assert [(l["debit"], l["credit"]) for l in d["journals"][0]["lines"]] == [(0.0, 400.0), (400.0, 0.0)]  # pembalik jurnal posting


def test_unapply_preview_dan_sudah_dilepas(kon):
    k = kon(Kon())
    d = run(VD.preview_unapply_vendor_deposit(_Req(), DID, APP))["data"]
    _tanpa_kunci(k)
    assert d["can_proceed"] and [(l["account_code"], l["debit"], l["credit"]) for l in d["journals"][0]["lines"]] == [("2-10100", 0.0, 400.0), ("1-10550", 400.0, 0.0)]
    assert d["bills"][0]["remaining_after"] == 1000.0
    kon(Kon(app_status="reversed"))
    d = run(VD.preview_unapply_vendor_deposit(_Req(), DID, APP))["data"]
    assert [b["code"] for b in d["blocks"]] == ["SUDAH_DILEPAS"] and d["journals"] == []


def test_delete_preview_hanya_draf(kon):
    kon(Kon(status="draft"))
    d = run(VD.preview_delete_vendor_deposit(_Req(), DID))["data"]
    assert d["can_proceed"] and d["after"] == {"exists": False} and d["payload"] is None
    kon(Kon(status="posted"))
    assert [b["code"] for b in run(VD.preview_delete_vendor_deposit(_Req(), DID))["data"]["blocks"]] == ["BUKAN_DRAF"]


def test_create_preview_vendor_po_dan_simpan_posting(kon):
    k = kon(Kon(vendor=False, po={"vendor_id": uuid.uuid4(), "status": "sent"}))
    body = VS.VendorDepositCreate(deposit_date=HARI, vendor_id=VID, amount="1000.50", purchase_order_id=PO)
    d = run(VD.preview_create_vendor_deposit(_Req(), body))["data"]
    _tanpa_kunci(k)
    assert [b["code"] for b in d["blocks"]] == ["VENDOR_TIDAK_DITEMUKAN", "PO_BEDA_VENDOR"] and d["deposit_id"] is None
    kon(Kon())
    body = VS.VendorDepositCreate(deposit_date=HARI, vendor_id=VID, amount="1000.50", bank_account_id=BCA, simpan_dan_posting=True)
    d = run(VD.preview_create_vendor_deposit(_Req(), body))["data"]
    assert d["can_proceed"] and d["after"]["status"] == "posted" and d["after"]["remaining"] == 1000.5 and len(d["journals"]) == 1


def test_preview_404(kon):
    k = Kon(); k.vd = None
    kon(k)
    with pytest.raises(HTTPException) as e:
        run(VD.preview_post_vendor_deposit(_Req(), DID))
    assert e.value.status_code == 404


# ---------------------------------------------------------------- tulis memakai penentu
def test_tulis_memakai_penentu_bersama():
    src = {n: inspect.getsource(getattr(VD, n)) for n in ("create_vendor_deposit", "_posting_inti", "apply_vendor_deposit",
                                                          "unapply_vendor_deposit", "refund_vendor_deposit", "void_vendor_deposit",
                                                          "delete_vendor_deposit")}
    assert "R.rencana_buat(" in src["create_vendor_deposit"] and "_posting_inti(conn, ctx, row[\"id\"])" in src["create_vendor_deposit"]
    assert "R.rencana_posting(" in src["_posting_inti"] and "R.rencana_terapkan(" in src["apply_vendor_deposit"]
    from app.services import uang_muka_vendor_lepas as L
    lep = inspect.getsource(L.lepas_penerapan)
    assert "lepas_penerapan(conn, ctx, deposit_id, application_id)" in src["unapply_vendor_deposit"]
    assert "R.rencana_lepas(" in lep and "kunci=True" in lep
    assert "R.rencana_kembali(" in src["refund_vendor_deposit"] and 'rk["akun_kas"]' in src["refund_vendor_deposit"]
    assert "R.rencana_batal(" in src["void_vendor_deposit"] and "VD_VOIDED" in src["void_vendor_deposit"]
    assert "R.rencana_hapus(" in src["delete_vendor_deposit"] and "status = 'draft'" in src["delete_vendor_deposit"]
    assert inspect.getsource(VD.post_vendor_deposit).count("_posting_inti(conn, ctx, deposit_id)") == 1


def test_skema_baru():
    assert VS.VendorDepositCreate.model_fields["simpan_dan_posting"].default is False
    assert "account_id" in VS.VendorDepositRefundCreate.model_fields
    assert VS.VoidDepositRequest(reason=None).reason is None
