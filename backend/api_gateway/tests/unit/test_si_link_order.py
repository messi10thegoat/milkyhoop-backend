"""Tautkan faktur ke pesanan — POST /api/sales-invoices/{id}/link-order (BUG-006, 25 Sep 2026).

Putusan MASTER/Anton: beraudit, quantity_invoiced SO DIHITUNG ULANG dari faktur bertaut
(bukan ditambah), nol jurnal; pagar satu tenant + satu pelanggan + item cocok (atau pemetaan
eksplisit), faktur void ditolak, tautan ganda ditolak. Kasus nyata yang jadi acuan: SO 001-09-26
(qty 63, quantity_invoiced 63 sisa draf hantu, faktur bertaut 0) + INV-2609-0001 lepas (63).
"""
import ast
import json
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from starlette.routing import Match

from app.routers import sales_invoice_link_order as LO
from app.routers import sales_invoices as SI

TENANT = "grapgrap-manado"
CUST = uuid.uuid4()
INV = uuid.uuid4()
SO = uuid.uuid4()
PROD_A, PROD_B = uuid.uuid4(), uuid.uuid4()
II1, II2 = uuid.uuid4(), uuid.uuid4()          # baris faktur
SOI1, SOI2 = uuid.uuid4(), uuid.uuid4()        # baris SO
APP = Path(LO.__file__).resolve().parents[1]


def _inv(**k):
    d = {"status": "paid", "customer_id": CUST, "sales_order_id": None}
    d.update(k)
    return d


def _so(**k):
    d = {"status": "completed", "customer_id": CUST}
    d.update(k)
    return d


def _ii(id_, item, qty, soi=None):
    return {"id": id_, "item_id": item, "quantity": Decimal(qty), "sales_order_item_id": soi,
            "description": f"baris {id_}"}


def _soi(id_, item, qty, inv_qty):
    return {"id": id_, "item_id": item, "quantity": Decimal(qty), "quantity_invoiced": Decimal(inv_qty),
            "description": f"so {id_}"}


def _kode(e):
    return e.value.status_code, e.value.detail["code"]


# ---------- pagar (murni) ----------

@pytest.mark.parametrize("inv,so,inv_items,so_items,pemetaan,harap", [
    (_inv(status="void"), _so(), [_ii(II1, PROD_A, 63)], [_soi(SOI1, PROD_A, 63, 63)], None, (400, "TAUTAN_FAKTUR_VOID")),
    (_inv(), _so(status="cancelled"), [_ii(II1, PROD_A, 63)], [_soi(SOI1, PROD_A, 63, 63)], None, (400, "TAUTAN_SO_BATAL")),
    (_inv(customer_id=uuid.uuid4()), _so(), [_ii(II1, PROD_A, 63)], [_soi(SOI1, PROD_A, 63, 63)], None, (400, "TAUTAN_PELANGGAN_BEDA")),
    (_inv(customer_id=None), _so(), [_ii(II1, PROD_A, 63)], [_soi(SOI1, PROD_A, 63, 63)], None, (400, "TAUTAN_PELANGGAN_BEDA")),
    (_inv(), _so(customer_id=None), [_ii(II1, PROD_A, 63)], [_soi(SOI1, PROD_A, 63, 63)], None, (400, "TAUTAN_PELANGGAN_BEDA")),
    (_inv(sales_order_id=SO), _so(), [_ii(II1, PROD_A, 63)], [_soi(SOI1, PROD_A, 63, 63)], None, (409, "TAUTAN_SUDAH_ADA")),
    (_inv(), _so(), [_ii(II1, PROD_A, 63, soi=uuid.uuid4())], [_soi(SOI1, PROD_A, 63, 63)], None, (409, "TAUTAN_SUDAH_ADA")),
    (_inv(), _so(), [_ii(II1, PROD_B, 63)], [_soi(SOI1, PROD_A, 63, 63)], None, (400, "TAUTAN_TAK_ADA_BARIS_COCOK")),
    (_inv(), _so(), [_ii(II1, None, 63)], [_soi(SOI1, PROD_A, 63, 63)], None, (400, "TAUTAN_TAK_ADA_BARIS_COCOK")),
    (_inv(), _so(), [_ii(II1, PROD_A, 10)], [_soi(SOI1, PROD_A, 5, 0), _soi(SOI2, PROD_A, 5, 0)], None, (400, "TAUTAN_PERLU_PEMETAAN")),
    (_inv(), _so(), [_ii(II1, PROD_B, 5)], [_soi(SOI1, PROD_A, 5, 0)], [(II1, SOI1)], (400, "TAUTAN_ITEM_BEDA")),
    (_inv(), _so(), [_ii(II1, PROD_A, 5)], [_soi(SOI1, PROD_A, 5, 0)], [(II1, SOI1), (II1, SOI1)], (400, "TAUTAN_BARIS_GANDA")),
    (_inv(), _so(), [_ii(II1, PROD_A, 5)], [_soi(SOI1, PROD_A, 5, 0)], [(uuid.uuid4(), SOI1)], (400, "TAUTAN_BARIS_TIDAK_SAH")),
    (_inv(), _so(), [_ii(II1, PROD_A, 5)], [_soi(SOI1, PROD_A, 5, 0)], [(II1, uuid.uuid4())], (400, "TAUTAN_BARIS_TIDAK_SAH")),
])
def test_pagar_menolak(inv, so, inv_items, so_items, pemetaan, harap):
    with pytest.raises(HTTPException) as e:
        LO.rencanakan_tautan(inv, inv_items, so, so_items, pemetaan)
    assert _kode(e) == harap


def test_pemetaan_otomatis_per_item_dan_baris_lepas_dibiarkan():
    items = [_ii(II1, PROD_A, 63), _ii(II2, None, 1)]      # baris kedua (mis. jasa lepas) tanpa item
    assert LO.rencanakan_tautan(_inv(), items, _so(), [_soi(SOI1, PROD_A, 63, 63)], None) == [(II1, SOI1)]


def test_pemetaan_eksplisit_menyelesaikan_ambigu():
    so_items = [_soi(SOI1, PROD_A, 5, 0), _soi(SOI2, PROD_A, 5, 0)]
    items = [_ii(II1, PROD_A, 5), _ii(II2, PROD_A, 5)]
    assert LO.rencanakan_tautan(_inv(), items, _so(), so_items, [(II1, SOI2), (II2, SOI1)]) == [(II1, SOI2), (II2, SOI1)]


# ---------- hitung ulang (bukan tambah) ----------

def test_kasus_rahayu_63_tetap_63_bukan_126():
    so_items = [_soi(SOI1, PROD_A, 63, 63)]            # 63 = sisa draf hantu
    b = LO.hitung_ulang(so_items, {}, [_ii(II1, PROD_A, 63)], [(II1, SOI1)])
    assert (b[0]["before"], b[0]["after"]) == (Decimal(63), Decimal(63))


def test_baris_so_lain_ikut_dikoreksi_ke_faktur_bertaut():
    so_items = [_soi(SOI1, PROD_A, 10, 0), _soi(SOI2, PROD_B, 7, 7)]   # SOI2: 7 hantu, bertaut 3
    b = LO.hitung_ulang(so_items, {SOI2: Decimal(3)}, [_ii(II1, PROD_A, 4)], [(II1, SOI1)])
    assert [(x["before"], x["after"]) for x in b] == [(Decimal(0), Decimal(4)), (Decimal(7), Decimal(3))]


def test_melebihi_pesanan_ditolak():
    so_items = [_soi(SOI1, PROD_A, 63, 63)]
    with pytest.raises(HTTPException) as e:
        LO.hitung_ulang(so_items, {SOI1: Decimal(10)}, [_ii(II1, PROD_A, 63)], [(II1, SOI1)])
    assert _kode(e) == (400, "TAUTAN_MELEBIHI_PESANAN")


# ---------- handler dengan DB tiruan ----------

class DB:
    def __init__(self, inv=None, so=None, so_items=None, inv_items=None, lain=()):
        self.inv = inv if inv is not None else {"id": INV, "invoice_number": "INV-2609-0001", **_inv()}
        self.so = so if so is not None else {"id": SO, "order_number": "001-09-26", **_so()}
        self.so_items = so_items if so_items is not None else [_soi(SOI1, PROD_A, 63, 63)]
        self.inv_items = inv_items if inv_items is not None else [_ii(II1, PROD_A, 63)]
        self.lain = list(lain)
        self.tulis = []
        self.sql = []

    async def execute(self, sql, *a):
        self.sql.append(sql)
        if "pg_advisory_xact_lock" not in sql:
            self.tulis.append((sql, a))

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        if "FROM sales_invoices" in sql:
            assert a == (INV, TENANT) and "FOR UPDATE" in sql
            return self.inv or None
        if "FROM sales_orders" in sql:
            assert a[1] == TENANT
            return self.so if a[0] == SO else None
        raise AssertionError(sql)

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "FROM sales_order_items" in sql:
            assert "FOR UPDATE" in sql
            return self.so_items
        if "FROM sales_invoice_items sii" in sql:
            assert "si.status <> 'void'" in sql and "si.id <> $3" in sql and a[0] == TENANT and a[2] == INV
            return self.lain
        if "FROM sales_invoice_items" in sql:
            return self.inv_items
        raise AssertionError(sql)

    def transaction(self):
        class T:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *e):
                return False
        return T()


class Pool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        db = self.db

        class A:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *e):
                return False
        return A()


@pytest.fixture
def pasang(monkeypatch):
    def _p(db):
        async def _pool():
            return Pool(db)
        monkeypatch.setattr(LO, "get_pool", _pool)
        return db
    return _p


def _req(badan):
    async def _j():
        return badan
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": str(uuid.uuid4())}),
                           headers={}, json=_j)


@pytest.mark.asyncio
async def test_dry_run_nol_tulisan(pasang):
    db = pasang(DB())
    out = await LO.tautkan_ke_pesanan(str(INV), _req({"sales_order_id": str(SO), "dry_run": True}))
    assert out["dry_run"] is True and db.tulis == []
    assert out["so_lines"][0]["quantity_invoiced_before"] == 63 and out["so_lines"][0]["quantity_invoiced_after"] == 63
    assert out["lines"] == [{"invoice_item_id": str(II1), "sales_order_item_id": str(SOI1),
                             "description": f"baris {II1}", "quantity": 63.0}]


@pytest.mark.asyncio
async def test_tulis_tautan_hitung_ulang_audit_tanpa_jurnal(pasang):
    so_items = [_soi(SOI1, PROD_A, 63, 63), _soi(SOI2, PROD_B, 7, 7)]
    db = pasang(DB(so_items=so_items, lain=[{"soi": SOI2, "q": Decimal(3)}]))
    out = await LO.tautkan_ke_pesanan(str(INV), _req({"sales_order_id": str(SO)}))
    assert out["dry_run"] is False
    sqls = [s for s, _ in db.tulis]
    assert any("UPDATE sales_invoices SET sales_order_id" in s for s in sqls)
    assert any("UPDATE sales_invoice_items SET sales_order_item_id" in s for s in sqls)
    upd_soi = [(a[0], a[1]) for s, a in db.tulis if "UPDATE sales_order_items" in s]
    assert upd_soi == [(Decimal(3), SOI2)]          # SOI1 63->63 tak disentuh; SOI2 7->3 dikoreksi
    audit = [a for s, a in db.tulis if "INSERT INTO audit_logs" in s]
    assert len(audit) == 1
    meta = json.loads(audit[0][4])
    assert meta["entity_type"] == "SALES_INVOICE" and meta["entity_id"] == str(INV)  # dibaca /{id}/history
    assert audit[0][3] == TENANT
    assert not any("journal" in s.lower() for s in sqls)   # nol jurnal


@pytest.mark.asyncio
async def test_faktur_tenant_lain_404_tanpa_tulis(pasang):
    db = pasang(DB(inv={}))
    with pytest.raises(HTTPException) as e:
        await LO.tautkan_ke_pesanan(str(INV), _req({"sales_order_id": str(SO)}))
    assert _kode(e) == (404, "TAUTAN_FAKTUR_TIDAK_ADA") and db.tulis == []


@pytest.mark.asyncio
async def test_so_tenant_lain_404(pasang):
    db = pasang(DB())
    with pytest.raises(HTTPException) as e:
        await LO.tautkan_ke_pesanan(str(INV), _req({"sales_order_id": str(uuid.uuid4())}))
    assert _kode(e) == (404, "TAUTAN_SO_TIDAK_ADA") and db.tulis == []


@pytest.mark.asyncio
async def test_pagar_gagal_nol_tulisan(pasang):
    db = pasang(DB(inv={"id": INV, "invoice_number": "X", **_inv(sales_order_id=SO)}))
    with pytest.raises(HTTPException) as e:
        await LO.tautkan_ke_pesanan(str(INV), _req({"sales_order_id": str(SO)}))
    assert _kode(e) == (409, "TAUTAN_SUDAH_ADA") and db.tulis == []


@pytest.mark.parametrize("badan,kode", [
    (None, "TAUTAN_BADAN_TIDAK_SAH"),
    ({}, "TAUTAN_SO_TIDAK_SAH"),
    ({"sales_order_id": "x"}, "TAUTAN_SO_TIDAK_SAH"),
    ({"sales_order_id": str(SO), "lines": []}, "TAUTAN_BADAN_TIDAK_SAH"),
    ({"sales_order_id": str(SO), "lines": [{"invoice_item_id": "x", "sales_order_item_id": str(SOI1)}]}, "TAUTAN_BARIS_TIDAK_SAH"),
])
def test_badan_tidak_sah(badan, kode):
    with pytest.raises(HTTPException) as e:
        LO.baca_badan(badan)
    assert _kode(e) == (400, kode)


# ---------- rute + izin ----------

def test_rute_terdaftar_di_main_dan_menang():
    pohon = ast.parse((APP / "main.py").read_text())
    inc = {}
    for n in ast.walk(pohon):
        if (isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "include_router" and n.args
                and isinstance(n.args[0], ast.Attribute) and isinstance(n.args[0].value, ast.Name)
                and n.args[0].value.id in ("sales_invoice_link_order", "sales_invoices")):
            pre = [k.value.value for k in n.keywords if k.arg == "prefix"]
            inc[n.args[0].value.id] = (n.lineno, pre[0] if pre else "")
    assert set(inc) == {"sales_invoice_link_order", "sales_invoices"}
    app = FastAPI()
    for nama, (_, pre) in sorted(inc.items(), key=lambda kv: kv[1][0]):
        app.include_router({"sales_invoice_link_order": LO, "sales_invoices": SI}[nama].router, prefix=pre)
    scope = {"type": "http", "method": "POST", "path": f"/api/sales-invoices/{INV}/link-order", "root_path": ""}
    ep = next(r.endpoint for r in app.router.routes if r.matches(scope)[0] == Match.FULL)
    assert ep is LO.tautkan_ke_pesanan


def test_izin_link_order():
    from app.middleware.permission_middleware import PermissionMiddleware
    pm = PermissionMiddleware(app=None)
    assert pm._find_permission(f"/api/sales-invoices/{INV}/link-order", "POST") == ("sales_invoice", "U")
