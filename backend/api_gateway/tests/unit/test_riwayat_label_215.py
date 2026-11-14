"""BUG-215: riwayat detail PO memakai label Indonesia ("Syarat bayar") bukan nama kolom mentah; semua medan ubah dokumen berlabel."""
import inspect

import pytest

from app.schemas import purchase_orders as SPO
from app.schemas import quotes as SQ
from app.schemas import sales_orders as SSO
from app.services import so_riwayat as RW


def test_syarat_bayar_berlabel_bukan_nama_kolom():
    assert RW.label_medan(["payment_terms_days"]) == "syarat bayar"
    assert RW.label_medan(["payment_terms_days", "notes"]) == "syarat bayar, catatan"        # urutan = urutan LABEL_MEDAN


@pytest.mark.parametrize("skema", [SPO.UpdatePurchaseOrderRequest, SQ.UpdateQuoteRequest, SSO.UpdateSalesOrderRequest])
def test_semua_medan_ubah_dokumen_berlabel(skema):
    belum = [f for f in skema.model_fields if RW.label_medan([f]) == f]
    assert belum == [], f"medan tanpa label Indonesia di riwayat: {belum}"                      # tak ada nama kolom mentah yang bocor


def test_gudang_tujuan_po_berlabel():
    assert RW.label_medan(["warehouse_id"]) == "gudang tujuan"                              # BUG-216: medan warehouse_id di UpdatePurchaseOrderRequest


def test_medan_po_baru_label_dan_dedupe():
    assert RW.label_medan(["po_date", "expected_date", "ship_to_address"]) == "tanggal, tanggal kirim, alamat kirim"
    assert RW.label_medan(["discount_percent", "discount_amount"]) == "diskon"               # label ganda dirapatkan
    assert RW.label_medan(["ref_no"]) == "no. faktur vendor"                                  # Faktur pembelian: tak berubah
    assert RW.label_medan(["ref_no"], "PO_UPDATED") == "referensi" and RW.label_medan(["ref_no"], "QUOTE_UPDATED") == "no. faktur vendor"
    assert RW.label_medan(["tidak_dikenal"]) == "tidak_dikenal"                              # medan tak dikenal TAMPIL nama aslinya (tak disembunyikan)


def test_render_riwayat_meneruskan_kejadian():
    src = inspect.getsource(RW)
    assert "label_medan(_medan, r['eventType'])" in src and 'LABEL_PER_KEJADIAN = {_AD.nama("purchase_order", "updated"): {"ref_no": "referensi"}}' in src
