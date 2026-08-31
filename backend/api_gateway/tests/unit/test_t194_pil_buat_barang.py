"""T194 — jalur "daftarkan barang baru" dari pil entity (`_pil_buat_barang`).

KLAIM yang diuji:
  1. PENJAGA PUTARAN menyala pada pertanyaan KEDUA dengan sidik sama, dan
     pesannya MENYEBUT field yang macet.
  2. T1 (nama persis, produk hidup) → memakai id master, NOL pembuatan
     (fungsi POST tidak pernah dipanggil).
  3. T3 (dua kandidat longgar) → CLARIFICATION dengan DUA opsi kandidat +
     opsi buat-baru yang memuat NAMA PERSIS, dan TIDAK ada item dibuat.
  4. HANYA DUA pertanyaan: tipe lalu harga. Satuan TIDAK PERNAH disebut.
  5. Penulisan state APPEND-ONLY: resolved_payload / entity_queue /
     entity_cursor MASIH ADA sesudah jalur baru menulis.
  6. vendor/customer TETAP perilaku lama (`_pil_create_new_lama`).
  7. Nilai item_type yang dikirim ke REST = 'goods'/'service' (Literal),
     BUKAN label Indonesia ('barang' → 422).

TIDAK DIKLAIM: bahwa POST /api/items sungguh berjalan, atau bahwa praperiksa
SQL-nya benar terhadap PostgreSQL. Kedua batas itu diganti tiruan; yang diuji
adalah KEPUTUSAN alur di sekitarnya.
"""

import os
import sys

import pytest

sys.path.insert(0, "/app/backend/api_gateway")
os.environ.setdefault("OPENAI_API_KEY", "uji-t194-bukan-kunci-asli")

from app.routers import unified_chat as uc  # noqa: E402
from app.routers.unified_chat import (  # noqa: E402
    ChatMessageResponse,
    _pil_buat_barang,
    _pil_create_new_lama,
    _t194_sidik,
    _t194_sidik_tawar,
)


CTX = {
    "tenant_id": "t-1",
    "user_id": "u-1",
    "auth_token": "tok",
    "tenant_name": "Tenant Uji",
}

ANTREAN = [{"slot": "item_id", "entity_type": "item", "line_index": 0}]


def doc_ctx_awal(payload):
    return {
        "pending_entity_selection": True,
        "resolved_action_key": "create_bill",
        "resolved_payload": payload,
        "entity_queue": ANTREAN,
        "entity_cursor": 0,
    }


class SMPalsu:
    """document_context bersemantik KOLOM: setiap penulisan = TIMPA UTUH."""

    def __init__(self, awal):
        self.document_context = dict(awal)

    async def get_or_create_session(self, sid):
        return sid

    async def update_state(self, sid, **updates):
        if "document_context" in updates:
            self.document_context = updates["document_context"]


class Rekam:
    """Pencatat panggilan untuk batas yang ditiru."""

    def __init__(self):
        self.post = []
        self.gerbang = []


@pytest.fixture
def rekam(monkeypatch):
    r = Rekam()

    async def _post_palsu(ctx, nama, fields, satuan):
        r.post.append({"nama": nama, "fields": dict(fields), "satuan": satuan})
        return {"ok": True, "id": "ID-BARU"}

    async def _gerbang_palsu(**kw):
        r.gerbang.append(kw)
        return ChatMessageResponse(
            message_type="DIRECT_ACTION_PREVIEW",
            text="Pilihan diterima.",
            session_id=kw.get("sid"),
        )

    monkeypatch.setattr(uc, "_t194_buat_item_http", _post_palsu)
    monkeypatch.setattr(uc, "_gerbang_lanjut", _gerbang_palsu)
    return r


def pasang_praperiksa(monkeypatch, hasil):
    """Ganti praperiksa DB dengan hasil tetap; catat pemanggilannya."""
    dipanggil = []

    async def _pp(pool, tenant_id, nama):
        dipanggil.append(nama)
        return hasil

    monkeypatch.setattr(uc, "_t194_praperiksa", _pp)
    return dipanggil


async def panggil(sm, nilai, payload, ep_line_index=0):
    return await _pil_buat_barang(
        sm=sm,
        sid="sid-1",
        session_id="sid-1",
        ctx=CTX,
        pool=None,
        nilai=nilai,
        doc_ctx=sm.document_context,
        ep_queue=ANTREAN,
        ep_cursor=0,
        ep_action_key="create_bill",
        ep_payload=payload,
        ep_line_index=ep_line_index,
    )


# ───────────────────────── klaim 1: penjaga putaran ─────────────────────────


@pytest.mark.asyncio
async def test_penjaga_putaran_menyala_pada_tanya_kedua_sidik_sama(
    monkeypatch, rekam
):
    pasang_praperiksa(monkeypatch, {"tingkat": "T4"})
    payload = {"items": [{"product_name": "Kaos Ungu", "qty": 3}]}
    sm = SMPalsu(doc_ctx_awal(payload))

    r1 = await panggil(sm, "create_new:item", payload)
    assert r1.message_type == "CLARIFICATION"
    prog = sm.document_context["pil_progres"]["0"]
    assert prog["tanya"] == 1
    assert prog["sidik"] == _t194_sidik(0, ["tipe", "harga"])

    # jawaban yang TIDAK terbaca → kurang tak berubah → sidik sama
    r2 = await panggil(sm, "hmmm gimana ya", payload)
    assert r2.message_type == "TEXT", "penjaga putaran harus BERHENTI, bukan bertanya lagi"
    assert "berhenti" in r2.text.lower()
    # pesannya WAJIB menyebut APA yang macet
    assert "tipe" in r2.text.lower()
    assert "harga" in r2.text.lower()
    assert rekam.post == [], "tidak boleh ada item dibuat saat macet"


# ─────────────────────────── klaim 2: T1 pakai master ───────────────────────


@pytest.mark.asyncio
async def test_t1_nama_persis_hidup_memakai_id_master_nol_pembuatan(
    monkeypatch, rekam
):
    pasang_praperiksa(
        monkeypatch,
        {"tingkat": "T1", "id": "ID-MASTER", "nama": "Kaos Ungu", "base_unit": "pcs"},
    )
    payload = {"items": [{"product_name": "Kaos Ungu", "qty": 3}]}
    sm = SMPalsu(doc_ctx_awal(payload))

    resp = await panggil(sm, "create_new:item", payload)

    assert rekam.post == [], "T1 TIDAK BOLEH membuat barang"
    assert payload["items"][0]["item_id"] == "ID-MASTER"
    assert len(rekam.gerbang) == 1, "harus KEMBALI ke _gerbang_lanjut, bukan lompat kartu"
    assert resp.message_type == "DIRECT_ACTION_PREVIEW"
    assert "sudah ada di master" in resp.text
    assert sm.document_context.get("pending_item_create") is None


# ───────────────────────── klaim 3: T3 tawar pilihan ────────────────────────


@pytest.mark.asyncio
async def test_t3_dua_kandidat_menawarkan_pilihan_dan_tidak_membuat(
    monkeypatch, rekam
):
    pasang_praperiksa(
        monkeypatch,
        {
            "tingkat": "T3",
            "kandidat": [
                {"id": "ID-A", "name": "Kaos Hitam Polos"},
                {"id": "ID-B", "name": "Kaos Hitam Sablon"},
            ],
        },
    )
    payload = {"items": [{"product_name": "Kaos Hitam", "qty": 2}]}
    sm = SMPalsu(doc_ctx_awal(payload))

    resp = await panggil(sm, "create_new:item", payload)

    assert resp.message_type == "CLARIFICATION"
    opsi = resp.data["options"]
    assert len(opsi) == 3, "DUA kandidat + SATU opsi buat-baru"
    nilai = [o["value"] for o in opsi]
    assert nilai[0] == "pakai_barang:ID-A"
    assert nilai[1] == "pakai_barang:ID-B"
    assert nilai[2].startswith("buat_paksa:")
    # K8: tawaran memuat NAMA PERSIS yang akan didaftarkan
    assert 'Kaos Hitam"' in opsi[2]["label"]
    assert rekam.post == [], "T3 TIDAK BOLEH memilih otomatis / membuat"
    assert rekam.gerbang == [], "T3 belum boleh lanjut ke gerbang"
    assert "item_id" not in payload["items"][0]


@pytest.mark.asyncio
async def test_t3_tap_pakai_barang_memakai_id_tanpa_membuat(monkeypatch, rekam):
    pasang_praperiksa(monkeypatch, {"tingkat": "T4"})
    payload = {"items": [{"product_name": "Kaos Hitam", "qty": 2}]}
    sm = SMPalsu(doc_ctx_awal(payload))
    resp = await panggil(sm, "pakai_barang:ID-A", payload)
    assert rekam.post == []
    assert payload["items"][0]["item_id"] == "ID-A"
    assert len(rekam.gerbang) == 1
    assert resp.message_type == "DIRECT_ACTION_PREVIEW"


# ──────────────────── klaim 4: HANYA dua pertanyaan, tanpa satuan ───────────


@pytest.mark.asyncio
async def test_hanya_dua_pertanyaan_tipe_dan_harga_tanpa_satuan(monkeypatch, rekam):
    pasang_praperiksa(monkeypatch, {"tingkat": "T4"})
    payload = {"items": [{"product_name": "Kaos Ungu", "qty": 3}]}
    sm = SMPalsu(doc_ctx_awal(payload))
    pertanyaan = []

    r1 = await panggil(sm, "create_new:item", payload)
    assert r1.message_type == "CLARIFICATION"
    pertanyaan.append(r1.text)
    assert "barang atau jasa" in r1.text.lower()

    r2 = await panggil(sm, "barang", payload)
    assert r2.message_type == "CLARIFICATION"
    pertanyaan.append(r2.text)
    assert "harga" in r2.text.lower()

    r3 = await panggil(sm, "Rp 25.000", payload)
    assert r3.message_type == "DIRECT_ACTION_PREVIEW", "dua jawaban harus cukup"

    # TEPAT dua pertanyaan, dan tak satu pun menanyakan satuan
    assert len(pertanyaan) == 2
    for q in pertanyaan:
        low = q.lower()
        assert "satuan" not in low, f"satuan TIDAK BOLEH ditanyakan: {q!r}"
        assert "unit" not in low, f"satuan TIDAK BOLEH ditanyakan: {q!r}"
        assert "kategori" not in low and "sku" not in low and "pajak" not in low

    # K12: nilai Literal, bukan label Indonesia
    assert len(rekam.post) == 1
    assert rekam.post[0]["fields"]["item_type"] == "goods"
    assert rekam.post[0]["fields"]["purchase_price"] == 25000.0
    # satuan di-backfill diam-diam, tidak pernah ditanya
    assert rekam.post[0]["satuan"] == "pcs"
    # K9: pesan sukses memisahkan pendaftaran dari kartu
    assert "sudah terdaftar di master barang" in r3.text
    assert "Pilihan diterima." in r3.text
    assert payload["items"][0]["item_id"] == "ID-BARU"


@pytest.mark.asyncio
async def test_jasa_dipetakan_ke_service_bukan_label_indonesia(monkeypatch, rekam):
    pasang_praperiksa(monkeypatch, {"tingkat": "T4"})
    payload = {"items": [{"description": "Jahit lengan", "qty": 1}]}
    sm = SMPalsu(doc_ctx_awal(payload))
    await panggil(sm, "create_new:item", payload)
    await panggil(sm, "jasa", payload)
    await panggil(sm, "15000", payload)
    assert rekam.post[0]["fields"]["item_type"] == "service"
    assert rekam.post[0]["fields"]["item_type"] not in {"jasa", "persediaan"}


# ───────────────────── klaim 5: penulisan state APPEND-ONLY ─────────────────


@pytest.mark.asyncio
async def test_state_append_only_kunci_lama_tidak_lenyap(monkeypatch, rekam):
    pasang_praperiksa(monkeypatch, {"tingkat": "T4"})
    payload = {"items": [{"product_name": "Kaos Ungu", "qty": 3}]}
    sm = SMPalsu(doc_ctx_awal(payload))

    await panggil(sm, "create_new:item", payload)
    d = sm.document_context
    for kunci in ("resolved_payload", "entity_queue", "entity_cursor",
                  "resolved_action_key"):
        assert kunci in d, f"{kunci} LENYAP — penulisan tidak append-only"
    assert d["entity_queue"] == ANTREAN
    assert d["entity_cursor"] == 0
    assert d["pending_item_create"]["nama"] == "Kaos Ungu"


# ──────────────────── klaim 6: vendor/customer perilaku lama ────────────────


@pytest.mark.asyncio
async def test_vendor_masih_perilaku_lama():
    sm = SMPalsu(doc_ctx_awal({}))
    resp = await _pil_create_new_lama(
        sm=sm, sid="sid-1", ep_value="create_new:vendor", ep_etype="vendor"
    )
    assert resp.message_type == "TEXT"
    assert "vendor ini belum terdaftar" in resp.text
    assert "tambah vendor <nama>" in resp.text


@pytest.mark.asyncio
async def test_customer_masih_perilaku_lama():
    sm = SMPalsu(doc_ctx_awal({}))
    resp = await _pil_create_new_lama(
        sm=sm, sid="sid-1", ep_value="create_new:customer", ep_etype="customer"
    )
    assert resp.message_type == "TEXT"
    assert "pelanggan ini belum terdaftar" in resp.text
    assert "tambah pelanggan <nama>" in resp.text


@pytest.mark.asyncio
async def test_jalankan_pil_entity_meneruskan_vendor_ke_jalur_lama(monkeypatch, rekam):
    """Routing: create_new:vendor TIDAK BOLEH masuk jalur daftar-barang."""
    dipanggil = []

    async def _buat_palsu(**kw):
        dipanggil.append(kw)
        return ChatMessageResponse(message_type="TEXT", text="JALUR BARU", session_id="x")

    monkeypatch.setattr(uc, "_pil_buat_barang", _buat_palsu)
    sm = SMPalsu({})
    doc_ctx = {
        "pending_entity_selection": True,
        "resolved_action_key": "create_bill",
        "resolved_payload": {"items": [{"product_name": "Kaos"}]},
        "entity_queue": [{"slot": "vendor_id", "entity_type": "vendor",
                          "line_index": None}],
        "entity_cursor": 0,
    }
    resp = await uc._jalankan_pil_entity(
        sm=sm, sid="sid-1", session_id="sid-1", ctx=CTX,
        text="create_new:vendor", doc_ctx=doc_ctx, pool=None,
    )
    assert dipanggil == [], "vendor TIDAK BOLEH masuk _pil_buat_barang"
    assert "vendor ini belum terdaftar" in resp.text


# ───────── klaim 8: PENJAGA PUTARAN cabang TAWARAN (T2/T3) ──────────────────
#
# Cabang T2/T3 menulis `kurang: []`, jadi jawaban teks bebas TIDAK terserap
# (`_kur_lama` kosong -> `_target` None). `praperiksa_selesai` juga tak ditulis
# cabang ini, jadi praperiksa jalan LAGI dan menerbitkan tawaran yang sama.
# Tes berikut mengunci: giliran kedua dengan keadaan sama = BERHENTI.


def pasang_praperiksa_urut(monkeypatch, hasil_urut):
    """Praperiksa yang mengembalikan hasil BERBEDA tiap panggilan.

    Dipakai kontrol negatif: membuktikan penjaga membaca KEADAAN, bukan
    sekadar menghitung berapa kali cabang tawaran terbit.
    """
    dipanggil = []
    sisa = list(hasil_urut)

    async def _pp(pool, tenant_id, nama):
        dipanggil.append(nama)
        return sisa.pop(0) if len(sisa) > 1 else sisa[0]

    monkeypatch.setattr(uc, "_t194_praperiksa", _pp)
    return dipanggil


def _t3(n):
    return {
        "tingkat": "T3",
        "kandidat": [
            {"id": f"ID-{i}", "name": f"Kaos Hitam {i}"} for i in range(n)
        ],
    }


_T2 = {"tingkat": "T2", "id": "ID-MATI", "nama": "Kaos Hitam"}


@pytest.mark.asyncio
async def test_t3_terbit_dua_kali_keadaan_sama_giliran_kedua_berhenti(
    monkeypatch, rekam
):
    pasang_praperiksa(monkeypatch, _t3(2))
    payload = {"items": [{"product_name": "Kaos Hitam", "qty": 2}]}
    sm = SMPalsu(doc_ctx_awal(payload))

    r1 = await panggil(sm, "create_new:item", payload)
    assert r1.message_type == "CLARIFICATION", "giliran pertama harus menawarkan"
    prog = sm.document_context["pil_progres"]["0"]
    assert prog["tanya"] == 1
    assert prog["sidik"] == _t194_sidik_tawar(0, "t3", 2)

    # jawaban teks bebas: tak ada yang terserap (kurang == []), keadaan SAMA
    r2 = await panggil(sm, "yang mana ya", payload)
    assert r2.message_type == "TEXT", (
        "tawaran T3 KETIGA tidak boleh terbit; penjaga harus berhenti"
    )
    assert "berhenti" in r2.text.lower()
    # menyebut keadaan yang macet dengan bahasa manusia
    assert "Kaos Hitam" in r2.text
    assert "2 barang yang sudah ada" in r2.text
    # TIDAK menyalahkan user
    low = r2.text.lower()
    for tuduhan in ("anda salah", "kamu salah", "jawaban anda salah", "keliru"):
        assert tuduhan not in low, f"pesan menyalahkan user: {r2.text!r}"
    assert rekam.post == [], "tidak boleh ada item dibuat saat macet"
    assert rekam.gerbang == [], "tidak boleh lanjut ke gerbang saat macet"


@pytest.mark.asyncio
async def test_t2_terbit_dua_kali_keadaan_sama_giliran_kedua_berhenti(
    monkeypatch, rekam
):
    pasang_praperiksa(monkeypatch, _T2)
    payload = {"items": [{"product_name": "Kaos Hitam", "qty": 2}]}
    sm = SMPalsu(doc_ctx_awal(payload))

    r1 = await panggil(sm, "create_new:item", payload)
    assert r1.message_type == "CLARIFICATION", "giliran pertama harus menawarkan"
    assert "sudah dihapus" in r1.text
    prog = sm.document_context["pil_progres"]["0"]
    assert prog["tanya"] == 1
    assert prog["sidik"] == _t194_sidik_tawar(0, "t2", 1)

    r2 = await panggil(sm, "gimana ya", payload)
    assert r2.message_type == "TEXT", (
        "tawaran T2 KEDUA tidak boleh terbit; penjaga harus berhenti"
    )
    assert "berhenti" in r2.text.lower()
    assert "sudah " + "dihapus" in r2.text
    assert "Kaos Hitam" in r2.text
    low = r2.text.lower()
    for tuduhan in ("anda salah", "kamu salah", "keliru"):
        assert tuduhan not in low, f"pesan menyalahkan user: {r2.text!r}"
    assert rekam.post == [], "T2 macet TIDAK BOLEH mendaftarkan diam-diam"
    assert rekam.gerbang == []


# ─────────────────── KONTROL NEGATIF: penjaga tidak terlalu galak ───────────


@pytest.mark.asyncio
async def test_kontrol_negatif_jumlah_kandidat_berubah_penjaga_tidak_menyala(
    monkeypatch, rekam
):
    """ADA KEMAJUAN (2 -> 3 kandidat) → tawaran BOLEH terbit lagi."""
    pasang_praperiksa_urut(monkeypatch, [_t3(2), _t3(3)])
    payload = {"items": [{"product_name": "Kaos Hitam", "qty": 2}]}
    sm = SMPalsu(doc_ctx_awal(payload))

    r1 = await panggil(sm, "create_new:item", payload)
    assert r1.message_type == "CLARIFICATION"
    assert len(r1.data["options"]) == 3  # 2 kandidat + buat-baru

    r2 = await panggil(sm, "yang mana ya", payload)
    assert r2.message_type == "CLARIFICATION", (
        "keadaan BERUBAH (kandidat 2->3) — penjaga TIDAK BOLEH menyala"
    )
    assert len(r2.data["options"]) == 4  # 3 kandidat + buat-baru
    prog = sm.document_context["pil_progres"]["0"]
    assert prog["tanya"] == 1, "sidik baru → hitungan tanya kembali ke 1"
    assert prog["sidik"] == _t194_sidik_tawar(0, "t3", 3)


@pytest.mark.asyncio
async def test_kontrol_negatif_tahap_berpindah_t3_ke_t2_penjaga_tidak_menyala(
    monkeypatch, rekam
):
    """ADA KEMAJUAN (vonis t3 → t2) → tawaran BOLEH terbit lagi."""
    # SATU kandidat, supaya n_kandidat SAMA (1) di kedua giliran: yang
    # berbeda HANYA `tahap`. Ini mengisolasi unsur tahap di dalam sidik.
    pasang_praperiksa_urut(monkeypatch, [_t3(1), _T2])
    payload = {"items": [{"product_name": "Kaos Hitam", "qty": 2}]}
    sm = SMPalsu(doc_ctx_awal(payload))

    r1 = await panggil(sm, "create_new:item", payload)
    assert r1.message_type == "CLARIFICATION"
    assert "Maksud Anda" in r1.text
    assert len(r1.data["options"]) == 2  # 1 kandidat + buat-baru
    assert sm.document_context["pil_progres"]["0"]["sidik"] == _t194_sidik_tawar(
        0, "t3", 1
    )

    r2 = await panggil(sm, "yang mana ya", payload)
    assert r2.message_type == "CLARIFICATION", (
        "vonis praperiksa BERUBAH t3->t2 — penjaga TIDAK BOLEH menyala"
    )
    assert "sudah dihapus" in r2.text, "harus tawaran T2, bukan pesan berhenti"
    prog = sm.document_context["pil_progres"]["0"]
    assert prog["tanya"] == 1
    assert prog["sidik"] == _t194_sidik_tawar(0, "t2", 1)


# ───────────── state append-only TETAP di cabang tawaran T2/T3 ──────────────


@pytest.mark.asyncio
async def test_cabang_tawaran_menulis_pil_progres_tanpa_menghapus_kunci_lama(
    monkeypatch, rekam
):
    pasang_praperiksa(monkeypatch, _t3(2))
    payload = {"items": [{"product_name": "Kaos Hitam", "qty": 2}]}
    sm = SMPalsu(doc_ctx_awal(payload))

    await panggil(sm, "create_new:item", payload)
    d = sm.document_context
    for kunci in ("resolved_payload", "entity_queue", "entity_cursor",
                  "resolved_action_key"):
        assert kunci in d, f"{kunci} LENYAP — penulisan tidak append-only"
    assert d["entity_queue"] == ANTREAN
    assert d["entity_cursor"] == 0
    # pil_progres DAN pending_item_create ditulis BARENGAN (satu tulis)
    assert d["pending_item_create"]["nama"] == "Kaos Hitam"
    assert d["pil_progres"]["0"]["sidik"] == _t194_sidik_tawar(0, "t3", 2)
