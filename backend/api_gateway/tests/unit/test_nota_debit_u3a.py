"""Nota Debit U3a (22 Okt 2026): terapkan/lepas tanpa jurnal, sisa jurnal, void lengkap, refund bank, kolom hantu, izin rute, satu sumber sisa
tagihan. Siklus nyata: scripts/ops/uji-nyata-nota-debit-u3a.sh (DB salinan + V443)."""
import inspect
from pathlib import Path

import pytest

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import bill_payments as BP
from app.routers import tax_invoices as TI
from app.routers import vendor_credits as VC
from app.services import nota_debit as ND
from app.services import tagihan_daftar as TD

MIG = Path(__file__).resolve().parents[3] / "migrations"


@pytest.mark.parametrize("jalur,harap", [
    ("/api/vendor-credits/x/post", ("debit_note", "P")),
    ("/api/vendor-credits/x/receive-refund", ("debit_note", "P")),
    ("/api/vendor-credits/x/void", ("debit_note", "V")),
    ("/api/vendor-credits/x/unapply", ("debit_note", "V")),
    ("/api/vendor-credits/x/apply", ("debit_note", "A")),
    ("/api/vendor-credits", ("debit_note", "C")),
    ("/api/vendor-credits/x/create-tax-invoice", ("debit_note", "C")),
])
def test_izin_rute_kembar_u5e(jalur, harap):
    r = izin_untuk_jalur(jalur, "POST")
    assert (r[0], r[1]) == harap, r


def test_router_mendelegasikan_ke_inti():
    assert "ND.terapkan_nota_debit(" in inspect.getsource(VC.apply_vendor_credit)
    assert "len(body.applications) != 1" in inspect.getsource(VC.apply_vendor_credit)
    assert "ND.lepas_nota_debit(" in inspect.getsource(VC.unapply_vendor_credit)
    assert "ND.balik_hpp_stok_pajak(" in inspect.getsource(VC.void_vendor_credit)


def test_terapkan_cek_tautan_sebelum_status_dan_aplikasi_sebelum_kaitan():
    src = inspect.getsource(ND.terapkan_nota_debit)
    assert src.index('vc["original_bill_id"] is not None') < src.index('vc["status"] not in')
    assert src.index("INSERT INTO vendor_credit_applications") < src.index("UPDATE vendor_credits SET original_bill_id")
    assert "segarkan_cache_hutang_tagihan(" in src


def test_sisa_nd_predikat_sama_dengan_v441():
    s = ND.sql_sisa_nd()
    for k in ("original_bill_id IS NOT NULL THEN 0", "IN ('VENDOR_CREDIT', 'VENDOR_CREDIT_REFUND')", "reversed_by_id IS NULL",
              "reversal_of_id IS NULL", "SUM(jl.debit) - SUM(jl.credit)"):
        assert k in s, k
    v441 = (MIG / "V441__ap_atribusi_nota_debit.sql").read_text()
    assert "AND je.source_type IN ('VENDOR_CREDIT', 'VENDOR_CREDIT_REFUND')" in v441


def test_refund_hanya_bebas_sisa_jurnal_akun_terselesaikan_dan_bank_txn():
    src = inspect.getsource(VC.receive_refund)
    assert 'vc["original_bill_id"] is not None' in src
    assert "ND.sisa_nd(" in src
    assert "UUID(body.account_id),\n                    body.amount" not in src
    assert "resolved_account_id,  # U3a" in src
    assert "INSERT INTO bank_transactions" in src and "'vendor_credit_refund'" in src


def test_void_cek_aplikasi_aktif_dan_refund():
    src = inspect.getsource(VC.void_vendor_credit)
    assert "status = 'active'" in src and "FROM vendor_credit_refunds" in src
    assert "and False" not in src


def test_kolom_hantu_dibuang():
    vc = inspect.getsource(VC)
    assert 'update_data["credit_date"] = update_data.pop("vendor_credit_date")' in vc
    ti = inspect.getsource(TI)
    assert "UPDATE vendor_credits SET tax_invoice_id" not in ti
    assert "vc.tax_invoice_id" not in ti and "vc.status IN ('posted', 'approved')" not in ti  # jalur NK (cn.*) = tiket terpisah


def test_satu_sumber_sisa_tagihan():
    assert "sisa_tagihan_jurnal(" in inspect.getsource(BP.get_bill_remaining_from_journal)
    assert "LEFT JOIN compute_ap_rincian($1) apr ON apr.bill_id = b.id" in inspect.getsource(BP.get_vendor_open_bills)
    for src in (inspect.getsource(BP), inspect.getsource(VC)):
        assert "vendor_credit_applications vca" not in src or "WHERE vca.bill_id" not in src
    assert ND.sisa_tagihan_jurnal is TD.sisa_tagihan_jurnal
    assert TD.sisa_tagihan is not TD.sisa_tagihan_jurnal  # kembaran Python unit (1) TIDAK tertimpa


def test_v443_kembar_v249_v250():
    m = (MIG / "V443__nota_debit_aplikasi_status_dan_pagar.sql").read_text()
    for k in ("ADD COLUMN status varchar(20) NOT NULL DEFAULT 'active'", "AND status = 'active';", "a.reversed_at = now()",
              "CREATE TRIGGER trg_vendor_credit_kaitan_tagihan_beku", "BEFORE UPDATE OF original_bill_id ON vendor_credits"):
        assert k in m, k
