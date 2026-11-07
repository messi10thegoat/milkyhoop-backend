"""Barang & Jasa standar SO (kontrak + putusan MASTER 7 Nov 2026): SATU penentu kunci aksi (services/barang_daftar) untuk daftar
?aksi=/?stock_status=, /items/summary {k}_count/_value, /items/stats, baris (aksi_semua, position_text, bisa_dijual) dan detail
(aksi_tersedia). Barang tak dilacak (non_inventory = mayoritas grapgrap) TAK PERNAH di pill stok. bisa_dijual per HALAMAN = satu
stok_baris.hitung (kueri tetap, bukan N+1). Paritas SQL nyata summary == daftar per kunci diuji di salinan:
scripts/ops/uji-salinan-barang-so.*"""
import ast
import asyncio
import inspect
import pathlib
import re
import uuid
from datetime import date, datetime
from decimal import Decimal as D

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import items as RI
from app.schemas import items as S
from app.services import barang_daftar as BD
from app.services import stok_baris as SB

RUTE = pathlib.Path(__file__).resolve().parents[2] / "app" / "routers" / "items.py"


def _b(**kw):
    b = {"item_type": "goods", "track_inventory": True, "status": "active", "reorder_level": None, "for_sales": True,
         "sales_price": D("5000"), "harga_jual": None}
    b.update(kw)
    return b


# --- penentu kunci -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("b", [
    _b(item_type="non_inventory", track_inventory=False),   # grapgrap: semua barang non_inventory
    _b(item_type="non_inventory", track_inventory=True),    # data kotor: lacak=true tapi bukan goods
    _b(track_inventory=False),                              # goods tanpa lacak stok
    _b(item_type="service", track_inventory=False),
    _b(status="inactive"),
])
@pytest.mark.parametrize("stok", [D("-5"), D("0"), D("3")])
def test_tak_dilacak_atau_nonaktif_tak_pernah_di_pill_stok(b, stok):
    b["reorder_level"] = D("10")
    assert not {"stok_habis", "di_bawah_titik_pesan"} & set(BD.aksi_semua(b, stok))


def test_sql_pill_stok_mensyaratkan_goods_lacak_aktif():
    for k in ("stok_habis", "di_bawah_titik_pesan"):
        sql = BD.sql_aksi(k)
        assert "p.item_type = 'goods'" in sql and "p.track_inventory = true" in sql and "p.status = 'active'" in sql, k


def test_aksi_semua_urut_desak_dan_minus_masuk_habis():
    assert BD.aksi_semua(_b(), D("-3")) == ["stok_habis"]
    assert BD.aksi_semua(_b(), D("0")) == ["stok_habis"]
    assert BD.aksi_semua(_b(reorder_level=D("10")), D("5")) == ["di_bawah_titik_pesan"]
    assert BD.aksi_semua(_b(reorder_level=D("10")), D("11")) == []
    assert BD.aksi_semua(_b(reorder_level=None), D("1")) == []
    # tanpa_harga_jual BUKAN partisi stok: satu barang bisa di dua pill, urut desak
    assert BD.aksi_semua(_b(sales_price=None), D("0")) == ["stok_habis", "tanpa_harga_jual"]
    assert BD.aksi_semua(_b(item_type="non_inventory", track_inventory=False, sales_price=D("0")), D("0")) == ["tanpa_harga_jual"]
    assert BD.aksi_semua(_b(sales_price=None, harga_jual=D("100")), D("1")) == []
    assert BD.aksi_semua(_b(sales_price=None, for_sales=False), D("1")) == []
    assert BD.aksi_semua(_b(sales_price=None, status="inactive"), D("1")) == []


def test_position_text():
    assert BD.position_text(_b(), D("172"), D("160"), "pcs") == "Stok 172 pcs · bisa dijual 160"
    assert BD.position_text(_b(), D("172"), D("172"), "pcs") == "Stok 172 pcs"
    assert BD.position_text(_b(), D("1500.5"), None, "kg") == "Stok 1.500,5 kg"
    assert BD.position_text(_b(), D("0"), D("-2"), "pcs") == "Stok habis"
    assert BD.position_text(_b(), D("-3"), None, "pcs") == "Stok minus 3 pcs · perlu hitung fisik"
    assert BD.position_text(_b(item_type="non_inventory", track_inventory=False), D("0")) == "Tanpa stok"
    assert BD.position_text(_b(item_type="service", track_inventory=False), D("0")) == "Jasa"
    assert BD.position_text(_b(status="inactive"), D("5")) == "Nonaktif"


def test_aksi_tersedia_detail():
    a = BD.aksi_tersedia(_b())
    assert a["nonaktifkan"]["tersedia"] and not a["aktifkan"]["tersedia"] and a["sesuaikan_stok"]["tersedia"]
    a = BD.aksi_tersedia(_b(item_type="non_inventory", track_inventory=False))
    assert a["sesuaikan_stok"] == {"tersedia": False, "alasan": "Stok barang ini tidak dilacak."}
    a = BD.aksi_tersedia(_b(status="inactive"))
    assert a["aktifkan"]["tersedia"] and not a["nonaktifkan"]["tersedia"] and not a["sesuaikan_stok"]["tersedia"]


# --- satu sumber: daftar / summary / stats memakai predikat yang sama -------------------------------------------------------

def test_satu_definisi_habis_di_daftar_summary_stats():
    habis = BD.sql_aksi("stok_habis")
    assert habis in RI.STATS_SQL and BD.sql_aksi("di_bawah_titik_pesan") in RI.STATS_SQL
    src_daftar = inspect.getsource(RI.list_items)
    src_sum = inspect.getsource(RI.get_items_summary)
    assert '"out_of_stock": BD.sql_aksi("stok_habis")' in src_daftar and "BD.sql_aksi(aksi)" in src_daftar
    assert 'BD.sql_aksi("stok_habis")' in src_sum and "BD.sql_aksi(k)" in src_sum
    assert "_syarat_cari(search, param_idx)" in src_daftar and "_syarat_cari(search, 2)" in src_sum
    # tak ada definisi "habis" ketiga yang ditulis tangan di router
    assert "COALESCE(per.jumlah, 0) <= 0" not in RUTE.read_text()


# --- rute daftar: placeholder, N+1, skema ---------------------------------------------------------------------------------

def _row(i, **kw):
    r = {"id": uuid.UUID(int=i + 1), "nama_produk": f"B{i}", "satuan": "pcs", "base_unit": "pcs", "item_type": "goods",
         "track_inventory": True, "status": "active", "reorder_level": None, "for_sales": True, "sales_price": D("1000"),
         "harga_jual": None, "current_stock": D("0"), "stock_value": D("0"), "transaction_count": 0, "low_stock": False,
         "conversions": [], "is_returnable": True, "created_at": datetime(2026, 11, 7), "updated_at": datetime(2026, 11, 7)}
    r.update(kw)
    return r


class _Conn:
    """Galat bila jumlah placeholder != jumlah argumen (asyncpg menolak argumen lebih)."""
    def __init__(self, rows):
        self.rows, self.panggil = rows, []

    def _cek(self, sql, a):
        n = max([int(x) for x in re.findall(r"\$(\d+)", sql)] or [0])
        assert n == len(a), (n, len(a), sql[-200:])
        self.panggil.append(sql)

    async def fetchrow(self, sql, *a):
        self._cek(sql, a)
        return {"total": len(self.rows), "nilai": D("1234.5")}

    async def fetch(self, sql, *a):
        self._cek(sql, a)
        return self.rows

    async def close(self):
        pass


def _req():
    r = Request({"type": "http", "method": "GET", "path": "/api/items", "headers": []})
    r.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
    return r


def _daftar(monkeypatch, rows, **kw):
    c, hit = _Conn(rows), []

    async def conn():
        return c

    async def hitung(conn_, tid, ids, tanggal, **_):
        hit.append(list(ids))
        return {"barang": {str(i): {"bisa_dijual": D("-1"), "satuan_dasar": "pcs"} for i in ids}, "catatan": [], "ktx": {}}

    async def hari(conn_, tid):
        return date(2026, 11, 7)
    monkeypatch.setattr(RI, "get_db_connection", conn)
    monkeypatch.setattr(RI.SB, "hitung", hitung)
    monkeypatch.setattr(RI, "tanggal_dokumen", hari)
    a = dict(item_type=None, track_inventory=None, search=None, kategori=None, stock_status=None, status=None,
             sort_by="created_at", sort_order="desc", limit=50, offset=0, aksi=None, with_totals=False)
    a.update(kw)
    return asyncio.run(RI.list_items(_req(), **a)), c, hit


@pytest.mark.parametrize("kw", [
    {}, {"search": "minyak goreng 2L"}, {"aksi": "stok_habis", "search": "a"}, {"stock_status": "out_of_stock", "status": "active"},
    {"aksi": "tanpa_harga_jual", "item_type": "goods", "kategori": "x", "track_inventory": True, "with_totals": True},
])
def test_placeholder_sesuai_argumen(monkeypatch, kw):
    _daftar(monkeypatch, [_row(0)], **kw)


def test_bisa_dijual_satu_hitung_per_halaman_bukan_n_plus_1(monkeypatch):
    jumlah = {}
    for n in (1, 40):
        rows = [_row(i) for i in range(n)] + [_row(100 + i, item_type="non_inventory", track_inventory=False) for i in range(n)]
        r, c, hit = _daftar(monkeypatch, rows)
        jumlah[n] = len(c.panggil)
        assert len(hit) == 1 and len(hit[0]) == n   # hanya barang dilacak, satu panggilan
        assert all(x.bisa_dijual is None for x in r.items if x.item_type == "non_inventory")
    assert jumlah[1] == jumlah[40] == 2             # hitung + halaman, tak tumbuh dengan jumlah baris


def test_stok_baris_kueri_tetap_untuk_banyak_barang():
    class C:
        def __init__(self):
            self.n = 0

        async def fetch(self, sql, *a):
            self.n += 1
            return []

        async def fetchval(self, sql, *a):
            self.n += 1
            return None

        async def fetchrow(self, sql, *a):
            self.n += 1
            return None
    n = {}
    for k in (1, 50):
        c = C()
        asyncio.run(SB.hitung(c, "t1", [uuid.UUID(int=i + 1) for i in range(k)], date(2026, 11, 7)))
        n[k] = c.n
    assert n[1] == n[50]


def test_halaman_tanpa_barang_dilacak_nol_panggilan_stok(monkeypatch):
    _, _, hit = _daftar(monkeypatch, [_row(0, item_type="non_inventory", track_inventory=False)])
    assert hit == []


def test_baris_membawa_aksi_posisi_dan_with_totals(monkeypatch):
    rows = [_row(0, current_stock=D("-3")), _row(1, current_stock=D("5"), reorder_level=D("10"), sales_price=None)]
    r, _, _ = _daftar(monkeypatch, rows, with_totals=True)
    a, b = r.items
    assert a.aksi_semua == ["stok_habis"] and a.position_text == "Stok minus 3 pcs · perlu hitung fisik"
    assert b.aksi_semua == ["di_bawah_titik_pesan", "tanpa_harga_jual"] and b.position_text == "Stok 5 pcs · bisa dijual -1"
    assert r.sum_nilai_stok == D("1234.50")
    r, _, _ = _daftar(monkeypatch, rows)
    assert r.sum_nilai_stok is None


def test_aksi_tak_dikenal_422(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _daftar(monkeypatch, [], aksi="habis")
    assert e.value.status_code == 422 and "aksi tidak dikenal" in str(e.value.detail)


def _kwargs(fn_nama, kelas):
    pohon = ast.parse(RUTE.read_text())
    f = next(n for n in pohon.body if isinstance(n, ast.AsyncFunctionDef) and n.name == fn_nama)
    return [{k.arg for k in n.keywords} for n in ast.walk(f)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == kelas]


def test_skema_menyatakan_semua_medan_daftar():
    # pola BUG-097: medan tak dideklarasi dibuang diam oleh response_model
    for kelas, model in (("ItemListItem", S.ItemListItem), ("ItemListResponse", S.ItemListResponse)):
        semua = _kwargs("list_items", kelas)
        assert semua, kelas
        for ks in semua:
            assert ks - set(model.model_fields) == set(), kelas
    assert {"aksi_semua", "position_text", "bisa_dijual"} <= _kwargs("list_items", "ItemListItem")[0]


# --- summary --------------------------------------------------------------------------------------------------------------

def test_summary_kunci_count_value_dan_cari(monkeypatch):
    from tests.unit.test_nilai_hpp_margin import BARIS, _C, _klien
    c = _C(BARIS)
    r = _klien(c, monkeypatch).get("/api/items/summary?search=minyak goreng")
    d = r.json()["data"]
    assert r.status_code == 200
    for k in BD.SEMUA_KUNCI:
        assert f"{k}_count" in d and f"{k}_value" in d
    assert d["stok_habis_count"] == BARIS["stok_habis_count"]
    sql = c.sql[-1]
    assert "p.nama_produk ILIKE $2" in sql and "p.nama_produk ILIKE $3" in sql
