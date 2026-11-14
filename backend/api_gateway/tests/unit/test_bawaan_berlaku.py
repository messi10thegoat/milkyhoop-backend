"""bawaan_berlaku (14 Nov 2026): akun/pajak yang BERLAKU untuk barang + sumbernya. Kolom NULL atau sama dgn bawaan usaha -> bawaan_usaha."""
import asyncio
from app.services import bawaan_berlaku as BB

A = {"id": "a1", "kode": "4-10000", "nama": "Penjualan"}
B = {"id": "a2", "kode": "4-19999", "nama": "Penjualan Khusus"}
USAHA = {"akun_penjualan": A, "akun_persediaan": None, "akun_hpp": None, "akun_pembelian": None, "pajak_jual": None, "pajak_beli": None}

def test_kolom_null_ikut_bawaan_usaha():
    r = BB.berlaku({}, USAHA, {"a1": A})
    assert r["akun_penjualan"] == {**A, "sumber": "bawaan_usaha"}

def test_kolom_sama_dengan_bawaan_usaha_tetap_bawaan_usaha():
    r = BB.berlaku({"sales_account_id": "a1"}, USAHA, {"a1": A})
    assert r["akun_penjualan"]["sumber"] == "bawaan_usaha"

def test_kolom_beda_adalah_barang():
    r = BB.berlaku({"sales_account_id": "a2"}, USAHA, {"a1": A, "a2": B})
    assert r["akun_penjualan"] == {**B, "sumber": "barang"}

def test_tanpa_bawaan_dan_tanpa_kolom_kosong_tegas():
    r = BB.berlaku({}, USAHA, {})
    assert r["akun_hpp"] is None and set(r) == set(BB.BUTIR)

def test_pajak_dipetakan_dari_tax_id():
    usaha = {**USAHA, "pajak_jual": {"id": "p1", "kode": "PPN11", "nama": "PPN 11%"}}
    r = BB.berlaku({"sales_tax_id": "p2"}, usaha, {}, {"p2": {"id": "p2", "kode": "BBS", "nama": "Bebas"}})
    assert r["pajak_jual"]["sumber"] == "barang" and r["pajak_jual"]["kode"] == "BBS"
    assert BB.berlaku({}, usaha, {})["pajak_jual"]["sumber"] == "bawaan_usaha"

def test_id_kolom_tak_dikenal_tidak_diam_jadi_bawaan():
    r = BB.berlaku({"sales_account_id": "zz"}, USAHA, {"a1": A})
    assert r["akun_penjualan"]["sumber"] == "barang" and r["akun_penjualan"]["id"] == "zz"

def test_item_detail_memuat_bawaan_berlaku():
    import inspect
    from app.routers import items as IT
    assert "bawaan_berlaku" in inspect.getsource(IT.get_item)
