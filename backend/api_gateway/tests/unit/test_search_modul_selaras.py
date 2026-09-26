"""/api/search — modul otorisasi per grup = modul rute daftarnya (26 Sep 2026).

Dulu quotes/proformas/deliveries -> sales_order, customer_deposits ->
receive_payment, credit_notes -> sales_invoice, padahal rute daftarnya memakai
quote/proforma/sales_invoice/customer_deposit/credit_note. Akibat: pencarian
menampilkan dokumen yang daftarnya 403 (peran VIEWER: INVOICE R tanpa
CREDIT_NOTE -> nota kredit muncul di pencarian) atau menyembunyikan yang boleh.
"""
import pytest
from types import SimpleNamespace
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.routers import search as SR
from app.middleware.permission_middleware import PermissionMiddleware

RUTE_DAFTAR = {
    "customers": "/api/customers", "vendors": "/api/vendors",
    "sales_invoices": "/api/sales-invoices", "sales_orders": "/api/sales-orders",
    "quotes": "/api/quotes", "proformas": "/api/proformas",
    "deliveries": "/api/deliveries", "receive_payments": "/api/receive-payments",
    "customer_deposits": "/api/customer-deposits", "credit_notes": "/api/credit-notes",
}


def test_setiap_grup_punya_rute_daftar():
    assert set(SR.GROUPS) == set(RUTE_DAFTAR), "grup pencarian baru wajib dipetakan ke rute daftarnya"


@pytest.mark.parametrize("grup", sorted(RUTE_DAFTAR))
def test_modul_grup_sama_dengan_rute_daftar(grup):
    izin = PermissionMiddleware(app=None)._find_permission(RUTE_DAFTAR[grup], "GET")
    assert izin is not None, RUTE_DAFTAR[grup]
    assert SR.GROUPS[grup][0] == izin[0], (grup, SR.GROUPS[grup][0], izin)


class _Eng:
    """Meniru peran VIEWER nyata: INVOICE/BILL/EXPENSE/REPORT R saja."""

    async def get_user_context(self, **k):
        return SimpleNamespace()

    async def can(self, ctx, aksi, modul):
        return modul in ("sales_invoice", "purchase_invoice", "expense", "report")


class _Conn:
    def __init__(self):
        self.sql = []

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a):
        pass

    async def fetch(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        return []


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return self.c


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": "u", "tenant_id": "t", "role": "ADMIN"}
        return await nxt(req)


def test_viewer_tak_mencari_nota_kredit(monkeypatch):
    c = _Conn()

    async def pool():
        return _Pool(c)

    monkeypatch.setattr(SR, "get_policy_engine", lambda: _Eng())
    monkeypatch.setattr(SR, "get_pool", pool)
    app = FastAPI()
    app.include_router(SR.router, prefix="/api/search")
    app.add_middleware(_SetUser)
    r = TestClient(app).get("/api/search?q=abc")
    assert r.status_code == 200, r.text
    dijalankan = " ".join(c.sql)
    assert "FROM credit_notes" not in dijalankan
    assert "FROM sales_invoices" in dijalankan  # kontrol positif: yang boleh tetap dicari
