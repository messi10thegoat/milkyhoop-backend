"""K9 (14 Nov 2026; banding Accurate): Kartu Stok barang per GUDANG (kolom Gudang, filter gudang/periode, saldo awal, saldo berjalan dari saldo awal, ringkasan)
dan laporan "Perbedaan Persediaan Terkirim" (dikirim vs diterima per barang; DITERIMA dari LEDGER karena kolom item beku oleh trigger DB sesudah Kirim)."""
import asyncio
import inspect
import re
import uuid
from datetime import date, datetime
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

import app.routers.items as IT
from app.middleware import permission_middleware as PM
from app.routers import stock_transfers as ST

T = "kaos-biru-konveksi"
ITEM, G = uuid.uuid4(), uuid.uuid4()
ROW = {"id": uuid.uuid4(), "movement_type": "RELOKASI_KIRIM_TRANSIT", "movement_date": datetime(2026, 11, 14), "source_type": "RELOKASI_KIRIM",
       "source_id": uuid.uuid4(), "source_number": "ST-1", "quantity_in": 3, "quantity_out": 0, "saldo_berjalan": 8, "unit_cost": 1, "total_cost": 3,
       "average_cost": 1, "warehouse_id": G, "warehouse_name": "Transit", "batch_id": None, "notes": None, "created_at": datetime(2026, 11, 14),
       "created_by": None, "counterparty": None}


class ConnH:
    def __init__(self):
        self.sql, self.args = [], []

    def _c(self, sql, a):
        self.sql.append(" ".join(sql.split()))
        self.args.append(a)

    async def fetchrow(self, sql, *a):
        self._c(sql, a)
        if "AS masuk" in sql:
            return {"masuk": D("30"), "keluar": D("12")}
        return {"id": a[0], "item_code": "K-1", "nama_produk": "Kaos"}

    async def fetchval(self, sql, *a):
        self._c(sql, a)
        return D("10") if re.search(r"movement_date < \$\d", sql) else 1

    async def fetch(self, sql, *a):
        self._c(sql, a)
        return [ROW]

    async def close(self):
        pass


def pasang(monkeypatch):
    c = ConnH()

    async def _conn():
        return c
    monkeypatch.setattr(IT, "get_db_connection", _conn)
    monkeypatch.setattr(IT, "get_tenant_id", lambda r: T)
    return c


def sejajar(c):
    for s, a in zip(c.sql, c.args):
        nomor = [int(x) for x in re.findall(r"\$(\d+)", s)]
        assert (max(nomor) if nomor else 0) == len(a), (max(nomor) if nomor else 0, len(a), s[:200])


def utama(c):
    return [s for s in c.sql if "LEFT JOIN sales_invoices" in s][0]


@pytest.mark.asyncio
async def test_kartu_stok_tanpa_filter_perilaku_lama_plus_kolom_gudang(monkeypatch):
    c = pasang(monkeypatch)
    r = await IT.get_item_history(SimpleNamespace(), ITEM, page=1, limit=5)
    u = utama(c)
    assert "LEFT JOIN warehouses w ON w.id = il.warehouse_id AND w.tenant_id = il.tenant_id" in u and "w.name AS warehouse_name" in u and "LIMIT $3 OFFSET $4" in u
    assert r["data"][0]["warehouse_name"] == "Transit" and r["data"][0]["quantity_balance"] == 8
    assert r["saldo_awal"] == 0 and r["ringkasan"] is None and r["filter"] == {"warehouse_id": None, "date_from": None, "date_to": None}
    assert not any("AS masuk" in s or re.search(r"movement_date < \$\d", s) for s in c.sql)              # tanpa filter: nol kueri tambahan
    sejajar(c)


@pytest.mark.asyncio
async def test_kartu_stok_filter_gudang_dan_periode_saldo_awal_dan_ringkasan(monkeypatch):
    c = pasang(monkeypatch)
    df, dt = date(2026, 11, 1), date(2026, 11, 30)
    r = await IT.get_item_history(SimpleNamespace(), ITEM, page=1, limit=5, warehouse_id=G, date_from=df, date_to=dt)
    u = utama(c)
    assert "il.warehouse_id = $3::uuid" in u and "il.movement_date >= $4" in u and "il.movement_date <= $5" in u and "LIMIT $6 OFFSET $7" in u
    assert c.args[c.sql.index(u)] == (str(ITEM), T, str(G), df, dt, 5, 0)
    awal = [(s, a) for s, a in zip(c.sql, c.args) if re.search(r"movement_date < \$\d", s)]
    assert len(awal) == 1 and "il.movement_date < $4" in awal[0][0] and awal[0][1] == (str(ITEM), T, str(G), df)   # saldo awal = SEBELUM tanggal mulai, gudang yang sama
    assert r["saldo_awal"] == 10 and r["data"][0]["quantity_balance"] == 18                                       # saldo berjalan MULAI dari saldo awal (8 + 10)
    assert r["ringkasan"] == {"saldo_awal": D("10"), "total_masuk": D("30"), "total_keluar": D("12"), "saldo_akhir": D("28")}
    assert r["filter"] == {"warehouse_id": str(G), "date_from": "2026-11-01", "date_to": "2026-11-30"}
    sejajar(c)


@pytest.mark.asyncio
async def test_kartu_stok_hanya_periode_semua_gudang_dan_hanya_sampai_tanggal(monkeypatch):
    c = pasang(monkeypatch)
    df = date(2026, 11, 1)
    r = await IT.get_item_history(SimpleNamespace(), ITEM, page=1, limit=5, date_from=df)
    u = utama(c)
    assert "il.movement_date >= $3" in u and "il.warehouse_id =" not in u and "LIMIT $4 OFFSET $5" in u and r["saldo_awal"] == 10
    sejajar(c)
    c2 = pasang(monkeypatch)
    r2 = await IT.get_item_history(SimpleNamespace(), ITEM, page=1, limit=5, date_to=date(2026, 11, 30))
    assert "il.movement_date <= $3" in utama(c2) and r2["saldo_awal"] == 0 and r2["ringkasan"] is not None          # tanpa tanggal mulai: saldo awal 0, ringkasan ada
    assert not any(re.search(r"movement_date < \$\d", s) for s in c2.sql)
    sejajar(c2)


# ---------------------------------------------------------------- Perbedaan Persediaan Terkirim
class KonV:
    def __init__(self, rows):
        self.rows, self.sql, self.args = rows, [], []

    async def execute(self, sql, *a):
        return "OK"

    async def fetch(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        self.args.append(a)
        return self.rows


def pasang_v(monkeypatch, rows):
    k = KonV(rows)

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
    monkeypatch.setattr(ST, "get_pool", pool)
    return k


def req_v():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": str(uuid.uuid4())}))


def baris_v(kirim, terima, biaya="1000", n="Kaos", tid=None):
    return {"transfer_id": tid or uuid.UUID(int=1), "transfer_number": "ST-1", "shipped_date": date(2026, 11, 14), "status": "in_transit",
            "from_warehouse_name": "A", "to_warehouse_name": "B", "item_id": uuid.uuid4(), "item_code": "K", "item_name": n, "unit": "pcs",
            "quantity_shipped": D(kirim), "quantity_received": D(terima), "unit_cost": D(biaya)}


def test_perbedaan_terkirim_tertunda_nilai_ringkasan_dan_sql(monkeypatch):
    k = pasang_v(monkeypatch, [baris_v("10", "4"), baris_v("5", "0", n="Celana")])
    r = asyncio.run(ST.get_transfer_variance(req_v()))
    a, b = r["data"]
    assert (a["quantity_shipped"], a["quantity_received"], a["quantity_tertunda"], a["nilai_tertunda"], a["diterima_sebagian"]) == (10.0, 4.0, 6.0, 6000.0, True)
    assert (b["quantity_tertunda"], b["nilai_tertunda"], b["diterima_sebagian"]) == (5.0, 5000.0, False)
    assert r["summary"] == {"jumlah_pemindahan": 1, "jumlah_baris": 2, "total_tertunda": 11.0, "total_nilai_tertunda": 11000.0}
    s = k.sql[0]
    assert "st.tenant_id = $1" in s and "st.status = 'in_transit'" in s and "il.source_type = 'RELOKASI_TERIMA'" in s and "il.source_id = st.id" in s
    assert "d.diterima AS quantity_received" in s and "COALESCE(i.quantity_shipped, 0) > d.diterima" in s and k.args[0] == (T,)   # DITERIMA dari ledger


def test_perbedaan_terkirim_tanpa_baris_ringkasan_nol(monkeypatch):
    pasang_v(monkeypatch, [])
    r = asyncio.run(ST.get_transfer_variance(req_v()))
    assert r["data"] == [] and r["summary"] == {"jumlah_pemindahan": 0, "jumlah_baris": 0, "total_tertunda": 0.0, "total_nilai_tertunda": 0.0}


def test_perbedaan_terkirim_izin_dan_urutan_rute():
    pola = [(re.compile(p), m, mod, aksi) for p, m, mod, aksi in PM.ROUTE_PERMISSIONS]
    hit = next(((mod, aksi) for rx, ms, mod, aksi in pola if rx.match("/api/stock-transfers/variance") and "GET" in ms), None)
    assert hit == ("item", "R")
    rute = [(r.path, tuple(sorted(r.methods))) for r in ST.router.routes]
    assert rute.index(("/variance", ("GET",))) < rute.index(("/{transfer_id}", ("GET",)))                # literal sebelum /{id}
