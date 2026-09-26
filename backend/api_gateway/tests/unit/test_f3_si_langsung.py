"""F3 SI langsung + termin di detail pelanggan (26 Sep 2026, BACKEND; aturan termin_bayar milik BACKEND3).

- POST /api/sales-invoices: due_date kini OPSIONAL. Kosong -> tentukan_jatuh_tempo (termin pelanggan, else tanggal
  faktur); isian pengguna menang. Nilai yang DISIMPAN (INSERT) dan yang DIBALAS sama, + due_date_source.
- GET /api/customers/{id}: + payment_terms_source dari termin_hari (aturan yang SAMA) supaya FE mengisi awal jatuh
  tempo faktur langsung = tgl faktur + payment_terms_days. response_model MENYARING medan tak dideklarasi -> dicek.
Penyambungan handler dicek lewat AST (helper benar + handler tak memanggil = tetap hijau bila hanya helper diuji).
"""
import ast
from datetime import date

import app.routers.customers as CU
import app.routers.sales_invoices as si
from app.schemas.customers import CustomerDetailResponse
from app.schemas.sales_invoices import CreateInvoiceRequest


def _fn(mod, nama):
    for n in ast.walk(ast.parse(open(mod.__file__, encoding="utf-8").read())):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == nama:
            return n
    raise AssertionError(nama)


def _panggil(fn, nama):
    return [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "id", None) == nama]


def _baris_sql(fn, frasa):
    return [c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call) and c.args
            and isinstance(c.args[0], ast.Constant) and frasa in str(c.args[0].value)]


def test_skema_due_date_opsional_isian_tetap():
    b = {"customer_name": "Toko", "invoice_date": "2026-09-26",
         "items": [{"description": "x", "quantity": 1, "unit_price": 1000}]}
    assert CreateInvoiceRequest(**b).due_date is None
    assert CreateInvoiceRequest(**b, due_date="2026-10-26").due_date == date(2026, 10, 26)


def test_create_menentukan_jatuh_tempo_sebelum_insert_dan_memakainya():
    fn = _fn(si, "create_invoice")
    p = _panggil(fn, "tentukan_jatuh_tempo")
    assert len(p) == 1
    a = [ast.unparse(x) for x in p[0].args]
    assert a == ["conn", "ctx['tenant_id']", "body.invoice_date", "body.due_date", "None", "customer_id_str"], a
    ins = _baris_sql(fn, "INSERT INTO sales_invoices")[0]
    # sesudah pelanggan diselesaikan dari nama (BUG-02), sebelum INSERT
    resolusi = max(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
                   and any(getattr(t, "id", None) == "customer_id_str" for t in n.targets))
    assert resolusi < p[0].lineno < ins
    # INSERT memakai hasil aturan, bukan body.due_date (yang bisa None)
    call = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and c.lineno == ins][0]
    arg = [ast.unparse(x) for x in call.args]
    assert arg[5] == "body.invoice_date" and arg[6] == "due_date", arg[:8]
    assert "body.due_date" not in arg


def test_respons_create_membalas_nilai_tersimpan_dan_sumber():
    s = " ".join(ast.unparse(_fn(si, "create_invoice")).split())
    assert "'due_date': str(due_date), 'due_date_source': due_date_source" in s
    assert "str(body.due_date)" not in s


def test_detail_pelanggan_sumber_termin_dari_aturan_sama():
    fn = _fn(CU, "get_customer")
    p = _panggil(fn, "termin_hari")
    assert len(p) == 1
    assert [ast.unparse(x) for x in p[0].args] == ["conn", "ctx['tenant_id']", "None", "customer_id"]
    s = " ".join(ast.unparse(fn).split())
    assert "'payment_terms_source': _termin_sumber" in s


def test_response_model_tidak_menyaring_sumber_termin():
    d = CustomerDetailResponse(data={"id": "c1", "name": "Toko", "payment_terms_days": 30,
                                     "payment_terms_source": "customer_terms"}).model_dump()
    assert d["data"]["payment_terms_days"] == 30 and d["data"]["payment_terms_source"] == "customer_terms"


# ---------- konversi penawaran -> faktur (putusan pemilik via MASTER: SATU aturan, +30 hardcode dihapus) ----------
import app.routers.quotes as QU  # noqa: E402


def test_konversi_penawaran_memakai_aturan_termin():
    fn = _fn(QU, "convert_to_invoice")
    p = _panggil(fn, "tentukan_jatuh_tempo")
    assert len(p) == 1
    a = [ast.unparse(x) for x in p[0].args]
    assert a == ["conn", "ctx['tenant_id']", "invoice_date", "body.due_date if body else None",
                 "quote['terms']", "quote['customer_id']"], a
    ins = _baris_sql(fn, "INSERT INTO sales_invoices")[0]
    assert p[0].lineno < ins
    call = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and c.lineno == ins][0]
    assert "due_date" in [ast.unparse(x) for x in call.args]


def test_konversi_penawaran_tanpa_plus_30_hardcode():
    s = " ".join(ast.unparse(_fn(QU, "convert_to_invoice")).split())
    assert "timedelta(days=30)" not in s and "days=30" not in s
    assert "'due_date_source': due_date_source" in s
