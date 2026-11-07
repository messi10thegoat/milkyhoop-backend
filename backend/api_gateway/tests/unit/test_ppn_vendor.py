"""BUG-030-BE (putusan pemilik LANGSUNG 5 Nov 2026 "Peringatan saja"): vendor tercatat bukan PKP + PPN di Tinjau PO/tagihan =
PERINGATAN non-fatal (data.peringatan), tidak memblok (ok tetap true)."""
import asyncio
import inspect
import uuid

import pytest

from app.routers import bills as RB
from app.routers import purchase_orders as RPO
from app.services import ppn_vendor as PV

V = uuid.uuid4()


class _C:
    def __init__(self, pkp):
        self.pkp, self.tanya = pkp, []

    async def fetchrow(self, sql, *a):
        self.tanya.append((sql, a))
        if self.pkp == "tak_ada":
            return None
        return {"is_pkp": self.pkp}


@pytest.mark.parametrize("pkp, pajak, ada", [
    (False, 1867800, True), (None, "0.01", True), (True, 1867800, False),
    (False, 0, False), (False, None, False), ("tak_ada", 100, False)])
def test_peringatan(pkp, pajak, ada):
    c = _C(pkp)
    p = asyncio.run(PV.peringatan(c, "t1", V, pajak))
    assert bool(p) == ada
    if ada:
        assert p == [{"code": "VENDOR_BUKAN_PKP", "message": PV.PESAN, "field": "vendor_id"}]
        assert c.tanya[0][0] == "SELECT is_pkp FROM vendors WHERE id = $1 AND tenant_id = $2" and c.tanya[0][1] == (V, "t1")


def test_tanpa_vendor_atau_vendor_tak_sah_tanpa_baca():
    c = _C(False)
    assert asyncio.run(PV.peringatan(c, "t1", None, 100)) == [] and asyncio.run(PV.peringatan(c, "t1", "bukan-uuid", 100)) == []
    assert c.tanya == []


def test_kedua_kalkulator_memasang_peringatan_tanpa_memblok():
    po = inspect.getsource(RPO.calculate_purchase_order)
    assert 'PPNV.peringatan(conn, ctx["tenant_id"], body.vendor_id, h["header"]["tax_amount"])' in po
    assert '"ok": not bloks, "blocks": PR.untuk_respons(bloks), "peringatan": peringatan' in po
    bi = inspect.getsource(RB.calculate_bill_totals)
    assert 'PPNV.peringatan(conn, ctx["tenant_id"], req.get("vendor_id"), h["akhir"]["tax_amount"])' in bi
    assert '"ok": not bloks, "blocks": bloks, "peringatan": peringatan' in bi


def test_rute_po_calculate_vendor_bukan_pkp_peringatan_ok_tetap_true(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class _K:
        async def fetchrow(self, sql, *a):
            if "FROM vendors" in sql and "is_pkp" in sql:
                return {"is_pkp": False}
            if "FROM vendors" in sql:
                return {"id": V, "name": "CV Mitra Pangan Sulut"}
            raise AssertionError(sql[:80])

        async def fetchval(self, sql, *a):  # BUG-088: /calculate menyelesaikan faktor DPP (kode masukan 11% = DPP penuh)
            if "FROM tax_codes" in sql and "direction = 'input'" in sql:
                return None
            raise AssertionError(sql[:80])

        async def fetch(self, sql, *a):
            if "FROM tax_codes" in sql:
                return [{"dpp_factor_num": 1, "dpp_factor_den": 1}]
            raise AssertionError(sql[:80])

    class _P:
        def acquire(self):
            class _A:
                async def __aenter__(s):
                    return _K()

                async def __aexit__(s, *e):
                    return False
            return _A()

    async def pool():
        return _P()
    monkeypatch.setattr(RPO, "get_pool", pool)
    app = FastAPI()

    @app.middleware("http")
    async def masuk(request, call_next):
        request.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
        return await call_next(request)
    app.include_router(RPO.router, prefix="/api/purchase-orders")
    body = {"vendor_id": str(V), "vendor_name": "CV Mitra Pangan Sulut", "po_date": "2026-11-05", "tax_rate": 11,
            "items": [{"description": "Bimoli 2L", "quantity": 50, "unit_price": 210000}]}
    r = TestClient(app).post("/api/purchase-orders/calculate", json=body)
    d = r.json()["data"]
    assert r.status_code == 200 and d["ok"] is True and [p["code"] for p in d["peringatan"]] == ["VENDOR_BUKAN_PKP"]
