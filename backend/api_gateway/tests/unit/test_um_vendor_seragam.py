"""P-U3 Uang Muka Pembelian seragam (22 Okt 2026): fungsi murni services/uang_muka_vendor_daftar + bentuk SQL + penyambungan router.
Predikat SQL pada data SINTETIS dibuktikan di harness nyata (uji-nyata-um-vendor-seragam)."""
import inspect
import os
import uuid
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.routers import vendor_deposits as VD  # noqa: E402
from app.schemas import vendor_deposits as VS  # noqa: E402
from app.services import uang_muka_vendor_daftar as UMD  # noqa: E402
from app.services import po_daftar as PD  # noqa: E402

D = Decimal


@pytest.mark.parametrize("status,amount,terpakai,kembali,sisa,harap", [
    ("draft", "1000", "0", "0", "0", "draf"),
    ("void", "1000", "0", "0", "0", "batal"),
    ("posted", "1000", "0", "0", "1000", "utuh"),
    ("posted", "1000", "300", "0", "700", "sebagian"),
    ("partial", "1000", "300", "100", "600", "sebagian"),
    ("applied", "1000", "1000", "0", "0", "terpakai"),
    ("applied", "1000", "800", "200", "0", "terpakai"),
    ("posted", "1000", "0", "400", "600", "sebagian_dikembalikan"),
    ("applied", "1000", "0", "1000", "0", "dikembalikan"),
    ("posted", "1000", "0", "0", "0", "utuh"),                      # jurnal belum ada: bukan terpakai palsu
])
def test_keadaan_vd(status, amount, terpakai, kembali, sisa, harap):
    assert UMD.keadaan_vd(status, D(amount), D(terpakai), D(kembali), D(sisa)) == harap
    assert harap in UMD.KEADAAN_VD


@pytest.mark.parametrize("status,sisa,harap", [
    ("posted", "1000", "belum_diterapkan"), ("partial", "1", "belum_diterapkan"),
    ("posted", "0", None), ("applied", "0", None), ("draft", "1000", None), ("void", "1000", None), ("posted", None, None),
])
def test_aksi_vd_pill_tunggal(status, sisa, harap):
    assert UMD.aksi_vd(status, None if sisa is None else D(sisa)) == harap


def test_position_text_dan_status_text_satu_peta():
    assert [UMD.position_text(k) for k in UMD.KEADAAN_VD] == ["Draf", "Belum diterapkan", "Diterapkan sebagian", "Diterapkan penuh",
                                                               "Dikembalikan", "Dikembalikan sebagian", "Dibatalkan"]


def test_sql_aksi_dan_agregat_bentuk():
    assert UMD.sql_aksi("belum_diterapkan") == "(vd.status NOT IN ('draft', 'void') AND ag.sisa > 0)"
    with pytest.raises(ValueError):
        UMD.sql_aksi("lainnya")
    q = " ".join(UMD.sql_agregat("$2").split())
    assert "jl.account_id = $2" in q and "je.tenant_id = vd.tenant_id" in q                   # Law 24
    assert "je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL" in q                     # hanya jurnal efektif
    assert "'VENDOR_DEPOSIT', 'DEPOSIT_APPLICATION', 'DEPOSIT_REFUND'" in q                   # = uang_muka_vendor.SUMBER_JURNAL
    assert "FILTER (WHERE je.source_type = 'DEPOSIT_APPLICATION')" in q and "FILTER (WHERE je.source_type = 'DEPOSIT_REFUND')" in q


def test_syarat_cari():
    assert UMD.syarat_cari(None, 3) == (None, []) and UMD.syarat_cari("  ", 3) == (None, []) and UMD.syarat_cari(object(), 3) == (None, [])
    s, p = UMD.syarat_cari("kain biru", 3)
    assert p == ["%kain%", "%biru%"] and s.count(")) AND (vd.deposit_number") == 1 and "$3" in s and "$4" in s
    assert "v.tenant_id = vd.tenant_id" in s and "p.tenant_id = vd.tenant_id" in s


def test_skema_menampung_medan_baru():
    for k in ("sisa", "terpakai", "dikembalikan", "sisa_keadaan", "status_text", "position_text", "aksi", "documents", "doc_count"):
        assert k in VS.VendorDepositResponse.model_fields, k
    assert VS.VendorDepositResponse.model_fields["documents"].default == []
    r = VS.VendorDepositListResponse(items=[], total=0)
    assert r.has_more is False and r.sum_total is None and r.sum_sisa is None
    sm = VS.VendorDepositSummary(total_deposits=1, total_applied=0, total_remaining=1, deposit_count=1, pending_count=1)
    assert sm.belum_diterapkan_count == 0 and sm.draft_count == 0


def test_router_menyambung_dan_detail_tak_diparkir_lagi():
    lst = inspect.getsource(VD.list_vendor_deposits)
    assert "ORDER BY {sort_field} {sort_dir}, vd.id" in lst and "UMD.sql_aksi(aksi)" in lst and "UMD.syarat_cari(search, param_idx)" in lst
    assert "with_totals is True" in lst and "UMD.fakta_vd(" in lst and "UMD.kelompok_untuk(" in lst
    sm = inspect.getsource(VD.get_vendor_deposit_summary)
    assert "UMD.syarat_cari(search, 3)" in sm and "get_vendor_deposit_summary(" not in sm.replace("def get_vendor_deposit_summary(", "")
    det = inspect.getsource(VD.get_vendor_deposit)
    assert "UMD.fakta_vd(" in det and "UMD.kelompok_untuk(" in det
    rute = {(r.path, tuple(sorted(r.methods))): r for r in VD.router.routes}
    d = rute[("/{deposit_id}", ("GET",))]
    assert not [x for x in d.dependencies], "GET detail masih diparkir (fitur_belum_tersedia)"
    assert ("/{deposit_id}/documents", ("GET",)) in rute


def test_pembentuk_dokumen_bersama_dengan_po():
    assert UMD.PD is PD
    d = PD.dok_vendor_deposit({"id": uuid.uuid4(), "deposit_number": "VD-1", "deposit_date": None, "amount": D("1000"), "status": "void"})
    assert d["kind"] == "vendor_deposit" and d["batal"] is True and d["status"] == "batal" and d["amount"] == "1000.00"
    assert PD.dok_vendor_deposit({"id": uuid.uuid4(), "deposit_number": "VD-2", "deposit_date": None, "amount": 5, "status": "draft"})["status"] == "draf"
    assert PD.dok_vendor_deposit({"id": uuid.uuid4(), "deposit_number": "VD-3", "deposit_date": None, "amount": 5, "status": "posted"})["status"] == "dibayar"


def test_detail_memakai_sql_langsung_bukan_fungsi_db_rusak():
    src = inspect.getsource(VD.get_vendor_deposit)
    assert "get_vendor_deposit_applications" not in src and "get_vendor_deposit_refunds" not in src
    assert "UMD.SQL_APLIKASI" in src and "UMD.SQL_REFUND" in src
    assert "b.invoice_number AS bill_number" in UMD.SQL_APLIKASI and "d.tenant_id = $2" in UMD.SQL_APLIKASI and "d.tenant_id = $2" in UMD.SQL_REFUND
