"""Item 7 Pembelian (25 Okt 2026, izin pemilik LANGSUNG "Approve HALF_UP 0.01"): int() uang -> Decimal 0.01 HALF_UP (Law 9), simetri NK
(credit_notes._q2 / sales_doc_calc.q2). Dampak terukur 25 Okt: 0 dokumen ber-sen di kaos + grapgrap-manado -> hanya dokumen BARU berhitung
pecahan yang berubah. Perilaku nyata = harness scripts/ops/uji-nyata-p2p7."""
import ast
import asyncio
import inspect
import uuid
from decimal import Decimal
from typing import Union

from app.routers import bill_payments as BP
from app.routers import credit_notes as CN
from app.routers import vendor_credits as VC
from app.schemas import bill_payments as SBP
from app.schemas import bills as SB
from app.schemas import vendor_credits as SVC
from app.schemas.vendor_credits import CreateVendorCreditRequest
from app.services import bayar_keluar_rencana as BK
from app.services import bills_service as BS
from app.services import nota_debit_rencana as NDR

D = Decimal


def _int_dipanggil(fn) -> list:
    src = inspect.getsource(fn)
    pohon = ast.parse(src if not src.startswith(" ") else "if 1:\n" + src)
    return [ast.unparse(c) for c in ast.walk(pohon) if isinstance(c, ast.Call) and getattr(c.func, "id", None) == "int"]


def test_baris_nd_sama_dengan_nk_tanpa_potong():
    it = {"description": "x", "quantity": D("3"), "unit_price": 3333, "discount_percent": 5, "tax_rate": 11}
    nd, nk = VC.calculate_item_totals(dict(it)), CN.calculate_item_totals(dict(it))
    for k in ("subtotal", "discount_amount", "tax_amount", "total"):
        assert nd[k] == nk[k], k
    assert nd["discount_amount"] == D("499.95") and nd["tax_amount"] == D("1044.89") and nd["total"] == D("10543.94")


class _Conn:
    async def fetchval(self, q, *a):
        return 1


def test_susun_nd_diskon_pajak_dokumen_half_up():
    b = CreateVendorCreditRequest(vendor_id=str(uuid.uuid4()), vendor_name="V", vendor_credit_date="2026-10-25", reason="other",
                                  items=[{"description": "x", "quantity": 3, "unit_price": 3333}], discount_percent=10, tax_rate=11)
    s = asyncio.run(VC._susun_nd(_Conn(), {"tenant_id": "t"}, b, NDR.Blok(False)))
    assert s["subtotal"] == D("9999.00") and s["discount_amount"] == D("999.90")  # dulu int() -> 999
    assert s["tax_amount"] == D("989.90") and s["total_amount"] == D("9989.00")  # 8999.10 x 11% = 989.901 -> 989.90


def test_sisa_tagihan_validasi_tanpa_potong(monkeypatch):
    import app.services.tagihan_daftar as TD
    import app.services.nota_debit as ND

    async def sisa(*a, **k):
        return D("274.50")
    monkeypatch.setattr(TD, "sisa_tagihan_jurnal", sisa)
    monkeypatch.setattr(ND, "sisa_tagihan_jurnal", sisa)
    bid = uuid.uuid4()
    assert asyncio.run(BK.sisa_tagihan(None, "t", bid)) == D("274.50")
    assert asyncio.run(BP.get_bill_remaining_from_journal(None, "t", bid)) == D("274.50")
    assert asyncio.run(VC.get_bill_remaining_from_journal(None, "t", bid)) == D("274.50")


def test_uang_tampilan_half_up():
    for f in (VC._uang, BP._uang, BS._uang):
        assert f(D("999.905")) == 999.91 and f(None) == 0 and f(D("1704006.00")) == 1704006
        assert isinstance(f(D("1704006.00")), int) and isinstance(f(D("999.90")), float)  # JSON bulat "1704006" sama seperti dulu


def test_nol_int_uang_di_fungsi_tersentuh():
    for fn in (VC.calculate_item_totals, VC._susun_nd, VC.update_vendor_credit, VC.get_vendor_credits_summary,
               BS.BillsService.get_outstanding_summary, BS.BillsService.get_summary, BS.BillsService.list_bills,
               BS.BillsService.record_payment, BK.sisa_tagihan, BP.get_bill_remaining_from_journal, BP.get_vendor_open_bills, BP.get_payment_journal_entries):
        assert _int_dipanggil(fn) == [], (fn.__qualname__, _int_dipanggil(fn))


def test_skema_respons_uang_float():
    for cls, medan in ((SVC.VendorCreditItemResponse, ("unit_price", "discount_amount", "tax_amount", "subtotal", "total")),
                       (SVC.VendorCreditListItem, ("total_amount", "amount_applied", "amount_refunded", "remaining_amount")),
                       (SVC.VendorCreditDetail, ("subtotal", "discount_amount", "tax_amount", "total_amount", "remaining_amount")),
                       (SBP.OpenBillItem, ("total_amount", "paid_amount", "remaining_amount")),
                       (SB.BillListItem, ("amount", "amount_paid", "amount_due"))):
        for m in medan:
            assert cls.model_fields[m].annotation == Union[int, float], (cls.__name__, m)
    # bulat tetap JSON int (FE/stub teks tak berubah), ber-sen float
    d = SB.BillListItem.model_fields
    assert set(d) >= {"amount"}
    from pydantic import TypeAdapter
    ta = TypeAdapter(Union[int, float])
    assert ta.dump_json(ta.validate_python(D("50000.00"))) == b"50000" and ta.dump_json(ta.validate_python(D("999.90"))) == b"999.9"


def test_kontrol_pemindai_int_bisa_gagal():
    def contoh(row):
        return int(row["amount"])
    assert _int_dipanggil(contoh) == ["int(row['amount'])"]


def test_sisa_bulat_tetap_tampil_seperti_dulu(monkeypatch):
    import app.services.tagihan_daftar as TD

    async def sisa(*a, **k):
        return D("50000.00")
    monkeypatch.setattr(TD, "sisa_tagihan_jurnal", sisa)
    v = asyncio.run(BK.sisa_tagihan(None, "t", uuid.uuid4()))
    assert v == 50000 and str(v) == "50000" and f"Sisa tagihan: {v}" == "Sisa tagihan: 50000"
