"""POST /sales-orders/{id}/receive-payment/preview (29 Sep 2026, MASTER/WORKSPACE, halaman CW "Terima pelunasan").

Rencana (services/so_pelunasan.py): faktur TERBIT SO, sisa dari compute_ar_outstanding, alokasi TERTUA-DULU, SEMUA
penghalang rencana; lalu `payload` dijalankan lewat inti create yang SAMA (receive_payments.buat_penerimaan) di
transaksi yang SELALU di-ROLLBACK. Kelebihan bayar = uang muka (aturan create), bukan penghalang.
"""
import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.services import so_pelunasan as SP  # noqa: E402
from app.routers import sales_orders as SO  # noqa: E402
from app.routers import receive_payments as RP  # noqa: E402
from app.routers import customer_deposits as CD  # noqa: E402
from app.utils import tanggal_tenant as TT  # noqa: E402

T = "kaos-biru-konveksi"
SOID = UUID("10000000-0000-0000-0000-0000000000e1")
CUST = UUID("40000000-0000-0000-0000-0000000000c1")
I1, I2, I3, IX = (UUID(f"6000000{i}-0000-0000-0000-000000000001") for i in range(4))
BA1, BA2 = UUID("70000000-0000-0000-0000-0000000000b1"), UUID("70000000-0000-0000-0000-0000000000b2")
HARI = date(2026, 9, 29)


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": "00000000-0000-0000-0000-0000000000a1",
                                                       "tenant_id": T, "role": "OWNER"}))


class _Tx:
    def __init__(self, c):
        self.c = c

    async def start(self):
        self.c.tx.append("start")

    async def rollback(self):
        self.c.tx.append("rollback")

    async def __aenter__(self):
        self.c.tx.append("sp")
        return self.c

    async def __aexit__(self, et, *a):
        self.c.tx.append("sp-rollback" if et else "sp-release")
        return False


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


def _fak():
    # urutan dari DB sudah tertua-dulu (ORDER BY invoice_date, invoice_number, id)
    return [
        {"id": I1, "invoice_number": "INV-1", "invoice_date": date(2026, 9, 1), "due_date": date(2026, 9, 15), "status": "partial"},
        {"id": I2, "invoice_number": "INV-2", "invoice_date": date(2026, 9, 10), "due_date": None, "status": "posted"},
        {"id": I3, "invoice_number": "INV-3", "invoice_date": date(2026, 9, 20), "due_date": None, "status": "draft"},
    ]


class _C:
    def __init__(self, fak=None, sisa=None, sisa_sesudah=None, rek=None, so_rek=None, cust=CUST, ada=True):
        self.fak = _fak() if fak is None else fak
        self.sisa = {I1: Decimal("300000"), I2: Decimal("500000")} if sisa is None else sisa
        self.sisa_sesudah = sisa_sesudah
        self.rek = rek if rek is not None else [
            {"id": BA1, "coa_id": UUID(int=11), "account_name": "BCA", "bank_name": "BCA", "account_number": "123-456",
             "account_type": "bank", "is_active": True, "is_default": True}]
        self.so_rek, self.cust, self.ada = so_rek, cust, ada
        self.tx, self.q, self.tulis = [], [], []
        self.inti_jalan = False

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql, *a):
        self.q.append(sql)
        if "pg_advisory" not in sql and "set_config" not in sql:
            self.tulis.append(sql)

    async def fetchrow(self, sql, *a):
        self.q.append(sql)
        if "FROM sales_orders" in sql:
            assert a[1] == T
            return ({"id": SOID, "order_number": "SO-7", "status": "invoiced", "customer_id": self.cust,
                     "customer_name": "Budi", "payment_account_number": self.so_rek} if self.ada else None)
        if "FROM bank_accounts" in sql:
            u = a[1]
            return next((r for r in self.rek if u in (r["id"], r["coa_id"])), None)
        if "FROM chart_of_accounts" in sql:
            return None
        if "FROM receive_payments rp" in sql:
            return {"payment_method": "bank_transfer", "created_deposit_id": None, "deposit_number": self.ovp}
        raise AssertionError(sql[:60])

    async def fetch(self, sql, *a):
        self.q.append(sql)
        if "FROM sales_invoices" in sql:
            assert a[0] == T and "sales_order_id = $2" in sql
            assert "ORDER BY invoice_date, invoice_number, id" in sql  # tertua-dulu ditentukan SQL
            return list(self.fak)
        if "compute_ar_outstanding" in sql:
            src = self.sisa_sesudah if (self.inti_jalan and self.sisa_sesudah is not None) else self.sisa
            return [{"invoice_id": i, "outstanding": src[i]} for i in a[1] if i in src]
        if "FROM bank_accounts" in sql:
            return [r for r in self.rek if r["is_active"]]
        raise AssertionError(sql[:60])

    ovp = None


@pytest.fixture
def pasang(monkeypatch):
    tangkap = {}

    def _p(c, dp=(), periode_tutup=False, inti=None):
        async def tgl(conn, tid):
            return HARI
        monkeypatch.setattr(TT, "tanggal_dokumen", tgl)

        async def periode(conn, tid, d):
            if periode_tutup:
                raise HTTPException(status_code=400, detail="Periode akuntansi sudah CLOSED")
        monkeypatch.setattr(RP, "check_period_is_open", periode)

        async def dps(conn, tid, sid):
            return [{"id": UUID(int=99), "deposit_number": "DP-9"}] if dp else []

        async def sisa_dp(conn, tid, did):
            return dp[0]
        monkeypatch.setattr(CD, "linked_so_deposits", dps)
        monkeypatch.setattr(CD, "compute_deposit_remaining", sisa_dp)

        async def buat(conn, ctx, body):
            tangkap["body"] = body
            conn.inti_jalan = True
            if inti:
                return await inti(conn, ctx, body)
            return {"data": {"id": str(UUID(int=5)), "payment_number": "RCV-2609-0009",
                             "allocated_amount": sum(a.amount_applied for a in body.allocations)}}
        monkeypatch.setattr(RP, "buat_penerimaan", buat)

        async def prasyarat(*a, **k):
            return None
        monkeypatch.setattr(RP, "_ensure_receive_payments_role_preconditions", prasyarat)

        async def pool():
            return _Pool(c)
        monkeypatch.setattr(SO, "get_pool", pool)
        return c
    _p.tangkap = tangkap
    return _p


async def _pv(**badan):
    r = await SO.preview_receive_payment_from_order(_req(), str(SOID), SP.SOReceivePaymentPreviewRequest(**badan))
    return r["data"]


# ---------- alokasi ----------

def test_alokasi_tertua_dulu_literal():
    f = [{"remaining": Decimal("300000")}, {"remaining": Decimal("500000")}, {"remaining": Decimal("0")}]
    assert SP.alokasi_tertua_dulu(f, Decimal("450000")) == [Decimal("300000"), Decimal("150000"), Decimal("0")]
    assert SP.alokasi_tertua_dulu(f, Decimal("900000")) == [Decimal("300000"), Decimal("500000"), Decimal("0")]
    assert SP.alokasi_tertua_dulu(f, Decimal("100000.50")) == [Decimal("100000.50"), Decimal("0"), Decimal("0")]


# ---------- rencana + inti ----------

@pytest.mark.asyncio
async def test_bawaan_lunasi_semua_tertua_dulu_payload_ke_inti(pasang):
    c = pasang(_C(sisa_sesudah={}))
    d = await _pv()
    assert d["can_save"] is True and d["blocks"] == []
    assert [(x["invoice_number"], x["remaining"], x["applied"], x["remaining_after"]) for x in d["invoices"]] == \
        [("INV-1", 300000.0, 300000.0, 0.0), ("INV-2", 500000.0, 500000.0, 0.0)]
    assert d["total_remaining"] == 800000.0 and d["total_applied"] == 800000.0 and d["amount"] == 800000.0
    assert d["overpayment"] == 0.0 and d["total_remaining_after"] == 0.0
    assert d["bank_account"] == {"id": str(BA1), "label": "BCA", "type": "bank", "source": "default"}
    assert d["payment_method"] == "bank_transfer" and d["payment_number_preview"] == "RCV-2609-0009"
    assert [n["code"] for n in d["notes"]] == ["RP_DRAFT_INVOICES"] and "INV-3" in d["notes"][0]["message"]
    # payload = badan PERSIS yang dijalankan inti (dan yang FE kirim ke POST /api/receive-payments)
    b = pasang.tangkap["body"]
    assert RP.CreateReceivePaymentRequest(**d["payload"]) == b
    assert d["payload"]["customer_id"] == str(CUST) and d["payload"]["payment_date"] == "2026-09-29"
    assert [(a.invoice_id, a.amount_applied) for a in b.allocations] == [(str(I1), Decimal("300000")), (str(I2), Decimal("500000"))]
    assert b.total_amount == Decimal("800000") and b.save_as_draft is False
    assert c.tx == ["start", "sp", "sp-release", "rollback"]


@pytest.mark.asyncio
async def test_sebagian_dan_sisa_sesudah_dari_ledger_bukan_rencana(pasang):
    # sisa sesudah DIBACA ulang dari compute_ar_outstanding setelah inti (bukan remaining - applied)
    pasang(_C(sisa_sesudah={I2: Decimal("349999")}))
    d = await _pv(amount=Decimal("450000"))
    assert [(x["applied"], x["remaining_after"]) for x in d["invoices"]] == [(300000.0, 0.0), (150000.0, 349999.0)]


@pytest.mark.asyncio
async def test_kelebihan_bayar_jadi_uang_muka_bukan_penghalang(pasang):
    c = pasang(_C(sisa_sesudah={}))
    c.ovp = "OVP-2609-0001"
    d = await _pv(amount=Decimal("1000000"))
    assert d["can_save"] is True and d["overpayment"] == 200000.0 and d["total_applied"] == 800000.0
    assert d["overpayment_deposit_number"] == "OVP-2609-0001"
    assert "RP_OVERPAYMENT_DEPOSIT" in [n["code"] for n in d["notes"]]
    assert pasang.tangkap["body"].total_amount == Decimal("1000000")


@pytest.mark.asyncio
async def test_semua_penghalang_sekaligus_tanpa_inti(pasang):
    rek = [{"id": BA1, "coa_id": UUID(int=11), "account_name": "BCA", "bank_name": "BCA", "account_number": "1",
            "account_type": "bank", "is_active": True, "is_default": False},
           {"id": BA2, "coa_id": UUID(int=12), "account_name": "Mandiri", "bank_name": "Mandiri", "account_number": "2",
            "account_type": "bank", "is_active": True, "is_default": False}]
    c = pasang(_C(rek=rek, cust=None), periode_tutup=True)
    d = await _pv(invoice_ids=[str(IX)], amount=Decimal("0"))
    assert [b["code"] for b in d["blocks"]] == ["RP_INVOICE_NOT_IN_SO", "RP_AMOUNT_INVALID", "RP_CUSTOMER_MISSING",
                                                "RP_ACCOUNT_REQUIRED", "RP_PERIOD_CLOSED"]
    assert d["can_save"] is False and d["payload"] is None and "body" not in pasang.tangkap
    assert c.tulis == [] and c.tx == ["start", "rollback"]


@pytest.mark.asyncio
async def test_lunas_semua_dan_tanpa_faktur(pasang):
    pasang(_C(sisa={}))
    assert [b["code"] for b in (await _pv())["blocks"]] == ["RP_NOTHING_DUE"]
    pasang(_C(fak=[_fak()[2]]))
    assert [b["code"] for b in (await _pv())["blocks"]] == ["RP_NO_OPEN_INVOICES"]


@pytest.mark.asyncio
async def test_rekening_dari_so_menang_atas_default(pasang):
    rek = [{"id": BA1, "coa_id": UUID(int=11), "account_name": "BCA", "bank_name": "BCA", "account_number": "123-456",
            "account_type": "bank", "is_active": True, "is_default": True},
           {"id": BA2, "coa_id": UUID(int=12), "account_name": "Mandiri", "bank_name": "Mandiri", "account_number": "999 000",
            "account_type": "bank", "is_active": True, "is_default": False}]
    pasang(_C(rek=rek, so_rek="999000", sisa_sesudah={}))
    d = await _pv()
    assert d["bank_account"]["id"] == str(BA2) and d["bank_account"]["source"] == "so"
    assert d["payload"]["bank_account_id"] == str(BA2)


@pytest.mark.asyncio
async def test_rekening_tak_aktif_ditolak(pasang):
    rek = [{"id": BA1, "coa_id": UUID(int=11), "account_name": "BCA lama", "bank_name": "BCA", "account_number": "1",
            "account_type": "bank", "is_active": False, "is_default": True}]
    pasang(_C(rek=rek))
    d = await _pv(bank_account_id=str(BA1))
    assert [b["code"] for b in d["blocks"]] == ["RP_ACCOUNT_INACTIVE"]


@pytest.mark.asyncio
async def test_penolakan_inti_jadi_blok_dan_payload_dicabut(pasang):
    async def tolak(conn, ctx, body):
        raise HTTPException(status_code=400, detail="Allocation (500000) exceeds invoice remaining (400000)")
    c = pasang(_C(), inti=tolak)
    d = await _pv()
    assert d["can_save"] is False and d["payload"] is None
    assert d["blocks"] == [{"code": "RP_REJECTED", "message": "Allocation (500000) exceeds invoice remaining (400000)"}]
    assert c.tx == ["start", "sp", "sp-rollback", "rollback"]


@pytest.mark.asyncio
async def test_uang_muka_so_hanya_info(pasang):
    pasang(_C(sisa_sesudah={}), dp=(Decimal("50000"),))
    d = await _pv()
    assert d["can_save"] is True
    assert d["deposits_unapplied"] == [{"deposit_id": str(UUID(int=99)), "deposit_number": "DP-9", "remaining": 50000.0}]
    n = [x for x in d["notes"] if x["code"] == "RP_DEPOSIT_AVAILABLE"][0]
    assert "DP-9 (sisa Rp 50.000)" in n["message"]
    assert all(a.invoice_id != "DP-9" for a in pasang.tangkap["body"].allocations)


@pytest.mark.asyncio
async def test_so_tak_ada_404_dan_uuid_buruk(pasang, monkeypatch):
    c = pasang(_C(ada=False))
    with pytest.raises(HTTPException) as e:
        await _pv()
    assert e.value.status_code == 404 and c.tx == ["start", "rollback"]

    async def pool():
        raise AssertionError("DB tersentuh")
    monkeypatch.setattr(SO, "get_pool", pool)
    with pytest.raises(HTTPException) as e:
        await SO.preview_receive_payment_from_order(_req(), "bukan-uuid", None)
    assert e.value.status_code == 404


def test_create_rute_memakai_inti_yang_sama():
    import inspect
    src = inspect.getsource(RP.create_receive_payment)
    assert "return await buat_penerimaan(conn, ctx, body)" in src
    assert "INSERT INTO receive_payments" not in src   # tak ada salinan kedua di rute
    assert "INSERT INTO receive_payments" in inspect.getsource(RP.buat_penerimaan)
