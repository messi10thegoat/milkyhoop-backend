"""Sub-baris daftar Pembelian di bawah vendor (28 Okt 2026, tiket MASTER): baris /bills membawa ref_no (no. faktur vendor; sudah ada
di buat/detail) dan baris /bill-payments membawa reference_number. Aditif baca saja -- kolom ikut SELECT daftar dan diteruskan ke skema."""
import inspect
import re

from app.routers import bill_payments as BP
from app.schemas.bill_payments import BillPaymentListItem
from app.schemas.bills import BillListItem
from app.services import bills_service as BS


def test_skema_baris_punya_rujukan_opsional():
    assert "ref_no" in BillListItem.model_fields and BillListItem.model_fields["ref_no"].default is None
    assert "reference_number" in BillPaymentListItem.model_fields and BillPaymentListItem.model_fields["reference_number"].default is None


def test_daftar_tagihan_select_dan_teruskan_ref_no():
    src = inspect.getsource(BS.BillsService.list_bills)
    assert re.search(r"\bb\.ref_no\b", src), "SELECT daftar tagihan wajib memuat b.ref_no"
    assert re.search(r"\"ref_no\":\s*row\[\"ref_no\"\]", src), "baris daftar tagihan wajib meneruskan ref_no"


def test_daftar_bayar_select_dan_teruskan_reference_number():
    src = inspect.getsource(BP.list_bill_payments)
    assert re.search(r"\bbp\.reference_number\b", src), "SELECT daftar pembayaran wajib memuat bp.reference_number"
    assert re.search(r"reference_number=r\[\"reference_number\"\]", src), "baris daftar pembayaran wajib meneruskan reference_number"


def test_baris_tanpa_rujukan_tetap_sah():
    b = BillPaymentListItem(id="x", payment_number="PAY-1", vendor_name="V", payment_date="2026-10-28", payment_method="cash",
                            total_amount=1, allocated_amount=0, unapplied_amount=1, status="posted", created_at="2026-10-28")
    assert b.reference_number is None
    assert BillPaymentListItem(**{**b.model_dump(), "reference_number": "TRF-9"}).reference_number == "TRF-9"
