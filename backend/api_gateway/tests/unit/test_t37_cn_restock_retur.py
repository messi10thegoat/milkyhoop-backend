"""#37 -- restock nota kredit HANYA untuk retur (putusan pemilik via MASTER 25 Sep 2026).

Dulu post_credit_note mengembalikan SETIAP baris barang ber-track_inventory ke stok dan
menulis jurnal Dr Persediaan / Cr HPP, APA PUN alasan CN-nya -> CN koreksi harga/diskon
yang memilih barang katalog = stok fiktif + HPP berkurang. Kini:
  * reason 'return'  -> 1 gerakan masuk berlabel SALES_RETURN (dulu 'PURCHASE') + jurnal HPP;
  * pricing_error / discount / damaged / other -> 0 gerakan masuk, 0 jurnal HPP
    (damaged: putusan pemilik -- kerugian lewat penyesuaian stok);
  * pembalik void CREDIT_NOTE berlabel SALES_RETURN_REVERSAL (label_pembalikan_masuk).

post_credit_note dipanggil UTUH di atas DB palsu; record_inventory_inbound diganti perekam
(stimulus terbukti sampai ke titik restock: kasus 'return' WAJIB merekam 1 panggilan).
"""
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.routers import credit_notes as CN
from app.services import inventory_helpers as IH

TENANT = "tenant-uji"
USER = "22222222-2222-2222-2222-222222222222"
CN_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
PRODUK = uuid.UUID("66666666-6666-6666-6666-666666666666")
GUDANG = uuid.UUID("77777777-7777-7777-7777-777777777777")


class DB:
    def __init__(self, reason):
        self.cn = {
            "id": CN_ID, "tenant_id": TENANT, "status": "draft", "reason": reason,
            "credit_note_number": "CN-UJI-0001", "credit_note_date": date(2026, 9, 25),
            "customer_id": uuid.uuid4(), "customer_name": "Pelanggan", "original_invoice_id": None,
            "total_amount": Decimal("100000"), "subtotal": Decimal("100000"),
            "tax_amount": Decimal("0"), "discount_amount": Decimal("0"),
        }
        self.items = [{
            "id": uuid.uuid4(), "credit_note_id": CN_ID, "item_id": PRODUK, "quantity": Decimal("2"),
            "subtotal": Decimal("100000"), "total": Decimal("100000"), "tax_amount": Decimal("0"),
            "discount_amount": Decimal("0"), "description": "Kaos", "tax_code_id": None, "dpp": None,
        }]
        self.jurnal = []  # (source_type, journal_number)

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SELECT * FROM credit_notes"):
            return self.cn
        if s.startswith("SELECT status FROM fiscal_periods"):
            return None
        if s.startswith("SELECT id, item_code, nama_produk, track_inventory FROM products"):
            return {"id": PRODUK, "item_code": "K1", "nama_produk": "Kaos", "track_inventory": True}
        if s.startswith("SELECT cogs_account_id, inventory_account_id FROM products"):
            return {"cogs_account_id": uuid.uuid4(), "inventory_account_id": uuid.uuid4()}
        raise AssertionError(f"fetchrow tak dikenal: {s}")

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split())
        if "get_next_journal_number" in s:
            return "JN-UJI"
        if "get_weighted_average_cost" in s:
            return Decimal("30000")
        if s.startswith("SELECT id FROM warehouses"):
            return GUDANG
        raise AssertionError(f"fetchval tak dikenal: {s}")

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SELECT * FROM credit_note_items"):
            return self.items
        if "FROM credit_note_items cni" in s:
            return []
        raise AssertionError(f"fetch tak dikenal: {s}")

    async def execute(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO journal_entries"):
            st = "CREDIT_NOTE_COGS" if "'CREDIT_NOTE_COGS'" in s else "CREDIT_NOTE"
            self.jurnal.append(st)
        return "OK"

    def transaction(self):
        class T:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *e):
                return False
        return T()


class Pool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        db = self.db

        class A:
            async def __aenter__(s):
                return db

            async def __aexit__(s, *e):
                return False
        return A()


@pytest.fixture
def jalankan(monkeypatch):
    async def _j(reason):
        db = DB(reason)
        masuk = []

        async def _pool():
            return Pool(db)

        async def _nop(*a, **k):
            return None

        async def _akun(*a, **k):
            return uuid.uuid4()

        async def _inbound(**kw):
            masuk.append(kw)
            return {"ledger_id": uuid.uuid4(), "new_average_cost": Decimal("30000")}

        monkeypatch.setattr(CN, "get_pool", _pool)
        monkeypatch.setattr(CN, "_ensure_role_preconditions", _nop)
        monkeypatch.setattr(CN, "resolve_account_id", _akun)
        monkeypatch.setattr(CN, "resolve_account_id_by_role", _akun)
        monkeypatch.setattr(CN, "resolve_account_id_by_role_if_pkp", _akun)
        monkeypatch.setattr(IH, "record_inventory_inbound", _inbound)
        req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": USER}))
        r = await CN.post_credit_note(req, CN_ID)
        assert r["success"] and r["data"]["status"] == "posted"
        return masuk, db.jurnal
    return _j


@pytest.mark.asyncio
async def test_retur_restock_berlabel_sales_return_dan_jurnal_hpp(jalankan):
    masuk, jurnal = await jalankan("return")
    assert len(masuk) == 1, "stimulus wajib mencapai titik restock"
    assert masuk[0]["movement_type"] == "SALES_RETURN"
    assert masuk[0]["source_type"] == "CREDIT_NOTE"
    assert masuk[0]["quantity"] == 2.0
    assert jurnal == ["CREDIT_NOTE", "CREDIT_NOTE_COGS"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["pricing_error", "discount", "damaged", "other", None])
async def test_bukan_retur_tanpa_restock_tanpa_jurnal_hpp(jalankan, reason):
    masuk, jurnal = await jalankan(reason)
    assert masuk == []
    assert jurnal == ["CREDIT_NOTE"]  # jurnal nilai (retur penjualan / piutang) tetap ada


def test_pembalik_void_nota_kredit_berlabel_sales_return_reversal():
    assert IH.label_pembalikan_masuk("CREDIT_NOTE") == "SALES_RETURN_REVERSAL"
    assert IH.label_pembalikan_masuk("BILL") == "PURCHASE_RETURN"
    assert IH.label_pembalikan_masuk("STOCK_ADJUSTMENT") == "STOCK_ADJUSTMENT_REVERSAL"


def test_hanya_retur_yang_memulihkan_stok():
    assert CN.ALASAN_RESTOCK == frozenset({"return"})
