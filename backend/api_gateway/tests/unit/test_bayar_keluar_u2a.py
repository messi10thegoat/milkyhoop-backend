"""U2a (putusan pemilik LANGSUNG 23 Okt 2026): posting draf Pembayaran Keluar = efek buat-posted (rekaman PPh + cermin bank lewat SATU
penulis efek_posting_bayar) + sisa tagihan dicek ulang di bawah kunci; record_payment atas tagihan DRAF ditolak 422 (pola Xero/QBO)."""
import asyncio
import inspect
from pathlib import Path
from uuid import uuid4

from app.services import bayar_keluar_rencana as BKR

APP = Path(__file__).resolve().parents[2] / "app"
BP = (APP / "routers/bill_payments.py").read_text()


def _fungsi(src: str, mulai: str, akhir: str) -> str:
    return src[src.index(mulai):src.index(akhir)]


def test_satu_penulis_efek_posting():
    efek = _fungsi(BP, "async def efek_posting_bayar(", "# === Journal Preview")
    assert efek.count("INSERT INTO withholding_tax_records") == 1 and efek.count("INSERT INTO bank_transactions") == 1
    assert "BKR.cermin_bank(" in efek
    buat = _fungsi(BP, "async def create_bill_payment(request", "async def delete_bill_payment")
    posting = _fungsi(BP, "async def post_bill_payment(", "async def void_bill_payment(")
    for f in (buat, posting):
        assert f.count("await efek_posting_bayar(") == 1
        assert "INSERT INTO withholding_tax_records" not in f and "INSERT INTO bank_transactions" not in f
    # posting: efek sesudah jurnal + status posted + cache (urutan == buat-posted)
    assert posting.index("create_bill_payment_journal(") < posting.index("SET status = 'posted'") < posting.index("await efek_posting_bayar(")


def test_posting_draf_cek_ulang_sisa_di_bawah_kunci():
    # AS-GP3 (24 Okt 2026): cek pindah ke penentu bersama BKR.rencana_posting_draf_bayar (dipanggil penulis sesudah advisory)
    posting = _fungsi(BP, "async def post_bill_payment(", "async def void_bill_payment(")
    assert posting.index("pg_advisory_xact_lock") < posting.index("await BKR.rencana_posting_draf_bayar(") < posting.index("create_bill_payment_journal(")
    s = inspect.getsource(BKR.rencana_posting_draf_bayar)
    i_kunci = s.index("FOR UPDATE")
    i_cek = s.index("await sisa_tagihan(conn, tid, bid)")
    assert i_kunci < i_cek
    assert 'bl.galat("MELEBIHI_SISA_TAGIHAN", 400, "Amount exceeds remaining")' in s  # pesan == buat-posted


class _K:
    def __init__(self, bill):
        self.bill = bill

    async def fetchrow(self, sql, *a):
        assert "FROM bills" in sql
        return self.bill

    async def fetchval(self, sql, *a):
        raise AssertionError("tagihan draf harus berhenti sebelum menghitung sisa")


def _bill(status, status_v2):
    return {"id": uuid4(), "amount": 50000, "amount_paid": 0, "status": status, "status_v2": status_v2, "ap_id": None,
            "vendor_id": uuid4(), "vendor_name": "V", "invoice_number": "BILL-X"}


def test_record_payment_tagihan_draf_422():
    for st, st2 in (("draft", None), ("draft", "draft"), ("posted", "draft")):
        r = asyncio.run(BKR.rencana_bayar_tagihan(_K(_bill(st, st2)), "t", uuid4(), {"amount": 1000}))
        assert [(b["code"], b["status"]) for b in r["blocks"]] == [("TAGIHAN_DRAF", 422)]
        assert "draf" in r["blocks"][0]["message"].lower()


def test_status_v2_bukan_draf_menang_atas_status_lama():
    # status_v2 'posted' dengan kolom status lama 'draft' = BUKAN draf (status_v2 sumber, sama dgn tagihan_daftar._draft)
    s = inspect.getsource(BKR.rencana_bayar_tagihan)
    assert 'bill["status_v2"] == "draft" or (bill["status_v2"] is None and bill["status"] == "draft")' in s


def test_router_meneruskan_status_422():
    bl = (APP / "routers/bills.py").read_text()
    assert bl.count('raise HTTPException(status_code=result.get("status_code", 400), detail=result["message"])') == 2
    bs = (APP / "services/bills_service.py").read_text()
    assert '**({"status_code": _b["status"]} if _b["status"] != 400 else {})' in bs
