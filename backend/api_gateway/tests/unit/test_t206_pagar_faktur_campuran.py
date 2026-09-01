"""T206 — pagar penolakan FAKTUR CAMPURAN (barang berstok + jasa).

Faktur campuran masuk cabang persediaan; cabang jasa tidak pernah jalan →
pendapatan baris jasa tidak pernah diakui → sisa mengendap permanen di
2-10750. Pagar menolak saat POSTING, sebelum jurnal apa pun dibuat.
"""

import pytest
from fastapi import HTTPException

from app.routers.sales_invoices import (
    _t206_classify_lines,
    _t206_reject_mixed_invoice,
)

TENANT = "kaos-biru-konveksi"
INV_ID = "11111111-1111-1111-1111-111111111111"


class FakeConn:
    """Konektor palsu: merekam SQL + params, mengembalikan baris tetap."""

    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self._rows


def goods(desc, val=100):
    return {
        "description": desc,
        "item_code": None,
        "line_value": val,
        "is_inventory": True,
    }


def service(desc, val=100):
    return {
        "description": desc,
        "item_code": None,
        "line_value": val,
        "is_inventory": False,
    }


# --------------------------------------------------------------- classifier
def test_classify_memisahkan_barang_dan_jasa():
    g, s = _t206_classify_lines([goods("Kaos Biru"), service("Jasa Sablon")])
    assert g == ["Kaos Biru"]
    assert s == ["Jasa Sablon"]


def test_classify_baris_nonpersediaan_bernilai_nol_diabaikan():
    g, s = _t206_classify_lines([goods("Kaos Biru"), service("Bonus", val=0)])
    assert g == ["Kaos Biru"]
    assert s == []


def test_classify_label_jatuh_ke_item_code_lalu_placeholder():
    rows = [
        {"description": None, "item_code": "SVC-01", "line_value": 5,
         "is_inventory": False},
        {"description": None, "item_code": None, "line_value": 5,
         "is_inventory": False},
    ]
    g, s = _t206_classify_lines(rows)
    assert g == []
    assert s == ["SVC-01", "(tanpa nama)"]


# --------------------------------------------------------------- pagar utama
@pytest.mark.asyncio
async def test_faktur_campuran_ditolak_dan_pesan_menyebut_kedua_jenis():
    conn = FakeConn([goods("Kaos Biru"), service("Jasa Sablon")])
    with pytest.raises(HTTPException) as ei:
        await _t206_reject_mixed_invoice(conn, TENANT, INV_ID, "INV-2609-0001")
    assert ei.value.status_code == 400
    d = ei.value.detail
    # menyebut baris barang DAN baris jasa, keduanya per nama
    assert "Kaos Biru" in d
    assert "Jasa Sablon" in d
    # menjelaskan APA yang salah + akibat neracanya
    assert "2-10750" in d
    # menawarkan jalan keluar konkret
    assert "pisahkan" in d.lower()
    assert "dua" in d.lower()
    assert "INV-2609-0001" in d


@pytest.mark.asyncio
async def test_faktur_hanya_barang_lolos():
    conn = FakeConn([goods("Kaos Biru"), goods("Kaos Merah")])
    assert await _t206_reject_mixed_invoice(
        conn, TENANT, INV_ID, "INV-2609-0002"
    ) is None


@pytest.mark.asyncio
async def test_faktur_hanya_jasa_lolos():
    conn = FakeConn([service("Jasa Sablon"), service("Jasa Desain")])
    assert await _t206_reject_mixed_invoice(
        conn, TENANT, INV_ID, "INV-2609-0003"
    ) is None


@pytest.mark.asyncio
async def test_faktur_kosong_tidak_crash():
    conn = FakeConn([])
    assert await _t206_reject_mixed_invoice(
        conn, TENANT, INV_ID, "INV-2609-0004"
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [goods("Kaos Biru"), service("Jasa Sablon")])
async def test_faktur_satu_baris_tidak_crash(row):
    conn = FakeConn([row])
    assert await _t206_reject_mixed_invoice(
        conn, TENANT, INV_ID, "INV-2609-0005"
    ) is None


@pytest.mark.asyncio
async def test_barang_dengan_track_inventory_false_dihitung_jasa():
    """Produk ada tapi track_inventory=false → SQL memberi is_inventory False."""
    conn = FakeConn([goods("Kaos Biru"), service("Ongkos Kirim")])
    with pytest.raises(HTTPException) as ei:
        await _t206_reject_mixed_invoice(conn, TENANT, INV_ID, "INV-2609-0006")
    assert "Ongkos Kirim" in ei.value.detail


# --------------------------------------------------------------- tenant scope
@pytest.mark.asyncio
async def test_query_menyaring_tenant_id():
    conn = FakeConn([])
    await _t206_reject_mixed_invoice(conn, TENANT, INV_ID, "INV-2609-0007")
    assert len(conn.calls) == 1
    sql, args = conn.calls[0]
    assert args[0] == TENANT
    assert args[1] == INV_ID
    # gateway BYPASSRLS: tenant HARUS disaring eksplisit, di kedua sisi join
    assert "si.tenant_id = $1" in sql
    assert "p.tenant_id = $1" in sql
    assert sql.count("$1") == 2
