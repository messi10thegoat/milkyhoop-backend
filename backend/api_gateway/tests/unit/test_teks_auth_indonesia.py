"""TEKS-AUTH-ID (16 Okt 2026): teks 401/403 middleware auth berbahasa Indonesia. Kode status, kunci JSON (error/message/code/
required_*) TIDAK berubah: FE mencocokkan `code` (MEMBERSHIP_INACTIVE/PERMISSION_DENIED/TOKEN_EXPIRED), bukan teks."""
import re
from pathlib import Path

from app.middleware import auth_middleware as AM
from app.middleware import permission_middleware as PM
from app.middleware import tenant_validation_middleware as TM
from app.services import dashboard_izin as DI

BERKAS = [AM, PM, TM, DI]
INGGRIS = ["Authentication required", "Authentication failed", "Invalid token", "Permission denied",
           "Permission check failed", "You don't have permission", '"error": "Forbidden"']
KODE_TETAP = ["MISSING_TOKEN", "EMPTY_TOKEN", "INVALID_TOKEN", "VALIDATION_ERROR", "UNAUTHENTICATED", "PERMISSION_DENIED",
              "PERMISSION_CHECK_ERROR", "MEMBERSHIP_INACTIVE", "TENANT_MISMATCH"]


def _src(m):
    return Path(m.__file__).read_text()


def test_tak_ada_teks_inggris_401_403_di_middleware_auth():
    for m in BERKAS:
        s = _src(m)
        for t in INGGRIS:
            # log (logger.*) boleh Inggris; cek hanya literal respons: baris ber-"error"/"message"/detail
            for baris in s.splitlines():
                if t in baris and not baris.lstrip().startswith(("logger", "f\"Permission denied", "#", "\"\"\"")) \
                        and "logger" not in baris and "logging" not in baris:
                    raise AssertionError(f"{m.__name__}: {baris.strip()}")


def test_kode_dan_kunci_tidak_berubah():
    s = _src(AM) + _src(PM) + _src(TM)
    for k in KODE_TETAP:
        assert k in s, k
    for kunci in ('"required_module"', '"required_action"', '"message"', '"error"'):
        assert kunci in s


def test_status_tetap():
    s = _src(AM)
    assert s.count("status_code=401") >= 5
    p = _src(PM)
    assert "status_code=403" in p and "status_code=401" in p


def test_aksi_diterjemahkan_ke_indonesia():
    s = _src(PM)
    for a in ("membuat", "melihat", "mengubah", "menghapus", "membatalkan", "menyetujui", "memposting", "mengekspor"):
        assert f'"{a}"' in s
    assert re.search(r'Anda tidak punya izin untuk \{action_names', s)
