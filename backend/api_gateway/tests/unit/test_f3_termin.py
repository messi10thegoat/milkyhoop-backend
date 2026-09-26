"""F3 (26 Sep 2026): jatuh tempo faktur bawaan = termin (NET <n> SO -> termin pelanggan -> 0).
Dulu to-invoice tanpa due_date -> due_date = invoice_date -> terlambat sejak besok."""
import inspect
import os
from datetime import date

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import termin_bayar as TB  # noqa: E402
from app.routers import sales_orders as SO  # noqa: E402

TGL = date(2026, 9, 26)
CUST = "30000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize("teks,hari", [
    ("NET 30", 30), ("net14", 14), ("  Net 7 hari", 7), ("NET 0", 0), ("NET30 (transfer)", 30),
    ("DP 60% di muka, pelunasan sebelum pengiriman", None), ("DP 30% di muka", None),
    ("NETTO 30", None), ("NET30hari", 30), ("Termin NET 30", None), ("30 hari", None), ("NET 999", None), ("", None), (None, None),
])
def test_pengurai(teks, hari):
    assert TB.hari_dari_termin(teks) == hari


class _K:
    def __init__(self, hari):
        self.hari, self.q = hari, []

    async def fetchval(self, sql, *a):
        self.q.append((sql, a))
        return self.hari


@pytest.mark.asyncio
async def test_isian_pengguna_selalu_menang():
    k = _K(10)
    assert await TB.tentukan_jatuh_tempo(k, "t", TGL, date(2026, 10, 1), "NET 30", CUST) == (date(2026, 10, 1), "body")
    assert k.q == []


@pytest.mark.asyncio
async def test_net_so():
    assert await TB.tentukan_jatuh_tempo(_K(10), "t", TGL, None, "NET 30", CUST) == (date(2026, 10, 26), "so_terms")


@pytest.mark.asyncio
async def test_teks_dp_jatuh_ke_termin_pelanggan_bertenant():
    k = _K(10)
    assert await TB.tentukan_jatuh_tempo(k, "t", TGL, None, "DP 60% di muka", CUST) == (date(2026, 10, 6), "customer_terms")
    sql, a = k.q[0]
    assert "tenant_id = $2" in sql and a == (CUST, "t")


@pytest.mark.asyncio
@pytest.mark.parametrize("hari,cust", [(0, CUST), (None, CUST), (10, None), (400, CUST)])
async def test_bawaan_termin_nol(hari, cust):
    assert await TB.tentukan_jatuh_tempo(_K(hari), "t", TGL, None, None, cust) == (TGL, "default")


def test_to_invoice_memakai_layanan_dan_melaporkan_sumber():
    src = " ".join(inspect.getsource(SO.convert_to_invoice).split())
    assert "else invoice_date" not in src
    assert "await tentukan_jatuh_tempo(" in src and '"due_date_source": due_date_source' in src
    assert src.index("tentukan_jatuh_tempo(") < src.index("INSERT INTO sales_invoices")


@pytest.mark.asyncio
async def test_termin_hari_tanpa_tanggal():
    assert await TB.termin_hari(_K(10), "t", "NET 45", CUST) == (45, "so_terms")
    assert await TB.termin_hari(_K(10), "t", "DP 30% di muka", CUST) == (10, "customer_terms")
    assert await TB.termin_hari(_K(0), "t", None, CUST) == (0, "default")


def test_detail_so_membawa_termin_dari_aturan_yang_sama():
    src = " ".join(inspect.getsource(SO.get_sales_order_detail).split())
    assert "await termin_hari(" in src
    assert "payment_terms_days=termin_n" in src and "payment_terms_source=termin_sumber" in src
    from app.schemas.sales_orders import SalesOrderDetail
    assert {"payment_terms_days", "payment_terms_source"} <= set(SalesOrderDetail.model_fields)
