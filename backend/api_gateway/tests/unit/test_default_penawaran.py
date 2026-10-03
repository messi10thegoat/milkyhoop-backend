"""Q6 GET /api/quotes/defaults (3 Okt 2026, MASTER): bentuk sama dengan /sales-orders/defaults + validity_days 14."""
from datetime import date
from types import SimpleNamespace

import pytest

from app.routers import quotes as Q
from app.services import default_dokumen as DD


class _Conn:
    def __init__(self, pct=None, rek=None, teks=None):
        self.pct, self.rek, self.teks = pct, rek, teks

    async def fetchval(self, q, *a):
        assert "accounting_settings" in q and a[0] == "t-uji"
        return self.pct

    async def fetchrow(self, q, *a):
        assert a[0] == "t-uji"
        if "default_quote_opening_text" in q:
            assert "accounting_settings" in q
            return self.teks
        assert "bank_accounts" in q
        return self.rek


@pytest.mark.asyncio
async def test_default_penawaran_14_hari_dan_bentuk_sama_pesanan():
    d = await DD.default_penawaran(_Conn(), "t-uji", date(2026, 10, 3))
    assert d == {"dp_percent": None, "receiving_account": None, "opening_text": None, "closing_text": None,
                 "validity_days": {"value": 14, "source": "system", "expiry_date": "2026-10-17"}}
    p = await DD.default_pesanan(_Conn(30, None), "t-uji")
    q = await DD.default_penawaran(_Conn(30, None), "t-uji", date(2026, 12, 25))
    assert {k: v for k, v in q.items() if k in p} == p
    t = await DD.default_penawaran(_Conn(teks={"default_quote_opening_text": "Dengan hormat,",
                                               "default_quote_closing_text": "   "}), "t-uji", date(2026, 10, 3))
    assert t["opening_text"] == {"value": "Dengan hormat,", "source": "company"} and t["closing_text"] is None
    assert q["validity_days"]["expiry_date"] == "2027-01-08"  # lintas tahun


@pytest.mark.asyncio
async def test_rute_defaults_memakai_tanggal_tenant(monkeypatch):
    conn = _Conn()

    class _Pool:
        def acquire(self):
            class _A:
                async def __aenter__(s):
                    return conn

                async def __aexit__(s, *a):
                    return False
            return _A()

    async def gp():
        return _Pool()

    async def tgl(c, t):
        return date(2026, 10, 3)
    monkeypatch.setattr(Q, "get_pool", gp)
    monkeypatch.setattr(Q, "tanggal_dokumen", tgl)
    req = SimpleNamespace(state=SimpleNamespace(user={"user_id": "0bccdb25-fdf0-4e99-9024-b9a20846f76c", "tenant_id": "t-uji"}))
    r = await Q.get_quote_defaults(req)
    assert r["success"] and r["data"]["validity_days"]["expiry_date"] == "2026-10-17"


def test_rute_defaults_di_atas_rute_id():
    """/defaults harus terdaftar SEBELUM /{quote_id} (kalau tidak, 'defaults' dibaca sebagai id penawaran)."""
    jalur = [(r.path, r.endpoint.__name__) for r in Q.router.routes if "GET" in r.methods]
    i = jalur.index(("/defaults", "get_quote_defaults"))
    assert i < [p for p, _ in jalur].index("/{quote_id}")
