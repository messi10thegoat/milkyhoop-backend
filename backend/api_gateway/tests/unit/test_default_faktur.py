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
