"""P3 SO-dokumen: SATU sumber per dokumen. Rute /pdf lama dan render HTML/dokumen baru memakai PEMUAT konteks
dan RENDER yang sama -- kalau rute lama menyusun konteksnya sendiri lagi, panel dan PDF akan menyimpang diam-diam.
(Paritas isi lama vs baru diukur di luar suite: 25 dokumen nyata kaos/grapgrap, teks/halaman/ukuran/font identik.)"""
import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

RUTE = [
    ("routers/receive_payments.py", "get_receive_payment_pdf", "muat_pdf_kwitansi_penerimaan", "generate_receipt_pdf"),
    ("routers/customer_deposits.py", "get_customer_deposit_pdf", "muat_pdf_kwitansi_uang_muka", "generate_receipt_pdf"),
    ("routers/quotes.py", "get_quote_pdf", "muat_pdf_penawaran", "generate_quote_pdf"),
    ("routers/proformas.py", "get_proforma_pdf", "muat_pdf_proforma", "generate_proforma_pdf"),
    ("routers/deliveries.py", "get_delivery_pdf", "muat_pdf_surat_jalan", "generate_delivery_note_pdf"),
    ("routers/sales_invoices.py", "get_invoice_pdf", "muat_pdf_faktur", "generate_sales_invoice_pdf"),
]


def _fungsi(berkas):
    pohon = ast.parse((APP / berkas).read_text(encoding="utf-8"))
    return {n.name: n for n in pohon.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _panggil(fn):
    return {c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", None)
            for c in ast.walk(fn) if isinstance(c, ast.Call)}


@pytest.mark.parametrize("berkas,rute,pemuat,generate", RUTE)
def test_rute_pdf_memakai_pemuat_bersama(berkas, rute, pemuat, generate):
    f = _fungsi(berkas)
    assert pemuat in f, f"{pemuat} hilang dari {berkas}"
    assert f[pemuat].args.args[0].arg == "conn"
    p = _panggil(f[rute])
    assert pemuat in p and generate in p, f"{rute} wajib memanggil {pemuat} lalu {generate}"
    # rute tak lagi menyusun konteks sendiri: tak ada fetch/fetchrow langsung kecuali baris proforma (cek format=url)
    langsung = {"fetch", "fetchrow", "fetchval"} & p
    assert not langsung or rute == "get_proforma_pdf", f"{rute} kembali menyusun konteks sendiri: {langsung}"


@pytest.mark.parametrize("generate,render", [
    ("generate_receipt_pdf", "render_receipt"), ("generate_quote_pdf", "render_quote"),
    ("generate_proforma_pdf", "render_proforma"), ("generate_delivery_note_pdf", "render_delivery_note"),
    ("generate_sales_invoice_pdf", "render_sales_invoice"),
])
def test_generate_lewat_render_dan_penulis_tunggal(generate, render):
    pohon = ast.parse((APP / "services/pdf_service.py").read_text(encoding="utf-8"))
    kelas = next(n for n in pohon.body if isinstance(n, ast.ClassDef) and n.name == "PDFService")
    m = {n.name: n for n in kelas.body if isinstance(n, ast.FunctionDef)}
    assert render in m
    p = _panggil(m[generate])
    assert {"tulis_pdf", render} <= p and "write_pdf" not in p
