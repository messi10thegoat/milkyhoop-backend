"""T199 — syarat DP terbawa dari Penawaran ke Sales Order (Tahap 2).

Setiap tes di berkas ini sudah dibuktikan BISA MERAH lewat sabotase pada
KODE PRODUKSI (bukan pada tes), lalu dipulihkan. Lihat laporan tiket.
"""

import uuid as uuid_module

import pytest

from app.routers import quotes as quotes_router
from app.schemas.sales_orders import SalesOrderDetail, UpdateSalesOrderRequest


QUOTE_ID = "22222222-2222-2222-2222-222222222222"
CUSTOMER_ID = "11111111-1111-1111-1111-111111111111"


class _FakeConn:
    """Menangkap tiap execute; menjawab fetchrow/fetch/fetchval seperlunya."""

    def __init__(self, quote_row):
        self.quote_row = quote_row
        self.executed = []  # (sql, args)

    # -- transaksi --
    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *a):
                return False

        return _Tx()

    async def fetchrow(self, sql, *args):
        if "FROM quotes" in sql:
            return self.quote_row
        return None

    async def fetch(self, sql, *args):
        if "quote_items" in sql:
            return [
                {
                    "item_id": None,
                    "description": "Kaos Biru 30s",
                    "quantity": 3,
                    "unit": "pcs",
                    "unit_price": 90000,
                    "discount_percent": 0,
                    "tax_id": None,
                    "tax_rate": 0,
                    "tax_amount": 0,
                    "line_total": 270000,
                }
            ]
        return []

    async def fetchval(self, sql, *args):
        if "generate_sales_order_number" in sql:
            return "SO-T199-0001"
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "OK"


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Acq:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *a):
                return False

        return _Acq()


def _quote_row(**over):
    row = {
        "id": uuid_module.UUID(QUOTE_ID),
        "status": "sent",
        "customer_id": uuid_module.UUID(CUSTOMER_ID),
        "customer_name": "Toko Merdeka",
        "notes": "Warna sesuai contoh",
        "terms": "DP 60% di muka, pelunasan sebelum kirim",
        "dp_percent": 60,
        "dp_amount": 162000,
        "payment_bank_name": "BCA",
        "payment_account_number": "1234567890",
        "payment_account_holder": "CV Kaos Biru",
    }
    row.update(over)
    return row


async def _convert(monkeypatch, quote_row):
    conn = _FakeConn(quote_row)
    monkeypatch.setattr(quotes_router, "get_pool", lambda: _pool(conn))
    monkeypatch.setattr(
        quotes_router,
        "get_user_context",
        lambda request: {"tenant_id": "kaos-biru-konveksi", "user_id": None},
    )
    monkeypatch.setattr(
        quotes_router, "tanggal_dokumen", _tanggal, raising=False
    )
    await quotes_router.convert_to_sales_order(object(), QUOTE_ID, None)
    return conn


async def _pool(conn):
    return _FakePool(conn)


async def _tanggal(conn, tenant_id):
    import datetime as _dt

    return _dt.date(2026, 9, 1)


def _insert_so(conn):
    for sql, args in conn.executed:
        if "INSERT INTO sales_orders" in sql:
            return sql, args
    raise AssertionError("INSERT INTO sales_orders tidak pernah dijalankan")


# ─────────────────────────── U1 ───────────────────────────
@pytest.mark.asyncio
async def test_u1_keenam_kolom_dp_plus_notes_ikut_di_insert(monkeypatch):
    conn = await _convert(monkeypatch, _quote_row())
    sql, args = _insert_so(conn)
    for kolom in (
        "dp_percent",
        "dp_amount",
        "payment_terms",
        "payment_bank_name",
        "payment_account_number",
        "payment_account_holder",
        "notes",
    ):
        assert kolom in sql, f"kolom {kolom} hilang dari INSERT INTO sales_orders"


@pytest.mark.asyncio
async def test_u2_nilai_dp_persis_sama_dengan_quote(monkeypatch):
    """Bukan sekadar 'kolomnya ada' — NILAI-nya harus benar-benar terbawa."""
    q = _quote_row()
    conn = await _convert(monkeypatch, q)
    _sql, args = _insert_so(conn)
    for nilai in (
        q["notes"],
        q["terms"],
        q["dp_percent"],
        q["dp_amount"],
        q["payment_bank_name"],
        q["payment_account_number"],
        q["payment_account_holder"],
    ):
        assert nilai in args, f"nilai {nilai!r} tidak sampai ke parameter INSERT"


@pytest.mark.asyncio
async def test_u3_urutan_placeholder_cocok_dengan_jumlah_argumen(monkeypatch):
    """Salah hitung \ = asyncpg meledak di runtime, bukan di tes tipe."""
    import re

    conn = await _convert(monkeypatch, _quote_row())
    sql, args = _insert_so(conn)
    placeholders = {int(m) for m in re.findall(r"\$(\d+)", sql)}
    assert placeholders == set(range(1, len(args) + 1)), (
        f"placeholder {sorted(placeholders)} vs {len(args)} argumen"
    )


# ─────────────────────────── U4 kontrol positif ───────────────────────────
@pytest.mark.asyncio
async def test_u4_quote_tanpa_dp_tetap_konversi_dengan_nilai_none(monkeypatch):
    q = _quote_row(
        notes=None,
        terms=None,
        dp_percent=None,
        dp_amount=None,
        payment_bank_name=None,
        payment_account_number=None,
        payment_account_holder=None,
    )
    conn = await _convert(monkeypatch, q)
    _sql, args = _insert_so(conn)
    assert args[12:] == (None,) * 7, f"ekor argumen tak semuanya None: {args[12:]}"


# ─────────────────────────── U5 skema API ───────────────────────────
def test_u5_detail_so_memaparkan_field_dp():
    fields = SalesOrderDetail.model_fields
    for f in (
        "dp_percent",
        "dp_amount",
        "payment_terms",
        "payment_bank_name",
        "payment_account_number",
        "payment_account_holder",
    ):
        assert f in fields, f"SalesOrderDetail tidak memaparkan {f}"
        assert not fields[f].is_required(), f"{f} wajib — SO lama akan 500"


def test_u5b_dp_di_respons_bukan_decimal():
    """pydantic v2 menyerialkan Decimal jadi STRING ("60.00", "3E+4").

    Kalau field respons bertipe Decimal, FE menerima string dan matematika
    di layar rusak. RESPONSE wajib float (aturan repo).
    """
    import json

    d = SalesOrderDetail(
        id="x", order_number="SO-1", order_date="2026-09-01",
        customer_id="c", customer_name="Toko", subtotal=0, discount_amount=0,
        tax_amount=0, shipping_amount=0, total_amount=0, status="draft",
        dp_percent=60, dp_amount=30000,
        created_at="t", updated_at="t",
    )
    payload = json.loads(d.model_dump_json())
    assert isinstance(payload["dp_percent"], (int, float)), payload["dp_percent"]
    assert isinstance(payload["dp_amount"], (int, float)), payload["dp_amount"]
    assert payload["dp_amount"] == 30000


def test_u6_update_so_menerima_field_dp():
    fields = UpdateSalesOrderRequest.model_fields
    for f in (
        "dp_percent",
        "dp_amount",
        "payment_terms",
        "payment_bank_name",
        "payment_account_number",
        "payment_account_holder",
    ):
        assert f in fields, f"UpdateSalesOrderRequest tidak menerima {f}"
