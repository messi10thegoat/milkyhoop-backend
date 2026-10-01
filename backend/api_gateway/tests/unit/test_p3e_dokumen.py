"""P3e SO-dokumen: HTML layar = HTML PDF, rute font publik sempit, izin render per jenis, kunci cache per versi.
(HTML=PDF diukur nyata di luar suite: 25 dokumen grapgrap+kaos, multiset huruf identik, ukuran kertas cocok,
kontrol merah alat terdeteksi; skema so-documents valid 7 SO; bundel kwitansi 3 halaman A5.)"""
import asyncio
import re

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.middleware.auth_middleware import AuthMiddleware
from app.routers import dokumen as DK
from app.services import pdf_service as PS

KOP = {"name": "Usaha", "address": None, "phone": None, "email": None, "logo_data": None}


def _kw():
    return PS.get_pdf_service().render_receipt({"receipt_number": "RCV-T", "amount": 1000, "amount_words": "Seribu Rupiah",
                                                "payer_name": "P", "status": "posted"}, KOP)


def _penawaran():
    return PS.get_pdf_service().render_quote({"quote_number": "Q-T", "status": "sent", "items": [], "total_amount": 0}, KOP)


def test_html_layar_memakai_html_pdf_yang_sama_dan_kertas_dari_tata_letak():
    s = PS.get_pdf_service()
    for r, kertas, lebar, tinggi in [(_kw(), "A5-landscape", 793.70, 559.37), (_penawaran(), "A4", 793.70, 1122.52)]:
        h = s.html_layar(r)
        badan_asli = r.html[r.html.index("<body>"):]
        assert badan_asli in h, "isi <body> layar harus string yang SAMA dengan PDF"
        assert f'data-kertas="{kertas}"' in h
        assert f"width: {lebar:.2f}px" in h and f"min-height: {tinggi:.2f}px" in h
        assert 'url("/api/public/fonts/Inter-Regular.ttf")' in h and 'url("fonts/' not in h
        # lembar gaya PDF disisipkan SESUDAH gaya inline dokumen (urutan WeasyPrint)
        assert h.index('data-sumber="pdf"') < h.index("</head>")


def test_font_publik_hanya_daftar_putih():
    assert DK.FONT_SAH == {"Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf", "Inter-Italic.ttf"}
    r = asyncio.run(DK.font_publik("Inter-Bold.ttf"))
    assert str(r.path).endswith("fonts/Inter-Bold.ttf") and "immutable" in r.headers["cache-control"]
    for jahat in ("LiberationSans-Bold.ttf", "../fonts.css", "LISENSI-Inter-OFL.txt", "Inter-Bold.ttf/../../x"):
        with pytest.raises(HTTPException) as e:
            asyncio.run(DK.font_publik(jahat))
        assert e.value.status_code == 404


@pytest.mark.parametrize("path,method,publik", [
    ("/api/public/fonts/Inter-Regular.ttf", "GET", True),
    ("/api/public/fonts/Inter-Bold.ttf", "GET", True),
    ("/api/public/fonts/Inter-Bold.ttf", "POST", False),
    ("/api/public/fonts/LiberationSans-Bold.ttf", "GET", False),
    ("/api/public/fonts/", "GET", False),
    ("/api/public/anything", "GET", False),
    ("/api/documents/rekap/x/html", "GET", False),
])
def test_pengecualian_auth_hanya_lima_font(path, method, publik):
    assert AuthMiddleware(app=None)._is_public_font(path, method) is publik


@pytest.mark.parametrize("path,izin", [
    ("/api/documents/rekap/X/html", ("sales_order", "R")),
    ("/api/documents/rekap/X/pdf", ("sales_order", "R")),
    ("/api/documents/quotation/X/pdf", ("quote", "R")),
    ("/api/documents/proforma/X/html", ("proforma", "R")),
    ("/api/documents/receipt/X/pdf", ("receive_payment", "R")),
    ("/api/documents/delivery/X/html", ("sales_invoice", "R")),
    ("/api/documents/invoice/X/pdf", ("sales_invoice", "R")),
    ("/api/documents/receipts/pdf", ("receive_payment", "R")),
    ("/api/sales-orders/X/documents", ("sales_order", "R")),
])
def test_render_dipetakan_ke_izin_baca_modulnya(path, izin):
    """Tanpa pola, GET /api/documents/* jatuh ke READ_DEFAULT_OPEN_ALLOWLIST (multi-doctype) = terbuka bagi anggota."""
    mw = PM.PermissionMiddleware(lambda *a: None, False)
    assert mw._find_permission(path, "GET") == izin


def test_kunci_cache_per_versi_dan_tenant():
    r = _kw()
    k1 = DK._kunci_cache("t1", r)
    assert k1.startswith("dokpdf:t1:")
    assert DK._kunci_cache("t1", r) == k1
    assert DK._kunci_cache("t2", r) != k1
    assert DK._kunci_cache("t1", PS.Render(r.html.replace("1.000", "1.001"), r.css, r.font)) != k1


def test_cache_mati_tetap_merender(monkeypatch):
    import app.services.redis_client as RC

    async def tanpa():
        return None
    monkeypatch.setattr(RC, "get_redis", tanpa)
    pdf = asyncio.run(DK.pdf_tercache("t1", _kw()))
    assert pdf[:4] == b"%PDF"


@pytest.mark.parametrize("kind,did", [("foo", "00000000-0000-0000-0000-000000000001"), ("rekap", "bukan-uuid")])
def test_jenis_atau_id_tak_sah_404(kind, did):
    with pytest.raises(HTTPException) as e:
        asyncio.run(DK._render(None, {"tenant_id": "t"}, None, kind, did))
    assert e.value.status_code == 404


def test_kwitansi_uang_muka_wajib_izin_customer_deposit():
    src = open(DK.__file__, encoding="utf-8").read()
    blok = src[src.index('if jenis == "receipt":'):src.index('if jenis == "delivery":')]
    assert re.search(r'FROM customer_deposits WHERE id = \$1 AND tenant_id = \$2', blok)
    assert blok.index('_wajib_izin(request, "R", "customer_deposit")') < blok.index("muat_pdf_kwitansi_uang_muka")
