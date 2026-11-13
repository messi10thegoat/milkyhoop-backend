"""Daftar harga (tiket ANDROID U3, 4 Nov 2026): harga tak memotong sen, GET /{id} membawa nama barang + is_default_unit (JOIN),
PATCH /{id}/items/{line_id} atomik lewat penulis YANG SAMA (tulis_baris_harga: validasi barang/satuan/unik)."""
import asyncio
import inspect
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routers import price_lists as PL
from app.schemas.price_lists import PriceListItemCreate, PriceListItemResponse

D = Decimal
PLID, LINE, ITEM = uuid4(), uuid4(), uuid4()


def test_harga_respons_sen_terjaga_bulat_tetap_int():
    r = PriceListItemResponse(id="a", item_id="b", price=D("15250.50"))
    assert r.price == 15250.5
    assert type(PriceListItemResponse(id="a", item_id="b", price=D("15000.00")).price) is int


class Conn:
    def __init__(self, baris=True):
        self.baris, self.sql = baris, []

    def transaction(self):
        class T:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *e):
                return False
        return T()

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM price_lists" in sql or "FROM products" in sql:
            return 1
        if sql.lstrip().startswith("UPDATE price_list_items"):
            return a[0]
        return None

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM price_list_items" in sql and self.baris:
            return {"id": LINE, "item_id": ITEM, "item_code": "MG-2L", "unit": None, "price": D("30000"), "min_quantity": D("1"),
                    "is_default_unit": False, "discount_percent": None, "start_date": None, "end_date": None, "dasar": "Pcs"}
        return None


def _patch(conn, monkeypatch, **badan):
    async def pool():
        class P:
            def acquire(s):
                class A:
                    async def __aenter__(x):
                        return conn

                    async def __aexit__(x, *e):
                        return False
                return A()
        return P()
    monkeypatch.setattr(PL, "get_pool", pool)
    monkeypatch.setattr(PL, "get_user_context", lambda r: {"tenant_id": "t", "user_id": None})
    return asyncio.run(PL.update_price_list_item(None, PLID, LINE, PL.PriceListItemUpdate(**badan)))


def test_patch_harga_lewat_penulis_sama_update_di_tempat(monkeypatch):
    c = Conn()
    r = _patch(c, monkeypatch, price=D("29500.50"))
    assert r["data"]["id"] == str(LINE)
    upd = [a for s, a in c.sql if s.lstrip().startswith("UPDATE price_list_items")]
    assert len(upd) == 1 and upd[0][0] == LINE and upd[0][1] == PLID and upd[0][3] == D("29500.50")
    assert not [s for s, _ in c.sql if "INSERT INTO price_list_items" in s or "DELETE FROM price_list_items" in s]
    assert any("FROM products WHERE id = $1 AND tenant_id = $2" in s for s, _ in c.sql)  # validasi barang = penulis sama
    assert any("FOR UPDATE" in s for s, _ in c.sql)


def test_patch_baris_tak_ada_404(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _patch(Conn(baris=False), monkeypatch, price=D("1"))
    assert e.value.status_code == 404


def test_satu_penulis_dan_get_detail_join():
    assert "await tulis_baris_harga(" in inspect.getsource(PL.update_price_list_item)
    src = inspect.getsource(PL.get_price_list)
    assert "LEFT JOIN products p ON p.id = pli.item_id AND p.tenant_id = $2" in src
    assert '"item_name": i["item_name"]' in src and '"is_default_unit": bool(i["is_default_unit"])' in src


def test_skema_buat_tetap_menolak_lebih_dari_dua_desimal():
    with pytest.raises(Exception):
        PriceListItemCreate(item_id=str(ITEM), price=D("1.234"))


# ── pelanggan baru membawa daftar harga (celah ke-4 ANDROID: dulu dibuang diam-diam) ─────────────────────────────

def test_buat_pelanggan_menyimpan_price_list_id_dan_memeriksanya():
    from app.routers import customers as CU
    from app.schemas.customers import CreateCustomerRequest
    pl = uuid4()
    assert CreateCustomerRequest(name="Warung Bu Siti", price_list_id=str(pl)).price_list_id == pl
    src = inspect.getsource(CU.tulis_pelanggan_baru)
    assert "await pastikan_daftar_harga(conn, ctx[\"tenant_id\"], body.price_list_id)" in src
    assert "nomor_dokumen, price_list_id" in src and "body.price_list_id,  # $34" in src
    # K-B (9 Nov 2026): PATCH lewat penentu bersama tulis + pratinjau ubah
    assert "_siapkan_ubah_pelanggan(" in inspect.getsource(CU.update_customer)
    assert "await pastikan_daftar_harga(" in inspect.getsource(CU._siapkan_ubah_pelanggan)  # satu pemeriksa buat + ubah


class _ConnDaftar:
    def __init__(self, milik):
        self.milik = milik

    async def fetchval(self, sql, pl, tid):
        assert "FROM price_lists WHERE id = $1 AND tenant_id = $2 AND is_active" in sql
        return 1 if (pl, tid) in self.milik else None


def test_daftar_harga_usaha_lain_ditolak_null_bawaan_lolos():
    from app.routers import customers as CU
    pl = uuid4()
    asyncio.run(CU.pastikan_daftar_harga(_ConnDaftar({(pl, "t")}), "t", pl))
    asyncio.run(CU.pastikan_daftar_harga(_ConnDaftar(set()), "t", None))
    with pytest.raises(HTTPException) as e:
        asyncio.run(CU.pastikan_daftar_harga(_ConnDaftar({(pl, "lain")}), "t", pl))
    assert e.value.status_code == 400 and "tidak ditemukan di usaha ini" in e.value.detail


# ── pratinjau tambah / ubah baris: penentu SAMA dengan tulis, READ ONLY, blok berkalimat sama ────────────────────

class ConnPv(Conn):
    def __init__(self, ganda=False, **kw):
        super().__init__(**kw)
        self.ganda, self.ro = ganda, []

    def transaction(self, readonly=False):
        self.ro.append(readonly)
        c = self

        class T:
            async def start(s):
                return None

            async def rollback(s):
                return None

            async def __aenter__(s):
                return s

            async def __aexit__(s, *e):
                return False
        return T()

    async def fetchval(self, sql, *a):
        if "FROM price_list_items WHERE price_list_id = $1 AND item_id = $2" in sql:
            self.sql.append((sql, a))
            return 1 if self.ganda else None
        return await super().fetchval(sql, *a)


def _pv(conn, monkeypatch, badan, line_id=None):
    async def pool():
        class P:
            def acquire(s):
                class A:
                    async def __aenter__(x):
                        return conn

                    async def __aexit__(x, *e):
                        return False
                return A()
        return P()
    monkeypatch.setattr(PL, "get_pool", pool)
    monkeypatch.setattr(PL, "get_user_context", lambda r: {"tenant_id": "t", "user_id": None})
    if line_id:
        return asyncio.run(PL.preview_update_price_list_item(None, PLID, line_id, badan))["data"]
    return asyncio.run(PL.preview_add_price_list_item(None, PLID, badan))["data"]


def test_pratinjau_tambah_ok_dan_ganda_berkalimat_sama(monkeypatch):
    ok = _pv(ConnPv(), monkeypatch, {"item_id": str(ITEM), "price": "15000"})
    assert ok["ok"] and ok["blocks"] == []
    c = ConnPv(ganda=True)
    g = _pv(c, monkeypatch, {"item_id": str(ITEM), "price": "15000"})
    assert not g["ok"] and [b["code"] for b in g["blocks"]] == ["HARGA_GANDA"]
    assert g["blocks"][0]["message"] == "Harga untuk barang, satuan, dan jumlah minimum itu sudah ada di daftar ini."
    assert c.ro == [True] and not [s for s, _ in c.sql if s.lstrip().startswith(("UPDATE", "INSERT", "DELETE"))]


def test_pratinjau_ubah_mengecualikan_baris_sendiri_dan_tanpa_kunci(monkeypatch):
    c = ConnPv()
    r = _pv(c, monkeypatch, {"price": "29500"}, line_id=LINE)
    assert r["ok"]
    dup = [a for s, a in c.sql if "AND ($5::uuid IS NULL OR id <> $5)" in s]
    assert dup and dup[0][-1] == LINE
    assert not any("FOR UPDATE" in s for s, _ in c.sql)


def test_pratinjau_badan_tak_sah_jadi_blok(monkeypatch):
    r = _pv(ConnPv(), monkeypatch, {"item_id": str(ITEM), "price": "1.234"})
    assert not r["ok"] and r["blocks"]
