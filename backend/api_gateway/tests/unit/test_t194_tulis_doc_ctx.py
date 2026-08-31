"""T194 — helper `_tulis_doc_ctx` (read-merge-write) untuk document_context.

KLAIM yang diuji di sini:
  1. update_state YANG SEBENARNYA (kelas asli, bukan tiruan) menyusun SQL
     `document_context = $N::jsonb` — satu kolom, DITIMPA UTUH. Ini fondasi
     dari seluruh tiket; kalau asersi ini merah, helper tak punya alasan ada.
  2. KONTROL POSITIF: menulis LANGSUNG lewat update_state MENGHAPUS kunci lain
     (resolved_payload / entity_queue / entity_cursor lenyap).
  3. Helper `_tulis_doc_ctx` menulis kunci baru TANPA menghapus kunci lama.
  4. Helper mengembalikan dict hasil merge.
  5. Helper tetap menulis walau get_or_create_session melempar (FK-safety
     try/except tidak boleh membuntukan).

TIDAK DIKLAIM: bahwa PostgreSQL sungguh menyimpannya. Sisi DB diuji sebagai
TEKS SQL (klaim 1), lalu semantik "timpa utuh" itu dipakai sebagai model di
penyimpan tiruan untuk klaim 2-5.
"""

import os
import sys

import pytest

sys.path.insert(0, "/app/backend/api_gateway")
os.environ.setdefault("OPENAI_API_KEY", "uji-t194-bukan-kunci-asli")

from app.services.unified_agent.session_manager import (  # noqa: E402
    SessionManager,
)
from app.routers.unified_chat import _tulis_doc_ctx  # noqa: E402


DOC_CTX_LAMA = {
    "pending_entity_selection": True,
    "resolved_action_key": "create_bill",
    "resolved_payload": {"items": [{"product_name": "Kaos", "qty": 3}]},
    "entity_queue": [{"slot": "item_id", "entity_type": "item", "line_index": 0}],
    "entity_cursor": 0,
}


# ─────────────────── klaim 1: SQL asli = timpa satu kolom ───────────────────


class _ConnPalsu:
    def __init__(self, rekam):
        self.rekam = rekam

    async def execute(self, sql, *values):
        self.rekam.append((sql, values))


class _AcquirePalsu:
    def __init__(self, rekam):
        self.rekam = rekam

    async def __aenter__(self):
        return _ConnPalsu(self.rekam)

    async def __aexit__(self, *a):
        return False


class _PoolPalsu:
    def __init__(self):
        self.rekam = []

    def acquire(self, timeout=None):
        return _AcquirePalsu(self.rekam)


@pytest.mark.asyncio
async def test_update_state_asli_menimpa_kolom_utuh():
    pool = _PoolPalsu()
    sm = SessionManager(db_pool=pool, tenant_id="t1", user_id="u1")
    await sm.update_state("sid-1", document_context={"a": 1})
    assert len(pool.rekam) == 1
    sql, values = pool.rekam[0]
    # satu kolom, ditimpa utuh — BUKAN jsonb_set / || merge
    assert "document_context = $3::jsonb" in sql
    assert "jsonb_set" not in sql and "||" not in sql
    import json

    assert json.loads(values[2]) == {"a": 1}


# ───────── penyimpan tiruan yang MENIRU semantik "timpa utuh" di atas ────────


class SMPalsu:
    """Menyimpan document_context dengan semantik kolom: penulisan = TIMPA."""

    def __init__(self, awal):
        self.document_context = dict(awal)
        self.gagal_get_or_create = False

    async def get_or_create_session(self, sid):
        if self.gagal_get_or_create:
            raise RuntimeError("FK belum ada")
        return sid

    async def update_state(self, sid, **updates):
        if "document_context" in updates:
            self.document_context = updates["document_context"]


# ───────────────────── klaim 2: KONTROL POSITIF (merah) ─────────────────────


@pytest.mark.asyncio
async def test_kontrol_positif_tulis_langsung_menghapus_kunci_lain():
    """Bukti bahwa tesnya BISA GAGAL: tanpa helper, kunci lain lenyap."""
    sm = SMPalsu(DOC_CTX_LAMA)
    await sm.update_state("sid-1", document_context={"pending_entity_selection": False})
    assert sm.document_context == {"pending_entity_selection": False}
    for hilang in ("resolved_payload", "entity_queue", "entity_cursor",
                   "resolved_action_key"):
        assert hilang not in sm.document_context, f"{hilang} seharusnya LENYAP"


# ───────────────────────── klaim 3-5: helper ────────────────────────────────


@pytest.mark.asyncio
async def test_helper_tidak_menghapus_kunci_lama():
    sm = SMPalsu(DOC_CTX_LAMA)
    await _tulis_doc_ctx(sm, "sid-1", sm.document_context, entity_cursor=1)
    d = sm.document_context
    assert d["entity_cursor"] == 1
    assert d["resolved_action_key"] == "create_bill"
    assert d["resolved_payload"] == {"items": [{"product_name": "Kaos", "qty": 3}]}
    assert d["entity_queue"] == DOC_CTX_LAMA["entity_queue"]
    assert d["pending_entity_selection"] is True


@pytest.mark.asyncio
async def test_helper_mengembalikan_hasil_merge():
    sm = SMPalsu(DOC_CTX_LAMA)
    hasil = await _tulis_doc_ctx(
        sm, "sid-1", sm.document_context, pending_entity_selection=False, baru="x"
    )
    assert hasil["pending_entity_selection"] is False
    assert hasil["baru"] == "x"
    assert hasil["entity_queue"] == DOC_CTX_LAMA["entity_queue"]
    assert hasil == sm.document_context


@pytest.mark.asyncio
async def test_helper_tidak_memutasi_dict_masukan():
    asal = dict(DOC_CTX_LAMA)
    sm = SMPalsu(DOC_CTX_LAMA)
    await _tulis_doc_ctx(sm, "sid-1", asal, entity_cursor=9)
    assert asal["entity_cursor"] == 0


@pytest.mark.asyncio
async def test_helper_tetap_menulis_walau_get_or_create_gagal():
    sm = SMPalsu(DOC_CTX_LAMA)
    sm.gagal_get_or_create = True
    await _tulis_doc_ctx(sm, "sid-1", sm.document_context, entity_cursor=2)
    assert sm.document_context["entity_cursor"] == 2
    assert sm.document_context["resolved_action_key"] == "create_bill"


@pytest.mark.asyncio
async def test_helper_aman_dengan_doc_ctx_none():
    sm = SMPalsu({})
    hasil = await _tulis_doc_ctx(sm, "sid-1", None, entity_cursor=0)
    assert hasil == {"entity_cursor": 0}
