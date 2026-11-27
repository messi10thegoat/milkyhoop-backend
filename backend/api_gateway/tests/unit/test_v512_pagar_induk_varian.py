"""V512 (GO pemilik LANGSUNG 27 Nov): trigger trg_tolak_induk_varian di 15 tabel baris dokumen menolak barang INDUK varian (SQLSTATE MH422)
-> main.py memetakan ke 422 VARIAN_INDUK_DI_DOKUMEN berkalimat; galat DB lain tetap dilempar ulang."""
import asyncio
import json
import pathlib
import re

import asyncpg
import pytest

DISINI = pathlib.Path(__file__).resolve()
MIG = DISINI.parents[3] / "migrations"
SQL = (MIG / "V512__pagar_induk_varian.sql").read_text()
RB = (MIG / "V512__pagar_induk_varian_ROLLBACK.sql").read_text()
TABEL = {"quote_items", "sales_order_items", "sales_invoice_items", "sales_receipt_items", "credit_note_items", "invoice_fulfillment_items",
         "purchase_order_items", "goods_receipt_items", "bill_items", "vendor_credit_items", "recurring_bill_items",
         "stock_adjustment_items", "stock_transfer_items", "stock_opname_lines", "branch_transfer_lines"}


def test_lima_belas_tabel_baris_dokumen():
    pasangan = re.findall(r"\['(\w+)','(\w+)'\]", SQL)
    assert {t for t, _ in pasangan} == TABEL and len(pasangan) == 15
    assert all(k in ("item_id", "product_id") for _, k in pasangan)


def test_ddl_trigger_sebelum_insert_update_dan_galat_berkode():
    assert "BEFORE INSERT OR UPDATE OF %I ON %I FOR EACH ROW" in SQL and "is_matrix_parent" in SQL
    assert "ERRCODE = 'MH422'" in SQL and "VARIAN_INDUK_DI_DOKUMEN:" in SQL
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", SQL, re.M | re.I)   # apply_mig menolak kendali transaksi


def test_rollback_membuang_trigger_dan_fungsi():
    assert "DROP TRIGGER trg_tolak_induk_varian" in RB and "DROP FUNCTION IF EXISTS tolak_induk_varian_di_dokumen()" in RB


class _Galat(asyncpg.PostgresError):
    pass


def _panggil(sqlstate, pesan):
    from app import main
    e = _Galat(pesan)
    e.sqlstate = sqlstate
    return asyncio.run(main._gudang_terkunci_trigger_handler(None, e))


def test_mh422_jadi_422_berkalimat():
    r = _panggil("MH422", 'VARIAN_INDUK_DI_DOKUMEN: "Kaos" adalah barang induk varian; pilih variannya (mis. ukuran/warna).')
    badan = json.loads(r.body)
    assert r.status_code == 422 and badan["code"] == "VARIAN_INDUK_DI_DOKUMEN"
    assert badan["message"].startswith('"Kaos" adalah barang induk varian')


def test_galat_db_lain_tetap_dilempar():
    with pytest.raises(_Galat):
        _panggil("23505", "duplicate")
