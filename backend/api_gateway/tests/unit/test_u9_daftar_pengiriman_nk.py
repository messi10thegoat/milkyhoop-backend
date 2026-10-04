"""U9 (5 Okt 2026, MASTER+WORKSPACE): kebutuhan BE tampilan U4 Pengiriman & U5 Nota Kredit, ADITIF.

Pengiriman: daftar + detail membawa sales_order_id/sales_order_number (via faktur), daftar menerima date_from/date_to
(tanpa tanggal -> SQL TANPA kondisi tanggal). Nota Kredit: daftar membawa faktur asal, SO induk (satu kueri per halaman,
tenant eksplisit), voided_at/void_reason; detail membawa SO induk; summary menambah void_count TERPISAH (total/nilai lama
TANPA void, arti tak berubah)."""
import asyncio
import inspect
import json
from datetime import date, datetime, timezone

import pytest

from app.routers import credit_notes as CN
from app.routers import deliveries as DL
from app.schemas.credit_notes import CreditNoteDetail, CreditNoteListItem

T = datetime(2026, 10, 3, 5, 0, tzinfo=timezone.utc)
SO = "22222222-2222-2222-2222-222222222222"
INV = "33333333-3333-3333-3333-333333333333"


class _Req:
    class state:
        user = {"tenant_id": "t1", "user_id": "0bccdb25-fdf0-4e99-9024-b9a20846f76c"}


def _pool(monkeypatch, mod, conn):
    class _A:
        async def __aenter__(s):
            return conn

        async def __aexit__(s, *a):
            return False

    class _P:
        def acquire(self_):
            return _A()

    async def gp():
        return _P()
    monkeypatch.setattr(mod, "get_pool", gp)

    async def tk(c, t, jenis, dokumen, kunci="id"):
        return dokumen
    import app.services.kode_order as KO
    monkeypatch.setattr(KO, "tempel_kode", tk)


class _KonnDL:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    async def execute(self, sql, *a):
        pass

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        return {"total": len(self.rows)}

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return self.rows


def _baris_dl(so=True):
    return {"id": "11111111-1111-1111-1111-111111111111", "delivery_number": "DO-1", "delivery_date": date(2026, 10, 1),
            "status": "posted", "notes": None, "created_at": T, "posted_at": T, "voided_at": None, "voided_reason": None,
            "invoice_id": INV, "invoice_number": "INV-1", "customer_id": "c1", "sales_order_id": SO if so else None,
            "sales_order_number": "SO-1" if so else None, "customer_name": "X", "customer_phone": None,
            "customer_address": None, "warehouse_name": "G", "item_count": 1, "total_cogs": 0}


def _lst(monkeypatch, rows, **kw):
    k = _KonnDL(rows)
    _pool(monkeypatch, DL, k)
    a = dict(status=None, customer_id=None, search=None, date_from=None, date_to=None, sort_by="delivery_date",
             sort_order="desc", page=1, per_page=20)
    a.update(kw)
    return k, asyncio.run(DL.list_deliveries(_Req(), **a))


def test_pengiriman_daftar_membawa_so_induk(monkeypatch):
    k, h = _lst(monkeypatch, [_baris_dl(), _baris_dl(so=False)])
    assert h["items"][0]["sales_order_id"] == SO and h["items"][0]["sales_order_number"] == "SO-1"
    assert h["items"][1]["sales_order_id"] is None and h["items"][1]["sales_order_number"] is None
    main = " ".join(k.sql[-1][0].split())
    assert "LEFT JOIN sales_orders so ON so.id = si.sales_order_id AND so.tenant_id = si.tenant_id" in main


def test_pengiriman_tanpa_tanggal_sql_tanpa_kondisi_tanggal(monkeypatch):
    k, _ = _lst(monkeypatch, [])
    for sql, a in k.sql:
        assert "fulfillment_date >=" not in sql and "fulfillment_date <=" not in sql
        assert list(a)[:1] == ["t1"]


def test_pengiriman_filter_tanggal_terikat_parameter(monkeypatch):
    k, _ = _lst(monkeypatch, [], date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))
    for sql, a in k.sql:
        s = " ".join(sql.split())
        assert "f.fulfillment_date >= $2" in s and "f.fulfillment_date <= $3" in s
        assert a[1] == date(2026, 9, 1) and a[2] == date(2026, 9, 30)


def test_pengiriman_detail_memilih_so_induk():
    src = " ".join(inspect.getsource(DL.get_delivery_detail).split())
    assert "si.sales_order_id, so.order_number AS sales_order_number" in src
    assert "LEFT JOIN sales_orders so ON so.id = si.sales_order_id AND so.tenant_id = si.tenant_id" in src
    assert '"sales_order_id": str(row["sales_order_id"])' in src and '"sales_order_number": row["sales_order_number"]' in src


class _KonnNK:
    def __init__(self, rows, so_rows, void=2):
        self.rows, self.so_rows, self.void, self.sql = rows, so_rows, void, []

    async def fetchval(self, sql, *a):
        self.sql.append(("val", sql, a))
        return self.void if "status = 'void'" in sql else len(self.rows)

    async def fetch(self, sql, *a):
        self.sql.append(("fetch", sql, a))
        return self.so_rows if "FROM sales_invoices" in sql else self.rows

    async def fetchrow(self, sql, *a):
        self.sql.append(("row", sql, a))
        return {"total": 3, "draft_count": 1, "posted_count": 1, "partial_count": 1, "applied_count": 0,
                "total_value": 300, "total_applied": 10, "total_refunded": 0, "available_balance": 290,
                "partially_refunded_count": 0, "refunded_count": 0}


def _baris_nk(inv=INV, status="posted"):
    return {"id": "44444444-4444-4444-4444-444444444444", "credit_note_number": "CN-1", "customer_id": "c1",
            "customer_name": "X", "credit_note_date": date(2026, 10, 1), "total_amount": 100, "amount_applied": 0,
            "amount_refunded": 0, "status": status, "reason": "retur", "created_at": T, "original_invoice_id": inv,
            "original_invoice_number": "INV-1" if inv else None, "voided_at": T if status == "void" else None,
            "voided_reason": "salah" if status == "void" else None}


def _nk(monkeypatch, k, **kw):
    _pool(monkeypatch, CN, k)
    a = dict(status="all", customer_id=None, search=None, date_from=None, date_to=None, skip=0, limit=20,
             sort_by="created_at", sort_order="desc")
    a.update(kw)
    return asyncio.run(CN.list_credit_notes(_Req(), **a))


def test_nk_daftar_membawa_faktur_asal_so_induk_dan_void(monkeypatch):
    import uuid
    inv = uuid.UUID(INV)
    k = _KonnNK([_baris_nk(inv), _baris_nk(None, "void")], [{"id": inv, "sales_order_id": uuid.UUID(SO), "order_number": "SO-1"}])
    h = _nk(monkeypatch, k)
    a, b = h["items"]
    assert a["original_invoice_id"] == str(inv) and a["original_invoice_number"] == "INV-1"
    assert a["sales_order_id"] == SO and a["sales_order_number"] == "SO-1" and a["voided_at"] is None
    assert b["original_invoice_id"] is None and b["sales_order_id"] is None and b["sales_order_number"] is None
    assert b["voided_at"].startswith("2026-10-03") and b["void_reason"] == "salah"
    so_q = [q for q in k.sql if q[0] == "fetch" and "FROM sales_invoices" in q[1]]
    assert len(so_q) == 1 and so_q[0][2][0] == "t1"  # satu kueri per halaman, tenant eksplisit
    assert "si.tenant_id = $1" in " ".join(so_q[0][1].split()) and "so.tenant_id = si.tenant_id" in so_q[0][1]


def test_nk_daftar_tanpa_faktur_asal_tak_ada_kueri_so(monkeypatch):
    k = _KonnNK([_baris_nk(None)], [])
    _nk(monkeypatch, k)
    assert not [q for q in k.sql if "FROM sales_invoices" in q[1]]


def test_nk_item_dan_detail_skema_punya_medan_baru():
    for kls, ks in ((CreditNoteListItem, {"original_invoice_id", "original_invoice_number", "sales_order_id",
                                           "sales_order_number", "voided_at", "void_reason"}),
                    (CreditNoteDetail, {"sales_order_id", "sales_order_number"})):
        assert ks <= set(kls.model_fields)
    src = " ".join(inspect.getsource(CN.get_credit_note).split())
    assert "si.tenant_id = $1 AND si.id = $2" in src and '"sales_order_id": str(_so["sales_order_id"])' in src


def test_nk_summary_void_count_terpisah_dan_nilai_lama_tak_berubah(monkeypatch):
    k = _KonnNK([], [], void=7)
    _pool(monkeypatch, CN, k)
    d = asyncio.run(CN.get_credit_notes_summary(_Req()))["data"]
    assert d["void_count"] == 7 and d["total"] == 3 and d["total_value"] == 300.0 and d["available_balance"] == 290.0
    main = next(" ".join(q[1].split()) for q in k.sql if q[0] == "row")
    assert "cn.status != 'void'" in main and "void_count" not in main  # arti lama utuh; void dihitung kueri lain
    vq = next(q for q in k.sql if q[0] == "val")
    assert "tenant_id = $1 AND status = 'void'" in " ".join(vq[1].split()) and vq[2] == ("t1",)


def test_kontrol_alat_tes_bisa_merah():
    assert "fulfillment_date >=" not in "SELECT 1" and "x" != "y"
