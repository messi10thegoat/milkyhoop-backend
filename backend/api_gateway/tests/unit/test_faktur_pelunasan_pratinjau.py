"""POST /sales-invoices/{id}/receive-payment/preview (30 Sep 2026, F2 a, halaman CW "Terima pembayaran" per faktur).

Rencana so_pelunasan.rencana_pelunasan_faktur (sisa compute_ar_outstanding, rekening _rekening, periode, kelebihan =
uang muka, SEMUA blok) + lapis 2 BERSAMA dengan pratinjau SO (_jalankan_inti: buat_penerimaan di savepoint, sisa sesudah
dibaca ulang). Transaksi SELALU di-ROLLBACK. Uji nyata 8 skenario di salinan DB: pratinjau = penerimaan nyata.
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
from app.routers import sales_invoices as SI  # noqa: E402
from app.routers import receive_payments as RP  # noqa: E402
from app.routers import customer_deposits as CD  # noqa: E402
from app.utils import tanggal_tenant as TT  # noqa: E402

T = "kaos-biru-konveksi"
INV = UUID("60000000-0000-0000-0000-0000000000f1")
CUST = UUID("40000000-0000-0000-0000-0000000000c1")
SOID = UUID("10000000-0000-0000-0000-0000000000e1")
BA1 = UUID("70000000-0000-0000-0000-0000000000b1")
HARI = date(2026, 9, 30)


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": "00000000-0000-0000-0000-0000000000a1",
                                                       "tenant_id": T, "role": "OWNER"}), headers={})


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


class _C:
    def __init__(self, status="partial", sisa=Decimal("300000"), sisa_sesudah=None, cust=CUST, so=None, rek=True):
        self.status, self.sisa, self.sisa_sesudah, self.cust, self.so = status, sisa, sisa_sesudah, cust, so
        self.rek = [{"id": BA1, "coa_id": UUID(int=11), "account_name": "BCA", "bank_name": "BCA",
                     "account_number": "123", "account_type": "bank", "is_active": True, "is_default": True}] if rek else []
        self.tx, self.inti = [], False

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql, *a):
        pass

    async def fetchrow(self, sql, *a):
        if "FROM sales_invoices WHERE id = $1 AND tenant_id = $2" in sql:
            assert a[1] == T
            return {"id": INV, "invoice_number": "INV-9", "invoice_date": date(2026, 9, 1), "due_date": None,
                    "status": self.status, "customer_id": self.cust, "customer_name": "Budi",
                    "sales_order_id": self.so, "payment_account_number": None} if a[0] == INV else None
        if "FROM receive_payments rp" in sql:
            return {"payment_method": "bank_transfer", "created_deposit_id": None, "deposit_number": None}
        raise AssertionError(sql[:70])

    async def fetchval(self, sql, *a):
        if "compute_ar_outstanding" in sql:
            assert a == (T, INV)
            return self.sisa
        raise AssertionError(sql[:70])

    async def fetch(self, sql, *a):
        if "FROM bank_accounts" in sql:
            return [r for r in self.rek if r["is_active"]]
        if "compute_ar_outstanding" in sql:
            src = self.sisa_sesudah if self.inti and self.sisa_sesudah is not None else {INV: self.sisa}
            return [{"invoice_id": i, "outstanding": v} for i, v in src.items()]
        raise AssertionError(sql[:70])


@pytest.fixture
def pasang(monkeypatch):
    tangkap = {}

    def _p(c, periode_tutup=False, dp=(), inti_gagal=None):
        async def tgl(conn, tid):
            return HARI
        monkeypatch.setattr(TT, "tanggal_dokumen", tgl)

        async def periode(conn, tid, d):
            if periode_tutup:
                raise HTTPException(status_code=403, detail="Periode sudah ditutup")
        monkeypatch.setattr(RP, "check_period_is_open", periode)

        async def dps(conn, tid, sid):
            return [{"id": UUID(int=99), "deposit_number": "DP-9"}] if dp else []

        async def sisa_dp(conn, tid, did):
            return dp[0]
        monkeypatch.setattr(CD, "linked_so_deposits", dps)
        monkeypatch.setattr(CD, "compute_deposit_remaining", sisa_dp)

        async def buat(conn, ctx, body):
            tangkap["body"] = body
            tangkap["tx"] = list(conn.tx)
            conn.inti = True
            if inti_gagal:
                raise HTTPException(400, inti_gagal)
            return {"data": {"id": str(UUID(int=5)), "payment_number": "RCV-2026-0040"}}
        monkeypatch.setattr(RP, "buat_penerimaan", buat)

        async def pra(*a, **k):
            return None
        monkeypatch.setattr(RP, "_ensure_receive_payments_role_preconditions", pra)

        async def pool():
            return _Pool(c)
        monkeypatch.setattr(SI, "get_pool", pool)
        return c
    _p.tangkap = tangkap
    return _p


async def _pv(**badan):
    return (await SI.preview_receive_payment_for_invoice(_req(), INV, SP.SIReceivePaymentPreviewRequest(**badan)))["data"]


@pytest.mark.asyncio
async def test_bawaan_lunasi_sisa_lewat_inti_lalu_rollback(pasang):
    c = pasang(_C(sisa_sesudah={}))
    d = await _pv()
    assert d["can_save"] is True and d["blocks"] == [] and d["invoice_number"] == "INV-9"
    assert d["customer"] == {"id": str(CUST), "name": "Budi"}
    assert (d["total_remaining"], d["total_applied"], d["total_remaining_after"]) == (300000.0, 300000.0, 0.0)
    assert d["payment_number_preview"] == "RCV-2026-0040" and d["payment_method"] == "bank_transfer"
    assert RP.CreateReceivePaymentRequest(**d["payload"]) == pasang.tangkap["body"]
    assert d["payload"]["allocations"] == [{"invoice_id": str(INV), "amount_applied": "300000"}]
    assert pasang.tangkap["tx"][-1] == "sp" and c.tx == ["start", "sp", "sp-release", "rollback"]


@pytest.mark.asyncio
async def test_sebagian_sisa_sesudah_dari_ledger(pasang):
    pasang(_C(sisa_sesudah={INV: Decimal("199999")}))
    d = await _pv(amount=Decimal("100000"))
    assert d["invoices"][0]["applied"] == 100000.0 and d["total_remaining_after"] == 199999.0


@pytest.mark.asyncio
async def test_kelebihan_jadi_uang_muka(pasang):
    pasang(_C(sisa_sesudah={}))
    d = await _pv(amount=Decimal("350000"))
    assert d["can_save"] is True and d["overpayment"] == 50000.0 and d["total_applied"] == 300000.0
    assert "RP_OVERPAYMENT_DEPOSIT" in [n["code"] for n in d["notes"]]
    assert pasang.tangkap["body"].total_amount == Decimal("350000")


@pytest.mark.asyncio
async def test_semua_blok_sekaligus_tanpa_inti(pasang):
    c = pasang(_C(status="draft", cust=None, rek=False), periode_tutup=True)
    d = await _pv(amount=Decimal("0"))
    assert [b["code"] for b in d["blocks"]] == ["RP_INVOICE_DRAFT", "RP_AMOUNT_INVALID", "RP_CUSTOMER_MISSING",
                                                "RP_ACCOUNT_REQUIRED", "RP_PERIOD_CLOSED"]
    assert d["payload"] is None and "body" not in pasang.tangkap and c.tx == ["start", "rollback"]


@pytest.mark.asyncio
async def test_void_lunas_dan_tak_ada(pasang):
    pasang(_C(status="void"))
    assert [b["code"] for b in (await _pv())["blocks"]] == ["RP_INVOICE_VOID"]
    pasang(_C(sisa=Decimal("0")))
    assert [b["code"] for b in (await _pv())["blocks"]] == ["RP_NOTHING_DUE"]
    pasang(_C())
    with pytest.raises(HTTPException) as e:
        await SI.preview_receive_payment_for_invoice(_req(), UUID(int=3), None)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_uang_muka_so_dicatat_dan_penolakan_inti(pasang):
    pasang(_C(so=SOID), dp=(Decimal("50000"),))
    d = await _pv()
    assert "RP_DEPOSIT_AVAILABLE" in [n["code"] for n in d["notes"]]
    pasang(_C(), inti_gagal="Rekening tidak aktif")
    d = await _pv()
    assert [b["code"] for b in d["blocks"]] == ["RP_REJECTED"] and d["payload"] is None
