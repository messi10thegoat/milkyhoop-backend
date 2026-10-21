"""NK potongan BRUTO PKP (putusan pemilik 21 Okt 2026): pecah harga jual + PPN keluaran menurut komposisi pajak faktur asal; Σ persis."""
import asyncio
import inspect
from decimal import Decimal

import pytest

from app.routers import credit_notes as CNR
from app.services import nk_potongan_pajak as NKP

D = Decimal


def _kel(rate, harga_jual, ppn, dpp, kode="k"):
    return {"tax_code_id": kode, "tax_rate": D(rate), "harga_jual": D(harga_jual), "ppn": D(ppn), "dpp": D(dpp),
            "bruto": D(harga_jual) + D(ppn)}


KOMPOSISI = {
    "11% penuh": [_kel(11, "440000", "48400", "440000")],
    "12% x 11/12": [_kel(12, "400000", "44000", "366666.67")],
    "campur kena + tak kena": [_kel(12, "400000", "44000", "366666.67", "a"), _kel(0, "55555", "0", "55555", None)],
    "11% + ongkir 12% 1/1 + nol": [_kel(11, "300000", "33000", "300000", "a"), _kel(12, "25000", "3000", "25000", "b"),
                                    _kel(0, "1000", "0", "1000", None)],
}
BRUTO = ["0.01", "1", "7", "99.99", "111111", "123457", "333333.33", "999999.99"]


@pytest.mark.parametrize("nama", list(KOMPOSISI))
@pytest.mark.parametrize("g", BRUTO)
def test_sigma_persis_dan_rasio_faktur(nama, g):
    kel, G = KOMPOSISI[nama], D(g)
    bag = NKP.pecah_bruto(G, kel)
    assert sum(b["bruto"] for b in bag) == G
    for b in bag:
        assert b["harga_jual"] + b["ppn"] == b["bruto"]
        k = next(k for k in kel if k["tax_code_id"] == b["tax_code_id"] and k["tax_rate"] == b["tax_rate"])
        assert abs(b["ppn"] - b["bruto"] * k["ppn"] / k["bruto"]) <= D("0.005")  # PPN = q2(G_i × T/B)
        if k["ppn"] == 0:
            assert b["ppn"] == 0 and b["dpp"] == b["harga_jual"]


def test_contoh_angka():
    (b,) = NKP.pecah_bruto(D("111000"), KOMPOSISI["12% x 11/12"])
    assert (b["harga_jual"], b["ppn"]) == (D("100000.00"), D("11000.00"))
    assert b["dpp"] == D("91666.67")  # DPP nilai lain 11/12 mengikuti faktur
    (b,) = NKP.pecah_bruto(D("111111"), KOMPOSISI["11% penuh"])
    assert (b["harga_jual"], b["ppn"]) == (D("100100.00"), D("11011.00"))


def test_tanpa_kelompok_atau_nol():
    assert NKP.pecah_bruto(D("0"), KOMPOSISI["11% penuh"]) == []
    assert NKP.pecah_bruto(D("100"), []) == []


@pytest.mark.parametrize("ubah,harap", [
    ({}, True), ({"reason": "damaged"}, False), ({"reason": "pricing_error"}, True),
    ({"item": {"item_id": "x"}}, False), ({"item": {"original_invoice_item_id": "x"}}, False),
    ({"item": {"tax_rate": 11}}, False), ({"item": {"tax_code_id": "x"}}, False), ({"item": {"discount_percent": 5}}, False),
    ({"doc_disc": 10}, False), ({"header_rate": 11}, False)])
def test_bentuk_potongan_bruto(ubah, harap):
    it = {"description": "Potongan", "quantity": 1, "unit_price": 1000, "item_id": None, "tax_rate": 0}
    it.update(ubah.get("item", {}))
    assert NKP.bentuk_potongan_bruto(ubah.get("reason", "discount"), [it], 0, ubah.get("doc_disc", 0), ubah.get("header_rate", 0)) is harap


def test_alasan_potongan_sama_dengan_router():
    assert NKP.ALASAN_POTONGAN == CNR.ALASAN_POTONGAN


class _Conn:
    def __init__(self, pkp, baris, ongkir=None):
        self.pkp, self.baris, self.ongkir = pkp, baris, ongkir

    async def fetchval(self, q, *a):
        assert "is_pkp" in q
        return self.pkp

    async def fetch(self, q, *a):
        return self.baris

    async def fetchrow(self, q, *a):
        return self.ongkir


def _run(coro):
    return asyncio.run(coro)


ITEM = [{"description": "Potongan", "quantity": D("1"), "unit_price": D("111000"), "item_id": None}]
BARIS_12 = [{"tax_code_id": "a", "tax_rate": D("12"), "harga_jual": D("400000"), "ppn": D("44000"), "dpp": D("366666.67")}]


def test_non_pkp_jalur_lama():
    assert _run(NKP.doc_potongan_bruto(_Conn(False, BARIS_12), "t", "f", ITEM)) is None
    assert _run(NKP.doc_potongan_bruto(_Conn(None, BARIS_12), "t", "f", ITEM)) is None


def test_faktur_tanpa_ppn_jalur_lama():
    nol = [{"tax_code_id": None, "tax_rate": D("0"), "harga_jual": D("400000"), "ppn": D("0"), "dpp": D("400000")}]
    assert _run(NKP.doc_potongan_bruto(_Conn(True, nol), "t", "f", ITEM)) is None


def test_pkp_dokumen_bentuk_kalkulator():
    doc = _run(NKP.doc_potongan_bruto(_Conn(True, BARIS_12), "t", "f", ITEM))
    assert doc["total_amount"] == D("111000") and doc["tax_amount"] == D("11000.00") and doc["gross_subtotal"] == D("100000.00")
    (ln,) = doc["items"]
    for k in ("subtotal", "total", "dpp", "dpp_harga_jual", "tax_amount", "tax_rate", "tax_code_id", "unit_price", "quantity"):
        assert k in ln
    assert ln["subtotal"] + ln["tax_amount"] == ln["total"] == D("111000")


def test_ongkir_ikut_kode_barang():
    ongkir = {"ongkir": D("25000"), "ppn": D("2750"), "rate": D("11"), "shipping_tax_code_id": None, "dpp": D("25000")}
    b11 = [{"tax_code_id": "a", "tax_rate": D("11"), "harga_jual": D("440000"), "ppn": D("48400"), "dpp": D("440000")}]
    kel = _run(NKP.kelompok_faktur(_Conn(True, b11, ongkir), "t", "f"))
    assert len(kel) == 1 and kel[0]["harga_jual"] == D("465000") and kel[0]["ppn"] == D("51150")


def test_gabung_tersimpan():
    rows = [{"item_id": None, "original_invoice_item_id": None, "description": "Potongan", "tax_amount": D("10873.89"), "total": D("109727.47")},
            {"item_id": None, "original_invoice_item_id": None, "description": "Potongan", "tax_amount": D("0"), "total": D("13729.53")}]
    (g,) = NKP.gabung_tersimpan(rows)
    assert g["unit_price"] == D("123457.00") and g["tax_rate"] == 0 and g["quantity"] == 1
    assert NKP.gabung_tersimpan([dict(rows[1])]) is None  # tanpa PPN (non-PKP) -> baris tak disentuh
    assert NKP.gabung_tersimpan([dict(rows[0], item_id="x")]) is None


def test_tiga_jalur_memakai_pemicu_yang_sama():
    src = inspect.getsource(CNR)
    assert src.count("reason=body.reason, invoice_id=body.original_invoice_id") == 2  # buat + pratinjau
    assert "reason=_alasan_e, invoice_id=_f_eff" in src  # sunting draf
    assert "NKP.gabung_tersimpan(" in src
    assert "NKP.doc_potongan_bruto(" in inspect.getsource(CNR._cn_doc)
