"""Catatan "BUKAN faktur pajak" HANYA di PDF proforma tenant PKP (12 Okt 2026, putusan pemilik). Non-PKP (mis. grapgrap):
tanpa catatan pajak sama sekali -- dulu tercetak tanpa syarat dan menyesatkan. Sumber PKP = "Tenant".is_pkp, SAMA dengan
penjaga PPN faktur (pkp_guard); ketat `is True` (Law-34: V154 dulu DEFAULT true)."""
import ast
import re
from pathlib import Path

import pytest

from app.routers import proformas as PF
from app.services import pdf_service as PS

KALIMAT = "BUKAN faktur pajak"
APP = Path(PF.__file__).resolve().parent.parent


@pytest.fixture
def tangkap(monkeypatch):
    html = {}

    class _HTML:
        def __init__(self, string=None, **kw):
            html["isi"] = string

        def write_pdf(self, **kw):
            return b"%PDF"
    monkeypatch.setattr(PS, "HTML", _HTML)
    return html


def _render(tangkap, **x):
    d = {"proforma_number": "PRO-T", "proforma_date": "2026-10-01", "due_date": "2026-10-03", "status": "issued",
         "purpose": "DP", "amount": 1000000, "paid_amount": 0, "outstanding_amount": 1000000, "is_paid": False,
         "sales_order_number": "SO-T", "customer_name": "Maria"}
    d.update(x)
    PS.get_pdf_service().generate_proforma_pdf(d, {"name": "Usaha"})
    return " ".join(re.sub(r"<[^>]+>", " ", tangkap["isi"].split("<body>")[1]).split())


@pytest.mark.parametrize("tujuan", ["DP", "TERMIN", "PELUNASAN"])
def test_pkp_catatan_tampil_untuk_semua_jenis_proforma(tangkap, tujuan):
    teks = _render(tangkap, purpose=tujuan, catatan_bukan_faktur_pajak=True)
    assert KALIMAT in teks and "Faktur pajak diterbitkan terpisah" in teks


@pytest.mark.parametrize("nilai", [False, None, "ABSEN"])
def test_non_pkp_atau_tak_diketahui_tanpa_catatan_pajak(tangkap, nilai):
    x = {} if nilai == "ABSEN" else {"catatan_bukan_faktur_pajak": nilai}
    teks = _render(tangkap, **x)
    assert "faktur pajak" not in teks.lower()
    assert "SO-T" in teks and "PRO-T" in teks          # dokumen lain tetap utuh


def test_tak_ada_templat_lain_memuat_catatan_pajak():
    kena = sorted(p.name for p in (APP / "templates" / "pdf").rglob("*.html")
                  if "faktur pajak" in p.read_text().lower() and p.name != "proforma.html")
    assert kena == [], kena
    t = (APP / "templates" / "pdf" / "proforma.html").read_text()
    i = t.index(KALIMAT)
    assert t.rfind("{% if proforma.get('catatan_bukan_faktur_pajak') %}", 0, i) > t.rfind("{% endif %}", 0, i)


def test_pemuat_membaca_is_pkp_ketat_dari_tenant():
    src = Path(PF.__file__).read_text()
    f = ast.get_source_segment(src, next(n for n in ast.walk(ast.parse(src))
                                         if isinstance(n, ast.AsyncFunctionDef) and n.name == "muat_pdf_proforma"))
    assert 'SELECT display_name, address, phone, logo_url, is_pkp FROM "Tenant" WHERE id = $1' in f
    assert '_catatan_pajak = bool(tenant_row and tenant_row["is_pkp"] is True)' in f
    assert '"catatan_bukan_faktur_pajak": _catatan_pajak,' in f
