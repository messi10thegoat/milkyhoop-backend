"""Q-012 (25 Sep 2026): GET /api/sales-orders/aggregate + /summary.uninvoiced_value.

Kontrak disetujui MASTER 25 Sep: uang "belum ditagih" = turunan jurnal (proforma_terbayar.belum_ditagih,
SAMA dengan payment_summary); faktur DRAF dihitung belum ditagih (ditampilkan terpisah, tak mengurangi);
top_customers tanpa draf/batal; SO berjalan = selain draft/cancelled/completed; parameter salah -> 400.
Latar ukur: grapgrap belum-ditagih nyata 50.285.000 sementara summary.pending_invoice_value = 0.
"""
import ast
import uuid
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.routing import Match

from app.routers import sales_orders as SOR
from app.schemas.sales_orders import SalesOrderSummary
from app.services import proforma_terbayar as PT
from app.services import so_agregat as SA

TENANT = "kaos-biru-konveksi"


def _so(total, status="confirmed", nomor=None, draf=0, nama="Rahayu", tgl=date(2026, 9, 1), kirim=None):
    return {"id": uuid.uuid4(), "order_number": nomor or f"SO-{uuid.uuid4().hex[:4]}",
            "customer_id": uuid.uuid4(), "customer_name": nama, "order_date": tgl, "status": status,
            "total_amount": D(str(total)), "draft_invoice_amount": D(str(draf)), "expected_ship_date": kirim}


class DB:
    """Kueri dicatat; jawaban per penanda SQL."""
    def __init__(self, rows=(), pelanggan=None, tanpa_kirim=0, agregat=()):
        self.rows, self.pelanggan, self.tanpa_kirim, self.agregat = list(rows), pelanggan, tanpa_kirim, list(agregat)
        self.sql, self.args = [], []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        self.args.append(a)
        if "GROUP BY so.customer_id" in sql:
            return self.agregat
        return self.rows

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        self.args.append(a)
        return self.pelanggan

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        self.args.append(a)
        return self.tanpa_kirim


@pytest.fixture
def tagihan(monkeypatch):
    """{so_id: Decimal invoiced} -> ringkasan_pesanan palsu; mencatat so_ids yang diminta."""
    t, diminta = {}, []

    async def palsu(conn, tenant_id, so_ids):
        diminta.append(list(so_ids))
        out = {}
        for s in so_ids:
            r = {k: D(0) for k in PT._KOSONG}
            r["invoiced"] = t.get(s, D(0))
            r["tertutup"] = D(0)
            out[s] = r
        return out
    monkeypatch.setattr(SA, "ringkasan_pesanan", palsu)
    t["_diminta"] = diminta
    return t


# ---------- satu definisi "belum ditagih" ----------

def test_belum_ditagih_min_nol():
    r = {k: D(0) for k in PT._KOSONG}
    r["invoiced"] = D("404000")                       # faktur > SO (SO-2609-0252 kaos)
    assert PT.belum_ditagih(D("170000"), r) == D(0)
    r["invoiced"] = D("400000")
    assert PT.belum_ditagih(D("1000000"), r) == D("600000")


def test_payment_summary_memakai_definisi_yang_sama(monkeypatch):
    monkeypatch.setattr(PT, "belum_ditagih", lambda total, r: D("7"))
    r = {k: D(0) for k in PT._KOSONG}
    r["tertutup"] = D(0)
    assert PT.ringkasan_pembayaran_so(D("100"), r)["uninvoiced_amount"] == 7.0


# ---------- q=uninvoiced ----------

@pytest.mark.asyncio
async def test_uninvoiced_draf_dihitung_dan_urut(tagihan):
    a = _so(1000000, nomor="SO-A")
    lunas = _so(500000, status="invoiced", nomor="SO-B")
    draf = _so(1625000, status="invoiced", nomor="SO-2609-0006", draf=1625000)   # faktur masih DRAF
    sebagian = _so(3000000, status="partial_invoiced", nomor="SO-C")
    tagihan[lunas["id"]] = D("500000")
    tagihan[sebagian["id"]] = D("1000000")
    db = DB([a, lunas, draf, sebagian])
    d = await SA.uninvoiced(db, TENANT)
    assert [r["order_number"] for r in d["rows"]] == ["SO-C", "SO-2609-0006", "SO-A"]
    assert d["count"] == 3 and d["total"] == 4625000.0 and d["truncated"] is False
    r = next(x for x in d["rows"] if x["order_number"] == "SO-2609-0006")
    assert (r["uninvoiced_amount"], r["draft_invoice_amount"]) == (1625000.0, 1625000.0)   # draf TAK mengurangi
    assert next(x for x in d["rows"] if x["order_number"] == "SO-C")["uninvoiced_amount"] == 2000000.0


@pytest.mark.asyncio
async def test_uninvoiced_sql_dan_himpunan(tagihan):
    db = DB([])
    await SA.uninvoiced(db, TENANT)
    sql, args = db.sql[0], db.args[0]
    assert args == (TENANT, ["draft", "cancelled", "completed"])
    assert "so.status <> ALL($2::text[])" in sql and "so.tenant_id = $1" in sql
    assert "COALESCE(c.nama, so.customer_name)" in sql and "c.tenant_id = so.tenant_id" in sql
    assert "si.status = 'draft'" in sql and "si.tenant_id = so.tenant_id" in sql


@pytest.mark.asyncio
async def test_uninvoiced_dipotong_50_total_tetap_semua(tagihan):
    db = DB([_so(1000 + i, nomor=f"SO-{i:03d}") for i in range(51)])
    d = await SA.uninvoiced(db, TENANT)
    assert d["count"] == 51 and len(d["rows"]) == 50 and d["truncated"] is True
    assert d["total"] == float(sum(1000 + i for i in range(51)))
    assert d["rows"][0]["order_number"] == "SO-050"                 # terbesar dulu


# ---------- q=top_customers ----------

@pytest.mark.asyncio
async def test_top_customers_tanpa_draf_dan_batal():
    cid, oid = uuid.uuid4(), uuid.uuid4()
    db = DB(agregat=[{"customer_id": cid, "customer_name": "Nutrindo", "total": D("5000000"),
                      "count": 2, "order_ids": [oid]}])
    d = await SA.top_customers(db, TENANT, "2026-09-21", "2026-09-27", None)
    assert d == {"period": {"from": "2026-09-21", "to": "2026-09-27"},
                 "rows": [{"customer_id": str(cid), "customer_name": "Nutrindo", "total": 5000000.0,
                           "count": 2, "order_ids": [str(oid)]}]}
    sql = db.sql[0]
    assert "so.status NOT IN ('draft', 'cancelled')" in sql and "so.order_date BETWEEN $2 AND $3" in sql
    assert "COALESCE(c.nama, so.customer_name)" in sql and "LIMIT $4" in sql
    assert db.args[0] == (TENANT, date(2026, 9, 21), date(2026, 9, 27), 3)


# ---------- q=customer_status ----------

@pytest.mark.asyncio
async def test_customer_status_404_pelanggan_lain_tenant(tagihan):
    with pytest.raises(HTTPException) as e:
        await SA.customer_status(DB(pelanggan=None), TENANT, str(uuid.uuid4()))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_customer_status_per_status_dan_belum_ditagih_hanya_berjalan(tagihan):
    a, b, batal, selesai = (_so(1000000), _so(2000000, status="invoiced"),
                            _so(700000, status="cancelled"), _so(300000, status="completed"))
    tagihan[b["id"]] = D("1500000")
    db = DB([a, b, batal, selesai], pelanggan={"nama": "Rahayu Kini"})
    cid = str(uuid.uuid4())
    d = await SA.customer_status(db, TENANT, cid)
    assert d["customer_name"] == "Rahayu Kini" and d["customer_id"] == cid
    assert {s["status"]: (s["count"], s["total"]) for s in d["by_status"]} == {
        "confirmed": (1, 1000000.0), "invoiced": (1, 2000000.0), "cancelled": (1, 700000.0), "completed": (1, 300000.0)}
    assert d["uninvoiced_total"] == 1500000.0                 # 1 jt + 0,5 jt; batal & selesai tak ikut
    assert set(tagihan["_diminta"][-1]) == {a["id"], b["id"]}
    assert db.args[0] == (uuid.UUID(cid), TENANT)             # pagar tenant di cek pelanggan


# ---------- q=ship_window ----------

@pytest.mark.asyncio
async def test_ship_window():
    rows = [_so(100000 * (i + 1), nomor=f"SO-{i:03d}", kirim=date(2026, 9, 22)) for i in range(51)]
    db = DB(rows, tanpa_kirim=4)
    d = await SA.ship_window(db, TENANT, "2026-09-21", "2026-09-27")
    assert d["count"] == 51 and len(d["rows"]) == 50 and d["truncated"] is True
    assert d["total"] == float(sum(100000 * (i + 1) for i in range(51)))
    assert d["without_ship_date_count"] == 4 and d["rows"][0]["expected_ship_date"] == "2026-09-22"
    assert "so.expected_ship_date BETWEEN $3 AND $4" in db.sql[0]
    assert "ORDER BY so.expected_ship_date, so.order_number" in db.sql[0]
    assert db.args[0] == (TENANT, ["draft", "cancelled", "completed"], date(2026, 9, 21), date(2026, 9, 27))
    assert "so.expected_ship_date IS NULL" in db.sql[1]


# ---------- validasi 400 ----------

@pytest.mark.parametrize("q,kw", [
    (None, {}), ("apa", {}),
    ("top_customers", {}), ("top_customers", {"dari": "2026-9-1", "sampai": "2026-09-30"}),
    ("top_customers", {"dari": "2026-09-30", "sampai": "2026-09-01"}),
    ("top_customers", {"dari": "2025-01-01", "sampai": "2026-01-02"}),
    ("top_customers", {"dari": "2026-09-01", "sampai": "2026-09-30", "limit": "0"}),
    ("top_customers", {"dari": "2026-09-01", "sampai": "2026-09-30", "limit": "11"}),
    ("top_customers", {"dari": "2026-09-01", "sampai": "2026-09-30", "limit": "tiga"}),
    ("customer_status", {}), ("customer_status", {"customer_id": "bukan-uuid"}),
    ("ship_window", {"dari": "2026-09-01"}),
])
@pytest.mark.asyncio
async def test_parameter_salah_400_tanpa_sql(q, kw):
    db = DB()
    with pytest.raises(HTTPException) as e:
        await SA.agregat(db, TENANT, q, **kw)
    assert e.value.status_code == 400
    assert db.sql == []


def test_rentang_366_hari_sah():
    assert SA.rentang("2024-01-01", "2024-12-31") == (date(2024, 1, 1), date(2024, 12, 31))   # kabisat = 366


# ---------- penyambungan: rute, HTTP, izin, summary ----------

def test_rute_aggregate_menang_atas_order_id():
    app = FastAPI()
    app.include_router(SOR.router, prefix="/api/sales-orders")
    scope = {"type": "http", "method": "GET", "path": "/api/sales-orders/aggregate", "root_path": ""}
    ep = next(r.endpoint for r in app.router.routes if r.matches(scope)[0] == Match.FULL)
    assert ep.__name__ == "get_sales_order_aggregate"


@pytest.fixture
def klien(monkeypatch, tagihan):
    db = DB([_so(250000, nomor="SO-X")])

    class Pool:
        def acquire(self):
            class Ctx:
                async def __aenter__(s):
                    return db
                async def __aexit__(s, *a):
                    return False
            return Ctx()

    async def pool():
        return Pool()
    monkeypatch.setattr(SOR, "get_pool", pool)
    app = FastAPI()

    @app.middleware("http")
    async def user(request, call_next):
        request.state.user = {"tenant_id": TENANT, "user_id": str(uuid.uuid4())}
        return await call_next(request)
    app.include_router(SOR.router, prefix="/api/sales-orders")
    return TestClient(app)


def test_http_aggregate_200_dan_400(klien):
    r = klien.get("/api/sales-orders/aggregate?q=uninvoiced")
    assert r.status_code == 200, r.text
    assert r.json()["q"] == "uninvoiced" and r.json()["data"]["total"] == 250000.0
    r = klien.get("/api/sales-orders/aggregate?q=top_customers&from=2026-09-30&to=2026-09-01")
    assert r.status_code == 400 and "from" in r.json()["detail"]
    assert klien.get("/api/sales-orders/aggregate?q=hapus").status_code == 400


def test_izin_aggregate_baca_sales_order():
    from app.middleware.permission_middleware import PermissionMiddleware
    pm = PermissionMiddleware(app=None)
    assert pm._find_permission("/api/sales-orders/aggregate", "GET") == ("sales_order", "R")


def test_summary_mendeklarasikan_dan_mengisi_uninvoiced_value():
    assert "uninvoiced_value" in SalesOrderSummary.model_fields
    pohon = ast.parse(Path(SOR.__file__).read_text())
    f = next(n for n in pohon.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_sales_order_summary")
    panggil = {(getattr(n.func.value, "id", None), n.func.attr) for n in ast.walk(f)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert ("so_agregat", "uninvoiced") in panggil
    kunci = {k.value for n in ast.walk(f) if isinstance(n, ast.Dict) for k in n.keys if isinstance(k, ast.Constant)}
    assert "uninvoiced_value" in kunci


def test_http_summary_uninvoiced_value_sama_dengan_aggregate(klien, monkeypatch):
    # perilaku, bukan bentuk: sabotase '"uninvoiced_value": 0' lolos tes AST — tes ini memerahkannya
    ringkas = {k: 0 for k in ("total_orders", "draft_count", "confirmed_count", "partial_shipped_count",
                              "shipped_count", "partial_invoiced_count", "invoiced_count", "completed_count",
                              "cancelled_count", "total_value", "pending_shipment_value", "pending_invoice_value")}

    async def fetchrow(self, sql, *a):
        return ringkas
    monkeypatch.setattr(DB, "fetchrow", fetchrow)
    r = klien.get("/api/sales-orders/summary")
    assert r.status_code == 200, r.text
    agg = klien.get("/api/sales-orders/aggregate?q=uninvoiced").json()["data"]["total"]
    assert r.json()["data"]["uninvoiced_value"] == agg == 250000.0
    assert r.json()["data"]["pending_invoice_value"] == 0          # medan lama tetap ada, definisi lama


def test_http_summary_uninvoiced_count_semua_bukan_baris_terpotong(klien, monkeypatch):
    # 51 SO bersisa + 1 lunas: count = 51 (bukan 50 baris yang dikirim aggregate, bukan 52 semua SO)
    ringkas = {k: 0 for k in ("total_orders", "draft_count", "confirmed_count", "partial_shipped_count",
                              "shipped_count", "partial_invoiced_count", "invoiced_count", "completed_count",
                              "cancelled_count", "total_value", "pending_shipment_value", "pending_invoice_value")}
    lunas = _so(900000, status="invoiced", nomor="SO-LUNAS")
    rows = [_so(1000 + i, nomor=f"SO-{i:03d}") for i in range(51)] + [lunas]

    async def fetch(self, sql, *a):
        return rows

    async def fetchrow(self, sql, *a):
        return ringkas
    monkeypatch.setattr(DB, "fetch", fetch)
    monkeypatch.setattr(DB, "fetchrow", fetchrow)
    tagihan_lunas = {lunas["id"]: 900000}

    async def ring(conn, tenant_id, so_ids):
        out = {}
        for s in so_ids:
            r = {k: D(0) for k in PT._KOSONG}
            r["invoiced"], r["tertutup"] = D(tagihan_lunas.get(s, 0)), D(0)
            out[s] = r
        return out
    monkeypatch.setattr(SA, "ringkasan_pesanan", ring)
    d = klien.get("/api/sales-orders/summary").json()["data"]
    agg = klien.get("/api/sales-orders/aggregate?q=uninvoiced").json()["data"]
    assert d["uninvoiced_count"] == agg["count"] == 51 and len(agg["rows"]) == 50
    assert "uninvoiced_count" in SalesOrderSummary.model_fields
