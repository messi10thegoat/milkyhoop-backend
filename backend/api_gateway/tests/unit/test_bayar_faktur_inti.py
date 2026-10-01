"""POST /sales-invoices/{id}/payments lewat inti buat_penerimaan (29 Sep 2026, putusan pemilik opsi A).

Rute tetap memiliki pemeriksaannya (lock INVOICE_PAYMENT, kunci idempotensi + amplop respons, status faktur,
kelebihan bayar DITOLAK, rekening wajib baris bank_accounts); PENCATATAN = receive_payments.buat_penerimaan.
Sisa = compute_ar_outstanding (dulu CTE sendiri: kaos INV-2609-0005 lama -475.000 vs kanon 1.825.000).
"""
import json
import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import sales_invoices as SI  # noqa: E402
from app.routers import receive_payments as RP  # noqa: E402
from app.schemas.sales_invoices import InvoicePaymentCreate  # noqa: E402

T = "kaos-biru-konveksi"
INV = UUID("80000000-0000-0000-0000-000000000001")
CUST = UUID("40000000-0000-0000-0000-0000000000c1")
BA = UUID("70000000-0000-0000-0000-0000000000b1")
COA = UUID("70000000-0000-0000-0000-0000000000c0")
RPID = UUID("90000000-0000-0000-0000-000000000001")


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *e):
        return False


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return _Acq(self.c)


class _C:
    def __init__(self, status="posted", kanon=Decimal("1825000"), cte_lama=Decimal("-475000"), idem=None, bank=True):
        self.status, self.kanon, self.cte_lama, self.idem, self.bank = status, kanon, cte_lama, idem, bank
        self.urut, self.tulis = [], []

    def transaction(self):
        return _Tx()

    async def execute(self, q, *a):
        if "pg_advisory_xact_lock" in q:
            self.urut.append(("lock", a[0]))
        elif "SET LOCAL" not in q:
            self.tulis.append(" ".join(q.split())[:60])

    async def fetchval(self, q, *a):
        if "SELECT customer_id FROM sales_invoices" in q:
            return CUST
        if "compute_ar_outstanding" in q:
            return self.kanon
        if "journal_lines" in q:           # CTE LAMA -- tak boleh lagi menentukan
            return self.cte_lama
        raise AssertionError(q[:60])

    async def fetchrow(self, q, *a):
        if "FROM idempotency_keys" in q:
            return {"result": json.dumps(self.idem)} if self.idem else None
        if "FROM sales_invoices" in q and "FOR UPDATE" in q:
            self.urut.append(("for_update_faktur",))
            return {"id": INV, "invoice_number": "INV-2609-0005", "customer_id": CUST, "customer_name": "Budi",
                    "status": self.status, "total_amount": Decimal("2300000"), "amount_paid": Decimal("0"), "ar_id": None}
        if "FROM bank_accounts" in q:
            return {"id": BA, "coa_id": COA, "account_name": "BCA Operasional"} if self.bank else None
        if "FROM receive_payments WHERE id" in q:
            return {"journal_id": UUID(int=3), "journal_number": "RCV-2609-0099", "payment_number": "RCV-2026-0099",
                    "payment_method": "bank_transfer"}
        raise AssertionError(q[:60])


@pytest.fixture
def pasang(monkeypatch):
    tangkap = {}

    def _p(c):
        async def pool():
            return _Pool(c)
        monkeypatch.setattr(SI, "get_pool", pool)

        async def nop(*a, **k):
            return None
        monkeypatch.setattr(SI, "_ensure_role_preconditions", nop)

        async def inti(conn, ctx, body):
            tangkap["body"], tangkap["ctx"] = body, ctx
            return {"data": {"id": str(RPID), "payment_number": "RCV-2026-0099"}}
        monkeypatch.setattr(RP, "buat_penerimaan", inti)
        return c
    _p.tangkap = tangkap
    return _p


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": "00000000-0000-0000-0000-0000000000a1",
                                                       "tenant_id": T, "role": "OWNER"}), headers={})


async def _bayar(jumlah="100000", metode="transfer", **kw):
    body = InvoicePaymentCreate(amount=Decimal(jumlah), payment_date=date(2026, 9, 29), account_id=str(BA),
                                bank_account_id=str(BA), payment_method=metode, **kw)
    return await SI.record_payment(_req(), INV, body)


@pytest.mark.asyncio
async def test_sisa_kanon_bukan_cte_lama_pembayaran_sah_lolos(pasang):
    # CTE lama = -475.000 (dulu MENOLAK), kanon compute_ar_outstanding = 1.825.000 -> lolos ke inti
    c = pasang(_C())
    r = await _bayar()
    assert r["created_payment_id"] == str(RPID) and r["data"]["payment_number"] == "RCV-2026-0099"
    assert r["data"]["journal_number"] == "RCV-2609-0099" and r["data"]["payment_method"] == "bank_transfer"
    assert r["data"]["invoice_id"] == str(INV) and r["data"]["status"] == "posted"


@pytest.mark.asyncio
async def test_badan_ke_inti(pasang):
    pasang(_C())
    await _bayar(jumlah="100000.75", metode="transfer", reference="TRF-1", notes="n")
    b = pasang.tangkap["body"]
    assert b.customer_id == str(CUST) and b.customer_name == "Budi"
    assert b.bank_account_id == str(BA) and b.bank_account_name == "BCA Operasional"
    assert b.payment_method is None            # kosakata lama 'transfer' -> diturunkan dari akun (#29) di inti
    assert b.total_amount == Decimal("100000.75") and b.save_as_draft is False
    assert [(a.invoice_id, a.amount_applied) for a in b.allocations] == [(str(INV), Decimal("100000.75"))]
    assert b.reference_number == "TRF-1" and b.notes == "n" and b.source_type == "cash"


@pytest.mark.asyncio
async def test_override_metode_sah_diteruskan(pasang):
    pasang(_C())
    await _bayar(metode="e_wallet")
    assert pasang.tangkap["body"].payment_method == "e_wallet"


@pytest.mark.asyncio
async def test_kelebihan_bayar_tetap_ditolak_tanpa_inti(pasang):
    pasang(_C(kanon=Decimal("150000")))
    with pytest.raises(HTTPException) as e:
        await _bayar(jumlah="150000.01")
    assert e.value.status_code == 400 and "Rp 150.000" in e.value.detail
    assert "body" not in pasang.tangkap


@pytest.mark.asyncio
async def test_status_faktur_draf_ditolak_tanpa_inti(pasang):
    pasang(_C(status="draft"))
    with pytest.raises(HTTPException) as e:
        await _bayar()
    assert e.value.status_code == 400 and "body" not in pasang.tangkap


@pytest.mark.asyncio
async def test_rekening_wajib_baris_bank_accounts(pasang):
    pasang(_C(bank=False))
    with pytest.raises(HTTPException) as e:
        await _bayar()
    assert e.value.detail == "Could not resolve bank account from account_id" and "body" not in pasang.tangkap


@pytest.mark.asyncio
async def test_lock_pelanggan_sebelum_baris_faktur(pasang):
    """Urutan SAMA dgn POST /receive-payments (domain -> FOR UPDATE faktur): tanpa ini dua penerimaan
    pelanggan yang sama bisa saling-tunggu (deadlock)."""
    c = pasang(_C())
    await _bayar()
    kunci = [x[1] if x[0] == "lock" else x[0] for x in c.urut]
    i_dom = kunci.index(f"RECEIVE_PAYMENT_CREATE:{T}:{CUST}")
    assert kunci[0] == f"INVOICE_PAYMENT:{INV}" and i_dom < kunci.index("for_update_faktur")
    assert kunci.count(f"RECEIVE_PAYMENT_CREATE:{T}:{CUST}") == 1


@pytest.mark.asyncio
async def test_replay_idempotensi_tanpa_inti(pasang):
    lama = {"success": True, "created_payment_id": "x", "data": {"id": "x"}}
    c = pasang(_C(idem=lama))
    assert await _bayar() == lama and "body" not in pasang.tangkap and c.tulis == []


@pytest.mark.asyncio
async def test_hasil_disimpan_ke_idempotency_keys(pasang):
    c = pasang(_C())
    await _bayar()
    assert len(c.tulis) == 1 and c.tulis[0].startswith("INSERT INTO idempotency_keys")


def test_pembaca_nama_rekening_menerima_dua_bentuk_id():
    """receive_payments.bank_account_id = bank_accounts.id (jalur faktur lama, 30 baris grapgrap) ATAU CoA id
    (inti create: kaos 26 baris, grapgrap baru sejak 29 Sep). Gabungan ba.id = rp.bank_account_id saja ->
    nama rekening KOSONG di riwayat pembayaran faktur (diukur di salinan: 'BCA Anthonius…' -> None)."""
    import inspect
    from pathlib import Path
    src = Path(SI.__file__).read_text()
    assert "LEFT JOIN bank_accounts ba ON ba.id = rp.bank_account_id" not in src
    assert src.count("(b.id = rp.bank_account_id OR b.coa_id = rp.bank_account_id)") == 2
    pdf = inspect.getsource(RP.muat_pdf_kwitansi_penerimaan)  # P3: konteks PDF dipindah ke pemuat bersama
    assert "(id = $1 OR coa_id = $1)" in pdf
