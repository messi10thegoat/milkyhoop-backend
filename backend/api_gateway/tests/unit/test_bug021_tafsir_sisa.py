"""BUG-021 temuan FRONTEND (6 Nov malam): tafsir_baris menelan teks tak dikenal ke NAMA tanpa blok -- "rusak ,,, jual abc" -> barang
bernama itu, Pcs, nol galat. Kini: kata medan (jual/beli/stok/kode/kategori/satuan/tipe) yang tak terbaca dan bagian/tanda baca sisa
di depan nama = blok per baris berkalimat (TAK_TERBACA), tak pernah jadi nama diam-diam."""
import pytest

from app.services import impor_barang as IB


def _blok(teks):
    b = IB.tafsir_baris(teks)
    _, bl, _ = IB.ke_badan(b)
    return b, [x for x in bl if x["code"] == "TAK_TERBACA"]


@pytest.mark.parametrize("teks, potongan", [
    ("rusak ,,, jual abc", "jual abc"),      # contoh FRONTEND
    ("Minyak Sania jual abc", "jual abc"),   # harga tak terbaca tanpa koma
    ("Minyak Sania beli", "beli"),           # kata medan tanpa nilai
    ("Minyak Sania, stok banyak", "stok banyak"),
    ("Minyak Sania, kemasan bagus, jual 19rb", "kemasan bagus"),  # bagian sisa sebelum medan pertama
])
def test_sisa_tak_dikenal_jadi_blok_bukan_nama(teks, potongan):
    b, bl = _blok(teks)
    assert bl and any(potongan in x["message"] for x in bl), (b, bl)
    assert potongan not in b["nama"]


@pytest.mark.parametrize("teks", ["rusak ,,, jual 19rb", "Minyak ;; beli 17rb", "Minyak,, jual 19rb"])
def test_tanda_baca_sisa_jadi_blok(teks):
    b, bl = _blok(teks)
    assert bl and any("tanda baca" in x["message"] for x in bl), (b, bl)


@pytest.mark.parametrize("teks, nama", [
    ("bimoli 2L, karton isi 6, beli 210rb, jual 243rb", "bimoli 2L"),
    ("Filma 2L karton isi 6, beli 205rb, jual 238rb", "Filma 2L"),
    ("Minyak Sania 1L", "Minyak Sania 1L"),
    ("Minyak isi ulang 1L jual 19rb", "Minyak isi ulang 1L"),  # "isi" bagian nama (tanpa angka) tetap nama
    ("Kopi Kapal Api, jual 15rb", "Kopi Kapal Api"),
])
def test_baris_sah_tetap_tanpa_galat(teks, nama):
    b, bl = _blok(teks)
    assert not bl and b["nama"] == nama, (b, bl)
