"""PARITAS detail == baris daftar (21 Okt 2026, MASTER): Uang Muka, Nota Kredit, Faktur. Sumber SAMA dengan baris daftar.
Harness nyata (SELURUH medan baris, semua baris kaos+grapgrap): scripts/ops/uji-nyata-paritas-s2.{py,sh}."""
import inspect

from app.routers import credit_notes as CN, customer_deposits as CD, sales_invoices as SI
from app.schemas.credit_notes import CreditNoteDetail
from app.schemas.customer_deposits import CustomerDepositDetail


def test_um_detail_documents_dari_kelompok_yang_sama():
    d, l = inspect.getsource(CD.get_customer_deposit), inspect.getsource(CD.list_customer_deposits)
    for src in (d, l):
        assert "_umd.kelompok_untuk(" in src and "_umd.documents_baris(" in src
    assert {"documents", "doc_count"} <= set(CustomerDepositDetail.model_fields)


def test_nk_detail_documents_dan_void_reason():
    d, l = inspect.getsource(CN.get_credit_note), inspect.getsource(CN.list_credit_notes)
    for src in (d, l):
        assert "_nkd.kelompok_untuk(" in src and "_documents_baris(" in src
    assert '"void_reason": cn["voided_reason"]' in d and '"void_reason": row["voided_reason"]' in l
    assert {"documents", "doc_count", "void_reason"} <= set(CreditNoteDetail.model_fields)


def test_faktur_detail_outstanding_format_sama():
    d, l = inspect.getsource(SI.get_invoice), inspect.getsource(SI.list_invoices)
    assert 'f"{Decimal(str(_sisa_daftar)):.2f}"' in d and "f\"{Decimal(str(row['sisa_faktur'])):.2f}\"" in l
    assert "_fd.sisa_baris(" in d  # sisa detail = aturan baris daftar
