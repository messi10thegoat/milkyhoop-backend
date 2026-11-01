"""#4 (1 Nov 2026): GET /api/purchase-orders/aggregate -- cermin Q-012 SO (test_so_agregat).

Putusan MASTER 1 Nov: "belum ditagih" = SATU definisi PO yang ada (PD.fakta_po sisa_ditagih == kolom Sisa daftar; tagihan DRAF
dihitung sudah ditagih, ditampilkan terpisah draft_bill_amount); PO berjalan = selain draft/cancelled/closed; validasi 400/404
SAMA dengan so_agregat; rute dideklarasikan sebelum /{po_id}; izin = pola eksplisit purchase_order R di atas catch-all.
"""
import ast
import asyncio
import re
import uuid
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

from app.middleware import permission_middleware as pm
from app.routers import purchase_orders as POR
from app.services import po_agregat as PA
from app.services import po_daftar as PD
from app.services import so_agregat as SA
from backend.api_gateway.app.services import policy_engine_client as pec

from ._jam_beku import bekukan_hari

TENANT = "kaos-biru-konveksi"
HARI = date(2026, 11, 1)


@pytest.fixture(autouse=True)
def hari_ini(monkeypatch):
    bekukan_hari(monkeypatch, HARI)


def _po(total, status="sent", nomor=None, ditagih=0, draf=0, nama="Bahan Jaya", tgl=date(2026, 10, 1), tiba=None):
    return {"id": uuid.uuid4(), "po_number": nomor or f"PO-{uuid.uuid4().hex[:4]}", "vendor_id": uuid.uuid4(),
            "vendor_name": nama, "po_date": tgl, "status": status, "total_amount": D(str(total)),
            "draft_bill_amount": D(str(draf)), "expected_date": tiba,
            # medan LATERAL SQL_AGREGAT (turunan baris PO) -- dibaca PD.fakta_po
            "sisa_terima": D(0), "belum_ditagih": D(0), "amount_received": D(str(total)), "amount_billed": D(str(ditagih))}


def _kueri_fakta(sql):
    return "ag.sisa_terima, ag.belum_ditagih" in sql


class DB:
    """Kueri dicatat; jawaban per penanda SQL. Kueri PD.fakta_po dijawab dari baris yang SAMA (disaring id)."""
    def __init__(self, rows=(), vendor=None, tanpa_tiba=0, agregat=()):
        self.rows, self.vendor, self.tanpa_tiba, self.agregat = list(rows), vendor, tanpa_tiba, list(agregat)
        self.sql, self.args = [], []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        self.args.append(a)
        if _kueri_fakta(sql):
            assert a[0] == TENANT
            return [r for r in self.rows if r["id"] in set(a[1])]
        if "GROUP BY po.vendor_id" in sql:
            return self.agregat
        return self.rows

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        self.args.append(a)
        return self.vendor

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        self.args.append(a)
        return self.tanpa_tiba


def _bukan_fakta(db):
    return [(s, a) for s, a in zip(db.sql, db.args) if not _kueri_fakta(s)]


# ---------- q=unbilled: SATU definisi sisa ----------

@pytest.mark.asyncio
async def test_unbilled_sisa_turunan_draf_terpisah_dan_urut():
    a = _po(1000000, nomor="PO-A")
    sebagian = _po(2000000, status="partial_billed", nomor="PO-B", ditagih=1500000, draf=300000)
    penuh = _po(700000, status="billed", nomor="PO-C", ditagih=700000)
    terima = _po(3000000, status="received", nomor="PO-D", ditagih=1000000)
    d = await PA.unbilled(DB([a, sebagian, penuh, terima]), TENANT)
    assert [r["po_number"] for r in d["rows"]] == ["PO-D", "PO-A", "PO-B"]
    assert d["count"] == 3 and d["total"] == 3500000.0 and d["truncated"] is False
    r = next(x for x in d["rows"] if x["po_number"] == "PO-B")
    assert (r["unbilled_amount"], r["draft_bill_amount"]) == (500000.0, 300000.0)   # draf TAK menambah sisa


@pytest.mark.asyncio
async def test_unbilled_sama_dengan_kolom_sisa_daftar_untuk_po_yang_sama():
    """Kesetaraan angka (syarat MASTER): unbilled_amount == PD.fakta_po(...)["sisa_ditagih"] = medan sisa_ditagih baris daftar."""
    rows = [_po(1234567.89, nomor="PO-1", ditagih=234567.12), _po(500000.5, status="partial_billed", nomor="PO-2", ditagih=0.25),
            _po(99, status="received", nomor="PO-3", ditagih=100)]
    db = DB(rows)
    agg = {r["id"]: r["unbilled_amount"] for r in (await PA.unbilled(db, TENANT))["rows"]}
    fakta = await PD.fakta_po(DB(rows), TENANT, [r["id"] for r in rows], HARI)
    daftar = {k: float(v["sisa_ditagih"]) for k, v in fakta.items() if v["sisa_ditagih"] > 0}
    assert {uuid.UUID(k): v for k, v in agg.items()} == daftar == {rows[0]["id"]: 1000000.77, rows[1]["id"]: 500000.25}


def test_baris_daftar_memakai_sisa_dari_fakta_po():
    """Sisi daftar dari kesetaraan: list_purchase_orders mengisi sisa_ditagih dari PD.fakta_po (bukan rumus sendiri)."""
    src = Path(POR.__file__).read_text()
    f = next(n for n in ast.parse(src).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "list_purchase_orders")
    teks = ast.get_source_segment(src, f)
    assert "fakta = await PD.fakta_po(" in teks and '"sisa_ditagih": float(f["sisa_ditagih"])' in teks


@pytest.mark.asyncio
async def test_unbilled_sql_himpunan_dan_pagar_tenant():
    db = DB([])
    await PA.unbilled(db, TENANT)
    sql, args = db.sql[0], db.args[0]
    assert args == (TENANT, ["draft", "cancelled", "closed"])
    assert "po.status <> ALL($2::text[])" in sql and "po.tenant_id = $1" in sql
    assert "COALESCE(v.name, po.vendor_name)" in sql and "v.tenant_id = po.tenant_id" in sql
    assert "COALESCE(b.status_v2, b.status) = 'draft'" in sql and "b.tenant_id = po.tenant_id" in sql
    assert "bi.purchase_order_item_id = i.id" in sql       # tautan SAMA dengan quantity_billed (SQL_QTY_TAGIH)


@pytest.mark.asyncio
async def test_unbilled_dipotong_50_total_tetap_semua():
    d = await PA.unbilled(DB([_po(1000 + i, nomor=f"PO-{i:03d}") for i in range(51)]), TENANT)
    assert d["count"] == 51 and len(d["rows"]) == 50 and d["truncated"] is True
    assert d["total"] == float(sum(1000 + i for i in range(51)))
    assert d["rows"][0]["po_number"] == "PO-050"


# ---------- q=top_vendors ----------

@pytest.mark.asyncio
async def test_top_vendors_tanpa_draf_dan_batal():
    vid, pid = uuid.uuid4(), uuid.uuid4()
    db = DB(agregat=[{"vendor_id": vid, "vendor_name": "Bahan Jaya", "total": D("5000000"), "count": 2, "po_ids": [pid]}])
    d = await PA.top_vendors(db, TENANT, "2026-10-01", "2026-10-31", None)
    assert d == {"period": {"from": "2026-10-01", "to": "2026-10-31"},
                 "rows": [{"vendor_id": str(vid), "vendor_name": "Bahan Jaya", "total": 5000000.0, "count": 2, "po_ids": [str(pid)]}]}
    sql = db.sql[0]
    assert "po.status NOT IN ('draft', 'cancelled')" in sql and "po.po_date BETWEEN $2 AND $3" in sql
    assert "COALESCE(v.name, po.vendor_name)" in sql and "LIMIT $4" in sql
    assert db.args[0] == (TENANT, date(2026, 10, 1), date(2026, 10, 31), 3)


# ---------- q=vendor_status ----------

@pytest.mark.asyncio
async def test_vendor_status_404_vendor_lain_tenant():
    db = DB(vendor=None)
    vid = str(uuid.uuid4())
    with pytest.raises(HTTPException) as e:
        await PA.vendor_status(db, TENANT, vid)
    assert e.value.status_code == 404
    assert db.args[0] == (uuid.UUID(vid), TENANT)             # pagar tenant di cek vendor


@pytest.mark.asyncio
async def test_vendor_status_per_status_dan_sisa_hanya_berjalan():
    a, b = _po(1000000), _po(2000000, status="partial_billed", ditagih=1500000)
    batal, selesai, draf = _po(700000, status="cancelled"), _po(300000, status="closed"), _po(50000, status="draft")
    db = DB([a, b, batal, selesai, draf], vendor={"name": "Bahan Jaya Kini"})
    vid = str(uuid.uuid4())
    d = await PA.vendor_status(db, TENANT, vid)
    assert d["vendor_name"] == "Bahan Jaya Kini" and d["vendor_id"] == vid
    assert {s["status"]: (s["count"], s["total"]) for s in d["by_status"]} == {
        "sent": (1, 1000000.0), "partial_billed": (1, 2000000.0), "cancelled": (1, 700000.0),
        "closed": (1, 300000.0), "draft": (1, 50000.0)}
    assert d["unbilled_total"] == 1500000.0                   # 1 jt + 0,5 jt; batal/selesai/draf tak ikut
    fakta_args = next(a for s, a in zip(db.sql, db.args) if _kueri_fakta(s))
    assert set(fakta_args[1]) == {a["id"], b["id"]}


# ---------- q=receive_window ----------

@pytest.mark.asyncio
async def test_receive_window():
    rows = [_po(100000 * (i + 1), nomor=f"PO-{i:03d}", tiba=date(2026, 11, 3)) for i in range(51)]
    db = DB(rows, tanpa_tiba=4)
    d = await PA.receive_window(db, TENANT, "2026-11-01", "2026-11-07")
    assert d["count"] == 51 and len(d["rows"]) == 50 and d["truncated"] is True
    assert d["total"] == float(sum(100000 * (i + 1) for i in range(51)))
    assert d["without_expected_date_count"] == 4 and d["rows"][0]["expected_date"] == "2026-11-03"
    assert "po.expected_date BETWEEN $3 AND $4" in db.sql[0] and "ORDER BY po.expected_date, po.po_number" in db.sql[0]
    assert db.args[0] == (TENANT, ["draft", "cancelled", "closed"], date(2026, 11, 1), date(2026, 11, 7))
    assert "po.expected_date IS NULL" in db.sql[1]


# ---------- validasi 400: paritas dengan so_agregat ----------

KASUS_400 = [
    ({}, {}),
    ({"dari": "2026-9-1", "sampai": "2026-09-30"}, {}),
    ({"dari": "2026-09-30", "sampai": "2026-09-01"}, {}),
    ({"dari": "2025-01-01", "sampai": "2026-01-02"}, {}),
    ({"dari": "2026-09-01", "sampai": "2026-09-30", "limit": "0"}, {}),
    ({"dari": "2026-09-01", "sampai": "2026-09-30", "limit": "11"}, {}),
    ({"dari": "2026-09-01", "sampai": "2026-09-30", "limit": "tiga"}, {}),
]
PASANGAN = {"top_customers": "top_vendors", "ship_window": "receive_window", "customer_status": "vendor_status"}


async def _detail(f, *a, **kw):
    db = DB()
    with pytest.raises(HTTPException) as e:
        await f(db, TENANT, *a, **kw)
    assert db.sql == []                                        # ditolak SEBELUM SQL
    return e.value.status_code, e.value.detail


@pytest.mark.parametrize("kw", [k for k, _ in KASUS_400] + [{"dari": "2026-09-01"}])
@pytest.mark.parametrize("q_so", ["top_customers", "ship_window"])
@pytest.mark.asyncio
async def test_400_rentang_dan_limit_identik_dengan_so(q_so, kw):
    if q_so == "ship_window" and "limit" in kw:
        pytest.skip("ship_window tak memakai limit")
    so = await _detail(SA.agregat, q_so, **kw)
    po = await _detail(PA.agregat, PASANGAN[q_so], **kw)
    assert so[0] == po[0] == 400 and so[1] == po[1]


@pytest.mark.parametrize("so_kw,po_kw,nama", [
    ({}, {}, None), ({"customer_id": "bukan-uuid"}, {"vendor_id": "bukan-uuid"}, ("customer_id", "vendor_id"))])
@pytest.mark.asyncio
async def test_400_id_pihak_paritas(so_kw, po_kw, nama):
    so = await _detail(SA.agregat, "customer_status", **so_kw)
    po = await _detail(PA.agregat, "vendor_status", **po_kw)
    assert so[0] == po[0] == 400 and so[1].replace("customer_id", "vendor_id") == po[1]


@pytest.mark.parametrize("q", [None, "apa", "uninvoiced", "top_customers"])
@pytest.mark.asyncio
async def test_q_salah_400_tanpa_sql(q):
    st, det = await _detail(PA.agregat, q)
    assert st == 400 and det == "Parameter 'q' harus salah satu: unbilled, top_vendors, vendor_status, receive_window."


def test_pembantu_validasi_adalah_milik_so_agregat():
    """Paritas lewat SATU sumber, bukan salinan: pembantu 400 diimpor dari so_agregat."""
    for n in ("rentang", "batas", "_uuid", "_400", "BATAS_BARIS"):
        assert getattr(PA, n) is getattr(SA, n), n


# ---------- penyambungan: rute, HTTP, izin ----------

def _app():
    app = FastAPI()
    app.include_router(POR.router, prefix="/api/purchase-orders")
    return app


def test_rute_aggregate_menang_atas_po_id():
    scope = {"type": "http", "method": "GET", "path": "/api/purchase-orders/aggregate", "root_path": ""}
    ep = next(r.endpoint for r in _app().router.routes if r.matches(scope)[0] == Match.FULL)
    assert ep.__name__ == "get_purchase_order_aggregate"
    jalur = [(r.path, sorted(r.methods)) for r in POR.router.routes]
    assert jalur.index(("/aggregate", ["GET"])) < jalur.index(("/{po_id}", ["GET"]))


@pytest.fixture
def klien(monkeypatch):
    db = DB([_po(250000, nomor="PO-X")])

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
    monkeypatch.setattr(POR, "get_pool", pool)
    app = _app()

    @app.middleware("http")
    async def user(request, call_next):
        request.state.user = {"tenant_id": TENANT, "user_id": str(uuid.uuid4())}
        return await call_next(request)
    return TestClient(app)


def test_http_aggregate_200_dan_400(klien):
    r = klien.get("/api/purchase-orders/aggregate?q=unbilled")
    assert r.status_code == 200, r.text
    assert r.json()["q"] == "unbilled" and r.json()["data"]["total"] == 250000.0
    r = klien.get("/api/purchase-orders/aggregate?q=top_vendors&from=2026-09-30&to=2026-09-01")
    assert r.status_code == 400 and "from" in r.json()["detail"]
    assert klien.get("/api/purchase-orders/aggregate?q=hapus").status_code == 400


AGG = "/api/purchase-orders/aggregate"
POLA_AGG = r"^/api/purchase\-orders/aggregate$"


def test_izin_pola_eksplisit_menang_dan_sama_dengan_summary():
    pertama = next(p for p, m, _, _ in pm.ROUTE_PERMISSIONS if "GET" in m and re.match(p, AGG))
    assert pertama == POLA_AGG                                 # bukan catch-all /[^/]+ di bawahnya
    mw = pm.PermissionMiddleware(app=None)
    assert mw._find_permission(AGG, "GET") == mw._find_permission("/api/purchase-orders/summary", "GET") == ("purchase_order", "R")


class Engine(pec.PolicyEngineClient):
    """can() ASLI; hanya sumber data DB yang diganti (pola test_izin_unapply_reklas)."""

    def __init__(self, peran, izin_peran):
        super().__init__(pool=None)
        self.peran, self.izin_peran = peran, izin_peran

    async def get_user_context(self, user_id, tenant_id, subscription_role):
        return pec.UserContext(user_id=user_id, tenant_id=tenant_id, subscription_role=subscription_role,
                               business_role_id="peran-1", business_role_code=self.peran, membership_active=True)

    async def _get_role_permissions(self, role_id):
        return self.izin_peran

    async def _get_user_overrides(self, user_id, tenant_id):
        return {}


def _dispatch(monkeypatch, engine):
    monkeypatch.setattr(pm, "get_policy_engine", lambda: engine)
    scope = {"type": "http", "method": "GET", "path": AGG, "headers": [], "query_string": b"q=unbilled",
             "state": {"user": {"user_id": str(uuid.uuid4()), "tenant_id": TENANT, "role": "USER"}}}
    dipanggil = []

    async def call_next(r):
        dipanggil.append(1)
        return Response("HANDLER", status_code=299)

    resp = asyncio.run(pm.PermissionMiddleware(app=None).dispatch(Request(scope), call_next))
    return resp.status_code, bool(dipanggil)


PO_MOD = pec.normalize_module_name("purchase_order")


def test_runtime_tanpa_r_purchase_order_403(monkeypatch):
    assert _dispatch(monkeypatch, Engine("STAFF", {PO_MOD: ["C", "U", "P"]})) == (403, False)


def test_runtime_dengan_r_purchase_order_lolos(monkeypatch):
    assert _dispatch(monkeypatch, Engine("STAFF", {PO_MOD: ["R"]})) == (299, True)


def test_runtime_owner_lolos(monkeypatch):
    assert _dispatch(monkeypatch, Engine("OWNER", {})) == (299, True)
