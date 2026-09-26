"""GET /api/customers/{id}/open-invoices — baris sintetis NK (invoice_id NULL) bukan faktur (V314, 26 Sep 2026).

Dulu: compute_customer_ar memulangkan baris 'CREDIT-NOTE' ber-invoice_id NULL; router men-str() -> id "None";
bila bernilai positif (pembalik void NK ikut terhitung — bug V314) baris itu lolos WHERE outstanding > 0 dan form
terima bayar FE mengalokasikan ke faktur "None". Kini: baris tanpa invoice_id tak pernah masuk invoices[], kredit
NK dilaporkan di summary.unapplied_credits (positif; medan TAMBAHAN — bentuk invoices[] tak berubah)."""
import uuid
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.routers import customers as C

TENANT = "kaos-biru-konveksi"
PELANGGAN = str(uuid.uuid4())
FAKTUR = uuid.uuid4()


class _Conn:
    def __init__(self, rows, kredit):
        self.rows, self.kredit, self.q = rows, kredit, []

    async def execute(self, q, *a):
        return "OK"

    async def fetch(self, q, *a):
        self.q.append(q)
        return self.rows

    async def fetchval(self, q, *a):
        self.q.append(q)
        if "timezone" in q.lower() or '"Tenant"' in q:
            return "Asia/Jakarta"
        assert "invoice_id IS NULL AND outstanding < 0" in q and a == (TENANT, PELANGGAN)
        return self.kredit


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        p = self

        class _A:
            async def __aenter__(self):
                return p.c

            async def __aexit__(self, *e):
                return False
        return _A()


def _baris(i, nomor, total, sisa):
    return {"id": i, "invoice_number": nomor, "invoice_date": date(2026, 9, 1), "due_date": date(2026, 9, 30),
            "total_amount": D(total), "remaining_amount": D(sisa), "is_overdue": False, "overdue_days": 0}


@pytest.mark.asyncio
async def test_baris_tanpa_invoice_id_tak_masuk_dan_kredit_terpisah(monkeypatch):
    c = _Conn([_baris(FAKTUR, "INV-1", "300000", "300000"), _baris(None, "CREDIT-NOTE", "0", "20000")], D("25000"))

    async def gp():
        return _Pool(c)
    monkeypatch.setattr(C, "get_pool", gp)
    monkeypatch.setattr(C, "get_user_context", lambda r: {"tenant_id": TENANT, "user_id": None})
    d = await C.get_customer_open_invoices(SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT})), PELANGGAN)
    assert [x["id"] for x in d["invoices"]] == [str(FAKTUR)]          # "None" tak pernah muncul
    assert d["summary"]["total_outstanding"] == 300000 and d["summary"]["invoice_count"] == 1
    assert d["summary"]["unapplied_credits"] == 25000.0
    utama = [q for q in c.q if "ORDER BY due_date" in q][0]
    assert "invoice_id IS NOT NULL" in utama


def test_v314_menambah_filter_pembalik_di_kedua_cte():
    import pathlib
    m = pathlib.Path(C.__file__).parents[3] / "migrations" / "V314__ar_ap_pembalik_void_nk_vc.sql"
    s = m.read_text()
    for cte in ("cn_unapplied AS (", "vc_unapplied AS ("):
        blok = s[s.index(cte):s.index("GROUP BY", s.index(cte))]
        assert "je.reversed_by_id IS NULL" in blok and "je.reversal_of_id IS NULL" in blok, cte
    assert "L2_KREDIT_NK" in s and "nk_cocok" in s
