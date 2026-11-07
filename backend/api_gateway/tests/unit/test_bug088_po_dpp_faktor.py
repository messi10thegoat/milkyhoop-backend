"""BUG-088 REOPEN (8 Nov 2026, konfirmasi pemilik LANGSUNG): PPN 12% masukan di PO = DPP 11/12 (PMK 131/2024), SAMA dengan tagihan.
Dulu kalkulator PO (po_hitung via /calculate, create, PATCH) memakai tarif saja -> 12% x DPP penuh (230.400 atas 1.920.000), dan
tagih-dari-PO per BARIS juga tanpa faktor; tagih-dari-PO HEADER + tagihan manual sudah 11/12. SATU penentu: po_rencana.faktor_masukan
(kode_pajak_masukan(tarif) -> resolve_dpp_factor 'input')."""
import inspect
import uuid
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.services import grn_rencana as G
from app.services import po_hitung as PH
from app.services import po_rencana as PR

T = "pt-kita-nyawit-bersama"
A = uuid.uuid4()
APP = Path(PR.__file__).resolve().parents[1]
BARIS = {"description": "UJI4 Minyak Goreng A 1L", "quantity": D("10"), "unit_price": D("192000"), "discount_percent": 0, "discount_amount": D("0")}


@pytest.fixture
def faktor(monkeypatch):
    """Kode masukan nyawit: PPN-12-IN 11/12, PPN-11-IN 1/1 (diukur 8 Nov)."""
    async def resolve(conn, tid, kode, rate, arah, cache=None):
        assert arah == "input" and kode == f"kode-{int(D(str(rate)))}-in"
        return (11, 12) if D(str(rate)) == D("12") else (1, 1)
    monkeypatch.setattr("app.services.tax_factor.resolve_dpp_factor", resolve)
    monkeypatch.setattr("app.services.bills_service.resolve_dpp_factor", resolve)  # _bill_line_tax (rumus tagihan manual)


class Kon:
    def __init__(self, baris=()):
        self.baris = list(baris)

    async def fetchval(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM tax_codes" in q and "direction = 'input'" in q:
            return {D("11"): "kode-11-in", D("12"): "kode-12-in"}.get(D(str(a[1])))
        raise AssertionError(q[:100])

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items WHERE id = $1 AND po_id = $2" in q:
            return next((b for b in self.baris if b["id"] == a[0]), None)
        raise AssertionError(q[:100])

    async def fetch(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_order_items WHERE po_id = $1" in q:
            return self.baris
        raise AssertionError(q[:100])


def test_kalkulator_murni_menerima_faktor():
    b = PH.hitung_baris({**BARIS, "tax_rate": 12}, faktor=(11, 12))
    assert (b["subtotal"], b["tax_amount"], b["total"]) == (D("1920000.00"), D("211200.00"), D("2131200.00"))
    h = PH.hitung_header([PH.hitung_baris({**BARIS, "tax_rate": 0})], 0, 0, 12, faktor=(11, 12))
    assert (h["tax_amount"], h["total_amount"]) == (D("211200.00"), D("2131200.00"))
    assert PH.hitung_baris({**BARIS, "tax_rate": 11})["tax_amount"] == D("211200.00")       # bawaan (1,1) = perilaku lama


@pytest.mark.asyncio
@pytest.mark.parametrize("tarif_baris,tarif_header", [(12, 0), (0, 12)])
async def test_po_ppn12_dpp_11_12_header_dan_baris(faktor, tarif_baris, tarif_header):
    h = await PR.hitung_po(Kon(), T, [{**BARIS, "tax_rate": tarif_baris}], 0, 0, tarif_header)
    assert (h["header"]["tax_amount"], h["header"]["total_amount"]) == (D("211200.00"), D("2131200.00"))   # dulu 230.400 / 2.150.400


@pytest.mark.asyncio
@pytest.mark.parametrize("tarif_baris,tarif_header", [(11, 0), (0, 11)])
async def test_kontrol_ppn11_dpp_penuh_tak_berubah(faktor, tarif_baris, tarif_header):
    h = await PR.hitung_po(Kon(), T, [{**BARIS, "tax_rate": tarif_baris}], 0, 0, tarif_header)
    assert (h["header"]["tax_amount"], h["header"]["total_amount"]) == (D("211200.00"), D("2131200.00"))


@pytest.mark.asyncio
async def test_tagih_dari_po_baris_ppn12_sama_dengan_tagihan_manual(faktor, monkeypatch):
    async def bt(conn, tid, ids):
        return {str(i): {"dipesan": D("10"), "diterima": D("0"), "ada_grn": False, "batas": D("10"), "dasar": "dipesan",
                         "qty_dasar": D("10"), "sudah_ditagih": D("0")} for i in ids}
    monkeypatch.setattr(G, "batas_tagih", bt)
    poi = {"id": A, "quantity": D("10"), "quantity_billed": D("0"), "unit_price": D("192000"), "discount_percent": D("0"),
           "discount_amount": D("0"), "tax_rate": D("12"), "item_id": uuid.uuid4(), "item_code": "X", "description": "Minyak", "unit": "Karton"}
    po = {"id": uuid.uuid4(), "tenant_id": T, "status": "sent", "tax_rate": D("0"), "po_number": "PO-1"}
    r = await PR.rencana_tagih(Kon([poi]), po, None, kunci=False)
    assert (r["lines"][0]["tax"], r["bill_tax"], r["bill_total"]) == (211200.0, 211200.0, 2131200.0)    # dulu 230.400


def test_router_po_tak_lagi_memanggil_kalkulator_tanpa_faktor():
    src = (APP / "routers" / "purchase_orders.py").read_text()
    assert "PH.hitung_baris(" not in src and "PH.hitung_header(" not in src
    assert src.count("PR.hitung_po(") >= 3                                          # /calculate, create, PATCH
    assert "PH.hitung_baris(" not in inspect.getsource(PR.rencana_ubah) and "hitung_po(" in inspect.getsource(PR.rencana_ubah)
