"""P-U1 PEMBELIAN-SERAGAM (22 Okt 2026): G1 PO /receive (Decimal), G2 UM Pembelian akun PERAN, G4 lock+baca ulang+sisa dari jurnal.

Data produksi 0 baris -> dibuktikan dengan tiruan koneksi yang MENCATAT URUTAN panggilan (lock sebelum baca), plus harness nyata
(uji-nyata-pembelian-p0): sintetis CTE untuk SQL sisa jurnal + akun peran != PPN Masukan di semua tenant."""
import asyncio
import inspect
import os
import re
import uuid
from datetime import date
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import purchase_orders as PO, vendor_deposits as VD  # noqa: E402
from app.schemas import purchase_orders as PS, vendor_deposits as VS  # noqa: E402
from app.services import uang_muka_vendor as UMV  # noqa: E402
from app.services.role_resolver import AccountRole, AccountRoleUnmappedError  # noqa: E402

T = "kaos-biru-konveksi"
DID = uuid.uuid4()
BILL = uuid.uuid4()
AP, PREPAID, KAS = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class _Tx:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Req:
    class state:
        user = {"tenant_id": T, "user_id": str(uuid.uuid4())}


class Kon:
    def __init__(self, vd=None, sisa=Decimal("0"), po=None, poi=None, today=date(2026, 10, 22)):
        self.vd, self.sisa, self.po, self.poi, self.today = vd, sisa, po, poi or [], today
        self.log, self.updates = [], []

    def transaction(self): return _Tx()

    async def execute(self, sql, *a):
        self.log.append(("exec", " ".join(sql.split())[:90]))
        if sql.lstrip().startswith("UPDATE purchase_order_items"):
            self.updates.append(a)
            for r in self.poi:
                if str(r["id"]) == str(a[0]): r["quantity_received"] = a[1]
        return "OK"

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split())
        self.log.append(("fetchrow", q[:90]))
        if "FROM purchase_orders" in q: return self.po
        if "FROM purchase_order_items" in q and "WHERE id = $1" in q:
            return next((r for r in self.poi if str(r["id"]) == str(a[0])), None)
        if "remaining_amount FROM vendor_deposits" in q: return {"remaining_amount": 0}
        if "amount - COALESCE" in q: return {"remaining": 0}
        if "FROM vendor_deposits" in q: return self.vd
        if "FROM bank_accounts" in q: return None
        if "FROM bills" in q:
            return {"id": BILL, "status": "posted", "invoice_number": "B-1"}
        if "fiscal_periods" in q: return None
        if "INSERT INTO journal_entries" in q: return {"id": uuid.uuid4(), "journal_number": "JV-1"}
        if "INSERT INTO vendor_deposit_applications" in q: return {"id": uuid.uuid4()}
        if "INSERT INTO vendor_deposit_refunds" in q:
            return {"id": uuid.uuid4(), "vendor_deposit_id": DID, "refund_date": date(2026, 10, 22), "amount": 100, "bank_account_id": None,
                    "reference": None, "journal_id": uuid.uuid4(), "notes": None, "created_at": date(2026, 10, 22), "created_by": None}
        if "remaining_amount FROM vendor_deposits" in q: return {"remaining_amount": 0}
        if "amount - COALESCE" in q: return {"remaining": 0}
        return None

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        self.log.append(("fetch", q[:90]))
        if "FROM purchase_order_items" in q: return self.poi
        return []

    async def fetchval(self, sql, *a):
        q = " ".join(sql.split())
        self.log.append(("fetchval", q[:90]))
        if "get_next_journal_number" in q: return "JV-1"
        if "SUM(jl.debit - jl.credit)" in q: return self.sisa
        if "COALESCE(SUM(" in q and "quantity_received" in q: return 0
        return None


def _pool(monkeypatch, mod, kon):
    class CM:
        async def __aenter__(s): return kon
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    monkeypatch.setattr(mod, "get_pool", gp)


@pytest.fixture
def peran(monkeypatch):
    async def akun(conn, tid, p): return {AccountRole.AP_TRADE: AP, AccountRole.AP_PREPAID: PREPAID, AccountRole.CASH_GENERAL: KAS}[p]
    monkeypatch.setattr(UMV, "akun_peran", akun)
    async def sisa_bill(conn, tid, bid): return 10_000_000
    monkeypatch.setattr(VD, "get_bill_remaining_from_journal", sisa_bill)
    async def seg(*a, **k): return None
    monkeypatch.setattr(VD, "segarkan_cache_hutang_tagihan", seg)


# ---------------------------------------------------------------- G1: PO /receive
def _po(status="sent"):
    return {"id": uuid.uuid4(), "status": status, "po_date": date(2026, 10, 1), "po_number": "PO-1"}


def _poi(qty="10", recv="0"):
    return {"id": uuid.uuid4(), "quantity": Decimal(qty), "quantity_received": Decimal(recv), "total": 1000}


def _terima(monkeypatch, kon, qty, tgl=date(2026, 10, 22)):
    _pool(monkeypatch, PO, kon)
    async def hari(conn, tid): return kon.today
    monkeypatch.setattr(PO, "tanggal_dokumen", hari)
    item = kon.poi[0]
    body = PS.ReceiveGoodsRequest(receive_date=tgl, items=[PS.ReceiveItemRequest(po_item_id=str(item["id"]), quantity_received=qty)])
    return asyncio.run(PO.receive_goods(_Req(), kon.po["id"], body))


def test_skema_terima_decimal_bukan_float():
    f = PS.ReceiveItemRequest.model_fields["quantity_received"]
    assert f.annotation is Decimal
    assert isinstance(PS.ReceiveItemRequest(po_item_id="x", quantity_received="2.5").quantity_received, Decimal)


def test_receive_decimal_tidak_lagi_typeerror_dan_status_benar(monkeypatch):
    kon = Kon(po=_po(), poi=[_poi("10", "0")])
    kon.poi[0]["quantity_received"] = Decimal("3")
    r = _terima(monkeypatch, kon, Decimal("2.5"))
    assert r["success"] is True and r["data"]["items_received"][0]["total_received"] == Decimal("5.5")
    assert r["data"]["status"] == "partial_received"
    assert kon.updates[0][1] == Decimal("5.5")


def test_receive_penuh_menjadi_received(monkeypatch):
    kon = Kon(po=_po(), poi=[_poi("10", "7")])
    r = _terima(monkeypatch, kon, Decimal("3"))
    assert r["data"]["status"] == "received"


def test_receive_melebihi_sisa_ditolak_indonesia(monkeypatch):
    kon = Kon(po=_po(), poi=[_poi("10", "9")])
    with pytest.raises(HTTPException) as e:
        _terima(monkeypatch, kon, Decimal("2"))
    assert e.value.status_code == 400 and "melebihi sisa" in e.value.detail


def test_receive_tanggal_masa_depan_dan_sebelum_po_ditolak(monkeypatch):
    kon = Kon(po=_po(), poi=[_poi()])
    with pytest.raises(HTTPException) as e:
        _terima(monkeypatch, kon, Decimal("1"), tgl=date(2026, 10, 30))
    assert "masa depan" in e.value.detail
    with pytest.raises(HTTPException) as e:
        _terima(monkeypatch, Kon(po=_po(), poi=[_poi()]), Decimal("1"), tgl=date(2026, 9, 1))
    assert "sebelum tanggal pesanan" in e.value.detail


def test_receive_po_dan_baris_dikunci_for_update():
    src = inspect.getsource(PO.receive_goods)
    assert len(re.findall(r"\n\s+FOR UPDATE\n", src)) == 2   # PO + baris
    assert "Decimal(str(recv_item.quantity_received))" in src and "float(" not in src


def test_kontrol_merah_kode_lama_memang_typeerror():
    with pytest.raises(TypeError):
        Decimal("0") + 2.0                                   # bentuk lama: Decimal + float


# ---------------------------------------------------------------- G2: akun PERAN, tanpa hardcode
def test_vendor_deposits_tanpa_kode_akun_hardcode():
    src = inspect.getsource(VD)
    for kode in ("1-10800", "2-10100", "1-10100"):
        assert not re.search(rf"[\"']{kode}[\"']", src.replace("'1-10800' = PPN", "")), kode
    assert "seed_vendor_deposit_account" not in src
    assert "resolve_account_id" not in src


def test_peran_belum_dipetakan_409_berkode(monkeypatch):
    async def gagal(conn, tid, p): raise AccountRoleUnmappedError("x")
    monkeypatch.setattr(UMV, "resolve_account_id_by_role", gagal)
    with pytest.raises(HTTPException) as e:
        asyncio.run(UMV.akun_peran(None, T, AccountRole.AP_PREPAID))
    assert e.value.status_code == 409 and e.value.detail["code"] == "PERAN_AKUN_BELUM_DIPETAKAN"
    assert "Uang Muka Pembelian" in e.value.detail["message"]


def _vd(status="draft", remaining=1000):
    return {"id": DID, "tenant_id": T, "status": status, "deposit_number": "VD-1", "deposit_date": date(2026, 10, 22), "amount": 1000,
            "bank_account_id": None, "vendor_id": uuid.uuid4(), "remaining_amount": remaining, "reference": None}


def test_post_memakai_peran_uang_muka_bukan_kode(monkeypatch, peran):
    kon = Kon(vd=_vd("draft"))
    _pool(monkeypatch, VD, kon)
    dipakai = []
    async def akun(conn, tid, p):
        dipakai.append(p); return {AccountRole.AP_PREPAID: PREPAID, AccountRole.CASH_GENERAL: KAS}[p]
    monkeypatch.setattr(UMV, "akun_peran", akun)
    asyncio.run(VD.post_vendor_deposit(_Req(), DID))
    assert AccountRole.AP_PREPAID in dipakai and AccountRole.CASH_GENERAL in dipakai


# ---------------------------------------------------------------- G4: lock sebelum baca, baca ulang di dalam, sisa dari jurnal
def _urut(kon):
    """indeks (lock saldo/deposit) dan (baca vendor_deposits FOR UPDATE) dalam log."""
    i_lock = next(i for i, (k, q) in enumerate(kon.log) if k == "exec" and "pg_advisory_xact_lock" in q)
    i_baca = next(i for i, (k, q) in enumerate(kon.log) if k == "fetchrow" and "FROM vendor_deposits" in q)
    return i_lock, i_baca


def test_post_lock_sebelum_baca_dan_cek_status_di_dalam(monkeypatch, peran):
    kon = Kon(vd=_vd("draft"))
    _pool(monkeypatch, VD, kon)
    asyncio.run(VD.post_vendor_deposit(_Req(), DID))
    i_lock, i_baca = _urut(kon)
    assert i_lock < i_baca, "lock harus SEBELUM baca (dulu dibaca di luar lock -> double-post)"
    assert "FOR UPDATE" in kon.log[i_baca][1]


def test_post_kedua_ditolak_karena_status_dibaca_ulang(monkeypatch, peran):
    kon = Kon(vd=_vd("posted"))                              # keadaan sesudah post pertama
    _pool(monkeypatch, VD, kon)
    with pytest.raises(HTTPException) as e:
        asyncio.run(VD.post_vendor_deposit(_Req(), DID))
    assert e.value.status_code == 400 and "sudah diposting" in e.value.detail
    assert not any("INSERT INTO journal_entries" in q for k, q in kon.log)    # nol jurnal kedua


def test_apply_melebihi_sisa_JURNAL_ditolak_walau_cache_besar(monkeypatch, peran):
    kon = Kon(vd=_vd("posted", remaining=1000), sisa=Decimal("100"))          # cache bilang 1000, jurnal bilang 100
    _pool(monkeypatch, VD, kon)
    async def hari(conn, tid): return date(2026, 10, 22)
    monkeypatch.setattr(VD, "tanggal_dokumen", hari)
    with pytest.raises(HTTPException) as e:
        asyncio.run(VD.apply_vendor_deposit(_Req(), DID, VS.ApplyDepositRequest(bill_id=BILL, amount=500)))
    assert e.value.status_code == 400 and "Saldo uang muka tidak cukup" in e.value.detail
    assert not any("INSERT INTO journal_entries" in q for k, q in kon.log)


def test_apply_lock_saldo_sebelum_baca_dan_jurnal_ditulis_bila_cukup(monkeypatch, peran):
    kon = Kon(vd=_vd("posted", remaining=1000), sisa=Decimal("1000"))
    _pool(monkeypatch, VD, kon)
    async def hari(conn, tid): return date(2026, 10, 22)
    monkeypatch.setattr(VD, "tanggal_dokumen", hari)
    asyncio.run(VD.apply_vendor_deposit(_Req(), DID, VS.ApplyDepositRequest(bill_id=BILL, amount=500)))
    i_lock, i_baca = _urut(kon)
    assert i_lock < i_baca
    locks = [q for k, q in kon.log if k == "exec" and "pg_advisory_xact_lock" in q]
    assert len(locks) == 2                                                    # SALDO bersama apply/refund + kunci apply
    assert any("INSERT INTO journal_entries" in q for k, q in kon.log)


def test_refund_melebihi_sisa_jurnal_ditolak(monkeypatch, peran):
    kon = Kon(vd=_vd("posted", remaining=1000), sisa=Decimal("50"))
    _pool(monkeypatch, VD, kon)
    with pytest.raises(HTTPException) as e:
        asyncio.run(VD.refund_vendor_deposit(_Req(), DID, VS.VendorDepositRefundCreate(refund_date=date(2026, 10, 22), amount=500)))
    assert e.value.status_code == 400 and "Saldo uang muka tidak cukup" in e.value.detail


def test_apply_dan_refund_memakai_kunci_saldo_yang_sama():
    for fn in (VD.apply_vendor_deposit, VD.refund_vendor_deposit):
        assert "VENDOR_DEPOSIT_SALDO:{deposit_id}" in inspect.getsource(fn)


def test_sql_sisa_jurnal_bentuk():
    q = " ".join(UMV.SQL_SISA_JURNAL.split())
    assert "je.tenant_id = $1" in q and "je.source_type = ANY($3::text[])" in q and "jl.account_id = $4" in q
    assert "je.reversed_by_id IS NULL" in q and "je.reversal_of_id IS NULL" in q
    assert UMV.SUMBER_JURNAL == ("VENDOR_DEPOSIT", "DEPOSIT_APPLICATION", "DEPOSIT_REFUND")
