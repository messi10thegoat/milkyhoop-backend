"""BUG-069 (putusan pemilik LANGSUNG 7 Nov): retur dari faktur pembelian LUNAS = saldo kredit vendor. ND tetap DARI faktur itu
(return_bill_id = sumber retur: batas qty, gudang, WAC) tetapi TANPA atribusi hutang (original_bill_id NULL) -> compute_ap cabang 5,
dipotong ke tagihan berikut / diterima tunai lewat jalur yang ada. Sebagian lunas -> blok MELEBIHI_SISA_TAGIHAN dengan jalan keluar."""
import asyncio
import uuid
from decimal import Decimal as D

from app.routers import vendor_credits as RVC
from app.services import nota_debit as ND
from app.services import nota_debit_gudang as NG
from app.services import nota_debit_rencana as NDR

BILL, VEN, ITEM, PROD = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


class _Conn:
    async def fetchrow(self, sql, *a):
        return {"id": uuid.UUID(BILL), "invoice_number": "BILL-2611-0002", "vendor_id": uuid.UUID(VEN), "vendor_name": "PT Sumber",
                "status_v2": "paid", "tax_rate": 0, "tax_code_id": None, "tax_inclusive": False, "dpp_manual": None,
                "invoice_discount_total": 0, "cash_discount_total": 0, "amount": D("2400000")}

    async def fetch(self, sql, *a):
        return [{"id": uuid.UUID(ITEM), "product_id": uuid.UUID(PROD), "product_code": "MKT", "product_name": "Minyak", "description": "Minyak",
                 "quantity": D("2"), "unit": "karton", "unit_price": D("1200000"), "discount_percent": 0, "discount_amount": 0,
                 "tax_rate": 0, "tax_code_id": None}]


def _rakit(monkeypatch, sisa):
    async def s(conn, tid, bid):
        return sisa

    async def catatan(conn, tid, bid, rows):
        return []
    monkeypatch.setattr(ND, "sisa_tagihan_jurnal", s)
    monkeypatch.setattr(NG, "catatan_sudah_diretur", catatan)
    body = RVC.VendorCreditPreviewRequest(original_bill_id=BILL, reason="return")
    return asyncio.run(RVC.rakit_dari_tagihan(_Conn(), "t", body))


def test_faktur_lunas_nd_bebas_dengan_sumber_retur(monkeypatch):
    r = _rakit(monkeypatch, D("0"))
    assert r["create"]["original_bill_id"] is None and r["create"]["return_bill_id"] == BILL
    assert [n["code"] for n in r["notes"]] == ["ND_JADI_KREDIT_VENDOR"]
    assert "saldo kredit vendor" in r["notes"][0]["message"] and "BILL-2611-0002" in r["notes"][0]["message"]


def test_faktur_belum_lunas_tetap_teratribusi(monkeypatch):
    r = _rakit(monkeypatch, D("2400000"))
    assert r["create"]["original_bill_id"] == BILL and "return_bill_id" not in r["create"]
    assert not [n for n in r["notes"] if n["code"] == "ND_JADI_KREDIT_VENDOR"]


def test_tagihan_tak_aktif_bukan_lunas(monkeypatch):
    r = _rakit(monkeypatch, None)   # None = draf/batal/dibalik -> BUKAN kredit vendor
    assert not [n for n in r["notes"] if n["code"] == "ND_JADI_KREDIT_VENDOR"]
    assert r["create"] is None or r["create"]["original_bill_id"] == BILL


def test_sumber_retur_utamakan_return_bill():
    assert NG.sumber_retur({"return_bill_id": "R", "return_bill_number": "BILL-R", "original_bill_id": None}) == ("R", "BILL-R")
    assert NG.sumber_retur({"return_bill_id": None, "original_bill_id": "O", "original_bill_number": "BILL-O"}) == ("O", "BILL-O")


def test_sebagian_lunas_kalimat_jalan_keluar():
    k = NDR.kalimat_nd_melebihi_sisa_tagihan(D("500000"), D("2664000"), "BILL-2611-0007")
    assert "Kurangi qty retur" in k and "saldo kredit vendor" in k
