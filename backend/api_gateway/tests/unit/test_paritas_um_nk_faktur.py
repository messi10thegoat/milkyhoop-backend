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


# ---- 21 Okt (MASTER): rincian JURNAL UM (diterapkan/dikembalikan) + NK (terpakai/dikembalikan), daftar & detail
def test_rincian_um_diturunkan_dari_sql_sisa():
    from app.services import uang_muka_daftar as U
    s = U.sql_rincian_um()
    assert s.startswith(CD.SQL_SISA_UANG_MUKA[:CD.SQL_SISA_UANG_MUKA.index("baris AS (")])  # pasangan & keefektifan SAMA
    assert "'DEPOSIT_APPLICATION'" in s and "'DEPOSIT_REFUND'" in s and "amount_applied" not in s and "amount_refunded" not in s


def test_rincian_nk_jurnal_bukan_cache():
    from app.services import nota_kredit_daftar as N
    assert "is_effective_journal(je.id)" in N.SQL_RINCIAN_NK and "'CREDIT_NOTE_REFUND'" in N.SQL_RINCIAN_NK
    assert "amount_applied" not in N.SQL_RINCIAN_NK and "amount_refunded" not in N.SQL_RINCIAN_NK


def test_rincian_dipakai_daftar_dan_detail():
    for fn in (CD.list_customer_deposits, CD.get_customer_deposit):
        assert "_umd.rincian_um_many(" in inspect.getsource(fn) and "_umd.medan_rincian(" in inspect.getsource(fn)
    for fn in (CN.list_credit_notes, CN.get_credit_note):
        assert "_nkd.rincian_nk_many(" in inspect.getsource(fn) and "_nkd.medan_rincian(" in inspect.getsource(fn)
    from app.schemas.credit_notes import CreditNoteListItem
    from app.schemas.customer_deposits import CustomerDepositListItem
    assert {"diterapkan", "dikembalikan"} <= set(CustomerDepositListItem.model_fields) & set(CustomerDepositDetail.model_fields)
    assert {"terpakai", "dikembalikan"} <= set(CreditNoteListItem.model_fields) & set(CreditNoteDetail.model_fields)
