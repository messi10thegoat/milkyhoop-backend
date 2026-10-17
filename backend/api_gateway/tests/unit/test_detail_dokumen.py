"""detailpage-polish data (17 Okt 2026, MASTER): bundel /documents + status/subtype/account_label; detail SO + Posisi/Kirim
dari so_posisi.fakta_daftar (fungsi SAMA dengan baris daftar). Harness nyata: scripts/ops/uji-nyata-detail-dokumen.py."""
import inspect

import pytest

from app.routers import dokumen as DK
from app.routers import sales_orders as SO
from app.schemas.sales_orders import SalesOrderDetail
from app.services.proforma_atribusi import label_rekening


@pytest.mark.parametrize("kind,batal,paid,harap", [
    ("proforma", False, True, "lunas"), ("proforma", False, False, "terbit"), ("proforma", True, True, "batal"),
    ("invoice", False, True, "lunas"), ("invoice", False, False, "diposting"), ("invoice", True, False, "batal"),
    ("receipt", False, None, "diterima"), ("delivery", False, None, "terbit"), ("quotation", False, None, "terbit"),
    ("credit_note", False, None, "diposting"), ("delivery", True, None, "batal")])
def test_status_dokumen(kind, batal, paid, harap):
    assert DK.status_dokumen(kind, batal, paid) == harap


def test_subtipe_kwitansi_dari_jenis_deposit():
    from app.services.deposit_jenis import JENIS
    assert set(DK.SUBTIPE_KWITANSI) == set(JENIS)
    assert DK.SUBTIPE_KWITANSI == {"DP": "uang_muka", "PELUNASAN": "pelunasan"}


def test_label_rekening_tanpa_pemilik():
    r = {"bank_name": "BCA", "account_number": "8295032185", "account_holder_name": "Anton", "account_name": "BCA Masuk",
         "coa_name": "Bank"}
    assert label_rekening(r, pemilik=False) == "BCA 8295032185"
    assert label_rekening(r).startswith("BCA 8295032185")
    kas = {"bank_name": None, "account_number": None, "account_holder_name": None, "account_name": "Kas Operasional",
           "coa_name": "Kas"}
    assert label_rekening(kas, pemilik=False) == "Kas Operasional"


def test_bundel_memakai_satu_penentu():
    src = inspect.getsource(DK.susun_dokumen)
    assert 'd["status"] = status_dokumen(d["kind"], d["batal"], d.get("paid"))' in src
    assert src.count("label_rekening(") == 2 and "pemilik=False" in src
    assert 'sql_jenis_efektif("cd")' in src and '"receipt_kind": "pelunasan"' in src
    # kwitansi TAK BOLEH membawa subtype (pembaca FE live menolak nilai di luar dp/termin/penuh/cicilan)
    blok_kw = src[src.index('"kind": "receipt"'):src.index('kelompok.append({"key": "kw"')]
    assert '"subtype"' not in blok_kw


def test_detail_so_dari_fakta_daftar():
    src = inspect.getsource(SO.get_sales_order_detail)
    assert "await fakta_daftar(conn, ctx[\"tenant_id\"], [order]" in src
    for f in ("position_text", "ship_text", "kirim_hari", "kirim_terbuka", "kirim_keadaan"):
        assert f in SalesOrderDetail.model_fields and f"{f}=_pos[\"{f}\"]" in src
