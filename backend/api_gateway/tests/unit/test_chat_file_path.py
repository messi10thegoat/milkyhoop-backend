"""GET /api/v3/chat/files/{storage_key}: kunci hanya boleh menunjuk objek
unggahan milik tenant pemanggil (888a9058, 24 Sep 2026; diperbarui Unit U1).

Sejak U1 berkas unggahan ada di MinIO berkunci
``<tenant>/uploads/<forms|chat>/<sha256><ext>`` dan rute tidak membaca disk.
Uji perilaku atas helper murni ``app/utils/chat_file_path.py`` (satu-satunya
sumber bentuk kunci) + kontrak sumber bahwa handler memakainya. Uji handler
penuh (storage palsu) ada di test_unggahan_persisten_u1.py.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.utils.chat_file_path import (  # noqa: E402
    kunci_sah_milik_tenant,
    kunci_unggahan,
    tipe_sajian,
    url_berkas,
)

H = "a" * 64
SUMBER = Path(__file__).resolve().parents[2] / "app/routers/unified_chat.py"


def test_kunci_sah_milik_sendiri_diterima():
    for sub in ("chat", "forms"):
        for ext in (".png", ".pdf", ".csv", ".xlsx", ".heic", ".gif"):
            assert kunci_sah_milik_tenant("tenant-a", f"tenant-a/uploads/{sub}/{H}{ext}")


def test_kunci_tenant_lain_ditolak():
    assert not kunci_sah_milik_tenant("tenant-a", f"tenant-b/uploads/chat/{H}.png")


def test_segmen_titik_kosong_backslash_persen_ditolak():
    for kunci in (
        "tenant-a/uploads/chat/../../di-luar.txt",
        f"tenant-a/../tenant-b/uploads/chat/{H}.png",
        f"tenant-a/uploads/chat/./{H}.png",
        f"tenant-a//uploads/chat/{H}.png",
        f"/tenant-a/uploads/chat/{H}.png",
        f"tenant-a\\uploads\\chat\\{H}.png",
        f"tenant-a/uploads/chat/{H}%2epng",
        f"tenant-a/uploads/chat/{H}.png/",
    ):
        assert not kunci_sah_milik_tenant("tenant-a", kunci), kunci


def test_nama_harus_hash_subdir_dan_ext_harus_sah():
    assert not kunci_sah_milik_tenant("tenant-a", "tenant-a/uploads/chat/di-luar.txt")
    assert not kunci_sah_milik_tenant("tenant-a", f"tenant-a/uploads/lain/{H}.png")
    # U2: `documents` (unggahan /api/document-intake) kini subdir SAH.
    assert kunci_sah_milik_tenant("tenant-a", f"tenant-a/uploads/documents/{H}.png")
    assert not kunci_sah_milik_tenant("tenant-a", f"tenant-a/uploads/dokumen/{H}.png")
    assert not kunci_sah_milik_tenant("tenant-a", f"tenant-a/uploads/chat/{H}.html")
    assert not kunci_sah_milik_tenant("tenant-a", f"tenant-a/uploads/chat/{H}")
    assert not kunci_sah_milik_tenant("tenant-a", f"tenant-a/uploads/chat/{H.upper()}.png")


@pytest.mark.parametrize("sub", ["chat", "documents", "forms"])
def test_kunci_bentuk_lama_berkas_lokal_ditolak(sub):
    assert not kunci_sah_milik_tenant("tenant-a", f"tenant-a/{sub}/{H}.png")


@pytest.mark.parametrize("tenant", ["", ".", "..", "a/b", "a b", None])
def test_tenant_tak_sah_ditolak(tenant):
    assert not kunci_sah_milik_tenant(tenant, f"{tenant}/uploads/chat/{H}.png")


def test_kunci_unggahan_gagal_keras_untuk_bagian_tak_sah():
    assert kunci_unggahan("tenant-a", "forms", H, ".jpg") == f"tenant-a/uploads/forms/{H}.jpg"
    assert url_berkas("x") == "/api/v3/chat/files/x"
    for args in (
        ("tenant-a", "lain", H, ".jpg"),
        ("tenant-a", "chat", H, ".exe"),
        ("tenant-a", "chat", "zz", ".jpg"),
        ("a/b", "chat", H, ".jpg"),
    ):
        with pytest.raises(ValueError):
            kunci_unggahan(*args)


def test_tipe_non_gambar_pdf_diunduh_bukan_dirender():
    assert tipe_sajian(f"t/uploads/chat/{H}.png") == ("image/png", True)
    assert tipe_sajian(f"t/uploads/chat/{H}.pdf") == ("application/pdf", True)
    for ext in (".gif", ".csv", ".xlsx", ".heic", ".html", ".svg", ""):
        assert tipe_sajian(f"t/uploads/chat/{H}{ext}") == ("application/octet-stream", False), ext


def test_handler_memakai_helper_dan_tak_join_kunci_mentah():
    teks = SUMBER.read_text(encoding="utf-8")
    i = teks.index("async def get_chat_file(")
    badan = teks[i : teks.index("\nasync def ", i + 10)]
    assert 'sajikan_objek_unggahan(\n        get_storage_service(), ctx["tenant_id"], storage_key' in badan
    assert not re.search(r"os\.path\.join\(\s*UPLOAD_BASE_DIR\s*,\s*storage_key", badan)
