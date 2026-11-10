"""BIAYA B6 (10 Nov 2026, WORKSPACE/MASTER): bundel GET /expenses/{id}/documents berbentuk SAMA dengan modul lain (pembaca bundel bersama FE):
setiap dok `paper` = "A4" (dulu None) dan anchor memuat `vendor` (nama vendor atau "", dulu tak ada) -- pola goods_receipt/bill/payment_out."""
import asyncio
import uuid
from datetime import date, datetime
from decimal import Decimal

from app.services import biaya_riwayat as BR

KUNCI_DOK = {"kind", "id", "number", "date", "amount", "paper", "sent", "batal", "status"}
KUNCI_ANCHOR = {"kind", "id", "number", "vendor", "date", "position_text", "total", "account_name"}


class Kon:
    def __init__(self, vendor):
        self.vendor, self.e, self.j = vendor, uuid.uuid4(), uuid.uuid4()

    async def fetchrow(self, sql, *a):
        assert "tenant_id = $2" in sql
        if "FROM expenses" in sql:
            assert "vendor_name" in sql
            return {"id": self.e, "expense_number": "EXP-2611-0001", "expense_date": date(2026, 11, 10), "status": "posted",
                    "total_amount": Decimal("150000"), "account_name": "Beban Listrik", "paid_through_name": "Kas Toko",
                    "journal_id": self.j, "vendor_name": self.vendor, "created_at": datetime(2026, 11, 10), "created_by": None}
        if "WHERE id = $1" in sql:
            return {"id": self.j, "journal_number": "EX-2611-0001", "journal_date": date(2026, 11, 10), "total_debit": Decimal("150000"),
                    "created_at": datetime(2026, 11, 10), "created_by": None}
        return None

    async def fetch(self, sql, *a):
        return [{"id": uuid.uuid4(), "file_name": "struk.png", "uploaded_at": datetime(2026, 11, 10), "uploaded_by": None}]


def bundel(vendor):
    return asyncio.run(BR.dokumen_biaya(Kon(vendor), "kaos-biru-konveksi", uuid.uuid4()))


def test_semua_dok_paper_a4_dan_kunci_seragam():
    d = bundel("PLN")
    docs = [x for g in d["groups"] for x in g["docs"]]
    assert {x["kind"] for x in docs} == {"expense", "journal", "attachment"}
    for x in docs:
        assert set(x) == KUNCI_DOK and x["paper"] == "A4", x


def test_anchor_memuat_vendor():
    a = bundel("PLN")["anchor"]
    assert set(a) == KUNCI_ANCHOR and a["vendor"] == "PLN"
    assert bundel(None)["anchor"]["vendor"] == ""  # tanpa vendor: string kosong, bukan None/hilang (pola goods_receipt)


def test_penanda():
    assert BR.PENANDA_BIAYA_B6 == "biaya-b6-bundel-w20261110"
