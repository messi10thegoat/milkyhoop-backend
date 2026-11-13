"""P3 Barang & Jasa standar SO (kontrak KONTRAK-barang-standar-so-20261113, putusan pemilik 13 Nov): keadaan aktif/nonaktif + teks server,
pratinjau status (nonaktif ber-stok/pesanan terbuka BOLEH dengan PERINGATAN, tanpa jurnal), pratinjau massal gabungan, pratinjau hapus
(rincian jejak, satu daftar dgn penulis), pratinjau duplikat (konversi + harga bertingkat disalin, stok/saldo awal/batch/riwayat tidak),
hapus kategori terpakai DITOLAK berkode + jumlah. Pratinjau = READ ONLY: tanpa INSERT/UPDATE/DELETE."""
import inspect
import pathlib
from decimal import Decimal as D

import pytest

from app.services import barang_aksi as BA
from app.services import barang_daftar as BD

RUTE = pathlib.Path(__file__).resolve().parents[2] / "app" / "routers" / "items.py"
MW = pathlib.Path(__file__).resolve().parents[2] / "app" / "middleware" / "permission_middleware.py"


def _b(**kw):
    b = {"id": "b1", "nama_produk": "Kaos", "item_type": "goods", "track_inventory": True, "status": "active"}
    b.update(kw)
    return b


# --- keadaan -----------------------------------------------------------------------------------------------------------
def test_keadaan_aktif_nonaktif_dan_teks_server():
    assert BA.keadaan(_b()) == "aktif" and BA.keadaan_text(_b()) == "Aktif"
    assert BA.keadaan(_b(status="inactive")) == "nonaktif" and BA.keadaan_text(_b(status="inactive")) == "Nonaktif"


# --- status: nonaktif ber-stok/pesanan terbuka = PERINGATAN, bukan blok -------------------------------------------------
def test_nonaktif_berstok_dan_pesanan_terbuka_peringatan_bukan_blok():
    r = BA.rencana_status(_b(), D("12"), D("1200000"), pesanan_terbuka=2, ke_aktif=False, satuan="pcs")
    assert r["blocks"] == [] and r["ok"] is True
    kode = [p["code"] for p in r["peringatan"]]
    assert kode == ["BARANG_MASIH_BERSTOK", "PESANAN_TERBUKA"]
    assert "Stok 12 pcs senilai Rp 1.200.000" in r["peringatan"][0]["message"]
    assert "2 pesanan terbuka" in r["peringatan"][1]["message"]
    assert r["rencana"]["dari"] == "aktif" and r["rencana"]["ke"] == "nonaktif" and r["rencana"]["jurnal"] is False


def test_nonaktif_tanpa_stok_tanpa_pesanan_tanpa_peringatan():
    r = BA.rencana_status(_b(), D("0"), D("0"), pesanan_terbuka=0, ke_aktif=False)
    assert r["ok"] and r["peringatan"] == []


def test_aktifkan_tak_beri_peringatan_stok():
    r = BA.rencana_status(_b(status="inactive"), D("5"), D("500"), pesanan_terbuka=3, ke_aktif=True)
    assert r["ok"] and r["peringatan"] == [] and r["rencana"]["ke"] == "aktif"


def test_sudah_pada_keadaan_itu_blok_berkode():
    assert BA.rencana_status(_b(), D("0"), D("0"), 0, ke_aktif=True)["blocks"][0]["code"] == "BARANG_SUDAH_AKTIF"
    assert BA.rencana_status(_b(status="inactive"), D("0"), D("0"), 0, ke_aktif=False)["blocks"][0]["code"] == "BARANG_SUDAH_NONAKTIF"


def test_barang_tak_ada_blok():
    r = BA.rencana_status(None, D("0"), D("0"), 0, ke_aktif=False)
    assert not r["ok"] and r["blocks"][0]["code"] == "BARANG_TAK_ADA"


def test_stok_minus_tetap_peringatan_stok():
    r = BA.rencana_status(_b(), D("-3"), D("-300"), 0, ke_aktif=False, satuan="pcs")
    assert r["peringatan"][0]["code"] == "BARANG_MASIH_BERSTOK"


def test_barang_tak_dilacak_tak_peringatan_stok():
    r = BA.rencana_status(_b(item_type="service", track_inventory=False), D("0"), D("0"), 1, ke_aktif=False)
    assert [p["code"] for p in r["peringatan"]] == ["PESANAN_TERBUKA"]


# --- hapus -------------------------------------------------------------------------------------------------------------
def test_hapus_berjejak_blok_dengan_rincian_dan_saran():
    r = BA.rencana_hapus(_b(), [("inventory_ledger", 4), ("sales_invoice_items", 2)])
    assert not r["ok"] and r["blocks"][0]["code"] == "BARANG_BERJEJAK"
    assert r["rencana"]["jenis"] == "tak_bisa" and r["rencana"]["saran"] == "Nonaktifkan"
    assert r["rencana"]["jejak"] == [{"sumber": "inventory_ledger", "jumlah": 4}, {"sumber": "sales_invoice_items", "jumlah": 2}]


def test_hapus_tanpa_jejak_permanen():
    r = BA.rencana_hapus(_b(), [])
    assert r["ok"] and r["rencana"]["jenis"] == "hapus_permanen"


def test_satu_daftar_jejak_untuk_penulis_dan_pratinjau():
    src = RUTE.read_text()
    assert "BA.JEJAK_BARANG" in src and src.count('("stock_transfer_items", "item_id", None)') == 0   # tak ada salinan literal di router
    assert ("stock_transfer_items", "item_id", None) in BA.JEJAK_BARANG
    assert ("inventory_ledger", "product_id", None) in BA.JEJAK_BARANG


# --- kategori ----------------------------------------------------------------------------------------------------------
def test_hapus_kategori_terpakai_ditolak_berkode_dan_jumlah():
    r = BA.rencana_kategori_hapus("Kaos", dipakai=7, ada=True)
    assert not r["ok"] and r["blocks"][0]["code"] == "KATEGORI_DIPAKAI" and "7" in r["blocks"][0]["message"]
    assert r["rencana"]["jumlah_barang_terpakai"] == 7


def test_hapus_kategori_bebas_ok_dan_tak_ada_blok_404():
    assert BA.rencana_kategori_hapus("Kaos", 0, True)["ok"]
    assert BA.rencana_kategori_hapus("Kaos", 0, False)["blocks"][0]["code"] == "KATEGORI_TAK_ADA"


# --- duplikat ----------------------------------------------------------------------------------------------------------
def test_duplikat_menyalin_konversi_dan_harga_bertingkat_bukan_stok():
    r = BA.rencana_duplikat(_b(), nama_baru="Kaos (Copy)", kode_baru="BRG-0009", n_konversi=2, n_harga=3)
    assert r["ok"]
    assert r["rencana"]["yang_disalin"] == ["data barang", "konversi satuan (2)", "harga bertingkat (3)"]
    assert r["rencana"]["yang_tidak_disalin"] == ["stok", "saldo awal", "batch", "riwayat"]
    assert r["rencana"]["nama_baru"] == "Kaos (Copy)" and r["rencana"]["kode_baru"] == "BRG-0009"


def test_penulis_duplikat_menyalin_item_pricing_dan_kode_unik():
    src = RUTE.read_text()
    i = src.index("async def duplicate_item")
    fn = src[i:src.index("# Alias for inventory_asset", i)]
    assert "INSERT INTO item_pricing" in fn and "_kode_otomatis" in fn


# --- pratinjau READ ONLY + rute + izin ---------------------------------------------------------------------------------
@pytest.mark.parametrize("rute", ["/items/{item_id}/status/preview", "/items/{item_id}/delete/preview",
                                  "/items/{item_id}/duplicate/preview", "/items/categories/delete/preview"])
def test_rute_pratinjau_terdaftar_dan_baca_saja(rute):
    src = RUTE.read_text()
    assert f'@router.post("{rute}")' in src, rute
    i = src.index(f'@router.post("{rute}")')
    fn = src[i:src.index("\n@router.", i + 10)]
    assert "readonly=True" in fn or "BA.ukur" in fn
    for tulis in ("INSERT ", "UPDATE ", "DELETE "):
        assert tulis not in fn, (rute, tulis)


def test_aksi_tersedia_detail_punya_ubah_hapus_duplikat():
    t = BD.aksi_tersedia(_b())
    assert {"ubah", "hapus", "duplikat"} <= set(t)
    assert t["ubah"]["tersedia"] is True


@pytest.mark.parametrize("pola", ["items/[^/]+/status/preview", "items/bulk/(status|status/preview)", "items/[^/]+/delete/preview",
                                  "items/[^/]+/duplicate/preview", "items/categories/delete/preview"])
def test_izin_pratinjau_dipetakan(pola):
    assert pola in MW.read_text(), pola


BULK = pathlib.Path(__file__).resolve().parents[2] / "app" / "routers" / "bulk_items.py"
MAIN = pathlib.Path(__file__).resolve().parents[2] / "app" / "main.py"


def test_massal_rute_bulk_status_satu_penentu_idempotensi_dan_audit():
    src = BULK.read_text()
    assert '@router.post("/bulk/status/preview")' in src and '@router.post("/bulk/status")' in src
    assert src.count("BA.ukur_rencana_status") == 2           # pratinjau + tulis = penentu SAMA
    assert "baca_saja=True" in src                           # pratinjau READ ONLY
    assert "BULK_KUNCI_WAJIB" in src and "jalankan_per_item" in src      # idempotensi per barang + audit BULK_STATUS
    assert "BA.terapkan_status" in src                       # satu penulis (riwayat item_activities)
    assert "pratinjau" in src and "r[\"blocks\"]" in src


def test_bulk_status_di_daftar_putih_bukan_aksi_uang():
    from app.services import bulk
    assert "status" in bulk.AKSI_DIIZINKAN


def test_router_bulk_items_didaftarkan_sebelum_router_items():
    m = MAIN.read_text()
    assert m.index("bulk_items_router.router") < m.index('app.include_router(items.router, prefix="/api"')


def test_terapkan_status_menulis_riwayat_aktivitas_dan_status_tunggal_memakainya():
    import inspect
    fn = inspect.getsource(BA.terapkan_status)
    assert "item_activities" in fn and "'activated'" not in fn and '"activated" if ke_aktif else "deactivated"' in fn
    assert "BA.terapkan_status" in RUTE.read_text()


def test_keadaan_di_baris_daftar_dan_detail():
    src = RUTE.read_text()
    assert "keadaan=BA.keadaan(row), keadaan_text=BA.keadaan_text(row)" in src
    assert '"keadaan": BA.keadaan(row), "keadaan_text": BA.keadaan_text(row)' in src
