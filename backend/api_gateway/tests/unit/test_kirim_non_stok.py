"""V318 Surat Jalan untuk barang NON-STOK ber-flag bisa_dikirim (27 Sep 2026; pola NetSuite/SAP NLAG).

_execute_fulfillment DIJALANKAN PENUH dengan koneksi tiruan yang merekam SEMUA SQL:
- baris non-stok perlu_kirim=true -> SJ + baris SJ ber-HPP 0, fulfilled_qty naik; NOL INSERT journal_entries /
  journal_lines / inventory_ledger, NOL cek stok/WAC.
- baris non-stok tanpa flag (perlu_kirim false/NULL) -> 409 FULFILL_LINE_NOT_SHIPPABLE, nol tulisan.
- baris stok -> tetap jalur lama (cek stok).
Status pengiriman faktur dihitung atas baris PERLU DIKIRIM; posting menandai 'pending' bila ada baris non-stok
perlu kirim; detail SO + ringkasan SJ memakai snapshot; API barang menerima bisa_dikirim (jasa ditolak).
"""
import ast
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.routers.items as IT
import app.routers.sales_invoices as SI
import app.routers.sales_orders as SO
from app.schemas.items import CreateItemRequest, ItemListItem, UpdateItemRequest

T = "kaos-biru-konveksi"
INV = uuid.uuid4()
BARIS = uuid.uuid4()
PROD = uuid.uuid4()


class Conn:
    def __init__(self, dilacak=False, perlu_kirim=True):
        self.dilacak, self.perlu_kirim = dilacak, perlu_kirim
        self.tulis, self.semua = [], []

    def _rek(self, sql, a):
        s = " ".join(sql.split())
        self.semua.append(s)
        if s.upper().startswith(("INSERT", "UPDATE", "DELETE")):
            self.tulis.append(s)
        return s

    async def fetchrow(self, sql, *a):
        s = self._rek(sql, a)
        if "FROM sales_invoice_items WHERE id=$1 AND invoice_id=$2 FOR UPDATE" in s:
            return {"id": BARIS, "item_id": PROD, "description": "Kain meteran", "quantity": Decimal("5"),
                    "fulfilled_qty": Decimal("0"), "allocated_amount": Decimal("500000"),
                    "recognized_amount": Decimal("500000"), "perlu_kirim": self.perlu_kirim}
        if "SUM(sii.quantity) FILTER" in s:
            return {"total_qty": Decimal("5"), "total_fulfilled": Decimal("5"),
                    "total_allocated": Decimal("500000"), "total_recognized": Decimal("500000")}
        return None

    async def fetchval(self, sql, *a):
        s = self._rek(sql, a)
        if "COALESCE(track_inventory, false) FROM products" in s:
            assert "tenant_id = $2" in s and a[1] == T
            return self.dilacak
        if "get_next_journal_number" in s:
            return "SJ-2609-0001"
        if "FROM warehouse_stock" in s:
            return Decimal("0")
        if "RETURNING id" in s.upper():
            return uuid.uuid4()
        return None

    async def fetch(self, sql, *a):
        self._rek(sql, a)
        return []

    async def execute(self, sql, *a):
        self._rek(sql, a)
        return "UPDATE 1"


@pytest.fixture(autouse=True)
def _tgl(monkeypatch):
    async def t(c, tid):
        return date(2026, 9, 27)
    monkeypatch.setattr(SI, "tanggal_dokumen", t)


async def _kirim(c, qty=5):
    return await SI._execute_fulfillment(c, T, "u1", {"id": INV, "invoice_number": "INV-2609-0200"},
                                         [{"invoice_item_id": BARIS, "quantity": qty}], None, date(2026, 9, 27))


@pytest.mark.asyncio
async def test_non_stok_ber_flag_dikirim_tanpa_jurnal_stok_hpp():
    c = Conn()
    await _kirim(c)
    t = " | ".join(c.tulis)
    assert "INSERT INTO invoice_fulfillments" in t and "INSERT INTO invoice_fulfillment_items" in t
    assert "fulfilled_qty = fulfilled_qty + $2" in t
    for dilarang in ("INSERT INTO journal_entries", "INSERT INTO journal_lines", "INSERT INTO inventory_ledger"):
        assert dilarang not in t, dilarang
    s = " | ".join(c.semua)
    assert "warehouse_stock" not in s and "get_weighted_average_cost" not in s


@pytest.mark.asyncio
@pytest.mark.parametrize("pk", [False, None])
async def test_non_stok_tanpa_flag_ditolak_nol_tulisan(pk):
    c = Conn(perlu_kirim=pk)
    with pytest.raises(HTTPException) as e:
        await _kirim(c)
    assert e.value.status_code == 409 and e.value.detail["code"] == "FULFILL_LINE_NOT_SHIPPABLE"
    assert "Kain meteran" in e.value.detail["message"] and c.tulis == []


@pytest.mark.asyncio
async def test_baris_stok_tetap_jalur_lama():
    c = Conn(dilacak=True)
    with pytest.raises(HTTPException) as e:
        await _kirim(c)
    assert "Stok" in str(e.value.detail)


def _src(mod, nama):
    for n in ast.walk(ast.parse(open(mod.__file__, encoding="utf-8").read())):
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == nama:
            return " ".join(ast.unparse(n).split())
    raise AssertionError(nama)


def test_status_pengiriman_atas_baris_perlu_kirim():
    s = _src(SI, "_update_invoice_fulfillment_status")
    assert "SUM(sii.quantity) FILTER (WHERE COALESCE(sii.perlu_kirim, p.track_inventory, false))" in s
    assert "p.tenant_id = $2" in s


def test_posting_menandai_pending_bila_ada_non_stok_perlu_kirim():
    s = _src(SI, "_internal_post_invoice")
    assert "sii.perlu_kirim AND NOT COALESCE(p.track_inventory, false)" in s
    assert "'fulfilled' if _f >= _q else 'partial' if _f > 0 else 'pending'" in s
    # penanda ditulis SEBELUM UPDATE status faktur (nilai itulah yang disimpan)
    assert s.index("sii.perlu_kirim AND NOT COALESCE") < s.index("SET status = 'posted'")


def test_detail_so_dan_ringkasan_sj_memakai_snapshot():
    assert "COALESCE(soi.perlu_kirim, p.track_inventory, false) AS requires_fulfillment" in _src(SO, "get_sales_order_detail")
    s = _src(SI, "get_invoice_fulfillments")
    assert "COALESCE(si.perlu_kirim, p.track_inventory, false) AS requires_fulfillment" in s
    assert "'requires_fulfillment': bool(s['requires_fulfillment'])" in s


def test_api_barang_bisa_dikirim():
    b = {"name": "Kain", "base_unit": "m"}
    assert CreateItemRequest(**b, item_type="non_inventory", track_inventory=False, bisa_dikirim=True).bisa_dikirim
    assert CreateItemRequest(**b, item_type="non_inventory", track_inventory=False).bisa_dikirim is False
    with pytest.raises(ValidationError):
        CreateItemRequest(**b, item_type="service", track_inventory=False, bisa_dikirim=True)
    assert "bisa_dikirim" in UpdateItemRequest.model_fields
    assert ItemListItem.model_fields["bisa_dikirim"].default is False
    s = _src(IT, "update_item")
    assert "'bisa_dikirim': 'bisa_dikirim'" in s and "Jasa tidak bisa ditandai 'bisa dikirim'" in s
    c = _src(IT, "create_item")
    assert "UPDATE products SET bisa_dikirim = true WHERE id = $1 AND tenant_id = $2" in c
