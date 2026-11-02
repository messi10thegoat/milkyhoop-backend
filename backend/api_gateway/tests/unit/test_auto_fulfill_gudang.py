"""AUTO-FULFILL-GUDANG (2 Nov 2026; putusan MASTER, pola NetSuite: lokasi wajib untuk barang stok).

SPEK: posting faktur ber-stok yang auto-kirim (cabang SELL-FROM-STOCK _internal_post_invoice) tanpa gudang faktur memakai
gudang_bawaan.bawaan (satu aktif / satu is_default); None -> GalatBerkode 400 GUDANG_WAJIB field warehouse_id (seluruh posting
batal, Law 23). Tak ada lagi "ORDER BY created_at LIMIT 1" gudang tanpa saring aktif di sales_invoices.
"""
import inspect
import pathlib

from app.routers import sales_invoices as SI


def test_konstanta_kode_dan_kalimat():
    assert SI.KODE_GUDANG_WAJIB_POSTING == "GUDANG_WAJIB"
    assert SI.PESAN_GUDANG_WAJIB_POSTING == ("Pilih gudang di faktur: barang ber-stok dikirim saat faktur diterbitkan dan wajib "
                                             "bergudang.")


def test_cabang_auto_kirim_memakai_bawaan_dan_menolak_bila_none():
    s = " ".join(inspect.getsource(SI._internal_post_invoice).split())
    i = s.index('posting_warehouse_id = invoice.get("warehouse_id")')
    blok = s[i:s.index("await _execute_fulfillment(", i)]
    assert "_g = await _gb.bawaan(conn, ctx[\"tenant_id\"])" in blok
    assert ('if _g is None: raise GalatBerkode(400, KODE_GUDANG_WAJIB_POSTING, PESAN_GUDANG_WAJIB_POSTING, field="warehouse_id")'
            in blok)
    assert 'posting_warehouse_id = _g["id"]' in blok


def test_tanpa_pilih_gudang_tertua_tanpa_saring():
    src = " ".join(pathlib.Path(SI.__file__).read_text().split())
    assert "FROM warehouses WHERE tenant_id=$1 ORDER BY created_at LIMIT 1" not in src
