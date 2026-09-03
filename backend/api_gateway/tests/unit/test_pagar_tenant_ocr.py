"""Pagar tenant pada pembacaan `chat_session_state` di jalur confirm.

APA YANG DIJAGA
`_confirm_direct_action` menerima `session_id` LANGSUNG dari body permintaan
(unified_chat.py:7595 `session_id=body.session_id`) dan tidak pernah memeriksa
kepemilikannya -- berbeda dengan `pending_action_id` yang dicari
`WHERE id = $1 AND tenant_id = $2`. Sampai 3 Sep 2026, pembacaan
`document_context` di jalur itu TIDAK menyaring tenant, sehingga pemanggil di
tenant A yang mengirim session_id milik tenant B membaca `document_context`
tenant B; `ocr_text`-nya lalu dipakai memulihkan nomor telepon yang masuk ke
record milik A.

DIUKUR ATAS DATA PRODUKSI (3 Sep 2026, sebelum tambalan):
    tanpa saringan  -> 1 baris milik grapgrap-manado terbaca oleh pemanggil
                       kaos-biru
    dengan saringan -> 0 baris
    sesi sendiri    -> 23 baris, tetap terbaca (bukan fitur yang dimatikan)

CAKUPAN YANG JUJUR: uji ini membaca SUMBER, bukan menjalankan permintaan HTTP.
Ia menjaga agar saringan tenant tidak hilang lagi dari situs itu. Bukti bahwa
saringan itu benar-benar menghentikan kebocoran ada pada pengukuran di atas,
yang dijalankan atas basis data sungguhan -- bukan pada uji ini.

Uji ini MERAH pada kode sebelum tambalan; itu diverifikasi dengan
mengembalikan sumber ke keadaan lama dan menjalankannya ulang.
"""

import re
from pathlib import Path

SUMBER = Path(__file__).resolve().parents[2] / "app/routers/unified_chat.py"

def _jendela_setelah(teks: str, penanda: str, n: int = 220) -> str:
    i = teks.index(penanda)
    return teks[i : i + n]


def test_pembacaan_document_context_menyaring_tenant():
    teks = SUMBER.read_text(encoding="utf-8")
    jendela = _jendela_setelah(teks, "SELECT document_context FROM chat_session_state")
    assert "WHERE session_id" in jendela, "situs berubah bentuk -- periksa ulang"
    assert "tenant_id" in jendela, (
        "pembacaan document_context TANPA saringan tenant: session_id datang "
        "dari body permintaan dan tak diperiksa kepemilikannya"
    )


def test_tenant_id_ikut_dikirim_sebagai_parameter():
    """Saringan di SQL tak berarti kalau parameternya tak dioper."""
    teks = SUMBER.read_text(encoding="utf-8")
    i = teks.index("SELECT document_context FROM chat_session_state")
    potongan = teks[i : i + 400]
    assert "tenant_id," in potongan, (
        "tenant_id tidak dioper sebagai parameter ke fetchrow"
    )


def test_situs_ini_satu_satunya_pembaca_document_context_tanpa_pagar():
    """Kalau muncul pembaca baru tanpa tenant_id, uji ini yang memberitahu."""
    teks = SUMBER.read_text(encoding="utf-8")
    # ⚠️ Jendela, BUKAN `[^"]*`: SQL di :6748 dipecah menjadi DUA literal yang
    # bersebelahan, jadi pola yang berhenti di tanda kutip memotong `WHERE`-nya
    # dan melaporkan pelanggaran palsu. Versi pertama uji ini melakukan persis
    # itu -- merah atas kode yang sudah benar.
    tanpa_pagar = []
    for m in re.finditer(r"FROM chat_session_state", teks):
        jendela = teks[m.start(): m.start() + 220]
        if "tenant_id" not in jendela:
            tanpa_pagar.append(jendela[:90].replace("\n", " "))
    assert not tanpa_pagar, (
        "ada pembacaan chat_session_state tanpa saringan tenant: " + repr(tanpa_pagar)
    )
