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
    assert r["akun_hpp"] is None and set(r) == set(BB.BUTIR) | {"teks"}

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
    assert '"bawaan_berlaku": bawaan_berlaku,' in inspect.getsource(IT.get_item)


# ---- putusan pemilik 14 Nov: akun_pembelian ikut Accurate; pajak_beli kosong = tanpa pajak
P = {"id": "p1", "kode": "5-10100", "nama": "HPP"}
I = {"id": "i1", "kode": "1-10600", "nama": "Persediaan"}
J = {"id": "j1", "kode": "5-10300", "nama": "Beban Jasa"}
U2 = {**USAHA, "akun_persediaan": I, "akun_hpp": P, "akun_beban_jasa": J}

def test_pembelian_barang_berstok_ikut_akun_persediaan():
    r = BB.berlaku({"item_type": "goods", "track_inventory": True}, U2, {})
    assert r["akun_pembelian"] == {**I, "sumber": "bawaan_usaha"}

def test_pembelian_jasa_ikut_beban_jasa_dan_barang_nonstok_ikut_hpp():
    assert BB.berlaku({"item_type": "service", "track_inventory": False}, U2, {})["akun_pembelian"]["id"] == "j1"
    assert BB.berlaku({"item_type": "goods", "track_inventory": False}, U2, {})["akun_pembelian"]["id"] == "p1"

def test_pembelian_diganti_per_barang_sumber_barang():
    r = BB.berlaku({"item_type": "service", "purchase_account_id": "zz"}, U2, {"zz": {"id": "zz", "kode": "5-9", "nama": "X"}})
    assert r["akun_pembelian"]["sumber"] == "barang"

def test_kolom_pembelian_sama_dengan_bawaan_efektif_tetap_bawaan_usaha():
    r = BB.berlaku({"item_type": "service", "purchase_account_id": "j1"}, U2, {})
    assert r["akun_pembelian"]["sumber"] == "bawaan_usaha"

def test_akun_tak_terpetakan_null_dengan_teks_jelas_bukan_tebakan():
    r = BB.berlaku({"item_type": "service"}, {**U2, "akun_beban_jasa": None}, {})
    assert r["akun_pembelian"] is None
    assert "belum diatur" in r["teks"]["akun_pembelian"] and "Daftar Akun" in r["teks"]["akun_pembelian"]

def test_pajak_beli_kosong_teks_tanpa_pajak():
    r = BB.berlaku({}, U2, {})
    assert r["pajak_beli"] is None and r["teks"]["pajak_beli"] == "Tanpa pajak"
    assert r["teks"]["pajak_jual"] == "Tanpa pajak"

def test_butir_terisi_tak_punya_teks():
    assert "akun_penjualan" not in BB.berlaku({}, U2 | {"akun_penjualan": A}, {})["teks"]

def test_muat_usaha_memuat_beban_jasa_dari_peran_cogs_service():
    assert BB.PERAN_AKUN["akun_beban_jasa"] == "COGS_SERVICE" and BB.PERAN_AKUN["akun_hpp"] == "COGS_SALES"
