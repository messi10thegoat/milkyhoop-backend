"""Audit izin usul C (25 Sep 2026) — widget dashboard disaring izin BACA anggota.

Latar: /api/dashboard/* SKIP PermissionMiddleware; pagar hanya keanggotaan aktif;
FCLMiddleware tak terpasang. Putusan pemilik (via MASTER): /all + /summary =
widget tanpa izin DIHILANGKAN (bukan 0) + `omitted`; rute tunggal = 403
PERMISSION_DENIED; OWNER tak berubah. Pemetaan: services/dashboard_izin.py.

Kontrol merah SINTETIS (peran tanpa BANK/INVOICE/BILL/REPORT) di sini; peran
VIEWER NYATA diuji lewat probe read-only terpisah (butuh DB).
"""
import inspect
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.routers import dashboard as D
from app.services import dashboard_izin as I
from app.services.role_resolution import require_active_membership

SEMUA_MODUL = {"report", "sales_invoice", "purchase_invoice", "kas_bank",
               "expense", "receive_payment", "send_payment"}
PERAN = {
    "OWNER": None,  # bypass
    "KOSONG": set(),  # sintetis: tanpa BANK/INVOICE/BILL/REPORT/...
    "KAS": {"kas_bank"},
    "JUAL": {"sales_invoice"},
    "SEMUA": SEMUA_MODUL,
}


class _Ctx:
    def __init__(self, role):
        self.business_role_code = role
        self.membership_active = True
        self.business_role_id = "r-" + role


class _Eng:
    def __init__(self):
        self.panggil_ctx = 0

    async def get_user_context(self, user_id, tenant_id, subscription_role):
        self.panggil_ctx += 1
        if user_id == "MELEDAK":
            raise RuntimeError("engine mati")
        return _Ctx(user_id)

    async def can(self, ctx, action, module):
        assert action == "R"
        izin = PERAN[ctx.business_role_code]
        return True if izin is None else module in izin


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": req.headers["x-peran"], "tenant_id": "t", "role": "ADMIN"}
        return await nxt(req)


@pytest.fixture
def eng(monkeypatch):
    e = _Eng()
    monkeypatch.setattr(I, "get_policy_engine", lambda: e)
    return e


@pytest.fixture
def dipanggil(monkeypatch):
    """Sub-handler /all diganti tiruan yang MENCATAT panggilan (nol DB)."""
    catat = []

    def palsu(nama, nilai):
        async def f(*a, **k):
            catat.append(nama)
            return nilai
        return f

    async def summary_palsu(request, **k):
        catat.append("summary")
        bagian = {"laba_rugi": {"x": 1}, "piutang": {"x": 2}, "hutang": {"x": 3},
                  "kas_bank": {"x": 4}, "kpi": {"x": 5}}
        om = await I.saring_bagian_summary(request, bagian)
        return {**bagian, "generated_at": "g", "omitted": om}

    monkeypatch.setattr(D, "get_dashboard_summary", summary_palsu)
    monkeypatch.setattr(D, "get_cash_flow_trends", palsu("cashFlow", {"cf": 1}))
    monkeypatch.setattr(D, "get_top_expenses", palsu("expenses", {"ex": 1}))
    monkeypatch.setattr(D, "get_overdue_invoices", palsu("overdueInvoices", {"oi": 1}))
    monkeypatch.setattr(D, "get_overdue_bills", palsu("overdueBills", {"ob": 1}))
    monkeypatch.setattr(D, "_get_upcoming_due", palsu("upcomingDue", {"items": []}))
    monkeypatch.setattr(D, "_get_sales_today", palsu("salesToday", {"st": 1}))
    return catat


@pytest.fixture
def klien(eng):
    app = FastAPI()
    app.include_router(D.router, prefix="/api/dashboard")
    app.dependency_overrides[require_active_membership] = lambda: None
    app.add_middleware(_SetUser)
    return TestClient(app)


def _get(k, path, peran):
    return k.get("/api/dashboard" + path, headers={"x-peran": peran})


# ---------- pemetaan ----------

def test_pemetaan_widget_sesuai_putusan():
    assert I.WIDGET_MODUL == {
        "summary.laba_rugi": ("report",), "summary.kpi": ("report",),
        "summary.piutang": ("sales_invoice",), "summary.hutang": ("purchase_invoice",),
        "summary.kas_bank": ("kas_bank",), "cashFlow": ("kas_bank",),
        "expenses": ("report",), "overdueInvoices": ("sales_invoice",),
        "overdueBills": ("purchase_invoice",), "salesToday": ("sales_invoice",),
    }
    assert I.RUTE_MODUL["/cash-flow-projection"] == ("kas_bank", "sales_invoice", "purchase_invoice")
    assert I.RUTE_MODUL["/top-expenses"] == ("report",)


def test_tiap_rute_dashboard_terpetakan_atau_disaring_di_dalam():
    """Rute dashboard BARU tanpa keputusan izin = MERAH."""
    di_dalam = {"/all", "/summary", "/daily-transactions", "/health"}
    for r in D.router.routes:
        deps = [getattr(d.dependency, "__qualname__", "") for d in r.dependencies]
        punya = any(q.startswith("wajib_baca_rute") for q in deps)
        if r.path in di_dalam:
            assert not punya, r.path
            continue
        assert r.path in I.RUTE_MODUL, f"rute dashboard tanpa pemetaan izin: {r.path}"
        assert punya, f"{r.path} terpetakan tapi dependensinya tak dipasang"
    assert set(I.RUTE_MODUL) == {r.path for r in D.router.routes} - di_dalam


def test_summary_memakai_penyaring_sebelum_kembali():
    src = inspect.getsource(D.get_dashboard_summary)
    i_saring = src.index("saring_bagian_summary(request, bagian)")
    i_kembali = src.index("return DashboardSummaryResponse(")
    assert i_saring < i_kembali
    assert "**bagian" in src[i_kembali:]


# ---------- /all ----------

def test_owner_melihat_semua_tanpa_omitted(klien, dipanggil):
    b = _get(klien, "/all", "OWNER").json()
    for k in ("summary", "cashFlow", "expenses", "overdueInvoices", "overdueBills", "upcomingDue", "salesToday"):
        assert k in b, k
    assert set(b["summary"]) >= {"laba_rugi", "piutang", "hutang", "kas_bank", "kpi"}
    assert b["omitted"] == []


def test_peran_kosong_semua_widget_hilang_bukan_nol(klien, dipanggil):
    b = _get(klien, "/all", "KOSONG").json()
    for k in ("cashFlow", "expenses", "overdueInvoices", "overdueBills", "upcomingDue", "salesToday"):
        assert k not in b, k
    for k in ("laba_rugi", "piutang", "hutang", "kas_bank", "kpi"):
        assert k not in b["summary"], k
    assert "omitted" not in b["summary"]
    nama = {o["widget"] for o in b["omitted"]}
    assert nama == {"cashFlow", "expenses", "overdueInvoices", "overdueBills", "upcomingDue",
                    "salesToday", "summary.laba_rugi", "summary.piutang", "summary.hutang",
                    "summary.kas_bank", "summary.kpi"}
    # widget tanpa izin TIDAK DIJALANKAN (nol kueri), bukan dijalankan lalu dibuang
    assert dipanggil == ["summary"]


def test_peran_kas_hanya_kas(klien, dipanggil):
    b = _get(klien, "/all", "KAS").json()
    assert b["cashFlow"] == {"cf": 1}
    assert b["summary"]["kas_bank"] == {"x": 4}
    for k in ("expenses", "overdueInvoices", "overdueBills", "upcomingDue", "salesToday"):
        assert k not in b
    assert "piutang" not in b["summary"] and "laba_rugi" not in b["summary"]
    assert {"widget": "cashFlow", "modules": ["kas_bank"]} not in b["omitted"]
    assert {"widget": "expenses", "modules": ["report"]} in b["omitted"]


def test_peran_jual_upcoming_dijalankan(klien, dipanggil):
    b = _get(klien, "/all", "JUAL").json()
    assert "upcomingDue" in b and "salesToday" in b and "overdueInvoices" in b
    assert "overdueBills" not in b
    assert sorted(dipanggil) == sorted(["summary", "overdueInvoices", "upcomingDue", "salesToday"])


def test_engine_galat_gagal_tertutup(klien, dipanggil):
    b = _get(klien, "/all", "MELEDAK").json()
    assert "cashFlow" not in b and "salesToday" not in b
    assert len(b["omitted"]) == 11


# ---------- rute tunggal ----------

@pytest.mark.parametrize("jalur", sorted(I.RUTE_MODUL))
def test_rute_tunggal_403_tanpa_izin(klien, jalur):
    r = _get(klien, jalur, "KOSONG")
    assert r.status_code == 403, (jalur, r.status_code, r.text)
    d = r.json()["detail"]
    assert d["code"] == "PERMISSION_DENIED"
    assert d["required_module"] in I.RUTE_MODUL[jalur]


def test_proyeksi_butuh_ketiganya(klien):
    # punya kas saja -> tetap 403 (butuh kas + piutang + hutang)
    r = _get(klien, "/cash-flow-projection", "KAS")
    assert r.status_code == 403
    assert r.json()["detail"]["required_module"] == "sales_invoice"


@pytest.mark.asyncio
async def test_dependensi_rute_lolos_untuk_owner(eng):
    from types import SimpleNamespace
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": "OWNER", "tenant_id": "t", "role": "ADMIN"}))
    for jalur in I.RUTE_MODUL:
        await I.wajib_baca_rute(jalur)(req)  # tak raise


# ---------- per baris ----------

class _Conn:
    def __init__(self):
        self.args = []

    async def execute(self, *a):
        pass

    async def fetch(self, sql, *a):
        self.args.append(a)
        self.sql = sql
        return []


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


@pytest.mark.asyncio
@pytest.mark.parametrize("peran,harap", [("JUAL", ["invoice"]), ("SEMUA", ["invoice", "bill"]), ("OWNER", ["invoice", "bill"])])
async def test_upcoming_disaring_di_sql(eng, monkeypatch, peran, harap):
    from types import SimpleNamespace
    c = _Conn()

    async def pool():
        return _Pool(c)

    async def tgl(conn, tid):
        return "2026-09-25"

    monkeypatch.setattr(D, "get_db_pool", pool)
    monkeypatch.setattr(D, "tanggal_dokumen", tgl)
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": peran, "tenant_id": "t", "role": "ADMIN"}))
    await D._get_upcoming_due(req)
    assert c.args[-1][2] == harap
    # saringan ada DI SQL, sebelum LIMIT (bukan argumen yatim)
    s = " ".join(c.sql.split())
    assert "WHERE type = ANY($3::text[]) ORDER BY due_date ASC LIMIT 10" in s


class _ConnHarian(_Conn):
    async def fetch(self, sql, *a):
        tabel = re.search(r"FROM (\w+)", sql).group(1)
        return [{"invoice_number": "D-" + tabel, "date": "2026-09-25", "total_amount": 100,
                 "status": "posted", "customer_name": "c", "vendor_name": "v"}]


@pytest.mark.asyncio
async def test_harian_jenis_ditolak_hilang_dan_tak_dijumlah(eng, monkeypatch):
    from types import SimpleNamespace
    c = _ConnHarian()

    async def pool():
        return _Pool(c)

    monkeypatch.setattr(D, "get_db_pool", pool)
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": "JUAL", "tenant_id": "t", "role": "ADMIN"}))
    b = await D.get_daily_transactions(req, date="2026-09-25")
    assert b["sales_invoices"][0]["amount"] == 100.0
    for k in ("bills", "expenses", "receive_payments", "bill_payments"):
        assert k not in b, k
    assert set(b["summary"]) == {"total_transactions", "total_invoice_amount"}
    assert b["summary"]["total_transactions"] == 1
    assert {o["widget"] for o in b["omitted"]} == {"bills", "expenses", "receive_payments", "bill_payments"}


@pytest.mark.asyncio
async def test_harian_owner_utuh(eng, monkeypatch):
    from types import SimpleNamespace
    c = _ConnHarian()

    async def pool():
        return _Pool(c)

    monkeypatch.setattr(D, "get_db_pool", pool)
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": "OWNER", "tenant_id": "t", "role": "ADMIN"}))
    b = await D.get_daily_transactions(req, date="2026-09-25")
    assert b["summary"]["total_transactions"] == 5 and b["omitted"] == []
    assert b["summary"]["total_paid_amount"] == 100.0


def test_konteks_izin_sekali_per_permintaan(klien, dipanggil, eng):
    _get(klien, "/all", "KAS")
    assert eng.panggil_ctx == 1
