"""BUG-451: satuan diketik di Kirim Faktur ("kirim 1 peti") -> qty satuan baris; tak dikenal = SATUAN_TAK_DIKENAL (satu penentu pratinjau + tulis)."""
import asyncio
from decimal import Decimal

from app.services import satuan_kirim as SK

IT, PR = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


class Kon:
    async def fetch(self, q, *a):
        if "FROM sales_invoice_items" in q:
            return [{"id": IT, "item_id": PR, "description": "Kopi", "unit": "pcs"}]
        if "product_units" in q:
            return []
        if "FROM products" in q:
            return [{"id": PR, "nama_produk": "Kopi", "dasar": "pcs"}]
        if "unit_conversions" in q:
            return [{"product_id": PR, "base_unit": "pcs", "conversion_unit": "peti", "conversion_factor": 120},
                    {"product_id": PR, "base_unit": "pcs", "conversion_unit": "dus", "conversion_factor": 12}]
        return []


def _jalan(unit, qty="1"):
    return asyncio.run(SK.terapkan(Kon(), "t", "inv", [{"invoice_item_id": IT, "quantity": Decimal(qty), "unit": unit}]))


def test_peti_jadi_pcs_dan_info():
    items, info, blok = _jalan("peti")
    assert not blok and items[0]["quantity"] == 120 and "unit" not in items[0]
    assert info[IT]["unit_input"] == "peti" and info[IT]["quantity_text"] == "1 peti = 120 pcs"


def test_tanpa_unit_apa_adanya():
    items, info, blok = _jalan(None, "5")
    assert items[0]["quantity"] == 5 and not info and not blok


def test_satuan_tak_dikenal_diblok():
    _, _, blok = _jalan("karung")
    assert blok[0]["code"] == "SATUAN_TAK_DIKENAL" and 'Satuan "karung" tak dikenal' in blok[0]["message"] and "pcs" in blok[0]["message"]


def test_satuan_sama_baris_tak_dikonversi():
    items, _, blok = _jalan("pcs", "7")
    assert not blok and items[0]["quantity"] == 7


def test_daftar_satuan_di_pesan_tanpa_ganda():
    assert SK._unik(["Peti", "dus", "peti", "Palet", "DUS"]) == ["Peti", "dus", "Palet"]
