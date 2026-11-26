"""BUG-381: PUT /items/{id} + edit/preview -- null/"" mengosongkan barcode/kategori/merek/deskripsi/reorder_level; harga & nama TIDAK (422);
Tinjau menampilkan "X → (kosong)". Satu penentu BA.normal_kosong di penulis DAN pratinjau."""
import pytest
from fastapi import HTTPException

from app.services import barang_aksi as BA
from pathlib import Path

ROUTER = (Path(__file__).parents[2] / "app" / "routers" / "items.py").read_text()
LAMA = {"nama_produk": "Kaos", "barcode": "899", "kategori": "Atasan", "deskripsi": "katun", "reorder_level": 5, "sales_price": 1000, "merek": None}


def test_null_dan_string_kosong_mengosongkan_medan_opsional():
    assert BA.normal_kosong({"barcode": None, "kategori": "  ", "deskripsi": "", "reorder_level": None}) == \
        {"barcode": None, "kategori": None, "deskripsi": None, "reorder_level": None}


@pytest.mark.parametrize("medan", ["sales_price", "purchase_price", "name"])
def test_harga_dan_nama_tidak_boleh_dikosongkan(medan):
    with pytest.raises(HTTPException) as e:
        BA.normal_kosong({medan: None})
    assert e.value.status_code == 422 and "tidak boleh dikosongkan" in e.value.detail


def test_null_medan_lain_diabaikan_bukan_menulis_null():
    assert BA.normal_kosong({"item_type": None, "sales_tax_id": None, "barcode": "1"}) == {"barcode": "1"}


def test_pratinjau_menampilkan_kosong():
    r = BA.beri_teks_ubah(BA.rencana_ubah(LAMA, BA.normal_kosong({"barcode": None, "reorder_level": None, "merek": "Nike"})), {})
    p = {x["medan"]: x for x in r["rencana"]["perubahan"]}
    assert (p["barcode"]["dari_text"], p["barcode"]["ke_text"]) == ("899", "(kosong)")
    assert p["reorder_level"]["ke_text"] == "(kosong)" and p["merek"]["dari_text"] == "(kosong)"


def test_kosongkan_yang_sudah_kosong_tanpa_perubahan():
    r = BA.rencana_ubah({**LAMA, "reorder_level": 0}, {"merek": None, "reorder_level": None})
    assert r["rencana"]["perubahan"] == [] and r["peringatan"][0]["code"] == "TANPA_PERUBAHAN"


def test_penulis_dan_pratinjau_memakai_penentu_sama():
    assert ROUTER.count("BA.normal_kosong(") == 2
