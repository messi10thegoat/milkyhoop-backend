"""#10b-3b — sisi TULIS sisa memakai tanggal bisnis tenant (penutup #10).

Manufaktur (jurnal MI/LB/FG/VAR/pembalik, tanggal WO, bill subkon, ledger), BOM, saldo awal
barang/pelanggan/vendor, transfer stok per-barang, batch/serial, cadangan tanggal di
inventory_helpers, fulfillment, snapshot aging, jurnal RECON-ADJ + cek periode rekonsiliasi,
payment_request. Plus: cek periode pada void gaji/kasbon/pembayaran gaji (dulu hanya trigger
DB -> 500), label pembalik gerakan masuk (dulu semua 'PURCHASE_RETURN'), list_vendors
has_overdue+sort ap_balance (dulu 500), batas atas periode ringkasan biaya.

Pemindai kali ini juga menangkap `now.date()` (now = datetime.utcnow()) — pola RECON-ADJ
yang lolos dari pemindai #10b-3a — dan `SELECT CURRENT_DATE` lewat fetchval.
"""

import ast
import re
from pathlib import Path

import pytest

from app.utils import tanggal_tenant as tt

APP = Path(tt.__file__).resolve().parents[1]

FUNGSI = {
    "routers/production.py": ["create_production_order", "release_order", "start_production",
                              "complete_order", "_reverse_journal", "_reverse_inventory_ledger",
                              "cancel_order", "issue_materials", "record_labor", "report_output"],
    "routers/bom.py": ["activate_bom", "obsolete_bom"],
    "routers/production_costing.py": ["calculate_standard_cost_from_bom"],
    "routers/customers.py": ["set_customer_opening_balance", "reverse_customer_opening_balance"],
    "routers/vendors.py": ["set_vendor_opening_balance", "list_vendors"],
    "routers/sales_invoices.py": ["fulfill_invoice"],
    "routers/accounting_settings.py": ["create_aging_snapshot"],
    "routers/reports.py": ["create_aging_snapshot"],
    "services/payment_request_service.py": ["_create_payment_journal"],
    "routers/bank_reconciliation.py": ["list_accounts", "categorize_statement_line", "complete_session"],
    "routers/payroll_runs.py": ["void_payroll"],
    "routers/employee_advances.py": ["void_advance"],
    "routers/payroll_payments.py": ["void_payment"],
    "routers/expenses.py": ["get_expenses_summary"],
    "routers/items.py": ["create_item", "create_single_item_stock_transfer", "create_stock_adjustment"],
    "routers/item_batches.py": ["create_item_batch", "get_available_batches_for_item"],
    "routers/item_serials.py": ["create_item_serial", "bulk_create_serials"],
    "services/inventory_helpers.py": ["record_inventory_outbound", "record_inventory_inbound",
                                      "record_inventory_reversal"],
}
ALIAS_DATE = ("date", "dt_date", "datetime", "date_type", "dateclass", "dateclass_today",
              "_date", "_date_var", "_date_rev", "_date_lb", "_date_ro")
POLA_SQL = re.compile(r"\bCURRENT_DATE\b|now\(\)::date", re.I)


def _fungsi(berkas, nama):
    t = ast.parse((APP / berkas).read_text())
    hasil = [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama]
    assert len(hasil) == 1, (berkas, nama, len(hasil))
    return hasil[0]


def _jam_utc(node):
    salah = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            v, a = n.func.value, n.func.attr
            if a == "today" and isinstance(v, ast.Name) and v.id in ALIAS_DATE:
                salah.append(n.lineno)
            if a == "now" and isinstance(v, ast.Name) and v.id in ("datetime", "dt") and not n.args:
                salah.append(n.lineno)
            # datetime.utcnow().date() / now.date() dengan now = utcnow(): tanggal UTC.
            if a == "date" and not n.args and (
                    (isinstance(v, ast.Name) and v.id in ("now", "sekarang"))
                    or (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                        and v.func.attr in ("utcnow", "now"))):
                salah.append(n.lineno)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and POLA_SQL.search(n.value):
            salah.append(n.lineno)
    return salah


@pytest.mark.parametrize("berkas,nama", [(b, f) for b, fs in FUNGSI.items() for f in fs])
def test_fungsi_tulis_tanpa_tanggal_utc(berkas, nama):
    salah = _jam_utc(_fungsi(berkas, nama))
    assert salah == [], f"{berkas}::{nama} tanggal UTC di baris {salah}"


@pytest.mark.parametrize("berkas", sorted(FUNGSI))
def test_berkas_tanpa_current_date_di_sql(berkas):
    t = ast.parse((APP / berkas).read_text())
    salah = [n.lineno for n in ast.walk(t) if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and POLA_SQL.search(n.value)]
    assert salah == [], f"{berkas}: CURRENT_DATE/now()::date di baris {salah}"


def test_penjaga_bisa_merah():
    t = ast.parse(
        "async def f():\n"
        "    a = dateclass_today.today()\n"
        "    now = datetime.utcnow()\n"
        "    b = now.date()\n"
        "    c = await conn.fetchval('SELECT CURRENT_DATE')\n"
        "    d = datetime.utcnow().date()\n"
        "    ok = now.isoformat()\n")
    assert sorted(_jam_utc(t)) == [2, 4, 5, 6]


def _baris_panggilan(node, nama):
    return sorted(n.lineno for n in ast.walk(node) if isinstance(n, ast.Call)
                  and ((isinstance(n.func, ast.Name) and n.func.id == nama)
                       or (isinstance(n.func, ast.Attribute) and n.func.attr == nama)))


def _baris_tulis(node):
    return sorted(n.lineno for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                  and re.search(r"^\s*(INSERT INTO|UPDATE)\s", n.value, re.I | re.M))


@pytest.mark.parametrize("berkas,nama", [("routers/payroll_runs.py", "void_payroll"),
                                         ("routers/employee_advances.py", "void_advance"),
                                         ("routers/payroll_payments.py", "void_payment")])
def test_void_cek_periode_asal_dan_pembalik_sebelum_tulis(berkas, nama):
    """Dulu hanya trigger DB yang menolak periode tertutup -> pengguna melihat 500."""
    f = _fungsi(berkas, nama)
    cek = _baris_panggilan(f, "check_period_is_open")
    tulis = _baris_tulis(f)
    assert len(cek) >= 2, f"{nama}: cek periode asal + pembalik wajib ada, dapat {cek}"
    assert tulis and max(cek[:2]) < min(tulis), f"{nama}: cek {cek} harus sebelum tulis pertama {tulis[:1]}"
    t = ast.parse((APP / berkas).read_text())
    tersedia = any(isinstance(n, ast.AsyncFunctionDef) and n.name == "check_period_is_open" for n in t.body) or any(
        isinstance(n, ast.ImportFrom) and any(a.name == "check_period_is_open" for a in n.names) for n in ast.walk(t))
    assert tersedia, f"{berkas}: check_period_is_open tak didefinisikan/diimpor"


@pytest.mark.parametrize("sumber,harap", [
    ("BILL", "PURCHASE_RETURN"), ("PURCHASE_INVOICE", "PURCHASE_RETURN"),
    ("STOCK_ADJUSTMENT", "STOCK_ADJUSTMENT_REVERSAL"), ("CREDIT_NOTE", "CREDIT_NOTE_REVERSAL"),
    ("STOCK_ADJUSTMENT_VOID", "STOCK_ADJUSTMENT_VOID_REVERSAL"), ("", "INBOUND_REVERSAL"),
    (None, "INBOUND_REVERSAL"), ("X" * 30, "INBOUND_REVERSAL"),
])
def test_label_pembalik_masuk_ikut_sumber(sumber, harap):
    from app.services.inventory_helpers import label_pembalikan_masuk

    hasil = label_pembalikan_masuk(sumber)
    assert hasil == harap and len(hasil) <= 30


def test_pembalik_masuk_memakai_label_bukan_literal():
    f = _fungsi("services/inventory_helpers.py", "record_inventory_reversal")
    assert _baris_panggilan(f, "label_pembalikan_masuk"), "pembalik masuk wajib lewat label_pembalikan_masuk"
    assert not [n for n in ast.walk(f) if isinstance(n, ast.Constant) and n.value == "PURCHASE_RETURN"]


def test_list_vendors_tanpa_replace_tenant_id():
    """`.replace("tenant_id", "v.tenant_id")` menulis ulang fragmen has_overdue jadi
    `vendors.v.tenant_id` -> 500 (terukur prod 25 Sep, has_overdue=true&sort_by=ap_balance)."""
    f = _fungsi("routers/vendors.py", "list_vendors")
    for n in ast.walk(f):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "replace" \
                and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "tenant_id":
            pytest.fail(f"list_vendors baris {n.lineno}: replace('tenant_id', ...) kembali")
    teks = ast.unparse(f)
    assert "compute_ap_outstanding(v.tenant_id)" in teks and "vendors.tenant_id" not in teks


def test_ringkasan_biaya_periode_berbatas_atas():
    """MASTER 25 Sep: 'bulan ini' = tgl 1 s/d akhir bulan (dulu tanpa batas atas)."""
    teks = ast.unparse(_fungsi("routers/expenses.py", "get_expenses_summary"))
    for satuan in ("1 month", "3 months", "1 year"):
        assert f"INTERVAL '{satuan}'" in teks, satuan
    assert teks.count("<= {h}") >= 2  # minggu: sampai hari ini, di jurnal DAN biaya
