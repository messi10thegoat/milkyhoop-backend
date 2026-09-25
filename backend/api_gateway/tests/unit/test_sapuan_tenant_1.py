"""Sapuan tenant 1 (26 Sep 2026) — kueri tabel-tenant tanpa predikat tenant (luar unified_agent).

Peran app = postgres BYPASSRLS -> set_config('app.tenant_id') BUKAN pagar.
Alat sapuan: /root/logs/klasifikasi_tenant.py (+ daftar BE2); 1044 literal ->
47 diperiksa tangan -> 0 bocor aktif, 5 laten diperbaiki di sini:
F1 fixed-assets DELETE categories/{id} + GET {asset_id}/maintenance
F2 items create/update: cek barcode duplikat lintas-tenant (409 palsu + oracle)
F3 team_members get_my_permissions fallback: roles by code tanpa tenant
F4 bank_reconciliation import_statement: chat_workflow_state tanpa tenant
F5 services/approval_state_service.py DIHAPUS (0 impor; escalate tanpa auth)
F6 (pengerasan) riwayat faktur: audit_logs + tenant_id
"""
import ast
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.routers import fixed_assets as FA

APP = Path(__file__).parents[2] / "app"
TA, TB = "tenant-a", "tenant-b"
KAT_A = "00000000-0000-0000-0000-0000000000a1"
ASET_A = "00000000-0000-0000-0000-0000000000a2"


# ---------- F1 perilaku, dua tenant ----------

class _Conn:
    """Kategori & aset milik TA saja."""

    def __init__(self):
        self.tulis = []
        self.fetch_args = []

    async def execute(self, sql, *a):
        s = " ".join(sql.split())
        if s.startswith("SELECT set_config"):
            return
        self.tulis.append((s, a))

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split())
        if "FROM asset_categories" in s:
            return 1 if (str(a[0]) == KAT_A and "tenant_id = $2" in s and a[1] == TA) else None
        if "FROM fixed_assets" in s:
            return 0
        raise AssertionError(s)

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        self.fetch_args.append((s, a))
        milik = "fa.tenant_id = $2" in s and a[1] == TA
        return [] if not milik else []


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return _Acq(self.c)


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": "u", "tenant_id": req.headers["x-t"], "role": "ADMIN"}
        return await nxt(req)


@pytest.fixture
def uji(monkeypatch):
    c = _Conn()

    async def pool():
        return _Pool(c)

    monkeypatch.setattr(FA, "get_pool", pool)
    app = FastAPI()
    app.include_router(FA.router, prefix="/api/fixed-assets")
    app.add_middleware(_SetUser)
    return TestClient(app), c


def test_hapus_kategori_tenant_lain_404_nol_tulis(uji):
    k, c = uji
    r = k.delete(f"/api/fixed-assets/categories/{KAT_A}", headers={"x-t": TB})
    assert r.status_code == 404, r.text
    assert c.tulis == []


def test_hapus_kategori_sendiri_dengan_pagar(uji):
    k, c = uji
    r = k.delete(f"/api/fixed-assets/categories/{KAT_A}", headers={"x-t": TA})
    assert r.status_code == 200, r.text
    assert len(c.tulis) == 1
    sql, args = c.tulis[0]
    assert sql.startswith("DELETE FROM asset_categories") and "tenant_id = $2" in sql and args[1] == TA


def test_perawatan_aset_dipagari_aset_induk(uji):
    k, c = uji
    k.get(f"/api/fixed-assets/{ASET_A}/maintenance", headers={"x-t": TB})
    sql, args = c.fetch_args[-1]
    assert "JOIN fixed_assets fa ON fa.id = am.asset_id AND fa.tenant_id = $2" in sql
    assert "v.tenant_id = $2" in sql
    assert args[1] == TB  # tenant PEMANGGIL, bukan dari path


# ---------- F2–F4, F6: penjaga literal ----------

def _literal(path):
    pohon = ast.parse((APP / path).read_text(encoding="utf-8"))
    for n in ast.walk(pohon):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield " ".join(n.value.split())


@pytest.mark.parametrize("path,penanda,pagar", [
    ("routers/items.py", "SELECT id FROM products WHERE barcode = $1", "tenant_id = $"),
    ("routers/team_members.py", "WHERE r.code = $1 AND r.is_active = TRUE", "r.tenant_id IN ('__SYSTEM__', $2)"),
    ("routers/bank_reconciliation.py", "FROM chat_workflow_state", "tenant_id = $"),
    ("routers/bank_reconciliation.py", "UPDATE chat_workflow_state", "tenant_id = $"),
    ("routers/sales_invoices.py", "metadata->>'entity_id' = $1::text", "tenant_id = $3"),
])
def test_kueri_yang_diperbaiki_berpagar_tenant(path, penanda, pagar):
    kena = [s for s in _literal(path) if penanda in s]
    assert kena, f"penanda tak ditemukan di {path}: {penanda}"
    for s in kena:
        assert pagar in s, f"{path}: tanpa pagar tenant -> {s[:120]}"


def test_barcode_dua_tempat():
    kena = [s for s in _literal("routers/items.py") if "FROM products WHERE barcode" in s]
    assert len(kena) == 2 and all("tenant_id" in s for s in kena)


# ---------- F5 ----------

def test_layanan_approval_mati_tak_kembali():
    assert not (APP / "services/approval_state_service.py").exists()
    for f in APP.rglob("*.py"):
        assert "approval_state_service" not in f.read_text(encoding="utf-8"), f
