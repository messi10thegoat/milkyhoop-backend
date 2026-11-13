"""DAFTAR HARGA D2 (putusan pemilik 13 Nov): pratinjau semua aksi tulis (status, bawaan, hapus, ubah header, hapus baris) READ ONLY dari penentu
SAMA dengan penulis (services/harga_aksi); hapus daftar yang dipakai pelanggan DITOLAK menyebut N pelanggan; nonaktifkan boleh dengan PERINGATAN;
barang TIDAK DIJUAL ditolak berkode DH_BARANG_TIDAK_DIJUAL; idempotensi (X-Idempotency-Key) di semua tulis; izin pratinjau dipetakan."""
import inspect
import pathlib

import pytest

from app.middleware import permission_middleware as PM
from app.services import harga_aksi as HA

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
RUTE = (APP / "routers" / "price_lists.py").read_text()


def _fn(nama: str) -> str:
    i = RUTE.index(f"async def {nama}(")
    j = RUTE.find("\n@router.", i)
    return RUTE[i:j if j != -1 else len(RUTE)]


def _r(**kw):
    r = {"id": "d1", "code": "WARUNG", "name": "Warung", "is_active": True, "is_default": False, "start_date": None, "end_date": None,
         "priority": 100, "description": None, "price_type": "fixed"}
    r.update(kw)
    return r


# --- status ----------------------------------------------------------------------------------------------------------
def test_nonaktifkan_dipakai_pelanggan_peringatan_bukan_blok():
    x = HA.rencana_status(_r(), 3, ke_aktif=False)
    assert x["blocks"] == [] and x["ok"] is True
    assert [p["code"] for p in x["peringatan"]] == ["DH_DIPAKAI_PELANGGAN"]
    assert "3 pelanggan" in x["peringatan"][0]["message"]
    assert x["rencana"]["dari"] == "aktif" and x["rencana"]["ke"] == "nonaktif"


def test_nonaktifkan_bawaan_peringatan_tambahan_dan_sudah_pada_keadaan_itu_blok():
    x = HA.rencana_status(_r(is_default=True), 0, ke_aktif=False)
    assert [p["code"] for p in x["peringatan"]] == ["DH_BAWAAN_DINONAKTIFKAN"]
    y = HA.rencana_status(_r(is_active=False), 0, ke_aktif=False)
    assert [b["code"] for b in y["blocks"]] == ["DH_SUDAH_NONAKTIF"] and y["ok"] is False
    z = HA.rencana_status(_r(), 0, ke_aktif=True)
    assert [b["code"] for b in z["blocks"]] == ["DH_SUDAH_AKTIF"]
    assert HA.rencana_status(None, 0, ke_aktif=False)["blocks"][0]["code"] == "DH_TAK_ADA"


# --- hapus -----------------------------------------------------------------------------------------------------------
def test_hapus_dipakai_pelanggan_ditolak_menyebut_jumlah():
    x = HA.rencana_hapus(_r(), 4, 10)
    assert x["ok"] is False and x["blocks"][0]["code"] == "DH_DIPAKAI_PELANGGAN" and "4 pelanggan" in x["blocks"][0]["message"]


def test_hapus_bebas_pelanggan_rencana_dan_peringatan_bawaan():
    x = HA.rencana_hapus(_r(is_default=True), 0, 7)
    assert x["ok"] is True and x["rencana"]["baris_terhapus"] == 7 and x["rencana"]["jenis"] == "hapus_permanen"
    assert [p["code"] for p in x["peringatan"]] == ["DH_BAWAAN_DIHAPUS"]


# --- bawaan ----------------------------------------------------------------------------------------------------------
def test_jadikan_bawaan_rencana_sebut_bawaan_lama_dan_blok():
    x = HA.rencana_bawaan(_r(), _r(id="d0", name="Umum", is_default=True))
    assert x["ok"] and x["rencana"]["bawaan_lama"] == "Umum" and x["rencana"]["bawaan_baru"] == "Warung"
    assert HA.rencana_bawaan(_r(is_default=True), None)["blocks"][0]["code"] == "DH_SUDAH_BAWAAN"
    assert HA.rencana_bawaan(_r(is_active=False), None)["blocks"][0]["code"] == "DH_NONAKTIF_BUKAN_BAWAAN"


# --- ubah header -----------------------------------------------------------------------------------------------------
def test_rencana_ubah_header_dari_ke_dan_periode_dan_kode_ganda():
    x = HA.rencana_ubah(_r(), {"name": "Warung Besar", "code": "WARUNG"}, kode_ganda=False)
    assert x["ok"] and x["rencana"]["perubahan"] == [{"medan": "name", "dari": "Warung", "ke": "Warung Besar"}]
    assert HA.rencana_ubah(_r(), {"name": "Warung"}, kode_ganda=False)["rencana"]["perubahan"] == []
    assert HA.rencana_ubah(_r(), {"code": "UMUM"}, kode_ganda=True)["blocks"][0]["code"] == "KODE_DAFTAR_GANDA"
    from datetime import date
    p = HA.rencana_ubah(_r(start_date=date(2026, 5, 1)), {"end_date": date(2026, 4, 1)}, kode_ganda=False)
    assert p["blocks"][0]["code"] == "DH_MASA_BERLAKU"
    assert HA.rencana_ubah(_r(), {"name": "  "}, kode_ganda=False)["blocks"][0]["code"] == "NAMA_DAFTAR_KOSONG"


# --- barang tidak dijual ---------------------------------------------------------------------------------------------
class _ConnBarang:
    def __init__(self, row):
        self.row = row

    async def fetchrow(self, sql, *a):
        assert "FROM products WHERE id = $1 AND tenant_id = $2" in sql
        return self.row


@pytest.mark.asyncio
@pytest.mark.parametrize("row,kode", [({"nama_produk": "Bodrex", "status": "active", "for_sales": False}, "DH_BARANG_TIDAK_DIJUAL"),
                                      ({"nama_produk": "Lama", "status": "inactive", "for_sales": True}, "DH_BARANG_TIDAK_DIJUAL"),
                                      ({"nama_produk": "Ok", "status": "active", "for_sales": True}, None),
                                      ({"nama_produk": "Ok", "status": "active", "for_sales": None}, None),
                                      (None, None)])
async def test_periksa_barang_dijual(row, kode):
    if kode:
        with pytest.raises(Exception) as e:
            await HA.periksa_barang_dijual(_ConnBarang(row), "t1", "p1")
        assert e.value.code == kode and row["nama_produk"] in str(e.value.detail)
    else:
        assert await HA.periksa_barang_dijual(_ConnBarang(row), "t1", "p1") is None


def test_penulis_dan_pratinjau_baris_memakai_penentu_barang_dijual():
    src = inspect.getsource(__import__("app.routers.price_lists", fromlist=["x"]).siapkan_baris_harga)
    assert "HA.periksa_barang_dijual(" in src and "line_id is None" in src     # tambah baris saja; ubah harga baris lama tetap boleh


# --- rute + idempotensi + izin ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("rute", ['"/{price_list_id}/status/preview"', '"/{price_list_id}/status"', '"/{price_list_id}/default/preview"',
                                  '"/{price_list_id}/default"', '"/{price_list_id}/delete/preview"', '"/{price_list_id}/edit/preview"',
                                  '"/{price_list_id}/items/{line_id}/delete/preview"'])
def test_rute_baru_terdaftar(rute):
    assert f"@router.post({rute}" in RUTE, rute


@pytest.mark.parametrize("pratinjau", ["preview_status_daftar", "preview_hapus_daftar", "preview_bawaan_daftar", "preview_ubah_daftar",
                                       "preview_hapus_baris"])
def test_pratinjau_baca_saja_dan_satu_penentu(pratinjau):
    fn = _fn(pratinjau)
    assert "readonly=True" in fn or "_baca_saja(" in fn
    assert "HA.rencana_" in fn and "INSERT" not in fn and "UPDATE" not in fn and "DELETE" not in fn


@pytest.mark.parametrize("fungsi,prefix", [("create_price_list", "DH_BUAT"), ("update_price_list", "DH_UBAH"), ("delete_price_list", "DH_HAPUS"),
                                           ("ubah_status_daftar", "DH_STATUS"), ("jadikan_bawaan_daftar", "DH_BAWAAN"),
                                           ("add_item_to_price_list", "DH_BARIS_TAMBAH"), ("update_price_list_item", "DH_BARIS_UBAH"),
                                           ("remove_item_from_price_list", "DH_BARIS_HAPUS")])
def test_idempotensi_semua_tulis(fungsi, prefix):
    fn = _fn(fungsi)
    assert f'"{prefix}"' in fn and "HA.idem_aksi(" in fn, fungsi


def test_hapus_daftar_menjaga_pelanggan_di_penulis_dan_ukur_dalam_transaksi():
    fn = _fn("delete_price_list")
    assert "HA.tolak_bila_blok(HA.rencana_hapus(" in fn and "HA.terapkan_hapus(" in fn         # penentu SAMA dengan pratinjau, dijaga
    src = inspect.getsource(HA.terapkan_hapus)
    assert "customers" not in src or "price_list_id" in src                  # penulis tak melewatkan penjaga


def test_penulis_status_bawaan_satu_fungsi():
    assert "HA.terapkan_status(" in _fn("ubah_status_daftar") and "HA.terapkan_bawaan(" in _fn("jadikan_bawaan_daftar")
    assert "is_default = false" in inspect.getsource(HA.terapkan_status)     # nonaktif mencabut bawaan
    assert "is_default = false" in inspect.getsource(HA.terapkan_bawaan)     # bawaan lama dicabut sebelum yang baru (indeks unik)


@pytest.mark.parametrize("metode,jalur,huruf", [("POST", "/api/price-lists/ID/status/preview", "U"), ("POST", "/api/price-lists/ID/status", "U"),
                                                ("POST", "/api/price-lists/ID/default/preview", "U"), ("POST", "/api/price-lists/ID/default", "U"),
                                                ("POST", "/api/price-lists/ID/edit/preview", "U"), ("POST", "/api/price-lists/ID/delete/preview", "D"),
                                                ("POST", "/api/price-lists/ID/items/L/delete/preview", "D")])
def test_izin_rute_baru(metode, jalur, huruf):
    assert PM.PermissionMiddleware(lambda *a: None, False)._find_permission(jalur, metode) == ("price_list", huruf), jalur


def test_kode_ganda_diperiksa_di_penulis_dan_pratinjau_ubah_header():
    assert "HA.kode_dipakai(" in _fn("update_price_list") and "HA.kode_dipakai(" in _fn("preview_ubah_daftar")


def test_pratinjau_dibungkus_transaksi_baca_saja_dan_rollback():
    src = inspect.getsource(__import__("app.routers.price_lists", fromlist=["x"])._baca_saja)
    assert "readonly=True" in src and "tr.rollback()" in src                 # pratinjau yang menulis = MERAH (H6 harness + tes ini)
    assert RUTE.count("readonly=True") >= 3                                  # _baca_saja + pratinjau buat + pratinjau baris


def test_peringatan_bentuk_bug207_satu_komponen():
    for x in (HA.rencana_status(_r(is_default=True), 2, ke_aktif=False), HA.rencana_hapus(_r(is_default=True), 0, 1)):
        assert x["peringatan"], x
        for p in x["peringatan"]:
            assert p["butuh_konfirmasi"] is True and p["pesan"] == p["message"] and p["code"].startswith("DH_")
