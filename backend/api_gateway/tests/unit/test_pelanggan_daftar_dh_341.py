"""BUG-341 (opsi B-ringan, aditif): daftar pelanggan membawa price_list_id + price_list_name, bentuk SAMA dgn detail; tanpa ubah resolver/FK/migrasi."""
import inspect

from app.routers import customers as C
from app.schemas.customers import CustomerListItem

UID = "11111111-1111-1111-1111-111111111111"


def test_tanpa_daftar_bawaan_null_null():
    assert C.medan_dh_pelanggan({"price_list_id": None, "price_list_name": None}) == {"price_list_id": None, "price_list_name": None}


def test_daftar_aktif_id_str_dan_nama():
    assert C.medan_dh_pelanggan({"price_list_id": UID, "price_list_name": "Grosir"}) == {"price_list_id": UID, "price_list_name": "Grosir"}


def test_daftar_nonaktif_tetap_bernama_seperti_detail():
    # nama diambil apa adanya dari price_lists (tak menyaring is_active) = perilaku detail sebelumnya
    assert C.medan_dh_pelanggan({"price_list_id": UID, "price_list_name": "Lama (nonaktif)"})["price_list_name"] == "Lama (nonaktif)"


def test_daftar_terhapus_fk_set_null_keduanya_null():
    assert C.medan_dh_pelanggan({"price_list_id": None, "price_list_name": None}) == {"price_list_id": None, "price_list_name": None}


def test_sql_satu_subselect_bertenant_tanpa_join_n1():
    s = " ".join(C.KOLOM_DH_PELANGGAN.split())
    assert "pl.tenant_id = customers.tenant_id" in s and "AS price_list_name" in s and "price_list_id," in s and "JOIN" not in s


def test_daftar_dan_detail_memakai_helper_dan_kolom_yang_sama():
    for fn in (C.list_customers, C.get_customer):
        assert "medan_dh_pelanggan(row)" in inspect.getsource(fn)
    assert "KOLOM_DH_PELANGGAN" in inspect.getsource(C.list_customers)


def test_skema_daftar_memuat_medan_opsional():
    f = CustomerListItem.model_fields
    assert f["price_list_id"].default is None and f["price_list_name"].default is None


def test_341b_label_tinjau_ubah_price_list_id_kategori_harga_kunci_tetap():
    h = C.perubahan_pelanggan({"price_list_id": None}, {"price_list_id": UID})
    assert h == [{"field": "price_list_id", "label": "Kategori harga", "lama": None, "baru": UID}]


def test_341b_galat_daftar_tak_ditemukan_tetap_teks_lama():
    assert 'detail="Daftar harga tidak ditemukan di usaha ini."' in inspect.getsource(C.pastikan_daftar_harga)
