"""Gudang K1 (pill Nonaktif), K5 (urutan stabil), K7 (detail == penentu daftar) + P5 (stok per gudang di detail Barang, barang minus dari ledger)."""
import asyncio
import inspect
import uuid
from datetime import datetime
from decimal import Decimal as D

import pytest

from app.routers import items as IT
from app.routers import warehouses as WH
from app.services import gudang_daftar as GD

T = "kaos-biru-konveksi"
G1, G2, P1 = (uuid.uuid4() for _ in range(3))


def jalan(aw):
    return asyncio.run(aw)


def baris(gid, nama, aktif=True, utama=False, minus=False, nilai=0):
    return {"id": gid, "code": nama[:3].upper(), "name": nama, "address": None, "city": None, "province": None, "postal_code": None, "country": None,
            "phone": None, "email": None, "manager_name": None, "is_default": utama, "is_active": aktif, "is_branch": False, "branch_code": None,
            "created_at": datetime(2026, 11, 1), "updated_at": None, "tenant_id": T, "stok_minus": minus, "nilai_stok": D(str(nilai))}


class Kon:
    def __init__(self, rows=None, minus=None, ledger=None):
        self.rows, self.minus, self.ledger, self.sql = rows or [], minus or [], ledger or [], []

    async def execute(self, sql, *a):
        return "OK"

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        return len(self.rows) if "COUNT(*) FROM warehouses" in sql else 0

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "HAVING SUM(il.quantity_in) - SUM(il.quantity_out) < 0" in sql and "il.product_id, p.nama_produk" in sql:
            return self.minus
        if "COALESCE(w.name, '(tanpa gudang)')" in sql:
            return self.ledger
        return self.rows

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return self.rows[0] if self.rows else None


def pasang(monkeypatch, k):
    class Cm:
        async def __aenter__(self):
            return k

        async def __aexit__(self, *e):
            return False

    class Pool:
        def acquire(self):
            return Cm()

    async def pool():
        return Pool()
    monkeypatch.setattr(WH, "get_pool", pool)


class Req:
    class state:
        user = {"tenant_id": T, "user_id": str(uuid.uuid4())}


def daftar(**kw):
    p = dict(skip=0, limit=50, search=None, is_active=None, is_branch=None, aksi=None, with_totals=False)
    p.update(kw)
    return jalan(WH.list_warehouses(Req(), **p))


def test_k1_pill_nonaktif_predikat_dan_urutan_aksi_semua():
    assert GD.AKSI == ("stok_minus", "nonaktif")
    assert GD.sql_aksi("nonaktif", "w") == "NOT w.is_active"
    assert GD.aksi_semua(True, True) == ["stok_minus", "nonaktif"] and GD.aksi_semua(False, True) == ["nonaktif"]
    assert GD.aksi_semua(True) == ["stok_minus"] and GD.aksi_semua(False, False) == []
    r = baris(G1, "Lama", aktif=False, minus=True)
    assert GD.turunan(r) == {"aksi": "stok_minus", "aksi_semua": ["stok_minus", "nonaktif"], "ada_stok_minus": True, "nilai_stok": 0}
    assert GD.turunan(baris(G1, "A"))["aksi"] is None and GD.turunan(baris(G1, "A"))["aksi_semua"] == []


def test_k1_daftar_aksi_nonaktif_memakai_predikat_sama_dan_summary_nonaktif_count(monkeypatch):
    k = Kon([baris(G1, "Lama", aktif=False)])
    pasang(monkeypatch, k)
    out = daftar(aksi="nonaktif")
    assert any("NOT warehouses.is_active" in s for s in k.sql)
    assert out.data[0].aksi == "nonaktif" and out.data[0].aksi_semua == ["nonaktif"]
    assert "NOT is_active" in inspect.getsource(WH.warehouses_summary) and "nonaktif_count" in inspect.getsource(WH.warehouses_summary)
    assert "(stok_minus|nonaktif)" in inspect.getsource(WH.list_warehouses)


def test_k5_urutan_stabil_nama_lalu_id(monkeypatch):
    k = Kon([baris(G1, "Gudang A", utama=True)])
    pasang(monkeypatch, k)
    daftar()
    assert any("ORDER BY is_default DESC, name ASC, id ASC" in s for s in k.sql)


def test_k7_detail_memakai_penentu_yang_sama_dengan_daftar(monkeypatch):
    r = baris(G1, "Utama", utama=True, minus=True, nilai=125000)
    k = Kon([r], minus=[{"product_id": P1, "nama": "Kaos", "satuan": "pcs", "saldo": D("-3.5")}])
    pasang(monkeypatch, k)
    d = jalan(WH.get_warehouse(Req(), G1)).data
    assert any(GD.kolom_turunan() in s for s in k.sql)                                   # SELECT *, kolom turunan YANG SAMA dgn daftar
    ls = daftar().data[0]
    assert (d.nilai_stok, d.aksi, d.aksi_semua, d.ada_stok_minus) == (125000, "stok_minus", ["stok_minus"], True)
    assert (ls.nilai_stok, ls.aksi, ls.aksi_semua, ls.ada_stok_minus) == (d.nilai_stok, d.aksi, d.aksi_semua, d.ada_stok_minus)
    assert d.barang_minus == [{"item_id": str(P1), "nama": "Kaos", "satuan": "pcs", "saldo": -3.5}]
    assert d.aksi_tersedia is not None


def test_k7_detail_tanpa_minus_tak_membaca_daftar_barang_minus(monkeypatch):
    k = Kon([baris(G1, "Ok", minus=False, nilai=10)])
    pasang(monkeypatch, k)
    d = jalan(WH.get_warehouse(Req(), G1)).data
    assert d.ada_stok_minus is False and d.barang_minus == [] and d.aksi_semua == [] and d.nilai_stok == 10
    assert not any("il.product_id, p.nama_produk" in s for s in k.sql)


def test_barang_minus_predikat_tunggal_bertenant_dan_batas():
    assert GD.HAVING_MINUS.endswith("< 0") and "<=" not in GD.HAVING_MINUS and GD.HAVING_MINUS in GD.sql_stok_minus("w")
    k = Kon(minus=[{"product_id": P1, "nama": "Kaos", "satuan": "pcs", "saldo": D("-1")}])
    jalan(GD.barang_minus(k, T, G1, 5))
    s = k.sql[0]
    assert f"HAVING {GD.HAVING_MINUS}" in s and "il.tenant_id = $1" in s and "il.warehouse_id = $2" in s and "LIMIT $3" in s and "ledger" not in s.lower().replace("inventory_ledger", "")
    assert "warehouse_stock" not in s                                                      # bukan cache yang diklamp 0


def test_p5_stok_per_gudang_dari_ledger_dengan_minus_ditandai():
    k = Kon(ledger=[{"warehouse_id": G1, "nama": "Utama", "kode": "UTA", "aktif": True, "saldo": D("5")},
                    {"warehouse_id": G2, "nama": "Toko", "kode": "TOK", "aktif": True, "saldo": D("-2.5")},
                    {"warehouse_id": None, "nama": "(tanpa gudang)", "kode": None, "aktif": True, "saldo": D("1")}])
    out = jalan(GD.stok_per_gudang(k, T, P1))
    assert [(o["nama"], o["saldo"], o["minus"]) for o in out] == [("Utama", 5.0, False), ("Toko", -2.5, True), ("(tanpa gudang)", 1.0, False)]
    assert out[2]["warehouse_id"] is None and out[0]["warehouse_id"] == str(G1)
    s = k.sql[0]
    assert "il.tenant_id = $1" in s and "il.product_id = $2" in s and "LEFT JOIN warehouses w" in s and "w.tenant_id = il.tenant_id" in s and "<> 0" in s
    assert "warehouse_stock" not in s


def test_p5_detail_barang_memuat_stok_per_gudang():
    src = inspect.getsource(IT.get_item)
    assert "_GD.stok_per_gudang(conn, tenant_id, item_id)" in src and '"stok_per_gudang": _spg,' in src and '"ada_stok_minus_gudang": any(g["minus"] for g in _spg)' in src
