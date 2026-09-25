"""GET /api/customers/{id}/rename-impact — jumlah dokumen ber-snapshot nama.

Latar: kasus pemilik SO-2609-0013 (pelanggan di-rename 2 hari sesudah dokumen
terbit -> dokumen mencetak nama lama). customer_name = snapshot; PATCH
pelanggan tak menyebarkan nama (dan tak boleh mengubah dokumen terbit). FE
butuh angka untuk memperingatkan sebelum menyimpan nama baru.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import customers as cu

TENANT = "kaos-biru-konveksi"
CID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class _Ctx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


class Conn:
    def __init__(self, ada=True, angka=None):
        self.ada = ada
        self.angka = angka or {}
        self.calls = []

    async def execute(self, sql, *a):
        self.calls.append(("execute", sql, a))

    async def fetchrow(self, sql, *a):
        self.calls.append(("fetchrow", sql, a))
        if "FROM customers" in sql:
            return {"id": CID, "nama": "Nutrindo"} if self.ada else None
        tabel = sql.rsplit(" FROM ", 1)[1].split()[0]
        t, b = self.angka.get(tabel, (0, 0))
        return {"total": t, "nama_berbeda": b}

    def transaction(self):
        return _Ctx()


def pasang(monkeypatch, conn):
    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq(conn))

    monkeypatch.setattr(cu, "get_pool", _pool)


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": "22222222-2222-2222-2222-222222222222"}))


@pytest.mark.asyncio
async def test_jumlah_per_jenis_dan_total(monkeypatch):
    conn = Conn(angka={"sales_invoices": (3, 1), "sales_orders": (2, 2), "customer_deposits": (1, 0)})
    pasang(monkeypatch, conn)
    r = (await cu.get_customer_rename_impact(req(), CID))["data"]
    assert r["nama"] == "Nutrindo"
    assert r["total_dokumen"] == 6 and r["nama_berbeda"] == 3
    assert r["per_jenis"]["faktur_penjualan"] == {"total": 3, "nama_berbeda": 1}
    assert r["per_jenis"]["pesanan_penjualan"] == {"total": 2, "nama_berbeda": 2}
    assert r["per_jenis"]["uang_muka"] == {"total": 1, "nama_berbeda": 0}
    assert set(r["per_jenis"]) == {k for _, k in cu.RENAME_TABEL}
    assert "6 dokumen" in r["pesan"]


@pytest.mark.asyncio
async def test_kueri_dipaku_tenant_pelanggan_dan_nama_sekarang(monkeypatch):
    conn = Conn()
    pasang(monkeypatch, conn)
    await cu.get_customer_rename_impact(req(), CID)
    hitung = [c for c in conn.calls if c[0] == "fetchrow" and "count(*)" in c[1]]
    assert len(hitung) == len(cu.RENAME_TABEL)
    for _, sql, a in hitung:
        assert "tenant_id = $1 AND customer_id = $2" in sql
        assert "customer_name IS DISTINCT FROM $3" in sql
        assert a == (TENANT, CID, "Nutrindo")


@pytest.mark.asyncio
async def test_tanpa_dokumen_pesan_none(monkeypatch):
    pasang(monkeypatch, Conn())
    r = (await cu.get_customer_rename_impact(req(), CID))["data"]
    assert r["total_dokumen"] == 0 and r["pesan"] is None


@pytest.mark.asyncio
async def test_pelanggan_tenant_lain_atau_tak_ada_404(monkeypatch):
    conn = Conn(ada=False)
    pasang(monkeypatch, conn)
    with pytest.raises(HTTPException) as e:
        await cu.get_customer_rename_impact(req(), CID)
    assert e.value.status_code == 404
    assert not [c for c in conn.calls if "count(*)" in c[1]]
    q = [c for c in conn.calls if "FROM customers" in c[1]][0]
    assert q[2] == (CID, TENANT)


def test_izin_rute_baca_pelanggan():
    from app.middleware.permission_middleware import PermissionMiddleware

    pm = PermissionMiddleware(app=None)
    assert pm._find_permission(f"/api/customers/{CID}/rename-impact", "GET") == ("customer", "R")
