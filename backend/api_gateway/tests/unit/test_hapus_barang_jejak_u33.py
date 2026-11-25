"""U3-3 (25 Nov 2026): Hapus barang hanya bila belum terpakai. JEJAK_BARANG = SEMUA rujukan ke products (diukur dari information_schema live),
tiap sumber punya teks ramah, penulis & pratinjau memakai penentu SAMA, galat FK jadi 409 berkalimat."""
import inspect
from pathlib import Path

from app.services import barang_aksi as BA

# kolom/tabel yang merujuk products di DB live (kecuali yang ikut terhapus bersama barang: unit_conversions, item_pricing, item_activities, item_aliases, bin_stock, persediaan[dropped])
LIVE = {
    "bill_items", "bill_of_materials", "bom_components", "bom_substitutes", "branch_transfer_lines", "cost_variances", "credit_note_items", "goods_receipt_items",
    "inventory_ledger", "invoice_fulfillment_items", "item_batches", "item_serials", "journal_lines", "kds_item_history", "kds_order_items", "price_list_items",
    "production_order_materials", "production_orders", "purchase_order_items", "quote_items", "recipe_ingredients", "recipes", "recurring_bill_items",
    "sales_invoice_items", "sales_order_items", "sales_receipt_items", "standard_costs", "stock_adjustment_items", "stock_opname_lines", "stock_transfer_items",
    "vendor_credit_items", "products",
}
ROUTER = (Path(__file__).parents[2] / "app" / "routers" / "items.py").read_text()


def test_jejak_mencakup_semua_rujukan_products():
    assert {t for t, _, _ in BA.JEJAK_BARANG} == LIVE


def test_tiap_sumber_punya_teks_ramah_bukan_nama_tabel():
    for t, _, _ in BA.JEJAK_BARANG:
        assert t in BA.LABEL_JEJAK and "_" not in BA.label_jejak(t), t
    assert BA.label_jejak("tabel_baru_x") == "Transaksi lain"


def test_rencana_hapus_teks_satu_kalimat_dan_sumber_text():
    b = {"id": "x", "nama_produk": "Kaos A"}
    r = BA.rencana_hapus(b, [("quote_items", 1), ("bill_of_materials", 2), ("inventory_ledger", 0), ("price_list_items", 3), ("item_batches", 1), ("recipes", 1)])
    rc = r["rencana"]
    assert rc["jenis"] == "tak_bisa" and not r["ok"]
    assert [j["sumber_text"] for j in rc["jejak"]] == ["Penawaran", "Formula produksi (BOM)", "Daftar harga", "Batch", "Resep"]
    assert "Kaos A sudah dipakai di Penawaran, Formula produksi (BOM), Daftar harga dan 2 lainnya" in rc["teks"] and "Nonaktifkan" in rc["teks"]
    assert r["blocks"][0]["message"] == rc["teks"]
    bebas = BA.rencana_hapus(b, [])
    assert bebas["ok"] and bebas["rencana"]["jenis"] == "hapus_permanen" and "permanen" in bebas["rencana"]["teks"]


def test_penulis_memakai_penentu_sama_dan_galat_fk_409():
    fn = ROUTER[ROUTER.index("async def delete_item"):]
    fn = fn[:fn.index("\n\n\n")]
    assert "BA.rencana_hapus(b, await BA.ukur_jejak(" in fn and "status_code=409" in fn
    assert "asyncpg.ForeignKeyViolationError" in fn
    assert "detail=str(e)" not in fn                      # galat mentah tak bocor


def test_detail_menghitung_jejak_dari_daftar_yang_sama():
    sql = BA.SQL_HITUNG_JEJAK
    for t, c, _ in BA.JEJAK_BARANG:
        assert f"SELECT 1 FROM {t} WHERE {c} = p.id" in sql
    assert "BA.SQL_HITUNG_JEJAK" in ROUTER
