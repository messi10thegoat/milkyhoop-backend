"""GET /sales-invoices/defaults (U1c CW, 4 Okt 2026): rekening utama + jatuh tempo dari penentu YANG SAMA dengan
to-invoice (tentukan_jatuh_tempo). Tanpa DB."""
import asyncio
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import sales_invoices as SI
from app.services import default_dokumen as DD

T = "t-uji"
CUST = str(uuid.uuid4())


class _Conn:
    def __init__(self, rek=True, hari=None):
        self.rek, self.hari = rek, hari

    async def fetchval(self, q, *a):
        if "default_dp_percent" in q:
            return None
        if "payment_terms_days" in q:
            assert a[1] == T, "tenant WAJIB di kueri pelanggan"
            return self.hari
        raise AssertionError(q)

    async def fetchrow(self, q, *a):
        assert "is_default" in q and a[0] == T
        return ({"id": uuid.uuid4(), "account_name": "BCA Ops", "bank_name": "BCA", "account_number": "123",
                 "account_holder_name": "PT A"} if self.rek else None)


def test_tanpa_pelanggan_tingkat_perusahaan_jatuh_tempo_tanggal_faktur():
    d = asyncio.run(DD.default_faktur(_Conn(), T, date(2026, 10, 4)))
    assert d["due_date"] == {"value": "2026-10-04", "source": "default", "terms_days": 0}
    assert d["payment_terms_label"] is None and d["invoice_date"] == "2026-10-04"
    assert d["receiving_account"]["bank_name"] == "BCA" and d["receiving_account"]["source"] == "company_main"


def test_termin_pelanggan_30_hari():
    d = asyncio.run(DD.default_faktur(_Conn(hari=30), T, date(2026, 10, 4), CUST))
    assert d["due_date"] == {"value": "2026-11-03", "source": "customer_terms", "terms_days": 30}
    assert d["payment_terms_label"] == "NET 30"


def test_tanpa_rekening_utama_null_eksplisit():
    d = asyncio.run(DD.default_faktur(_Conn(rek=False), T, date(2026, 10, 4)))
    assert "receiving_account" in d and d["receiving_account"] is None


def test_penentu_sama_dengan_to_invoice(monkeypatch):
    """Jatuh tempo WAJIB lewat tentukan_jatuh_tempo (satu aturan semua pembuat faktur), bukan salinan."""
    from app.services import termin_bayar
    panggil = []
    async def palsu(conn, t, tgl, body, so, cust):
        panggil.append((t, tgl, body, so, cust))
        return date(2030, 1, 1), "x"
    monkeypatch.setattr(termin_bayar, "tentukan_jatuh_tempo", palsu)
    d = asyncio.run(DD.default_faktur(_Conn(), T, date(2026, 10, 4), CUST))
    assert panggil == [(T, date(2026, 10, 4), None, None, CUST)] and d["due_date"]["value"] == "2030-01-01"


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class _A:
            async def __aenter__(s): return c
            async def __aexit__(s, *x): return False
        return _A()


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": str(uuid.uuid4()), "tenant_id": T, "role": "OWNER"}))


def test_rute_tanggal_usaha_tenant_dan_uuid_salah_422(monkeypatch):
    async def gp(): return _Pool(_Conn())
    async def hari_ini(conn, t):
        assert t == T
        return date(2026, 12, 31)
    monkeypatch.setattr(SI, "get_pool", gp)
    monkeypatch.setattr(SI, "get_user_context", lambda r: r.state.user)
    monkeypatch.setattr(SI, "tanggal_dokumen", hari_ini)
    r = asyncio.run(SI.get_sales_invoice_defaults(_req()))
    assert r["data"]["invoice_date"] == "2026-12-31" and r["data"]["due_date"]["value"] == "2026-12-31"
    r = asyncio.run(SI.get_sales_invoice_defaults(_req(), None, date(2026, 10, 1)))
    assert r["data"]["invoice_date"] == "2026-10-01"
    with pytest.raises(HTTPException) as e:
        asyncio.run(SI.get_sales_invoice_defaults(_req(), "bukan-uuid"))
    assert e.value.status_code == 422


def test_rute_defaults_di_atas_invoice_id():
    """/{invoice_id} di atasnya = 'defaults' ditangkap sebagai id -> 404/422 di produksi."""
    jalur = [r.path for r in SI.router.routes if "GET" in getattr(r, "methods", set())]
    assert jalur.index("/defaults") < jalur.index("/{invoice_id}")
    rute = {r.path: r.endpoint.__name__ for r in SI.router.routes if "GET" in getattr(r, "methods", set())}
    assert rute["/defaults"] == "get_sales_invoice_defaults"


# ---------- DEFAULTS-SO-TERMS (14 Okt 2026, MASTER): ?sales_order_id= -> termin SO, penentu SAMA dengan to-invoice ----------
SOID = str(uuid.uuid4())


class _ConnSO(_Conn):
    def __init__(self, termin, hari=None, ada=True):
        super().__init__(hari=hari)
        self.termin, self.ada, self.so_q = termin, ada, []

    async def fetchrow(self, q, *a):
        if "FROM sales_orders" in q:
            assert a == (SOID, T), "SO WAJIB disaring tenant"
            self.so_q.append(q)
            return {"payment_terms": self.termin, "customer_id": uuid.UUID(CUST)} if self.ada else None
        return await super().fetchrow(q, *a)


def test_so_termin_net_menang_atas_pelanggan():
    d = asyncio.run(DD.default_faktur(_ConnSO("NET 14", hari=30), T, date(2026, 10, 4), None, SOID))
    assert d["due_date"] == {"value": "2026-10-18", "source": "so_terms", "terms_days": 14}
    assert d["payment_terms_label"] == "NET 14"


def test_so_tanpa_termin_net_jatuh_ke_pelanggan_so():
    # termin teks bebas (bukan NET) -> pelanggan SO (bukan customer_id kiriman FE)
    d = asyncio.run(DD.default_faktur(_ConnSO("DP 50% di muka", hari=30), T, date(2026, 10, 4), str(uuid.uuid4()), SOID))
    assert d["due_date"] == {"value": "2026-11-03", "source": "customer_terms", "terms_days": 30}


def test_so_tenant_lain_none_dan_rute_404(monkeypatch):
    assert asyncio.run(DD.default_faktur(_ConnSO("NET 7", ada=False), T, date(2026, 10, 4), None, SOID)) is None
    async def gp(): return _Pool(_ConnSO("NET 7", ada=False))
    async def hari_ini(conn, t): return date(2026, 10, 14)
    monkeypatch.setattr(SI, "get_pool", gp)
    monkeypatch.setattr(SI, "get_user_context", lambda r: r.state.user)
    monkeypatch.setattr(SI, "tanggal_dokumen", hari_ini)
    with pytest.raises(HTTPException) as e:
        asyncio.run(SI.get_sales_invoice_defaults(_req(), None, None, SOID))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        asyncio.run(SI.get_sales_invoice_defaults(_req(), None, None, "bukan-uuid"))
    assert e.value.status_code == 422


def test_so_masukan_penentu_sama_dengan_to_invoice(monkeypatch):
    from app.services import termin_bayar
    panggil = []
    async def palsu(conn, t, tgl, body, so, cust):
        panggil.append((t, tgl, body, so, cust))
        return date(2030, 1, 1), "so_terms"
    monkeypatch.setattr(termin_bayar, "tentukan_jatuh_tempo", palsu)
    asyncio.run(DD.default_faktur(_ConnSO("NET 21"), T, date(2026, 10, 4), None, SOID))
    assert panggil == [(T, date(2026, 10, 4), None, "NET 21", CUST)]
    import inspect
    from app.routers import sales_orders as SO
    src = inspect.getsource(SO)
    assert 'order.get("payment_terms"), order.get("customer_id"),' in src   # to-invoice: masukan yang sama
