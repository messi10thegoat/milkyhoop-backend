"""Pill "Belum lunas" == kartu outstanding-summary (5 Okt 2026, temuan pemilik grapgrap)."""
import inspect
import uuid
from datetime import date
from decimal import Decimal

from app.services import dashboard_v2 as DV
from app.routers import sales_invoices as SI
from app.schemas.sales_invoices import InvoiceListItem

H = date(2026, 10, 5)


def _r(sisa, due, faktur=True):
    return {"invoice_id": uuid.uuid4() if faktur else None, "outstanding": Decimal(sisa), "due_date": due}


AR = [_r("100", date(2026, 9, 1)), _r("50", H), _r("10", date(2026, 11, 1)), _r("5", None),
      _r("0", date(2026, 9, 1)), _r("-45000", H, faktur=False), _r("-1000", H, faktur=False)]


def test_belum_lunas_tanpa_nota_kredit_dan_tanpa_sisa_nol():
    b = DV.pilih_belum_lunas(AR)
    assert len(b) == 4 and all(r["invoice_id"] is not None and r["outstanding"] > 0 for r in b)


def _kartu(ar):
    """Tiruan PERSIS predikat SQL outstanding-summary."""
    ov = [r for r in ar if r["invoice_id"] is not None and r["outstanding"] > 0 and (r["due_date"] is None or r["due_date"] < H)]
    cu = [r for r in ar if r["invoice_id"] is not None and r["outstanding"] > 0 and r["due_date"] is not None and r["due_date"] >= H]
    return len(ov), len(cu)


def test_kartu_sama_dengan_tugas_belum_lunas_dan_telat():
    ov, cu = _kartu(AR)
    assert ov + cu == len(DV.pilih_belum_lunas(AR))
    assert ov == len(DV.pilih_faktur_telat(AR, H))


def test_sql_kartu_memakai_predikat_yang_sama():
    src = inspect.getsource(SI.get_outstanding_summary)
    assert src.count("invoice_id IS NOT NULL AND outstanding > 0") == 2
    assert "(due_date < $2::date OR due_date IS NULL)) AS overdue_count" in src
    assert "due_date >= $2::date) AS current_count" in src
    # nominal TIDAK diubah (AR neto termasuk nota kredit)
    assert "COALESCE(SUM(outstanding), 0) AS total_outstanding" in src


def test_tugas_belum_lunas_terdaftar_dan_dipilih():
    assert "belum_lunas" in DV.TUGAS_FAKTUR
    assert "belum_lunas" in str(inspect.signature(SI.list_invoices).parameters["tugas"].annotation)
    assert "pilih_belum_lunas(ar)" in inspect.getsource(DV.id_tugas)


def test_daftar_sisa_per_baris_dan_urut_sisa():
    src = inspect.getsource(SI.list_invoices)
    assert "CASE WHEN si.status IN ('draft','void') THEN NULL" in src and "AS sisa_faktur" in src
    assert '"outstanding": "sisa_faktur"' in src and "NULLS LAST" in src
    assert "outstanding_amount" in InvoiceListItem.model_fields
