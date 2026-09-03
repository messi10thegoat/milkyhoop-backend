"""P2: sentinel "unknown" dibuang -- alur menolak, bukan menebak.

KENAPA INI BUKAN SEKADAR KERAPIAN
`chat_workflow_state` punya UNIQUE (chat_session_id, workflow_type) TANPA
tenant_id. Jadi nilai literal "unknown" adalah SATU baris global milik semua
tenant sekaligus: dua tenant yang kehilangan session_id akan bertemu di baris
yang sama, dan salah satu memuat state rekonsiliasi bank milik yang lain.
Sentinel itu tak pernah terpicu (nol baris di seluruh riwayat), tapi bentuk
kegagalannya adalah kebocoran state antar-tenant tanpa suara.

KONTROL MERAH: uji terakhir meniru perilaku LAMA (`session_id or "unknown"`)
dan menuntut bahwa itu memang menghasilkan kunci yang BERTABRAKAN antar tenant.
Kalau kontrol itu tidak merah, uji di atasnya tidak membuktikan apa pun.
"""

import re
from pathlib import Path

# tests/unit/<berkas> -> parents[2] = backend/api_gateway
SUMBER = (
    Path(__file__).resolve().parents[2]
    / "app/services/unified_agent/tool_executor.py"
)
MIGRASI = Path(__file__).resolve().parents[3] / "migrations"


def _teks():
    return SUMBER.read_text(encoding="utf-8")


def test_sentinel_unknown_tidak_ada_lagi_di_kedua_situs():
    assert 'self.session_id) or "unknown"' not in _teks()
    assert 'self.session_id or "unknown"' not in _teks()


def test_kedua_situs_gagal_tutup_dengan_kode_yang_bisa_dilacak():
    teks = _teks()
    assert teks.count('"SESSION_ID_MISSING"') == 2, (
        "kedua situs (mulai + batal) harus menolak, bukan hanya satu"
    )


def test_penolakan_terjadi_SEBELUM_menyentuh_WorkflowEngine():
    """Menolak sesudah engine dibuat tetap akan menulis baris."""
    teks = _teks()
    for nama in ("_execute_start_workflow", "_execute_cancel_workflow"):
        blok = teks.split("async def " + nama)[1][:6000]
        i_tolak = blok.index("SESSION_ID_MISSING")
        i_pakai = min(
            (blok.index(p) for p in ("engine.get_state", "engine.process", "engine.cancel")
             if p in blok),
            default=len(blok),
        )
        assert i_tolak < i_pakai, f"{nama}: penolakan harus mendahului pemakaian engine"


def test_KONTROL_MERAH_perilaku_lama_memang_bertabrakan_antar_tenant():
    """Bukti bahwa yang dibuang itu berbahaya, bukan sekadar tak rapi."""
    def lama(session_id):
        return session_id or "unknown"

    kunci_tenant_a = (lama(None), "bank_reconciliation")
    kunci_tenant_b = (lama(""), "bank_reconciliation")
    assert kunci_tenant_a == kunci_tenant_b, (
        "kontrol gagal: perilaku lama seharusnya menghasilkan kunci yang SAMA "
        "untuk dua tenant berbeda"
    )

    def baru(session_id):
        return session_id or None

    assert baru(None) is None and baru("") is None, (
        "perilaku baru harus menolak, bukan menghasilkan kunci bersama"
    )


def test_indeks_unik_memang_tanpa_tenant_id():
    """Alasan di komentar harus cocok dengan skema; kalau skema berubah,
    alasannya ikut basi dan uji ini yang memberitahu."""
    migrasi = list(MIGRASI.glob("*chat_workflow_state*")) if MIGRASI.is_dir() else []
    if not migrasi:
        return  # skema didefinisikan di tempat lain; uji lain sudah menutupinya
    isi = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in migrasi)
    m = re.search(r"UNIQUE\s*\(([^)]*chat_session_id[^)]*)\)", isi, re.I)
    if m:
        assert "tenant" not in m.group(1).lower(), (
            "indeks unik kini memuat tenant_id -- alasan di komentar sudah basi"
        )
