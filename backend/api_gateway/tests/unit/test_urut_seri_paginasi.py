"""Urutan seri DETERMINISTIK + paginasi seragam (21 Okt 2026, MASTER): daftar Penjualan diurut dengan pembeda id (halaman tak menggandakan/melewatkan
baris bertanggal sama) dan Penawaran/Proforma/Pengiriman menerima skip/limit/page/offset/per_page setara (aditif). Nyata: scripts/ops/uji-nyata-paginasi-urut.py."""
import inspect
import re

import pytest

from app.routers import credit_notes as CN
from app.routers import customer_deposits as CD
from app.routers import deliveries as DL
from app.routers import proformas as PR
from app.routers import quotes as QR
from app.routers import receive_payments as RP
from app.routers import sales_invoices as SI
from app.routers import sales_orders as SO


@pytest.mark.parametrize("modul,fn,pola", [
    (RP, "list_receive_payments", r"ORDER BY \{sort_field\} \{sort_dir\}, q\.id\b"),
    (SI, "list_invoices", r"ORDER BY \{sort_field\} \{sort_dir\}, si\.id\b"),
    (QR, "list_quotes", r"ORDER BY created_at DESC, id\b"),
    (CD, "list_customer_deposits", r"ORDER BY \{sort_field\} \{sort_dir\}, id\b"),
    (CN, "list_credit_notes", r"ORDER BY \{sort_field\} \{sort_dir\}, id\b"),
    (SO, "list_sales_orders", r"ORDER BY created_at DESC, id\b"),
    (PR, "list_proformas", r"ORDER BY p\.proforma_date DESC, p\.created_at DESC, p\.id\b"),
    (DL, "list_deliveries", r"ORDER BY \{db_sort\} \{direction\}, f\.id\b"),
])
def test_order_by_daftar_punya_pembeda_id(modul, fn, pola):
    assert re.search(pola, inspect.getsource(getattr(modul, fn))), (fn, pola)


def test_paginasi_seragam_parameter_setara():
    q, p, d = (inspect.signature(f).parameters for f in (QR.list_quotes, PR.list_proformas, DL.list_deliveries))
    assert {"skip", "limit", "page", "offset", "per_page"} <= set(q)
    assert {"skip", "limit", "page", "offset", "per_page"} <= set(p)
    assert {"skip", "limit", "page", "offset", "per_page"} <= set(d)
    # bawaan lama tak berubah
    assert q["limit"].default.default == 20 and p["limit"].default.default == 20 and d["per_page"].default.default == 20
    assert q["per_page"].default.default is None and p["per_page"].default.default is None and d["limit"].default.default is None
