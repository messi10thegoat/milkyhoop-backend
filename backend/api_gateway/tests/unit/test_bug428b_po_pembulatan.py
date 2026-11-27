"""BUG-428b (ikut Accurate, pola tagihan c-2): TOTAL PO dibulatkan ke presisi tenant; baris tetap bersen; selisih tampil (PO tanpa jurnal)."""
import asyncio
from decimal import Decimal

from app.services import po_rencana as PR


class Kon:
    def __init__(self, fitur=()):
        self.fitur = list(fitur)

    async def fetch(self, sql, *a):
        return [{"feature": f} for f in self.fitur]


def hitung(fitur=()):
    baris = [{"subtotal": Decimal("840000.24"), "discount_amount": Decimal("0"), "tax_amount": Decimal("0"), "total": Decimal("840000.24")}]
    return asyncio.run(PR.hitung_po(Kon(fitur), "t", baris))


def test_total_dibulatkan_rupiah_baris_tetap():
    h = hitung()
    assert h["header"]["total_amount"] == Decimal("840000") and h["header"]["pembulatan"] == Decimal("-0.24")
    assert h["baris"][0]["total"] == Decimal("840000.24")
    j = PR.hitung_json(h)
    assert j["total_amount"] == 840000.0 and j["pembulatan_text"] == "Pembulatan -Rp 0,24"


def test_tenant_bersen_tidak_dibulatkan():
    h = hitung(["uang_bersen"])
    assert h["header"]["total_amount"] == Decimal("840000.24") and h["header"]["pembulatan"] == Decimal("0")
    assert PR.hitung_json(h)["pembulatan_text"] is None
