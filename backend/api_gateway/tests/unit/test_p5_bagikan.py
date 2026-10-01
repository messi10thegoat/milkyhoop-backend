"""P5 SO-dokumen: tautan publik dokumen + lacak terkirim/dibuka (1 Okt 2026).
Diuji ujung-ke-ujung di salinan DB (20 cek: hash-saja, 30 hari, halaman/PDF publik, bot tak terhitung, internal tak
terhitung, cabut/kedaluwarsa 410, token asing 404, draf 409, tenant lain tak bisa mencabut). Di sini: permukaan yang
harus TETAP sempit + aturan dibuka."""
import asyncio
import inspect
import time

import jwt
import pytest

from app.middleware import permission_middleware as PM
from app.middleware.auth_middleware import AuthMiddleware
from app.middleware.rate_limit_middleware import RateLimitMiddleware
from app.routers import dokumen as DK
from app.services import dokumen_bagikan as DB

TOK = "A" * 21 + "b-_" + "9" * 19  # 43 karakter


@pytest.mark.parametrize("path,method,publik", [
    (f"/api/public/d/{TOK}", "GET", True),
    (f"/api/public/d/{TOK}/pdf", "GET", True),
    (f"/api/public/d/{TOK}/view", "POST", True),
    (f"/api/public/d/{TOK}", "POST", False),
    (f"/api/public/d/{TOK}/view", "GET", False),
    (f"/api/public/d/{TOK}/pdf", "POST", False),
    (f"/api/public/d/{TOK[:-1]}", "GET", False),
    (f"/api/public/d/{TOK}x", "GET", False),
    (f"/api/public/d/{TOK}/../x", "GET", False),
    ("/api/public/d/", "GET", False),
    (f"/api/public/d/{TOK}/html", "GET", False),
])
def test_auth_publik_hanya_tiga_rute_tepat(path, method, publik):
    assert AuthMiddleware(app=None)._is_public_share(path, method) is publik


@pytest.mark.parametrize("path,method,izin", [
    ("/api/documents/proforma/X/share", "POST", ("proforma", "E")),
    ("/api/documents/receipt/X/share", "POST", ("receive_payment", "E")),
    ("/api/documents/invoice/X/shares/Y/revoke", "POST", ("sales_invoice", "E")),
    ("/api/documents/rekap/X/share", "POST", ("sales_order", "E")),
    ("/api/documents/quotation/X/shares", "GET", ("quote", "R")),
    ("/api/documents/delivery/X/shares", "GET", ("sales_invoice", "R")),
])
def test_izin_bagikan_per_jenis(path, method, izin):
    assert PM.PermissionMiddleware(lambda *a: None, False)._find_permission(path, method) == izin


def test_ember_laju_publik_ketat():
    rl = RateLimitMiddleware.__new__(RateLimitMiddleware)
    rl.auth_paths = set()
    assert rl._bucket(f"/api/public/d/{TOK}", "GET") == "publik"
    assert rl._get_limits(f"/api/public/d/{TOK}", "GET") == (30, 60)
    assert rl._bucket("/api/public/fonts/Inter-Bold.ttf", "GET") == "read"


def test_token_hash_dan_berlaku():
    from datetime import datetime, timedelta, timezone
    assert len(DB.hash_token(TOK)) == 64 and DB.hash_token(TOK) != TOK
    sek = datetime.now(timezone.utc)
    assert DB.berlaku({"revoked_at": None, "expires_at": sek + timedelta(days=1)}, sek)
    assert not DB.berlaku({"revoked_at": sek, "expires_at": sek + timedelta(days=1)}, sek)
    assert not DB.berlaku({"revoked_at": None, "expires_at": sek - timedelta(seconds=1)}, sek)


def test_tenant_dari_jwt_hanya_tanda_tangan_sah(monkeypatch):
    rahasia = "r" * 40
    monkeypatch.setenv("JWT_SECRET", rahasia)
    sah = jwt.encode({"tenant_id": "t1", "exp": int(time.time()) + 60}, rahasia, algorithm="HS256")
    palsu = jwt.encode({"tenant_id": "t1"}, "x" * 40, algorithm="HS256")
    basi = jwt.encode({"tenant_id": "t1", "exp": int(time.time()) - 60}, rahasia, algorithm="HS256")
    assert DB.tenant_dari_jwt("Bearer " + sah) == "t1"
    for buruk in ("Bearer " + palsu, "Bearer " + basi, "Bearer x", None, sah):
        assert DB.tenant_dari_jwt(buruk) is None


def test_get_halaman_tidak_mencatat_dibuka():
    """Bot pratinjau tautan (WhatsApp) mengambil URL tanpa JS -> GET halaman/PDF tak boleh mencatat dibuka."""
    for fn in (DK.halaman_dokumen_publik, DK.pdf_dokumen_publik):
        assert "catat_dibuka" not in inspect.getsource(fn)
    assert "catat_dibuka" in inspect.getsource(DK.suar_dibuka)


def test_suar_internal_tidak_dihitung(monkeypatch):
    from datetime import datetime, timedelta, timezone
    t = {"id": 1, "tenant_id": "t1", "kind": "rekap", "doc_id": "d", "revoked_at": None,
         "expires_at": datetime.now(timezone.utc) + timedelta(days=1)}
    tercatat = []

    async def tautan(conn, token):
        return t, "Usaha"

    async def catat(conn, x):
        tercatat.append(x)

    class _Acq:
        async def __aenter__(self): return None
        async def __aexit__(self, *a): return False

    class _Pool:
        def acquire(self): return _Acq()

    async def pool(): return _Pool()
    monkeypatch.setattr(DK, "_tautan_dari_token", tautan)
    monkeypatch.setattr(DK, "_pool", pool)
    monkeypatch.setattr(DB, "catat_dibuka", catat)
    from types import SimpleNamespace
    monkeypatch.setattr(DB, "tenant_dari_jwt", lambda h: "t1" if h == "internal" else ("t2" if h == "lain" else None))
    for h in ("internal", "lain", None):
        asyncio.run(DK.suar_dibuka(SimpleNamespace(headers={"authorization": h} if h else {}), TOK))
    assert len(tercatat) == 2  # internal (tenant sama) tak dihitung; tenant lain & tanpa token dihitung


def test_halaman_tak_berlaku():
    assert DK._tak_berlaku(None, 404).status_code == 404 and "Hubungi" not in DK._tak_berlaku(None, 404).body.decode()
    r = DK._tak_berlaku("Kaos Biru", 410)
    assert r.status_code == 410 and "Tautan tidak berlaku. Hubungi Kaos Biru untuk tautan baru." in r.body.decode()


def test_iframe_dokumen_bersandbox_tanpa_izin():
    """Syarat tinjauan MASTER (A): srcdoc seasal milkyhoop.com (localStorage token aplikasi) -> sandbox TANPA
    allow-scripts/allow-same-origin supaya injeksi di HTML dokumen tak bisa membaca token/cookie."""
    src = inspect.getsource(DK.halaman_dokumen_publik)
    tag = src[src.index('<iframe id="dok"'):src.index("</iframe>")]
    assert tag.startswith('<iframe id="dok" sandbox=""')
    assert "allow-" not in tag
    assert "contentDocument" not in src  # skrip induk tak boleh bergantung pada akses ke isi iframe


def test_suar_tanpa_cookie_dan_jwt_hanya_header():
    src = inspect.getsource(DK.halaman_dokumen_publik)
    assert "credentials: 'omit'" in src and "set_cookie" not in inspect.getsource(DK.suar_dibuka)


def test_font_publik_boleh_lintas_asal():
    r = asyncio.run(DK.font_publik("Inter-Regular.ttf"))
    assert r.headers["access-control-allow-origin"] == "*"


def test_log_menyamarkan_token():
    import logging
    from app.utils import log_tautan as LT
    assert LT.samarkan(f'1.2.3.4 - "GET /api/public/d/{TOK}/pdf HTTP/1.1" 200') == '1.2.3.4 - "GET /api/public/d/***/pdf HTTP/1.1" 200'
    assert LT.samarkan(f"/d/{TOK}") == "/d/***"
    rec = logging.LogRecord("uvicorn.access", 20, "x", 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4", "GET", f"/api/public/d/{TOK}", "1.1", 200), None)
    LT.SaringTokenTautan().filter(rec)
    assert TOK not in rec.getMessage() and "/api/public/d/***" in rec.getMessage()


def test_saringan_log_terpasang_saat_impor_app():
    import pathlib
    main = (pathlib.Path(DK.__file__).parents[1] / "main.py").read_text()
    assert "_pasang_saring_log_tautan()" in main and main.index("_pasang_saring_log_tautan()") < main.index("app = FastAPI(")


def test_iframe_tak_menyusut_di_layar_sempit():
    """HP 390px: iframe item flex menyusut ke lebar layar lalu diskala lagi -> kertas terpotong (diukur 1 Okt).
    Lebar tata letak iframe harus tetap lebar kertas; pengecilan hanya lewat transform."""
    import re
    src = inspect.getsource(DK)
    aturan = re.search(r"\n  iframe \{\{([^}]*)\}\}", src).group(1)
    assert "flex: none" in aturan and "max-width" not in aturan
    assert "overflow: hidden" in re.search(r"\.kertas \{\{([^}]*)\}\}", src).group(1)


def test_documents_membawa_nama_usaha_dan_berlaku_penawaran():
    """Kartu Kirim (FE P5): pesan memakai nama usaha + berlaku sampai penawaran dari /documents (satu panggilan)."""
    src = inspect.getsource(DK.susun_dokumen)
    assert "\"business_name\"" in src and "display_name FROM \"Tenant\"" in src
    assert "\"valid_until\": _tgl(q[\"expiry_date\"])" in src
