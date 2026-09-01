"""T202 — jalur pengiriman lewat Sales Order dinonaktifkan (keputusan K2, butir 3.4.5).

Jalur tunggal penyerahan = `invoice_fulfillments`. POST /sales-orders/{id}/ship
menolak dengan 409 dan menunjuk jalur pengganti; GET /shipments TETAP hidup.

Tiap tes di berkas ini sudah dibuktikan BISA MERAH lewat sabotase pada KODE
PRODUKSI (bukan pada tes), lalu dipulihkan. Lihat laporan tiket.
"""

import inspect
import re

import pytest
from fastapi import HTTPException

from app.routers import sales_orders as so_router


ORDER_ID = "33333333-3333-3333-3333-333333333333"


def _cari_rute(path: str, method: str):
    for r in so_router.router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    return None


def test_fungsi_ship_masih_ada_dan_terdaftar_sebagai_rute():
    """Endpoint TIDAK dihapus — pemanggil lama harus dapat pesan, bukan 404."""
    assert hasattr(so_router, "create_shipment")
    assert callable(so_router.create_shipment)
    rute = _cari_rute("/{order_id}/ship", "POST")
    assert rute is not None, "rute POST /{order_id}/ship hilang"
    params = inspect.signature(so_router.create_shipment).parameters
    assert set(params) == {"request", "order_id", "body"}


@pytest.mark.asyncio
async def test_ship_ditolak_dengan_409():
    with pytest.raises(HTTPException) as exc:
        await so_router.create_shipment(request=object(), order_id=ORDER_ID, body=object())
    assert exc.value.status_code == 409, f"kode salah: {exc.value.status_code}"


@pytest.mark.asyncio
async def test_pesan_penolakan_menunjuk_jalur_pengganti():
    with pytest.raises(HTTPException) as exc:
        await so_router.create_shipment(request=object(), order_id=ORDER_ID, body=object())
    pesan = exc.value.detail
    assert "Faktur Penjualan" in pesan, pesan
    assert "fulfill" in pesan, pesan
    assert "dinonaktifkan" in pesan.lower(), pesan


@pytest.mark.asyncio
async def test_penolakan_tidak_menyentuh_basis_data():
    """Menolak /ship tidak boleh menulis apa pun — tak ada akses pool sama sekali."""
    dipanggil = []

    async def _jebakan(*a, **k):
        dipanggil.append("get_pool")
        raise AssertionError("get_pool dipanggil pada jalur yang seharusnya menolak")

    asli = so_router.get_pool
    so_router.get_pool = _jebakan
    try:
        with pytest.raises(HTTPException) as exc:
            await so_router.create_shipment(request=object(), order_id=ORDER_ID, body=object())
        assert exc.value.status_code == 409
    finally:
        so_router.get_pool = asli
    assert dipanggil == []

    sumber = inspect.getsource(so_router.create_shipment)
    assert "INSERT INTO sales_order_shipments" not in sumber
    assert "generate_shipment_number" not in sumber


def test_get_shipments_tidak_ikut_ditolak():
    """Baca riwayat harus TETAP BERFUNGSI (0 baris hari ini)."""
    rute = _cari_rute("/{order_id}/shipments", "GET")
    assert rute is not None, "rute GET /{order_id}/shipments hilang"
    sumber = inspect.getsource(so_router.get_order_shipments)
    assert "SELECT * FROM sales_order_shipments" in sumber
    assert "409" not in sumber, "GET /shipments ikut menolak — tidak boleh"


def test_komentar_menyebut_keputusan_k2():
    """Supaya tidak 'diperbaiki' balik jadi jalur tulis."""
    doc = so_router.create_shipment.__doc__ or ""
    assert "K2" in doc, doc
    assert "invoice_fulfillments" in doc, doc


def test_tabel_tidak_di_drop():
    """K2 melarang DROP TABLE sales_order_shipments."""
    berkas = inspect.getsourcefile(so_router)
    isi = open(berkas, encoding="utf-8").read()
    assert not re.search(r"DROP\s+TABLE\s+.*sales_order_shipment", isi, re.I)
