"""POST /sales-invoices/{id}/void/preview + void_invoice = rencana + penulis (30 Sep 2026, F2 b, halaman CW).

_rencana_void_faktur mengumpulkan SEMUA penghalang (dulu raise di yang pertama); /void menaikkan blok PERTAMA dengan
status + detail LAMA apa adanya, dibaca DI BAWAH kunci INVOICE_VOID. _tulis_void_faktur = isi transaksi lama tanpa
perubahan (paritas byte lama-vs-baru 13 skenario di salinan DB; kontrol merah memerah). Pratinjau: penulis di savepoint,
transaksi SELALU di-ROLLBACK.
"""
import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import sales_invoices as SI  # noqa: E402
from app.schemas.sales_invoices import VoidInvoiceRequest  # noqa: E402

T = "kaos-biru-konveksi"
INV = UUID("20000000-0000-0000-0000-0000000000c1")
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


class _Inv(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


class _C:
    def __init__(self, status="posted", paid=Decimal("0"), bayar=(), nk=(), dp=(), sj=(), periode=None):
        self.inv = _Inv(id=INV, invoice_number="INV-9", customer_id=UUID(int=4), customer_name="Budi",
                        total_amount=Decimal("500"), invoice_date=date(2026, 9, 1), status=status, ar_id=None,
                        journal_id=UUID(int=7), cogs_journal_id=None, total_cogs=None)
        self.paid, self.bayar, self.nk, self.dp, self.sj = paid, list(bayar), list(nk), list(dp), list(sj)
        self.periode = periode or {}
        self.tx, self.q, self.urutan = [], [], []

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql, *a):
        self.q.append(sql)
        if "pg_advisory_xact_lock" in sql:
            self.urutan.append("kunci")

    async def fetchrow(self, sql, *a):
        self.q.append(sql)
        if "FROM sales_invoices" in sql and "total_cogs" in sql:
            self.urutan.append("baca-faktur")
            return self.inv if a[0] == INV else None
        if "FROM fiscal_periods" in sql:
            st = self.periode.get(a[1])
            return {"id": UUID(int=1), "period_name": "2026-09", "status": st} if st else None
        if "JOIN sales_orders so" in sql:
            return None
        raise AssertionError(sql[:80])

    async def fetchval(self, sql, *a):
        self.q.append(sql)
        if "receive_payment_allocations" in sql and "COALESCE" in sql:
            return self.paid
        if "compute_ar_outstanding" in sql:
            return Decimal("500")
        if "FROM bank_transactions" in sql:
            return 0
        raise AssertionError(sql[:80])

    async def fetch(self, sql, *a):
        self.q.append(sql)
        if "SELECT DISTINCT rp.payment_number" in sql:
            return [{"payment_number": n} for n in self.bayar]
        if "FROM credit_notes" in sql:
            return [{"credit_note_number": n} for n in self.nk]
        if "FROM customer_deposit_applications" in sql:
            return list(self.dp)
        if "FROM invoice_fulfillments" in sql:
            return list(self.sj)
        if "reversal_of_id IS NOT NULL" in sql or "FROM inventory_ledger" in sql:
            return []
        raise AssertionError(sql[:80])


@pytest.fixture
def pasang(monkeypatch):
    rekam = {"tulis": []}

    def _p(c, hari_tutup=False, tulis_gagal=None):
        async def tgl(conn, tid):
            return HARI
        monkeypatch.setattr(SI, "tanggal_dokumen", tgl)

        async def periode(conn, tid, d):
            if hari_tutup:
                raise HTTPException(status_code=403, detail="Cannot post to closed period (2026-09)")
        monkeypatch.setattr(SI, "check_period_is_open", periode)

        async def tulis(conn, ctx, invoice_id, r, body):
            rekam["tulis"].append({"reason": body.reason, "tx": list(conn.tx)})
            conn.urutan.append("tulis")
            if tulis_gagal:
                raise HTTPException(status_code=409, detail=tulis_gagal)
            return {"success": True, "message": "Invoice voided successfully with reversal journals",
                    "data": {"status": "void", "id": str(invoice_id)}}
        monkeypatch.setattr(SI, "_tulis_void_faktur", tulis)

        async def pool():
            return _Pool(c)
        monkeypatch.setattr(SI, "get_pool", pool)
        return c
    _p.rekam = rekam
    return _p


def _kode(d):
    return [b["code"] for b in d["blocks"]]


async def _pv(reason="salah harga"):
    r = await SI.preview_void_invoice(_req(), INV, SI.VoidInvoicePreviewRequest(reason=reason))
    return r["data"]


@pytest.mark.asyncio
async def test_rencana_mengumpulkan_semua_blok(pasang):
    sj = [{"id": UUID(int=9), "fulfillment_number": "SJ-1", "fulfillment_date": date(2026, 8, 20),
           "journal_id": None, "revenue_journal_id": None, "status": "posted"}]
    pasang(_C(paid=Decimal("100"), bayar=["RCV-1"], nk=["CN-1"], sj=sj,
              periode={date(2026, 8, 20): "CLOSED", date(2026, 9, 1): "LOCKED"}), hari_tutup=True)
    d = await _pv()
    assert _kode(d) == ["VOID_HAS_PAYMENTS", "VOID_HAS_CREDIT_NOTES", "VOID_FULFILLMENT_PERIOD_CLOSED",
                        "VOID_INVOICE_PERIOD_CLOSED", "VOID_PERIOD_CLOSED"]
    assert d["blocks"][0]["payments"] == ["RCV-1"] and d["blocks"][1]["credit_notes"] == ["CN-1"]
    assert d["blocks"][2]["suggestion"] == "credit_note" and "from-invoice" in d["blocks"][2]["action_url"]
    assert d["ok"] is False and d["payload"] is None and d["reversals"] is None
    assert pasang.rekam["tulis"] == []


@pytest.mark.asyncio
async def test_void_menaikkan_blok_pertama_dengan_detail_lama(pasang):
    c = pasang(_C(paid=Decimal("100"), bayar=["RCV-1"], nk=["CN-1"]))
    with pytest.raises(HTTPException) as e:
        await SI.void_invoice(_req(), INV, VoidInvoiceRequest(reason="x"))
    assert e.value.status_code == 400
    assert e.value.detail == ("Faktur INV-9 sudah menerima pembayaran (RCV-1). Lepas pembayaran itu dulu "
                              "(Lepas Pembayaran), lalu batalkan fakturnya.")
    assert pasang.rekam["tulis"] == [] and c.tx == ["sp", "sp-rollback"]


@pytest.mark.asyncio
async def test_void_periode_tagihan_409_dengan_saran_nota_kredit(pasang):
    pasang(_C(periode={date(2026, 9, 1): "CLOSED"}))
    with pytest.raises(HTTPException) as e:
        await SI.void_invoice(_req(), INV, VoidInvoiceRequest(reason="x"))
    assert e.value.status_code == 409 and e.value.detail["suggestion"] == "credit_note"
    assert e.value.detail["message"] == "Faktur INV-9 ada di periode 2026-09 yang sudah ditutup"


@pytest.mark.asyncio
async def test_void_sudah_void_400(pasang):
    pasang(_C(status="void"))
    with pytest.raises(HTTPException) as e:
        await SI.void_invoice(_req(), INV, VoidInvoiceRequest(reason="x"))
    assert (e.value.status_code, e.value.detail) == (400, "Faktur INV-9 sudah dibatalkan.")


@pytest.mark.asyncio
async def test_void_kunci_lalu_rencana_lalu_tulis(pasang):
    c = pasang(_C())
    r = await SI.void_invoice(_req(), INV, VoidInvoiceRequest(reason="salah harga"))
    assert r["data"]["status"] == "void"
    assert c.urutan == ["kunci", "baca-faktur", "tulis"]  # rencana dibaca DI BAWAH kunci INVOICE_VOID
    assert c.tx == ["sp", "sp-release"] and pasang.rekam["tulis"][0]["reason"] == "salah harga"


@pytest.mark.asyncio
async def test_pratinjau_bersih_tulis_di_savepoint_lalu_rollback(pasang):
    c = pasang(_C(dp=[{"application_id": UUID(int=5), "deposit_id": UUID(int=6), "amount_applied": Decimal("200"),
                       "deposit_number": "DEP-1"}]))
    d = await _pv()
    assert d["ok"] is True and d["blocks"] == [] and d["payload"] == {"reason": "salah harga"}
    assert pasang.rekam["tulis"][0]["tx"][-1] == "sp"  # penulis jalan DI savepoint
    assert c.tx[0] == "start" and c.tx[-1] == "rollback"
    assert d["deposits_released"] == [{"deposit_number": "DEP-1", "amount": 200.0}]
    assert [n["code"] for n in d["notes"]] == ["VOID_DEPOSITS_RELEASED"]
    assert d["reversals"]["journals"] == [] and d["ar_outstanding_before"] == 500.0


@pytest.mark.asyncio
async def test_pratinjau_tanpa_alasan_tetap_menghitung_tanpa_payload(pasang):
    pasang(_C())
    d = await _pv(reason="  ")
    assert _kode(d) == ["VOID_REASON_REQUIRED"] and d["payload"] is None
    assert d["reversals"] is not None and pasang.rekam["tulis"][0]["reason"] == "(pratinjau)"


@pytest.mark.asyncio
async def test_pratinjau_penolakan_penulis_jadi_blok(pasang):
    pasang(_C(), tulis_gagal="Setoran sudah direkonsiliasi")
    d = await _pv()
    assert _kode(d) == ["VOID_REJECTED"] and d["blocks"][0]["message"] == "Setoran sudah direkonsiliasi"
    assert d["payload"] is None and d["reversals"] is None


@pytest.mark.asyncio
async def test_pratinjau_faktur_tak_ada_404(pasang):
    pasang(_C())
    with pytest.raises(HTTPException) as e:
        await SI.preview_void_invoice(_req(), UUID(int=1), None)
    assert e.value.status_code == 404
