"""BUG-104/105 (WORKSPACE, MASTER 7 Nov): GET /api/reports/aging-receivable + /aging-payable membawa total per pihak (customers[].total /
vendors[].total) = Σ 5 ember pihak itu dari kalkulator SAMA (compute_ar/ap_outstanding) == Σ balance; Σ total == total_outstanding.
Baris kredit tanpa dokumen (NK/bayar vendor belum teralokasi, kaos 7 Nov) -> invoice_id/bill_id null (dulu teks "None")."""
import asyncio
from datetime import date
from decimal import Decimal

from starlette.requests import Request

from app.routers import reports as R

HARI = date(2026, 11, 7)


def _ar(cid, nama, inv, due, sisa, ember):
    return {"customer_id": cid, "customer_name": nama, "invoice_id": inv, "invoice_number": inv and f"INV-{inv}", "invoice_date": HARI,
            "due_date": due, "total_amount": Decimal("1"), "amount_paid": Decimal("0"), "balance": Decimal(sisa), "days_overdue": 0,
            "aging_bucket": ember}


def _ap(vid, nama, bill, due, sisa, ember):
    return {"vendor_id": vid, "vendor_name": nama, "bill_id": bill, "bill_number": bill and f"BILL-{bill}", "bill_date": HARI,
            "due_date": due, "total_amount": Decimal("1"), "amount_paid": Decimal("0"), "balance": Decimal(sisa), "days_overdue": 0,
            "aging_bucket": ember}


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *a):
        return self.rows


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def _jalan(monkeypatch, fn, rows):
    async def pool():
        class P:
            def acquire(self):
                return _Acq(_Conn(rows))
        return P()

    async def hari(conn, tid):
        return HARI
    monkeypatch.setattr(R, "get_pool", pool)
    monkeypatch.setattr(R, "tanggal_dokumen", hari)
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
    return asyncio.run(fn(r))


def test_ar_total_per_pelanggan_sama_dengan_ember_dan_ringkasan(monkeypatch):
    rows = [_ar("c1", "Bu Sri", "1", HARI, "15700", "current"), _ar("c1", "Bu Sri", "2", date(2026, 10, 1), "141300", "31-60"),
            _ar("c2", "Rahayu", None, HARI, "-45000", "current"), _ar("c2", "Rahayu", "3", date(2026, 7, 1), "1000", "90+")]
    h = _jalan(monkeypatch, R.get_aging_receivable, rows)
    per = {c["customer_name"]: c for c in h["customers"]}
    assert per["Bu Sri"]["total"] == Decimal("157000") == sum(per["Bu Sri"]["totals"].values())
    assert per["Rahayu"]["total"] == Decimal("-44000") == sum(per["Rahayu"]["totals"].values())
    assert sum(c["total"] for c in h["customers"]) == h["total_outstanding"] == sum(h["summary"].values())
    assert [i["invoice_id"] for i in per["Rahayu"]["invoices"]] == [None, "3"]   # kredit tanpa faktur: null, bukan "None"


def test_ap_total_per_vendor_sama_dengan_ember_dan_ringkasan(monkeypatch):
    rows = [_ap("v1", "PT Minyak", "1", HARI, "660000", "current"), _ap("v1", "PT Minyak", "2", date(2026, 9, 1), "50000", "61-90"),
            _ap("v2", "Mas Ikul", None, HARI, "-100000", "current")]
    h = _jalan(monkeypatch, R.get_aging_payable, rows)
    per = {v["vendor_name"]: v for v in h["vendors"]}
    assert per["PT Minyak"]["total"] == Decimal("710000") == sum(per["PT Minyak"]["totals"].values())
    assert sum(v["total"] for v in h["vendors"]) == h["total_outstanding"] == sum(h["summary"].values())
    assert per["Mas Ikul"]["bills"][0]["bill_id"] is None
