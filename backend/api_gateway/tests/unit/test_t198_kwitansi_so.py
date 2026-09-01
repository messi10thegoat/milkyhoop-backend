"""T198 — Kwitansi di konteks Sales Order.

Tiap tes di berkas ini sudah dibuktikan BISA MERAH lewat sabotase pada KODE
PRODUKSI (bukan pada tes), lalu dipulihkan. Lihat laporan tiket.
"""

import uuid
from datetime import date

import pytest

from app.routers import customer_deposits as cd


# ─────────────────────────── U1: purpose_label ───────────────────────────
def test_u1_label_uang_muka_pesanan_saat_terikat_so():
    so_id = uuid.uuid4()
    assert cd._deposit_purpose_label(so_id) == "Uang Muka Pesanan"


def test_u2_label_lama_dipertahankan_saat_tanpa_so():
    """Kontrol negatif: tanpa SO label WAJIB tetap nilai lama."""
    assert cd._deposit_purpose_label(None) == "Uang Muka"


# ─────────────────────────── U3/U4: filter daftar ───────────────────────────
class _FakeConn:
    """Merekam query + params yang benar-benar dikirim ke Postgres."""

    def __init__(self):
        self.queries = []

    async def execute(self, q, *a):
        return None

    async def fetchval(self, q, *a):
        self.queries.append((q, a))
        return 0

    async def fetch(self, q, *a):
        self.queries.append((q, a))
        return []


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


async def _panggil_daftar(monkeypatch, **kwargs):
    conn = _FakeConn()
    monkeypatch.setattr(cd, "get_pool", lambda: _selesai(_FakePool(conn)))
    monkeypatch.setattr(
        cd, "get_user_context", lambda request: {"tenant_id": "kaos-biru-konveksi"}
    )
    params = dict(
        status="all",
        customer_id=None,
        sales_order_id=None,
        search=None,
        date_from=None,
        date_to=None,
        skip=0,
        limit=20,
        sort_by="created_at",
        sort_order="desc",
    )
    params.update(kwargs)
    await cd.list_customer_deposits(request=None, **params)
    return conn


async def _selesai(v):
    return v


@pytest.mark.asyncio
async def test_u3_filter_sales_order_id_menyaring(monkeypatch):
    so_id = str(uuid.uuid4())
    conn = await _panggil_daftar(monkeypatch, sales_order_id=so_id)
    q, args = conn.queries[-1]
    assert "sales_order_id = $" in q, (
        "kondisi sales_order_id tidak sampai ke SQL — filter tidak menyaring apa pun"
    )
    assert uuid.UUID(so_id) in args, (
        "nilai sales_order_id tidak dikirim sebagai parameter"
    )


@pytest.mark.asyncio
async def test_u4_tanpa_filter_perilaku_lama_persis(monkeypatch):
    """Kontrol negatif: tanpa param, SQL tidak boleh menyebut sales_order_id."""
    conn = await _panggil_daftar(monkeypatch)
    q, args = conn.queries[-1]
    assert "sales_order_id" not in q, (
        "filter bocor ke query padahal param tidak diberikan — perilaku lama berubah"
    )
    assert args == ("kaos-biru-konveksi", 20, 0)
