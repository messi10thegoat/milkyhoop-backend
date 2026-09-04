"""Rotasi sesi: ambang, sumber waktu, dan pengecualian aksi tertunda.

KENAPA TES INI ADA
Perilaku "sesi dengan aksi tertunda TIDAK berotasi" sudah benar SEBELUM ia
ditulis eksplisit -- tapi benar SECARA KEBETULAN: jalur "aksi menunggu
konfirmasi" pulang lebih awal, sehingga rotasi tak pernah tercapai. Tak
seorang pun memutuskannya. Kalau `return` awal itu kelak dipindahkan, rotasi
akan MEMBUANG kartu konfirmasi yang sedang dilihat pengguna, dan tak ada yang
berbunyi. Tes ini yang berbunyi.

Kelas yang sama dengan `batch_warehouse_stock`: aman hanya karena dipasangkan
dengan nilai lain yang kebetulan sudah diperiksa.
"""

import re
from pathlib import Path

SUMBER = (
    Path(__file__).resolve().parents[2]
    / "app/services/unified_agent/session_manager.py"
)


def _teks() -> str:
    return SUMBER.read_text(encoding="utf-8")


def test_ambang_ada_di_SATU_tempat():
    teks = _teks()
    assert "UMUR_SESI_MAKS_JAM = 2" in teks
    # Angka 2 tak boleh ditulis ulang di dalam kueri rotasi.
    assert teks.count("UMUR_SESI_MAKS_JAM") >= 2, "ambang harus DIPAKAI, bukan cuma didefinisikan"


def test_dihitung_dari_updated_at_BUKAN_created_at():
    """created_at akan membunuh sesi kerja panjang di tengah jalan."""
    teks = _teks()
    i = teks.index("AS basi")
    jendela = teks[max(0, i - 200) : i]
    assert "updated_at" in jendela, "rotasi harus memakai updated_at"
    assert "created_at" not in jendela, (
        "rotasi memakai created_at -- itu memutus sesi yang dipakai lintas hari"
    )


def test_sesi_lama_TIDAK_disentuh():
    """Sesi yang lenyap terbaca pengguna sebagai kehilangan data."""
    teks = _teks()
    blok = teks[teks.index("if row and row[\"basi\"]") : teks.index("async def _ada_aksi_tertunda")]
    for terlarang in ("DELETE FROM chat_sessions", "UPDATE chat_sessions SET status"):
        assert terlarang not in blok, f"rotasi menyentuh sesi lama: {terlarang}"


def test_aksi_tertunda_MENAHAN_rotasi_secara_eksplisit():
    teks = _teks()
    blok = teks[teks.index("if row and row[\"basi\"]") : teks.index("self.sesi_dirotasi = True")]
    assert "_ada_aksi_tertunda" in blok, (
        "pengecualian aksi tertunda harus EKSPLISIT di jalur rotasi, bukan "
        "bergantung pada `return` lebih awal di router"
    )


def test_pemeriksaan_aksi_tertunda_MENUNTUT_belum_kedaluwarsa():
    """Tanpa syarat waktu, sesi dengan PENDING basi terkunci SELAMANYA.

    Terukur 4 Sep 2026: 19 baris PENDING sudah lewat kedaluwarsa, terlama 81,6
    jam, dan tak ada penjadwal yang membersihkannya.
    """
    teks = _teks()
    blok = teks[teks.index("async def _ada_aksi_tertunda") :][:1600]
    assert "status = 'PENDING'" in blok
    assert "expires_at > now()" in blok, (
        "pemeriksaan harus menuntut kartu yang MASIH hidup; tanpa itu sesi "
        "dengan PENDING kedaluwarsa tak akan pernah berotasi"
    )


def test_gagal_TUTUP_saat_pemeriksaan_bermasalah():
    """Salah menahan rotasi cuma menunda; salah merotasi membuang kartu."""
    teks = _teks()
    blok = teks[teks.index("async def _ada_aksi_tertunda") :][:1800]
    ekor = blok[blok.index("except Exception") :]
    assert "return True" in ekor, "harus gagal-TUTUP (menahan rotasi), bukan gagal-buka"


def test_bendera_hanya_untuk_rotasi_umur():
    """`session_rotated` tak boleh true saat pengguna menekan 'percakapan baru'."""
    teks = _teks()
    assert teks.count("self.sesi_dirotasi = True") == 1, (
        "hanya SATU tempat boleh menyalakan bendera: cabang rotasi umur"
    )
    blok = teks[teks.index("async def _buat_sesi_baru") :]
    assert "sesi_dirotasi = True" not in blok, (
        "pembuatan sesi baru biasa TIDAK boleh menyalakan bendera rotasi"
    )
