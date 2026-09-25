"""Q-014 (25 Sep 2026): penanda `is_overdue` per baris di GET /api/sales-invoices.

Diukur dari kode + prod: `status` baris daftar = nilai kolom; 'overdue' tak pernah tersimpan (0 baris),
jadi tanpa filter ?status=overdue baris jatuh tempo tampil 'posted'/'partial' -- FE (W7a) memanggil dua
kali dan dibatasi 100. Kini satu aturan `_syarat_jatuh_tempo` dipakai filter DAN kolom per baris,
dengan tanggal bisnis tenant yang sama.
"""
import re
import uuid
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.routers import sales_invoices as SI
from app.schemas.sales_invoices import InvoiceListItem, InvoiceListResponse

TENANT = "grapgrap-manado"
HARI = date(2026, 9, 25)


class DB:
    def __init__(self, rows):
        self.rows, self.sql, self.args = rows, [], []

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        self.args.append(a)
        return len(self.rows)

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        self.args.append(a)
        return self.rows


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


def _baris(jt):
    return {"id": uuid.uuid4(), "invoice_number": "INV-1", "customer_id": None, "customer_name": "Budi",
            "invoice_date": date(2026, 9, 1), "due_date": date(2026, 9, 10), "total_amount": 100,
            "journal_paid": 0, "status": "posted", "operational_status": "SENT", "accounting_status": "POSTED",
            "fulfillment_status": None, "revenue_status": None, "created_at": datetime(2026, 9, 1),
            "sales_order_id": None, "sales_order_number": None, "is_overdue": jt}


async def _daftar(monkeypatch, rows, status=None, skip=0, limit=20):
    db = DB(rows)

    async def _pool():
        return Pool(db)

    async def _hari(conn, tid):
        assert tid == TENANT
        return HARI
    monkeypatch.setattr(SI, "get_pool", _pool)
    monkeypatch.setattr(SI, "tanggal_dokumen", _hari)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": None}), headers={})
    out = await SI.list_invoices(req, skip=skip, limit=limit, search=None, status=status, customer_id=None,
                                 start_date=None, end_date=None, sort_by="created_at", sort_order="desc",
                                 amount_min=None, amount_max=None)
    return db, out


def _p(sql, pola):
    return [int(n) for n in re.findall(pola, sql)]


@pytest.mark.asyncio
async def test_baris_membawa_is_overdue_lewat_response_model(monkeypatch):
    _, out = await _daftar(monkeypatch, [_baris(True), _baris(False)])
    items = InvoiceListResponse.model_validate(out).model_dump()["items"]   # response_model tak membuangnya
    assert [i["is_overdue"] for i in items] == [True, False]
    assert [i["status"] for i in items] == ["posted", "posted"]             # status tetap nilai kolom


@pytest.mark.asyncio
async def test_kolom_per_baris_memakai_aturan_bersama_dan_tanggal_bisnis(monkeypatch):
    db, _ = await _daftar(monkeypatch, [], skip=40, limit=20)
    q, args = db.sql[-1], db.args[-1]
    m = re.search(r"\((si\.status IN \('posted', 'partial'\) AND si\.due_date < \$(\d+)::date AND "
                  r"si\.id IN \(SELECT invoice_id FROM compute_ar_outstanding\(\$1\) WHERE outstanding > 0\))\) "
                  r"AS is_overdue", q)
    assert m, q
    assert args[int(m.group(2)) - 1] == HARI                               # $N menunjuk tanggal bisnis
    lim, off = map(int, re.search(r"LIMIT \$(\d+) OFFSET \$(\d+)", q).groups())
    assert (args[lim - 1], args[off - 1]) == (20, 40)                      # limit/offset tak tergeser
    assert len(db.args[0]) == 1                                            # COUNT tanpa parameter ekstra


@pytest.mark.asyncio
async def test_filter_overdue_dan_kolom_teks_aturan_sama(monkeypatch):
    db, _ = await _daftar(monkeypatch, [], status="overdue")
    q, args = db.sql[-1], db.args[-1]
    aturan = [re.sub(r"\$\d+", "$N", a) for a in re.findall(
        r"\(si\.status IN \('posted', 'partial'\) AND si\.due_date < \$\d+::date AND si\.id IN "
        r"\(SELECT invoice_id FROM compute_ar_outstanding\(\$1\) WHERE outstanding > 0\)\)", q)]
    assert len(aturan) == 2 and aturan[0] == aturan[1], q                  # WHERE dan kolom = teks sama
    for n in _p(q, r"si\.due_date < \$(\d+)::date"):
        assert args[n - 1] == HARI
    assert db.args[0] == (TENANT, HARI)                                    # COUNT memakai filter yang sama


def test_skema_is_overdue_bool_bawaan_false():
    f = InvoiceListItem.model_fields["is_overdue"]
    assert f.annotation is bool and f.default is False
