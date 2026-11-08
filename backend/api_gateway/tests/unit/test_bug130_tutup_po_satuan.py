"""BUG-130 sisi BE (ROW r, 8 Nov 2026): catatan pratinjau Tutup PO dulu "Sisa 2.00 unit belum diterima" -- kata "unit" mati, angka gaya en,
dan sisa DIJUMLAH lintas baris berbeda satuan (melanggar BUG-044). Kini qty baku Indonesia (teks_galat.qty) + satuan BARIS; total hanya
bila satuan seragam, selain itu rincian per baris."""
import asyncio
from decimal import Decimal as D

from app.services import po_rencana as PR


class _K:
    def __init__(self, items):
        self.items = items

    async def fetch(self, sql, *a):
        return self.items


def _i(nama, unit, pesan, terima, tagih):
    return {"description": nama, "unit": unit, "quantity": D(pesan), "quantity_received": D(terima), "quantity_billed": D(tagih)}


def _catatan(items):
    r = asyncio.run(PR.rencana_tutup(_K(items), {"id": "p1", "status": "partial_received"}))
    return {n["code"]: n["message"] for n in r["notes"]}


def test_satu_baris_karton_tanpa_kata_unit_angka_id():
    c = _catatan([_i("Minyak Bimoli 2L", "karton", "5", "3", "1")])
    assert c["SISA_BARANG_TAK_DITERIMA"] == "Sisa 2 karton belum diterima tidak akan ditunggu lagi."
    assert c["DITERIMA_BELUM_DITAGIH"].startswith("2 karton sudah diterima tetapi belum ditagih;")
    assert all("unit" not in m and ".00" not in m for m in c.values())


def test_dua_baris_beda_satuan_dirinci_per_baris_tanpa_jumlah():
    c = _catatan([_i("Minyak Bimoli 2L", "karton", "5", "3", "3"), _i("Gula Pasir", "kg", "1250.5", "0", "0")])
    assert c["SISA_BARANG_TAK_DITERIMA"] == "Sisa belum diterima tidak akan ditunggu lagi: Minyak Bimoli 2L 2 karton, Gula Pasir 1.250,5 kg."
    assert "DITERIMA_BELUM_DITAGIH" not in c


def test_satuan_seragam_dijumlah():
    c = _catatan([_i("A", "karton", "4", "1", "1"), _i("B", "karton", "3", "0", "0")])
    assert c["SISA_BARANG_TAK_DITERIMA"] == "Sisa 6 karton belum diterima tidak akan ditunggu lagi."
