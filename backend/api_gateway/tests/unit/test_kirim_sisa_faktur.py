"""GET /deliveries/{id}: invoice_total + invoice_outstanding faktur sumber (5 Okt 2026, WORKSPACE D-standar)."""
import asyncio
import inspect
import uuid
from decimal import Decimal

import pytest

from app.routers import deliveries as DL

T = "tenant-uji"
F = uuid.uuid4()


class Conn:
    def __init__(self, status, total="1000", ar=None):
        self.status, self.total, self.ar, self.q = status, total, ar, []

    async def fetchrow(self, sql, *a):
        self.q.append((sql, a))
        return None if self.status is None else {"status": self.status, "total_amount": Decimal(self.total)}

    async def fetchval(self, sql, *a):
        self.q.append((sql, a))
        assert "compute_ar_outstanding" in sql
        return None if self.ar is None else Decimal(self.ar)


def _j(c):
    return asyncio.run(DL.sisa_faktur_sumber(c, T, F))


@pytest.mark.parametrize("st", ["draft", "void", "voided", None])
def test_faktur_draf_void_atau_tak_ada_null(st):
    c = Conn(st)
    assert _j(c) == {"invoice_total": None, "invoice_outstanding": None}
    assert not any("compute_ar_outstanding" in s for s, _ in c.q)


def test_lunas_tanpa_baris_ar_nol():
    assert _j(Conn("paid", ar=None)) == {"invoice_total": "1000.00", "invoice_outstanding": "0.00"}


def test_sebagian_sisa_dari_jurnal_bukan_kolom_faktur():
    assert _j(Conn("partial", total="1000.5", ar="250.25")) == {"invoice_total": "1000.50", "invoice_outstanding": "250.25"}


def test_tenant_eksplisit_di_kedua_kueri():
    c = Conn("posted", ar="10")
    _j(c)
    assert len(c.q) == 2 and all(T in a for _, a in c.q)
    assert "tenant_id = $2" in c.q[0][0]


def test_detail_menempelkan_sisa():
    assert "detail.update(await sisa_faktur_sumber(" in inspect.getsource(DL.get_delivery_detail)
