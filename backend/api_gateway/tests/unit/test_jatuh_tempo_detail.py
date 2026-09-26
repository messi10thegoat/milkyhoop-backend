"""Detail == daftar untuk jatuh tempo (26 Sep 2026): GET /api/sales-invoices/{id} dan GET /api/bills/{id} (+v2)
membawa is_overdue + overdue_days dengan aturan YANG SAMA dengan daftar, di tanggal BISNIS tenant.
Probe DB scratch (tanggal_bisnis @2026-09-25T18:00Z = 26 Sep; jatuh tempo 25 Sep): detail faktur & tagihan
is_overdue True / 1 hari == daftar; kode lama tak punya medan ini (FE menghitung dari tanggal klien).
"""
import ast
import inspect
from datetime import date
from pathlib import Path

import pytest

from app.services.jatuh_tempo import hari_terlambat

APP = Path(__file__).resolve().parents[2] / "app"


@pytest.mark.parametrize("jt,due,hari,harap", [
    (True, date(2026, 9, 25), date(2026, 9, 26), 1),     # 18:00Z 25 Sep = 26 Sep WIB
    (False, date(2026, 9, 25), date(2026, 9, 26), 0),    # sudah lunas / bukan jatuh tempo -> 0
    (True, None, date(2026, 9, 26), 0),
    (True, date(2026, 9, 30), date(2026, 9, 26), 0),     # tak pernah negatif
])
def test_hari_terlambat(jt, due, hari, harap):
    assert hari_terlambat(jt, due, hari) == harap


def test_detail_faktur_memakai_aturan_daftar():
    from app.routers import sales_invoices as SI
    seg = inspect.getsource(SI.get_invoice)
    assert "_syarat_jatuh_tempo('$3')" in seg                        # SAMA dengan filter & penanda daftar (Q-014)
    assert "_hari_jt = await tanggal_dokumen(conn, ctx[\"tenant_id\"])" in seg
    assert '"is_overdue": _jt,' in seg and '"overdue_days": hari_terlambat(_jt, invoice["due_date"], _hari_jt),' in seg
    daftar = inspect.getsource(SI.list_invoices)
    assert "_syarat_jatuh_tempo(" in daftar


@pytest.mark.parametrize("fn", ["get_bill", "get_bill_v2"])
def test_detail_tagihan_memakai_status_terhitung(fn):
    from app.services import bills_service as BS
    seg = inspect.getsource(getattr(BS.BillsService, fn))
    assert '"is_overdue": bill["calculated_status"] == "overdue"' in seg
    assert 'hari_terlambat(bill["calculated_status"] == "overdue", bill["due_date"], hari_ini)' in seg
    assert "hari_ini = await tanggal_dokumen(conn, tenant_id)" in seg
    assert "b.due_date < $3::date THEN 'overdue'" in seg             # CASE yang sama dengan daftar
