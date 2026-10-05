"""Detail uang muka membawa SO induk (5 Okt 2026, permintaan WORKSPACE D-standar): sales_order_id + sales_order_number.
Aturan = SUMBER_SO['customer_deposit'] (sales_order_id uang muka, kalau kosong SO milik proformanya); tenant eksplisit; null bila tak bertaut."""
import asyncio
import inspect
import uuid

from app.routers import customer_deposits as CD
from app.schemas.customer_deposits import CustomerDepositDetail
from app.services.kode_order import SUMBER_SO

SO, PF = uuid.uuid4(), uuid.uuid4()


class _K:
    def __init__(self, row): self.row, self.sql = row, []

    async def fetchrow(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        return self.row


def _jalan(c): return asyncio.run(c)


def test_helper_mengembalikan_so_atau_none_dan_tenant_eksplisit():
    k = _K({"id": SO, "order_number": "SO-1"})
    out = _jalan(CD.so_induk_uang_muka(k, "t1", {"sales_order_id": SO, "proforma_id": None}))
    assert out == {"id": SO, "order_number": "SO-1"}
    sql, a = k.sql[0]
    assert "so.tenant_id = $1" in sql and "p.tenant_id = $1" in sql and a == ("t1", SO, None)
    assert "COALESCE( $2::uuid, (SELECT p.sales_order_id FROM proformas p WHERE p.id = $3::uuid AND p.tenant_id = $1))" in sql
    assert _jalan(CD.so_induk_uang_muka(_K(None), "t1", {"sales_order_id": None, "proforma_id": None})) is None  # tak bertaut


def test_aturan_sama_dengan_sumber_so_customer_deposit():
    sumber = " ".join(SUMBER_SO["customer_deposit"].split())
    assert "COALESCE(d.sales_order_id, p.sales_order_id)" in sumber  # sales_order_id uang muka dulu, lalu SO proformanya
    sql = " ".join(inspect.getsource(CD.so_induk_uang_muka).split())
    assert "so.id = COALESCE( $2::uuid, (SELECT p.sales_order_id FROM proformas p" in sql  # sales_order_id uang muka dulu, lalu proforma
    assert "FROM proformas p WHERE p.id = $3::uuid" in sql  # fallback lewat proforma


def test_handler_detail_memakai_helper_dan_skema_memuat_medan():
    src = " ".join(inspect.getsource(CD.get_customer_deposit).split())
    assert "so_induk_uang_muka(conn, ctx[\"tenant_id\"], dep)" in src
    assert '"sales_order_id": str(_so["id"]) if _so else None' in src and '"sales_order_number": _so["order_number"] if _so else None' in src
    assert {"sales_order_id", "sales_order_number"} <= set(CustomerDepositDetail.model_fields)
