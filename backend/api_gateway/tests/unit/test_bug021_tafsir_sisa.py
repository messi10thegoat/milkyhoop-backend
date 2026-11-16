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


# ---- BUG-276 (17 Nov 2026): satuan tunggal tanpa 'isi' ("UJI Teh Celup, pcs, beli 3rb, jual 4rb") = satuan dasar, bukan "tak terbaca: pcs"
@pytest.mark.parametrize("teks, nama, satuan", [
    ("UJI Teh Celup, pcs, beli 3rb, jual 4rb", "UJI Teh Celup", "pcs"),   # contoh tiket
    ("Teh Celup, pcs", "Teh Celup", "pcs"),                                 # tanpa medan sama sekali
    ("Gula Pasir, kg, jual 15rb", "Gula Pasir", "kg"),
    ("Minyak Sania 1L, botol, beli 17rb, jual 19rb, stok 10", "Minyak Sania 1L", "botol"),
])
def test_satuan_tunggal_menjadi_satuan_dasar(teks, nama, satuan):
    b, bl = _blok(teks)
    assert not bl and not b.get("_galat"), (b, bl)
    assert b["nama"] == nama and b["satuan_dasar"] == satuan


def test_satuan_tunggal_harga_ikut_satuan_dasar_dan_lolos_ke_badan():
    b = IB.tafsir_baris("UJI Teh Celup, pcs, beli 3rb, jual 4rb")
    assert (b["harga_beli"], b["harga_jual"]) == ("3rb", "4rb") and "satuan_besar" not in b
    badan, bl, _ = IB.ke_badan(b)
    assert not bl and badan["base_unit"] == "pcs" and badan["purchase_price"] == 3000.0 and badan["sales_price"] == 4000.0


@pytest.mark.parametrize("teks", ["Minyak Sania beli", "Minyak Sania, stok banyak", "Minyak Sania, kemasan bagus, jual 19rb", "rusak ,,, jual abc", "Minyak, jual, 19rb",
                                   "Teh, pcs, dus, jual 4rb"])
def test_bukan_satuan_tunggal_tetap_blok(teks):
    b, bl = _blok(teks)
    assert bl, (b, bl)            # dua kata/lebih, kata medan, atau dua sisa = tetap TAK_TERBACA (tak ditelan jadi satuan)


def test_satuan_medan_sesudahnya_menang_dan_konv_tak_berubah():
    assert IB.tafsir_baris("Teh Celup, pcs, satuan botol, jual 4rb")["satuan_dasar"] == "botol"
    b = IB.tafsir_baris("bimoli 2L, karton isi 6, beli 210rb, jual 243rb")
    assert (b["nama"], b["satuan_besar"], b["isi"], b["harga_jual_besar"]) == ("bimoli 2L", "karton", "6", "243rb") and b["satuan_dasar"] == "Pcs"


def test_tanda_baca_berlebih_di_sekitar_satuan_tetap_blok():
    b, bl = _blok("Teh ,, pcs, jual 4rb")
    assert any("tanda baca" in m["message"] for m in bl), (b, bl)      # galat tanda baca tak hilang karena satuan tunggal terbaca
