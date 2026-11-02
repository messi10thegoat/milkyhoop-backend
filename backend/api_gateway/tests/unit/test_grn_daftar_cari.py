"""U-GRN-DAFTAR (2 Nov 2026, disetujui MASTER): daftar Penerimaan Barang cermin Pengiriman -- ?search= server (SATU predikat untuk daftar
DAN /summary?search=: No. penerimaan, No. SJ vendor, No. PO, nama vendor, gudang), date_from/date_to, sort_by/sort_order, has_more;
/summary + total_all/total_posted/total_voided/today_count (receipt_date = tanggal_bisnis tenant). ADITIF: kunci & param lama tetap."""
import ast
import asyncio
import inspect
from datetime import date
from uuid import uuid4

from app.routers import goods_receipts as GR

T = "kaos-biru-konveksi"


def test_satu_predikat_cari_lima_kolom():
    assert GR._syarat_cari(None, 2) == (None, []) and GR._syarat_cari("", 2) == (None, [])
    p, a = GR._syarat_cari("SJ-77", 3)
    assert a == ["%SJ-77%"]
    for kol in ("g.receipt_number ILIKE $3", "g.vendor_delivery_note ILIKE $3", "po.po_number ILIKE $3",
                "COALESCE(v.name, po.vendor_name) ILIKE $3", "w.name ILIKE $3"):
        assert kol in p, kol


def _panggil(fn, nama):
    pohon = ast.parse(inspect.getsource(fn).lstrip())
    return [n for n in ast.walk(pohon) if isinstance(n, ast.Call) and ast.unparse(n.func) == nama]


def test_daftar_dan_ringkasan_memakai_predikat_yang_sama():
    assert _panggil(GR.list_goods_receipts, "_syarat_cari") and _panggil(GR.goods_receipts_summary, "_syarat_cari")
    s = inspect.getsource(GR.goods_receipts_summary)
    assert "g.receipt_date = tanggal_bisnis($1)" in s and "{FROM_RINGKASAN}" in s
    # FROM ringkasan = LEFT JOIN (tak menjatuhkan baris -> hitungan lama identik)
    assert "LEFT JOIN purchase_orders po" in GR.FROM_RINGKASAN and " JOIN purchase_orders" not in GR.FROM_RINGKASAN.replace("LEFT JOIN", "")


class _K:
    def __init__(self):
        self.sql = []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        return {"total": 7, "posted": 5, "void": 2, "today_count": 1, "n_aksi": 3, "v_aksi": "1250.50"}

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        return 3

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return []


def _pasang(monkeypatch, k):
    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False

    class Pool:
        def acquire(s): return CM()

    async def gp(): return Pool()
    monkeypatch.setattr(GR, "get_pool", gp)
    monkeypatch.setattr(GR, "get_user_context", lambda r: {"tenant_id": T, "user_id": uuid4()})

    async def kel(*a, **kw): return {}
    monkeypatch.setattr(GR.GD, "kelompok_grn_banyak", kel)


def test_ringkasan_kunci_cermin_pengiriman_dan_lama_tetap(monkeypatch):
    k = _K()
    _pasang(monkeypatch, k)
    d = asyncio.run(GR.goods_receipts_summary(None, purchase_order_id=None, search="SJ-77"))["data"]
    assert (d["total"], d["posted"], d["void"]) == (7, 5, 2)                       # kunci lama
    assert (d["total_all"], d["total_posted"], d["total_voided"], d["today_count"]) == (7, 5, 2, 1)
    assert d["belum_ditagih_count"] == 3 and d["belum_ditagih_value"] == "1250.50"
    sql, a = k.sql[0]
    assert a == (T, "%SJ-77%") and "g.vendor_delivery_note ILIKE $2" in sql
    k2 = _K()
    _pasang(monkeypatch, k2)
    asyncio.run(GR.goods_receipts_summary(None, purchase_order_id=None, search=None))
    assert k2.sql[0][1] == (T,) and "ILIKE" not in k2.sql[0][0]


def test_daftar_cari_tanggal_urut_has_more(monkeypatch):
    k = _K()
    _pasang(monkeypatch, k)
    d = asyncio.run(GR.list_goods_receipts(None, purchase_order_id=None, status=None, aksi=None, limit=50, offset=0, search="PO-1",
                                           date_from=date(2026, 11, 1), date_to=date(2026, 11, 2), sort_by="vendor_name",
                                           sort_order="asc"))["data"]
    hitung, ambil = k.sql[0], k.sql[1]
    assert hitung[1] == ambil[1] == (T, "%PO-1%", date(2026, 11, 1), date(2026, 11, 2))
    assert "g.receipt_date >= $3" in ambil[0] and "g.receipt_date <= $4" in ambil[0]
    assert "ORDER BY vendor_name ASC, g.id ASC" in ambil[0]
    assert d["total"] == 3 and d["has_more"] is True and d["items"] == []


def test_daftar_bawaan_urutan_lama(monkeypatch):
    k = _K()
    _pasang(monkeypatch, k)
    asyncio.run(GR.list_goods_receipts(None, purchase_order_id=None, status=None, aksi=None, limit=50, offset=0, search=None,
                                       date_from=None, date_to=None, sort_by="receipt_date", sort_order="desc"))
    assert "ORDER BY g.receipt_date DESC, g.created_at DESC, g.id DESC" in k.sql[1][0] and k.sql[1][1] == (T,)
