"""BF-1d: EKSTRAKSI dua penulis dari handler rute supaya bisa dipanggil Bank Feed: (E4) bill_payments.buat_bayar_core, (E5) bill_payments.kunci_void_bayar + void_bayar_core.
Urutan SQL HARUS sama dengan handler lama. Bukti identik pada DB nyata = harness uji-salinan-bankfeed-1d (dua pohon kode). TES MERAH DULU (dijalankan sesudah jendela malam)."""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal as D

import pytest

from app.routers import bill_payments as BP
from app.schemas.bill_payments import BillAllocationInput, CreateBillPaymentRequest

T = "t-uji"
CTX = {"tenant_id": T, "user_id": str(uuid.uuid4())}
VENDOR, BANK, BILL, PAY, JE_ASLI, BT_ASLI = (uuid.uuid4() for _ in range(6))


class _K:
    def __init__(self):
        self.sql, self.urut = [], []

    async def execute(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))

    async def fetchval(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        if "get_next_journal_number" in sql:
            return "VD-2611-0001"
        raise AssertionError(sql[:80])

    async def fetch(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        if "FROM bill_payment_allocations" in sql:
            return [{"bill_id": BILL, "amount_applied": D(500000)}]
        raise AssertionError(sql[:80])

    async def fetchrow(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        if "FROM bills" in sql and "FOR UPDATE" in sql:
            return {"id": BILL}
        raise AssertionError(sql[:80])


def _payload(draf=False):
    return CreateBillPaymentRequest(vendor_id=str(VENDOR), payment_date=date(2026, 10, 10), bank_account_id=str(BANK), total_amount=D(500000),
                                    allocations=[BillAllocationInput(bill_id=str(BILL), amount_applied=D(500000))], save_as_draft=draf)


def _stubs(monkeypatch, k):
    urut = k.urut

    async def rencana(conn, ctx, payload, pratinjau=False):
        urut.append("rencana_bayar")
        return {"blocks": [], "vendor_name": "PT Vendor", "bank_account_name": "BCA", "allocated_amount": D(500000), "unapplied_amount": D(0),
                "alokasi": [{"bill_id": str(BILL), "remaining_before": D(500000), "amount_applied": D(500000), "remaining_after": D(0)}],
                "pph": {"tax_code_id": None, "amount": D(0), "base": D(0)}}

    async def nomor(conn, tid):
        urut.append("nomor_pk")
        return "BP-2611-0001"

    async def jurnal(**kw):
        urut.append("jurnal")
        return uuid.UUID(int=7), "JU-1"

    async def efek(conn, ctx, **kw):
        urut.append("efek_posting")

    async def segar(conn, tid, bill_id):
        urut.append("segarkan_cache")

    async def audit(*a, **kw):
        urut.append("audit")
    monkeypatch.setattr(BP.BKR, "rencana_bayar", rencana)
    monkeypatch.setattr(BP, "nomor_pk", nomor)
    monkeypatch.setattr(BP, "create_bill_payment_journal", lambda **kw: jurnal(**kw))
    monkeypatch.setattr(BP, "efek_posting_bayar", efek)
    monkeypatch.setattr(BP, "segarkan_cache_hutang_tagihan", segar)
    monkeypatch.setattr(BP._ad, "catat_dokumen", audit)


# ====================================================================== E4: buat_bayar_core
def test_buat_bayar_core_urutan_sama_dengan_handler_lama(monkeypatch):
    k = _K()
    _stubs(monkeypatch, k)
    h = asyncio.run(BP.buat_bayar_core(k, CTX, _payload()))
    sql = [s for s, _ in k.sql]
    assert sql[0].startswith("INSERT INTO bill_payments_v2") and sql[1].startswith("INSERT INTO bill_payment_allocations") and sql[2].startswith("UPDATE bill_payments_v2 SET journal_id") and len(sql) == 3
    assert k.urut == ["rencana_bayar", "nomor_pk", "jurnal", "efek_posting", "segarkan_cache"] or k.urut == ["rencana_bayar", "nomor_pk", "jurnal", "segarkan_cache", "efek_posting"]
    assert h["success"] is True and h["message"] == "Pembayaran BP-2611-0001 dibayar." and h["data"]["status"] == "posted" and h["data"]["payment_number"] == "BP-2611-0001"
    a = k.sql[0][1]
    assert a[1] == T and a[2] == "BP-2611-0001" and a[4] == "PT Vendor" and a[9] == D(500000) and a[10] == D(500000) and a[11] == D(0)


def test_buat_bayar_core_draf_tanpa_jurnal(monkeypatch):
    k = _K()
    _stubs(monkeypatch, k)
    h = asyncio.run(BP.buat_bayar_core(k, CTX, _payload(draf=True)))
    sql = [s for s, _ in k.sql]
    assert len(sql) == 2 and "jurnal" not in k.urut and "efek_posting" not in k.urut and h["data"]["status"] == "draft" and h["message"] == "Pembayaran BP-2611-0001 disimpan sebagai draf."


# ====================================================================== E5: kunci_void_bayar + void_bayar_core
def test_kunci_void_bayar_memegang_kunci_advisory_per_pembayaran():
    k = _K()
    asyncio.run(BP.kunci_void_bayar(k, PAY))
    assert k.sql == [("SELECT pg_advisory_xact_lock(hashtext($1))", (f"BILL_PAYMENT_VOID:{PAY}",))]


def _r_void():
    return {"ulang": False, "hari_ini": date(2026, 11, 12), "jurnal_asli": {"total_debit": D(500000)},
            "payment": {"payment_number": "BP-2611-0001", "vendor_name": "PT Vendor", "journal_id": JE_ASLI, "bank_account_id": BANK},
            "baris_asli": [{"account_id": uuid.uuid4(), "debit": D(500000), "credit": D(0), "memo": "m1"}, {"account_id": uuid.uuid4(), "debit": D(0), "credit": D(500000), "memo": None}],
            "cermin_asli": {"transaction_type": "withdrawal", "bank_account_id": BANK, "amount": D(-500000)}}


def test_void_bayar_core_urutan_sql_sama_dengan_handler_lama(monkeypatch):
    k = _K()
    _stubs(monkeypatch, k)
    h = asyncio.run(BP.void_bayar_core(k, CTX, str(PAY), "salah bayar", _r_void()))
    sql = [s for s, _ in k.sql]
    assert sql[0].startswith("SELECT get_next_journal_number")
    assert sql[1].startswith("INSERT INTO journal_entries") and "'DRAFT'" in sql[1] and "reversal_of_id" in sql[1]
    assert sql[2].startswith("INSERT INTO journal_lines") and sql[3].startswith("INSERT INTO journal_lines")
    assert sql[4] == "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1"
    assert sql[5].startswith("UPDATE journal_entries SET reversed_by_id = $2")
    assert sql[6].startswith("INSERT INTO bank_transactions") and "'bill_payment_void'" in sql[6]
    assert sql[7].startswith("SELECT bill_id, amount_applied FROM bill_payment_allocations") and sql[8].startswith("SELECT id FROM bills") and "FOR UPDATE" in sql[8]
    assert sql[9].startswith("UPDATE withholding_tax_records SET status = 'void'") and sql[10].startswith("UPDATE bill_payments_v2 SET status = 'voided'") and len(sql) == 11
    assert k.urut == ["segarkan_cache", "audit"]
    je = k.sql[1][1]
    assert je[2] == "VD-2611-0001" and je[3] == "Void BP-2611-0001 - PT Vendor - salah bayar" and je[8] == date(2026, 11, 12)
    l1, l2 = k.sql[2][1], k.sql[3][1]       # debit/kredit DITUKAR
    assert l1[4] == D(0) and l1[5] == D(500000) and l1[6] == "Reversal - m1" and l2[4] == D(500000) and l2[5] == D(0)
    assert k.sql[6][1][3] == "deposit" and k.sql[6][1][4] == D(500000) and h["void_journal_id"] == je[0]


# ====================================================================== handler lama memanggil inti tanpa SQL tulis inline
def test_handler_lama_memanggil_inti_tanpa_sql_inline():
    a = inspect.getsource(BP.create_bill_payment)
    assert "buat_bayar_core(" in a and "INSERT INTO bill_payments_v2" not in a and "INSERT INTO bill_payment_allocations" not in a
    b = inspect.getsource(BP.void_bill_payment)
    assert "void_bayar_core(" in b and "kunci_void_bayar(" in b and "INSERT INTO journal_entries" not in b and "INSERT INTO bank_transactions" not in b
    for fn in (BP.buat_bayar_core, BP.void_bayar_core):
        assert "float(" not in inspect.getsource(fn)
