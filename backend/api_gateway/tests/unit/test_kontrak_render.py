"""KONTRAK RENDER (T185) — amplop yang backend terbitkan harus bisa digambar FE.

Kelas bug yang ditutup di sini: backend menjawab BENAR dan FE berperilaku
BENAR, tapi user melihat layar KOSONG. Gerbang entitas menerbitkan kalimat
sebab yang tepat di `content`/`text`/`error.message`; FE untuk CLARIFICATION
tidak pernah menggambar `content` — ia hanya menggambar `actionData.data` —
dan `_to_chat_response` memaksa `data=None` justru untuk bentuk yang gerbang
terbitkan. Tidak ada satu pun assertion di suite lama yang bisa melihat itu,
karena tak satu pun menyebut apa yang FE butuhkan.

Tabel kontraknya + bukti bundel per baris ada di
`app.services.unified_agent.kontrak_render`. Berkas ini MENEGAKKANNYA.

IMPOR LUNAK, disengaja: berkas tes ini harus bisa dibawa ke pohon SEBELUM
patch (bukti merah). Di sana modul/patch belum ada. `ModuleNotFoundError`
bukan bukti apa pun — ia hanya membuktikan berkasnya tidak ada. Jadi impor
yang gagal menjadi None, dan tesnya GAGAL sebagai assertion yang bisa dibaca,
bukan sebagai galat impor.
"""
import os
import sys

import pytest

sys.path.insert(0, "/app/backend/api_gateway")

from app.services.unified_agent.gerbang_entitas import (  # noqa: E402
    periksa_gerbang_entitas,
)

try:  # impor lunak — lihat docstring
    from app.services.unified_agent.kontrak_render import (  # noqa: E402
        KONTRAK,
        periksa_kontrak_render,
    )
except Exception:  # noqa: BLE001
    KONTRAK = None
    periksa_kontrak_render = None

# `app.routers.unified_chat` membangun SessionAwareAgent saat diimpor, dan
# UnifiedAgent memanggil `LLMRouter.from_env()` yang MELEDAK tanpa kunci.
# Kunci boneka ini hanya melewati pemeriksaan konfigurasi saat impor; tak satu
# pun tes di berkas ini memanggil model, jadi tak ada jaringan yang tersentuh.
# `setdefault`: kalau lingkungan sudah punya kunci sungguhan, jangan ditimpa.
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

try:  # impor lunak — lihat docstring
    from app.routers.unified_chat import _to_chat_response  # noqa: E402
except Exception:  # noqa: BLE001
    _to_chat_response = None


def _butuh_kontrak():
    assert periksa_kontrak_render is not None, (
        "modul kontrak_render TIDAK TERPASANG di pohon ini "
        "(impor lunak menghasilkan None) — patch T185 belum ada"
    )


# ══════════════ payload gerbang: satu bentuk per aksi ══════════════

def _bill():
    return {
        "vendor_name": "PT Sinar Abadi",
        "vendor_id": None,
        "items": [{"product_id": None, "product_name": "Kain Katun"}],
    }


def _jual():
    return {
        "customer_name": "Toko Maju",
        "customer_id": None,
        "items": [{"item_id": None, "description": "Kaos Polos"}],
    }


PAYLOAD = {
    "create_bill": _bill,
    "create_quote": _jual,
    "create_sales_invoice": _jual,
    "create_sales_order": _jual,
}

# Kalimat yang SUDAH LIVE, disalin dari keluaran gerbang terpasang. T185 tidak
# boleh menggores satu byte pun: yang berubah HANYA amplopnya. Pesan bill
# khususnya sudah dipakai berkali-kali sebagai kontrol sehat sejak Fase 1a.
PESAN_PERSIS = {
    "create_bill": (
        "PT Sinar Abadi belum terdaftar sebagai vendor, dan Kain Katun belum "
        "ada di master barang. Daftarkan dulu, lalu kirim ulang faktur ini."
    ),
    "create_quote": (
        "Toko Maju belum terdaftar sebagai pelanggan, dan Kaos Polos belum "
        "ada di master barang. Daftarkan dulu, lalu kirim ulang penawaran ini."
    ),
    "create_sales_invoice": (
        "Toko Maju belum terdaftar sebagai pelanggan, dan Kaos Polos belum "
        "ada di master barang. Daftarkan dulu, lalu kirim ulang faktur "
        "penjualan ini."
    ),
    "create_sales_order": (
        "Toko Maju belum terdaftar sebagai pelanggan, dan Kaos Polos belum "
        "ada di master barang. Daftarkan dulu, lalu kirim ulang pesanan "
        "penjualan ini."
    ),
}

AKSI = sorted(PESAN_PERSIS)


# ══════════════ A. isi pesan BYTE-IDENTIK (keempat aksi) ══════════════

@pytest.mark.parametrize("action_key", AKSI)
def test_isi_pesan_byte_identik(action_key):
    """Yang berubah di T185 adalah AMPLOP, bukan KALIMAT.

    Perbandingan `==` string penuh, bukan `in`/startswith: substring lolos
    walau kalimatnya berubah di ujung, dan ujung kalimat inilah yang memuat
    langkah berikutnya ("Daftarkan dulu, lalu kirim ulang <dokumen> ini").
    """
    hasil = periksa_gerbang_entitas(action_key, PAYLOAD[action_key]())
    assert hasil is not None, f"{action_key}: gerbang tidak menahan payload uji"
    assert hasil["content"] == PESAN_PERSIS[action_key]
    # amplop rangkap untuk ~20 pemanggil tanpa cabang eksplisit: TETAP ADA.
    assert hasil["text"] == PESAN_PERSIS[action_key]
    assert hasil["error"]["message"] == PESAN_PERSIS[action_key]
    # dan kalimat itu jugalah yang digambar FE
    assert hasil["data"]["question"] == PESAN_PERSIS[action_key]


# ══════════════ B. amplop gerbang memenuhi kontrak render ══════════════

@pytest.mark.parametrize("action_key", AKSI)
def test_amplop_gerbang_lolos_kontrak(action_key):
    _butuh_kontrak()
    h = periksa_gerbang_entitas(action_key, PAYLOAD[action_key]())
    salah = periksa_kontrak_render(h["message_type"], h.get("text"), h.get("data"))
    assert salah == [], f"{action_key}: {salah}"


@pytest.mark.parametrize("action_key", AKSI)
def test_options_wajib_list_kosong_bukan_none(action_key):
    """FE mengakses `t.options.length` LANGSUNG (bundel `nEt`).

    `None`/absen = TypeError di layar, yang LEBIH BURUK dari kosong: bukan
    cuma tak menggambar, ia meruntuhkan render bubble-nya.
    """
    h = periksa_gerbang_entitas(action_key, PAYLOAD[action_key]())
    assert h["data"]["options"] == []
    assert isinstance(h["data"]["options"], list)


# ══════════════ C. kontrak MENOLAK yang harus ditolak ══════════════

def test_kontrak_menolak_clarification_data_null():
    """Ini persis bentuk yang membuat layar kosong. Kontrak WAJIB menolaknya."""
    _butuh_kontrak()
    salah = periksa_kontrak_render(
        "CLARIFICATION", "kalimat sebab yang benar dan lengkap", None
    )
    assert salah != [], "kontrak MELOLOSKAN CLARIFICATION + data=null"
    assert any("data" in s for s in salah), salah


def test_kontrak_menolak_clarification_options_none():
    _butuh_kontrak()
    salah = periksa_kontrak_render(
        "CLARIFICATION", "x", {"question": "q", "options": None}
    )
    assert salah != []


def test_kontrak_menolak_clarification_tanpa_question():
    _butuh_kontrak()
    salah = periksa_kontrak_render("CLARIFICATION", "x", {"options": []})
    assert any("question" in s for s in salah), salah


def test_kontrak_menolak_tipe_di_luar_tabel():
    """Tipe tak dikenal TIDAK BOLEH lolos diam-diam.

    Kalau ia lolos, tabel yang ketinggalan menjadi tak terlihat — persis
    mekanisme yang membuat kelas bug ini hidup selama ini.
    """
    _butuh_kontrak()
    assert periksa_kontrak_render("TIPE_KARANGAN", "x", {}) != []


# ══════════════ D. yang SUDAH bekerja tetap lolos (kontrol) ══════════════

def test_pil_entitas_tetap_lolos_kontrak():
    """CLARIFICATION + options berisi = pil entitas, jalur yang hidup hari ini.

    Kalau T185 membuat ini gagal, T185 merusak fitur yang bekerja.
    """
    _butuh_kontrak()
    data = {
        "question": "Vendor mana yang Anda maksud?",
        "options": [
            {"value": "id-1", "label": "PT Sinar Abadi"},
            {"value": "id-2", "label": "PT Sinar Jaya"},
        ],
    }
    assert periksa_kontrak_render("CLARIFICATION", "Vendor mana?", data) == []


def test_text_hanya_butuh_teks_bukan_data():
    _butuh_kontrak()
    assert periksa_kontrak_render("TEXT", "halo", None) == []
    assert periksa_kontrak_render("TEXT", "", None) != []


def test_semua_tipe_yang_diminta_ada_di_tabel():
    """Radius tabel = yang tiket sebut. Menghapus baris = menghapus penjagaan."""
    _butuh_kontrak()
    for t in (
        "TEXT",
        "CLARIFICATION",
        "DIRECT_ACTION_PREVIEW",
        "ACTION_RESULT",
        "VALIDATION_ERROR",
        "CHART",
        "TUTORIAL_STEP",
    ):
        assert t in KONTRAK, t
        assert KONTRAK[t].bukti.strip(), f"{t}: baris kontrak tanpa bukti FE"


# ══════════════ E. SAMBUNGANNYA — di sinilah data hilang ══════════════
#
# Lapis A–D menguji gerbang dan tabel. Keduanya HIJAU bahkan sebelum T185,
# karena gerbang memang sudah menerbitkan `data` yang benar sejak Fase 1a.
# Yang RUSAK ada di antaranya: `_to_chat_response` membuang `data` itu.
# Tanpa tes di bawah, T185 akan tampak selesai tanpa memperbaiki apa pun.


class _Resp:
    """AgentResponse tiruan: hanya field yang `_to_chat_response` baca."""

    def __init__(self, message_type, content, extra_data):
        self.message_type = message_type
        self.content = content
        self.extra_data = extra_data
        self.pending_action_id = ""
        self.preview = {}
        self.expires_at = ""
        self.errors = []
        self.trace_id = "t"
        self.iterations = 1
        self.tool_calls_made = []
        self.model_used = "pipeline"
        self.total_latency_ms = 0
        self.session_id = "s"
        self.thinking_stages = []


def _butuh_konversi():
    assert _to_chat_response is not None, (
        "_to_chat_response tidak bisa diimpor dari app.routers.unified_chat"
    )


@pytest.mark.parametrize("action_key", AKSI)
def test_amplop_gerbang_selamat_sampai_respons_http(action_key):
    """REGRESI INTI T185.

    Sebelum patch, cabang CLARIFICATION mensyaratkan `extra_data["options"]`
    TRUTHY. `options: []` — daftar kosong yang SAH, artinya "tak ada pil,
    jawab dengan mengetik" — falsy, jadi `data` dipaksa None. FE lalu
    menggambar `null` dan berhenti. Tes ini gagal MERAH di pohon sebelum
    patch, dengan `data=None`.
    """
    _butuh_konversi()
    g = periksa_gerbang_entitas(action_key, PAYLOAD[action_key]())
    resp = _to_chat_response(_Resp("CLARIFICATION", g["content"], g["data"]))
    assert resp.data is not None, (
        f"{action_key}: `data` dibuang di _to_chat_response -> LAYAR KOSONG"
    )
    assert resp.data["question"] == PESAN_PERSIS[action_key]
    assert resp.data["options"] == []


def test_respons_http_gerbang_lolos_kontrak_render():
    _butuh_kontrak()
    _butuh_konversi()
    g = periksa_gerbang_entitas("create_bill", _bill())
    resp = _to_chat_response(_Resp("CLARIFICATION", g["content"], g["data"]))
    assert periksa_kontrak_render("CLARIFICATION", resp.text, resp.data) == []


def test_pil_entitas_selamat_sampai_respons_http():
    """Kontrol: jalur yang SUDAH bekerja tidak boleh berubah oleh T185."""
    _butuh_konversi()
    data = {
        "question": "Vendor mana yang Anda maksud?",
        "options": [{"value": "id-1", "label": "PT Sinar Abadi"}],
    }
    resp = _to_chat_response(_Resp("CLARIFICATION", "Vendor mana?", data))
    assert resp.data is not None
    assert len(resp.data["options"]) == 1
    assert resp.data["question"] == "Vendor mana yang Anda maksud?"


def test_clarification_tanpa_bahan_tetap_none():
    """Amplop yang benar-benar kosong TIDAK dipalsukan jadi bubble kosong.

    Kontrol negatif: patch memperluas syarat, ia tidak menghapus syarat.
    """
    _butuh_konversi()
    assert _to_chat_response(_Resp("CLARIFICATION", "x", {})).data is None
    assert _to_chat_response(_Resp("CLARIFICATION", "x", None)).data is None


def test_options_none_dinormalkan_jadi_list():
    """Tak ada jalur yang boleh mengirim `options: None` ke `.length` FE."""
    _butuh_konversi()
    resp = _to_chat_response(
        _Resp("CLARIFICATION", "x", {"question": "q", "options": None})
    )
    assert resp.data is not None
    assert resp.data["options"] == []
