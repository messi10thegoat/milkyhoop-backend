"""PO Selesai/Dibatalkan = sisa ditagih 0 (22 Okt 2026, temuan nyata kaos PO-2610-0001 "Belum ditagih" Rp 350.028 pada PO Selesai).
Pola NetSuite: baris PO yang ditutup tak bisa ditagih. Satu definisi PD.STATUS_TANPA_SISA_TAGIH: fakta_po (baris + detail) dan
sql_sisa_ditagih (with_totals sum_sisa); pill perlu_ditagih tak mencakup closed."""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal

from app.routers import purchase_orders as PO
from app.services import po_daftar as PD

H = date(2026, 10, 22)


class _Kon:
    def __init__(self, baris): self.baris = baris
    async def fetch(self, sql, *a): return self.baris


def _r(status, total="350028.00", billed="0", recv="10"):
    return {"id": uuid.uuid4(), "status": status, "expected_date": None, "total_amount": Decimal(total), "sisa_terima": Decimal("0"),
            "belum_ditagih": Decimal(recv) - Decimal("0"), "amount_received": Decimal(total), "amount_billed": Decimal(billed)}


def test_status_tanpa_sisa_tagih():
    assert PD.STATUS_TANPA_SISA_TAGIH == ("cancelled", "closed")
    assert "closed" not in PD.STATUS_BISA_TAGIH


def test_fakta_po_closed_dan_cancelled_nol_lainnya_total_minus_ditagih():
    rows = [_r("closed"), _r("cancelled"), _r("received"), _r("partial_billed", billed="100000.00"), _r("closed", billed="100000.00")]
    f = asyncio.run(PD.fakta_po(_Kon(rows), "t", [r["id"] for r in rows], H))
    assert [f[r["id"]]["sisa_ditagih"] for r in rows] == [Decimal("0"), Decimal("0"), Decimal("350028.00"), Decimal("250028.00"), Decimal("0")]
    assert f[rows[0]["id"]]["aksi"] is None and f[rows[2]["id"]]["aksi"] == "perlu_ditagih"


def test_sql_sisa_ditagih_padanan():
    q = PD.sql_sisa_ditagih()
    assert q == "(CASE WHEN po.status IN ('cancelled', 'closed') THEN 0 ELSE GREATEST(po.total_amount - ag.amount_billed, 0) END)"
    assert "status IN ('partial_received', 'received', 'partial_billed')" in PD.sql_aksi("perlu_ditagih", "$2")


def test_with_totals_pakai_sql_sisa_ditagih():
    src = inspect.getsource(PO.list_purchase_orders)
    assert "SUM({PD.sql_sisa_ditagih()})" in src
    assert "GREATEST(po.total_amount - ag.amount_billed, 0)) FILTER (WHERE po.status <> 'cancelled')" not in src
