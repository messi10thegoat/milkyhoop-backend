"""BUG-421 kelas: pratinjau ubah barang merinci konversi satuan (tambah / ubah faktor / harga per satuan / nonaktif) + lacak seri/batch/kedaluwarsa/metode."""
from decimal import Decimal

from app.services import barang_aksi as BA

LAMA = {"base_unit": "pcs", "track_serial": False, "track_batches": False, "track_expiry": False, "costing_method": "weighted_average",
        "_konversi": [{"id": "a", "conversion_unit": "dus", "conversion_factor": Decimal("12"), "sales_price": None, "purchase_price": None},
                      {"id": "b", "conversion_unit": "pak", "conversion_factor": Decimal("6"), "sales_price": Decimal("9000"), "purchase_price": None}]}


def teks(baru):
    r = BA.beri_teks_ubah(BA.rencana_ubah(dict(LAMA), baru), {})
    return [(p["medan_text"], p["dari_text"], p["ke_text"]) for p in r["rencana"]["perubahan"]], r


def test_konversi_saja_tidak_lagi_tanpa_perubahan():
    rinci, r = teks({"conversions": [{"id": "a", "conversion_unit": "dus", "conversion_factor": 10},
                                     {"id": "b", "conversion_unit": "pak", "conversion_factor": 6, "sales_price": 9500},
                                     {"conversion_unit": "karton", "conversion_factor": 120}]})
    assert ("Konversi satuan", "1 dus = 12 pcs", "1 dus = 10 pcs") in rinci
    assert ("Konversi satuan", "(kosong)", "+ 1 karton = 120 pcs") in rinci
    assert ("Konversi satuan", "harga jual per pak: 9000", "harga jual per pak: 9500") in rinci
    assert not [b for b in r["peringatan"] if b.get("code") == "TANPA_PERUBAHAN"]


def test_konversi_tak_dikirim_atau_nonaktif_dirinci():
    rinci, _ = teks({"conversions": [{"id": "a", "conversion_unit": "dus", "conversion_factor": 12, "is_active": False}]})
    assert ("Konversi satuan", "1 dus = 12 pcs", "dus dinonaktifkan") in rinci and ("Konversi satuan", "1 pak = 6 pcs", "pak dinonaktifkan") in rinci


def test_konversi_sama_tanpa_perubahan():
    rinci, r = teks({"conversions": [{"id": "a", "conversion_unit": "dus", "conversion_factor": 12}, {"conversion_unit": "PAK", "conversion_factor": 6}]})
    assert [x for x in rinci if x[0] == "Konversi satuan"] == [("Konversi satuan", "1 pak = 6 pcs", "1 PAK = 6 pcs")]


def test_lacak_dirinci():
    rinci, _ = teks({"track_serial": True, "track_batches": True, "track_expiry": True, "costing_method": "fifo"})
    assert {("Lacak nomor seri", "Tidak", "Ya"), ("Lacak batch", "Tidak", "Ya"), ("Lacak kedaluwarsa", "Tidak", "Ya"),
            ("Metode biaya", "Rata-rata", "FIFO")} <= set(rinci)
