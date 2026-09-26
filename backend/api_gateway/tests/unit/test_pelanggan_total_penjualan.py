"""Penjualan per pelanggan turunan jurnal (26 Sep 2026) — pengganti cache mati total_nilai/total_transaksi.

Putusan MASTER (b): daftar total_value/total_transactions -> null; detail mempertahankan fallback lama sementara;
field BARU total_penjualan / jumlah_faktur / last_invoice_date di daftar + detail.
"""
import inspect
import uuid
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.schemas.customers import CustomerDetail, CustomerListItem, CustomerListResponse
from app.services import pelanggan_penjualan as PP

T = "grapgrap-manado"


def test_sql_turunan_jurnal_berpagar_tenant():
    s = " ".join(PP.SQL_PENJUALAN.split())
    assert "je.source_type = 'INVOICE'" in s and "je.source_type = 'CREDIT_NOTE'" in s
    assert s.count("je.status = 'POSTED' AND je.reversed_by_id IS NULL") == 2
    assert s.count("coa.account_type = 'RECEIVABLE'") == 2
    assert "SUM(jl.debit) AS debit" in s and "SUM(jl.credit) AS kredit" in s
    assert "COALESCE(ar.debit, 0) - COALESCE(cn.kredit, 0) AS total" in s
    assert s.count("tenant_id = $1") >= 3 and s.count("customer_id = ANY($2::uuid[])") == 3
    assert "si.status NOT IN ('draft', 'void')" in s
    assert "total_amount" not in s                                          # BUKAN kolom wrapper (Law 16)


class Conn:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    async def fetch(self, sql, *a):
        self.calls.append(a)
        return self.rows


@pytest.mark.asyncio
async def test_pemetaan_satu_kueri():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = Conn([{"cid": a, "n": 3, "terakhir": date(2026, 9, 20), "total": D("5500000.00")},
                 {"cid": b, "n": 0, "terakhir": None, "total": D("-255000")}])   # hanya nota kredit
    h = await PP.penjualan_pelanggan(conn, T, [a, b, c])
    assert len(conn.calls) == 1 and conn.calls[0] == (T, [str(a), str(b), str(c)])
    assert h[str(a)] == {"total_penjualan": 5500000.0, "jumlah_faktur": 3, "last_invoice_date": "2026-09-20"}
    assert h[str(b)]["total_penjualan"] == -255000.0 and h[str(c)] == PP.KOSONG
    assert await PP.penjualan_pelanggan(Conn([]), T, []) == {}


class ConnDaftar:
    def __init__(self, cid):
        self.cid, self.sql = cid, []

    async def fetchval(self, sql, *a):
        return 1

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "FROM credit_notes" in sql:
            return [{"cid": self.cid, "n": 2, "terakhir": date(2026, 9, 18), "total": D("1500000")}]
        if "FROM sales_orders" in sql or "compute_ar_outstanding" in sql:
            return []
        return [{"id": self.cid, "nomor_member": None, "nama": "Rahayu", "company_name": None, "display_name": None,
                 "tipe": None, "telepon": None, "email": None, "alamat": None, "points": 0, "total_transaksi": 0,
                 "total_nilai": 0, "is_active": True, "created_at": None, "phone2": None, "community": None}]


@pytest.mark.asyncio
async def test_daftar_membawa_penjualan_dan_cache_lama_null(monkeypatch):
    from app.routers import customers as CU
    cid = uuid.uuid4()
    conn = ConnDaftar(cid)

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *e):
            return False

    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq())
    monkeypatch.setattr(CU, "get_pool", _pool)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": "22222222-2222-2222-2222-222222222222"}))
    r = await CU.list_customers(req, skip=0, limit=20, search=None, tipe=None, is_active=None, sort_by="created_at", sort_order="desc")
    it = CustomerListResponse(**r).model_dump()["items"][0]
    assert it["total_penjualan"] == 1500000.0 and it["jumlah_faktur"] == 2 and it["last_invoice_date"] == "2026-09-18"
    assert it["total_value"] is None and it["total_transactions"] is None


def test_detail_menyebar_penjualan_dan_menjaga_fallback():
    from app.routers import customers as CU
    seg = inspect.getsource(CU.get_customer)
    assert "_penjualan = (await penjualan_pelanggan(conn, ctx[\"tenant_id\"], [customer_id]))[str(customer_id)]" in seg
    assert "**_penjualan," in seg
    assert '"total_value": int(stats["total_nilai"] or 0)' in seg              # fallback lama TETAP (putusan b)


@pytest.mark.parametrize("model", [CustomerListItem, CustomerDetail])
def test_skema(model):
    for k in ("total_penjualan", "jumlah_faktur", "last_invoice_date"):
        assert k in model.model_fields, (model.__name__, k)
