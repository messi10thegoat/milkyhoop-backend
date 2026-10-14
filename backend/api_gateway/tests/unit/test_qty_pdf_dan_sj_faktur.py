"""14 Okt 2026 (MASTER, temuan pemilik SO-2609-0031 grapgrap):
(1) qty di PDF = SATU filter `qty` (teks_galat.qty): '25' / '2,5' / '1.250' -- dulu SJ '25.0000', faktur B membulatkan
    lewat `currency`, lainnya "%g" ('2.5', '1e+06').
(2) SJ-FAKTUR-SO-ID: SJ asal 'faktur' TIDAK punya invoice_fulfillments.sales_order_id (chk_if_asal_kolom: kolom itu
    khusus asal 'so'); SO-nya lewat fakturnya. Pembaca yang mengaitkan SJ ke SO WAJIB COALESCE(f.sales_order_id,
    si.sales_order_id), atau sadar asal (saring asal = 'so' / membaca f.asal). Dulu keluarga Lampiran SO melewatkan
    semua SJ faktur (grapgrap: 4 dari 4 SJ aktif)."""
import pathlib
import re
from decimal import Decimal

import pytest

from app.routers import sales_order_lampiran as SL
from app.services import pdf_service as PS

APP = pathlib.Path(PS.__file__).resolve().parents[1]
TPL = APP / "templates" / "pdf"


@pytest.mark.parametrize("v,harap", [(Decimal("25.0000"), "25"), (Decimal("2.5000"), "2,5"), (2.5, "2,5"),
                                     (Decimal("1250000"), "1.250.000"), (Decimal("0.125"), "0,125"), (0, "0"),
                                     (None, ""), ("", "")])
def test_format_qty(v, harap):
    assert PS.format_qty(v) == harap


def test_filter_qty_terdaftar_dan_merender():
    env = PS.PDFService().jinja_env
    assert env.filters["qty"] is PS.format_qty
    assert env.from_string("{{ q | qty }}").render(q=Decimal("25.0000")) == "25"


BERKAS_QTY = {"delivery_note.html": 1, "sales_invoice.html": 2, "sales_invoice_b.html": 1, "proforma.html": 1,
              "quote.html": 1, "credit_note.html": 1, "bill_invoice.html": 2}


@pytest.mark.parametrize("nama,n", sorted(BERKAS_QTY.items()))
def test_sel_qty_template_memakai_filter_qty(nama, n):
    s = (TPL / nama).read_text()
    sel = re.findall(r"\{\{[^}]*\b(?:item|it)\.(?:qty|quantity)\b[^}]*\}\}", s)
    assert len(sel) == n, sel
    for x in sel:
        assert "| qty" in x and "%g" not in x and "currency" not in x, x


def test_tak_ada_template_pdf_yang_mencetak_qty_mentah():
    for p in sorted(TPL.glob("*.html")) + sorted((TPL / "_partials").glob("*.html")):
        for x in re.findall(r"\{\{[^}]*\b(?:item|it)\.(?:qty|quantity)\b[^}]*\}\}", p.read_text()):
            assert "| qty" in x, (p.name, x)


# ---------- SJ-FAKTUR-SO-ID ----------

def test_keluarga_lampiran_sj_faktur_lewat_fakturnya():
    cabang = SL._SQL_KELUARGA[SL._SQL_KELUARGA.index("SELECT 'delivery'"):SL._SQL_KELUARGA.index("SELECT 'sales_invoice'")]
    assert "COALESCE(f.sales_order_id, fsi.sales_order_id) = so.id" in cabang
    assert "fsi.id = f.invoice_id AND fsi.tenant_id = $2" in cabang


_STR = re.compile(r'"""(.*?)"""', re.S)


def _pelanggar(akar=None):
    akar = akar or APP
    out = []
    for p in sorted(akar.rglob("*.py")):
        for m in _STR.finditer(p.read_text()):
            sql = m.group(1)
            for a in set(re.findall(r"\binvoice_fulfillments\s+([a-z_]\w*)", sql)) - {"where", "set", "on", "using"}:
                if not re.search(rf"\b{a}\.sales_order_id\b", sql):
                    continue
                sadar = (f"COALESCE({a}.sales_order_id" in sql or re.search(rf"\b{a}\.asal\s*=\s*'so'", sql)
                         or re.search(rf"\b{a}\.asal\b", sql))
                if not sadar:
                    out.append(f"{p.relative_to(akar)}: {' '.join(sql.split())[:140]}")
    return out


def test_pembaca_sj_ke_so_wajib_coalesce_atau_sadar_asal():
    assert _pelanggar() == []


def test_pemindai_menangkap_bentuk_lama(tmp_path):
    """Kontrol merah pemindai: SQL lama keluarga Lampiran harus tertangkap."""
    berkas = tmp_path / "app" / "x.py"
    berkas.parent.mkdir()
    berkas.write_text('Q = """SELECT f.id FROM invoice_fulfillments f JOIN so ON f.sales_order_id = so.id WHERE f.tenant_id = $2"""\n')
    assert len(_pelanggar(berkas.parent)) == 1
