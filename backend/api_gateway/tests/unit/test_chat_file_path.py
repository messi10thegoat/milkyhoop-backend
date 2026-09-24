"""GET /api/v3/chat/files/{storage_key}: kunci hanya boleh menunjuk berkas
milik tenant pemanggil di subdir yang sah (24 Sep 2026, unit darurat).

Sebelum tambalan, handler hanya memeriksa ``startswith(tenant_id + "/")`` lalu
``os.path.join(BASE, storage_key)`` tanpa normalisasi, sehingga kunci yang
berawalan tenant sendiri tetap bisa menunjuk ke luar direktori tenant.
Uji perilaku atas helper murni + kontrak sumber bahwa handler memakainya.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.utils.chat_file_path import resolve_berkas_tenant, tipe_sajian  # noqa: E402

H = "a" * 64
SUMBER = Path(__file__).resolve().parents[2] / "app/routers/unified_chat.py"


def _siapkan(tmp_path):
    base = tmp_path / "uploads"
    for t in ("tenant-a", "tenant-b"):
        (base / t / "chat").mkdir(parents=True)
        (base / t / "chat" / f"{H}.png").write_bytes(b"png-" + t.encode())
    (tmp_path / "di-luar.txt").write_text("bukan milik tenant")
    return str(base)


def test_berkas_sah_milik_sendiri_diterima(tmp_path):
    base = _siapkan(tmp_path)
    p = resolve_berkas_tenant(base, "tenant-a", f"tenant-a/chat/{H}.png")
    assert p is not None and open(p, "rb").read() == b"png-tenant-a"


def test_berkas_tenant_lain_ditolak(tmp_path):
    base = _siapkan(tmp_path)
    assert resolve_berkas_tenant(base, "tenant-a", f"tenant-b/chat/{H}.png") is None


def test_segmen_titik_ditolak(tmp_path):
    base = _siapkan(tmp_path)
    for kunci in (
        "tenant-a/chat/../../di-luar.txt",
        f"tenant-a/../tenant-b/chat/{H}.png",
        f"tenant-a/chat/./{H}.png",
        f"tenant-a//chat/{H}.png",
        f"/tenant-a/chat/{H}.png",
        f"tenant-a\\chat\\{H}.png",
    ):
        assert resolve_berkas_tenant(base, "tenant-a", kunci) is None, kunci


def test_nama_harus_hash_dan_subdir_harus_sah(tmp_path):
    base = _siapkan(tmp_path)
    # berkas BENAR-BENAR ada di subdir tak sah, supaya penolakan bukan karena tak ada
    (Path(base) / "tenant-a" / "lain").mkdir()
    (Path(base) / "tenant-a" / "lain" / f"{H}.png").write_bytes(b"x")
    assert resolve_berkas_tenant(base, "tenant-a", "tenant-a/chat/di-luar.txt") is None
    assert resolve_berkas_tenant(base, "tenant-a", f"tenant-a/lain/{H}.png") is None
    assert resolve_berkas_tenant(base, "tenant-a", f"tenant-a/chat/{'b' * 64}.png") is None


def test_symlink_keluar_ditolak(tmp_path):
    base = _siapkan(tmp_path)
    tautan = Path(base) / "tenant-a" / "chat" / f"{'c' * 64}.txt"
    os.symlink(tmp_path / "di-luar.txt", tautan)
    assert resolve_berkas_tenant(base, "tenant-a", f"tenant-a/chat/{'c' * 64}.txt") is None


def test_tipe_non_gambar_pdf_diunduh_bukan_dirender():
    assert tipe_sajian(f"/x/{H}.png") == ("image/png", True)
    assert tipe_sajian(f"/x/{H}.pdf") == ("application/pdf", True)
    for ext in (".html", ".svg", ".xml", ".js", ""):
        assert tipe_sajian(f"/x/{H}{ext}") == ("application/octet-stream", False), ext


def test_handler_memakai_helper_dan_tak_join_kunci_mentah():
    teks = SUMBER.read_text(encoding="utf-8")
    i = teks.index("async def get_chat_file(")
    badan = teks[i : teks.index("\nasync def ", i + 10)]
    assert "resolve_berkas_tenant(UPLOAD_BASE_DIR, tenant_id, storage_key)" in badan
    assert not re.search(r"os\.path\.join\(\s*UPLOAD_BASE_DIR\s*,\s*storage_key", badan)
    assert "tipe_sajian(" in badan and "nosniff" in badan
