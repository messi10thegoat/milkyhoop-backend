"""Penjaga tabel rute (insiden 27 Sep 2026 23:00 WIB): helper `terbitkan_faktur(conn, ctx, ...)` disisipkan TEPAT di
bawah `@router.post("/{invoice_id}/post")` -> dekorator menempel ke helper -> POST /sales-invoices/{id}/post = 422
(conn/ctx/... dianggap query) untuk SEMUA faktur. Unit test memanggil fungsi langsung, journey memakai jalur lain.

Statis (AST) atas SEMUA berkas routers/ — tak ada modul yang bisa lolos karena gagal diimpor:
  1. endpoint ber-dekorator @router.<metode>(...) tak boleh punya parameter conn/ctx/pool (tanda helper internal);
  2. rute yang disentuh 27 Sep -> nama endpoint yang diharapkan (literal)."""
import ast
import os

import pytest

ROUTERS = os.path.join(os.path.dirname(__file__), "..", "..", "app", "routers")
METODE = {"get", "post", "put", "patch", "delete"}
PARAM_HELPER = {"conn", "ctx", "pool"}


def _pindai():
    for nama in sorted(os.listdir(ROUTERS)):
        if not nama.endswith(".py"):
            continue
        src = open(os.path.join(ROUTERS, nama)).read()
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in fn.decorator_list:
                if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in METODE
                        and isinstance(d.func.value, ast.Name) and d.func.value.id.endswith("router")):
                    jalur = d.args[0].value if d.args and isinstance(d.args[0], ast.Constant) else None
                    yield nama, d.func.attr.upper(), jalur, fn


_SEMUA = None


def _rute():
    global _SEMUA
    if _SEMUA is None:
        _SEMUA = list(_pindai())
    return _SEMUA


def test_ada_banyak_rute_yang_dipindai():
    assert len(list(_rute())) > 500   # alat bisa bicara: pemindai benar-benar melihat rute


def test_endpoint_tak_berparameter_helper():
    salah = [f"{b}:{m} {j} -> {fn.name}({', '.join(a.arg for a in fn.args.args)})"
             for b, m, j, fn in _rute() if PARAM_HELPER & {a.arg for a in fn.args.args + fn.args.kwonlyargs}]
    assert not salah, "endpoint berparameter helper (dekorator menempel ke fungsi yang salah?):\n" + "\n".join(salah)


HARAP = [
    ("sales_invoices.py", "POST", "/{invoice_id}/post", "post_invoice"),
    ("sales_invoices.py", "POST", "/{invoice_id}/payments", "record_payment"),  # 29 Sep: lewat buat_penerimaan
    ("sales_invoices.py", "POST", "/{invoice_id}/void", "void_invoice"),
    ("sales_invoices.py", "GET", "/{invoice_id}/pdf", "get_invoice_pdf"),
    ("sales_orders.py", "POST", "/{order_id}/to-invoice", "convert_to_invoice"),
    ("sales_orders.py", "POST", "/{order_id}/to-invoice/preview", "preview_to_invoice"),
    ("sales_orders.py", "GET", "/summary", "get_sales_order_summary"),
    ("receive_payments.py", "POST", "/{payment_id}/void", "void_receive_payment"),
    # 29 Sep: inti create dipindah ke helper buat_penerimaan(conn, ctx, body) -- kelas insiden 27 Sep
    ("receive_payments.py", "POST", "", "create_receive_payment"),
    ("receive_payments.py", "POST", "/{payment_id}/post", "post_receive_payment"),
    ("sales_orders.py", "POST", "/{order_id}/receive-payment/preview", "preview_receive_payment_from_order"),
    ("sales_orders.py", "POST", "/{order_id}/close", "close_sales_order"),
    ("sales_orders.py", "POST", "/{order_id}/close/preview", "preview_close_sales_order"),
    ("sales_orders.py", "POST", "/{order_id}/cancel", "cancel_sales_order"),
    ("sales_orders.py", "POST", "/{order_id}/cancel/preview", "preview_cancel_sales_order"),
    ("bill_payments.py", "POST", "/{payment_id}/void", None),
    ("expenses.py", "POST", "/{expense_id}/void", None),
    ("kasbank_v2.py", "POST", "/bank-transactions/{transaction_id}/void", "void_transaction"),
    ("bank_transfers.py", "POST", "/{transfer_id}/void", None),
    ("customer_deposits.py", "POST", "/{deposit_id}/void", "void_customer_deposit"),
    # 30 Sep: modul CW kasbon (rencana/penulis dipisah dari rute)
    ("employee_advances.py", "POST", "", "grant_advance"),
    ("employee_advances.py", "POST", "/preview", "preview_grant_advance"),
    ("employee_advances.py", "POST", "/{advance_id}/void", "void_advance"),
    ("employee_advances.py", "POST", "/{advance_id}/void/preview", "preview_void_advance"),
    ("vendor_deposits.py", "POST", "/{deposit_id}/void", "void_vendor_deposit"),
    ("sales_receipts.py", "POST", "/{receipt_id}/void", None),
]


@pytest.mark.parametrize("berkas,metode,jalur,nama", HARAP)
def test_rute_disentuh_27_sep_ke_endpoint_yang_benar(berkas, metode, jalur, nama):
    cocok = [fn for b, m, j, fn in _rute() if b == berkas and m == metode and j == jalur]
    assert len(cocok) == 1, f"{berkas} {metode} {jalur}: {len(cocok)} rute"
    fn = cocok[0]
    if nama:
        assert fn.name == nama
    assert fn.args.args and fn.args.args[0].arg == "request", f"{fn.name}: parameter pertama bukan request"


def test_rute_statis_kasbon_sebelum_rute_berparameter():
    """GET /summary & /balances WAJIB dideklarasikan SEBELUM GET /{advance_id} (UUID): kalau sesudahnya,
    FastAPI mencocokkan /{advance_id} dulu -> 422 'bukan UUID' untuk /summary."""
    get = [j for b, m, j, fn in _rute() if b == "employee_advances.py" and m == "GET"]
    assert get.index("/summary") < get.index("/{advance_id}") and get.index("/balances") < get.index("/{advance_id}")
