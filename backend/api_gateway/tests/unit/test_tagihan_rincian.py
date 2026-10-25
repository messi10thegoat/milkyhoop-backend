"""Faktur Pembelian: uang + keadaan dari compute_ap_rincian (V440) -- SATU ungkapan untuk daftar, filter, urut, summary, detail v1/v2,
mark_paid (PEMBELIAN-SERAGAM unit (1), 22 Okt 2026). Pasangan SQL<->Python diuji nyata di scripts/ops/uji-nyata-tagihan-rincian."""
import inspect
from datetime import date
from decimal import Decimal

import pytest

from app.services import bills_service as BS
from app.services import tagihan_daftar as TD

H = date(2026, 10, 22)
LALU, NANTI = date(2026, 10, 1), date(2026, 11, 1)


def R(total, paid):
    return {"bill_total": Decimal(str(total)), "paid_amount": Decimal(str(paid)), "outstanding": Decimal(str(total)) - Decimal(str(paid))}


@pytest.mark.parametrize("status,v2,rin,due,harap_kd,harap_sisa", [
    ("void", "void", None, LALU, "void", 0),
    ("paid", "void", R(100, 100), LALU, "void", 0),            # status_v2 menang (kolom compute_ap_outstanding)
    ("void", None, None, LALU, "void", 0),                     # status_v2 NULL -> kolom lama
    ("draft", None, None, LALU, "draft", 500),                 # BILL-2609-0007: dulu 'overdue'
    ("draft", "draft", None, LALU, "draft", 500),
    ("posted", "posted", None, LALU, "void", 0),               # aktif tanpa baris rincian
    ("posted", "posted", R(0, 0), LALU, "void", 0),            # jurnal BILL dibalik -> Batal / 0 (8 tagihan kaos)
    ("posted", "posted", R(500, 500), LALU, "paid", 0),
    ("paid", "posted", R(500, 600), LALU, "paid", -100),       # lebih bayar: sisa jurnal apa adanya
    ("posted", "posted", R(500, 200), LALU, "overdue", 300),
    ("posted", "posted", R(500, 200), NANTI, "partial", 300),
    ("posted", "posted", R(500, 0), LALU, "overdue", 500),
    ("posted", "posted", R(500, 0), NANTI, "unpaid", 500),
    ("posted", "posted", R(500, 0), H, "unpaid", 500),         # jatuh tempo HARI INI belum lewat
])
def test_kembaran_python(status, v2, rin, due, harap_kd, harap_sisa):
    assert TD.keadaan_tagihan(status, v2, rin, due, H) == harap_kd
    assert TD.sisa_tagihan(status, v2, rin, Decimal("500")) == Decimal(str(harap_sisa))


def test_ungkapan_sql_urutan_cabang():
    s = TD.sql_keadaan("$3::date")
    urut = [s.index(x) for x in ("THEN 'void'", "THEN 'draft'", "apr.bill_total = 0", "THEN 'paid'", "THEN 'overdue'", "THEN 'partial'",
                                 "ELSE 'unpaid'")]
    assert urut == sorted(urut)
    assert "b.status_v2 = 'void' OR (b.status_v2 IS NULL AND b.status = 'void')" in s
    assert "b.due_date < $3::date" in s
    assert set(TD.URUT_KEADAAN) == set(TD.KEADAAN)


def test_satu_sumber_di_semua_jalur():
    src = inspect.getsource(BS)
    assert "BILL_JOURNAL_PAID_CTE" not in src and "ap_paid" not in src and "bjp." not in src
    daftar = inspect.getsource(BS.BillsService.list_bills)
    for k in ("TD.sql_keadaan(TOK_HARI_INI)", "TD.sql_keadaan(hari_ph)", "TD.sql_sisa()", "TD.sql_dibayar()", "TD.sql_urut_keadaan(", "TD.join_rincian()"):
        assert k in daftar, k
    for fn in ("get_bill", "get_bill_v2_conn"):
        seg = inspect.getsource(getattr(BS.BillsService, fn))
        assert "LEFT JOIN compute_ap_rincian($2) apr ON apr.bill_id = b.id" in seg
        assert 'TD.sql_keadaan("$3::date")' in seg and "TD.sql_sisa()" in seg and "TD.sql_dibayar()" in seg
    ring = inspect.getsource(BS.BillsService.get_summary)
    assert "TD.sql_keadaan(\"$4::date\")" in ring and "TD.join_rincian()" in ring
    bayar = inspect.getsource(BS.BillsService.mark_paid)
    assert "compute_ap_rincian($2)" in bayar and 'amount_due = bill["amount_due"]' in bayar
    assert 'bill["amount"] - bill["amount_paid"]' not in bayar


def test_join_placeholder():
    assert TD.join_rincian() == "LEFT JOIN compute_ap_rincian($1) apr ON apr.bill_id = b.id"
    assert TD.join_rincian("$2", "x") == "LEFT JOIN compute_ap_rincian($2) apr ON apr.bill_id = x.id"
