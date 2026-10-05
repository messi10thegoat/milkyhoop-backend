"""Kontrak + kunci GET /sales-orders/{id}/documents?bentuk=terkait (5 Okt 2026, WORKSPACE D0)."""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.services import so_terkait as ST
from app.routers import dokumen as D

T = "tenant-uji"
SO = uuid.uuid4()


def _rows(n, buat):
    return [buat(i) for i in range(n)]


class Conn:
    """Koneksi tiruan: menjawab per potongan SQL; mencatat SETIAP kueri (jumlah + teks + argumen)."""

    def __init__(self, n=1, so=True, batal=False, so_lain=None):
        self.n, self.so, self.batal, self.so_lain, self.q = n, so, batal, so_lain, []

    async def _jawab(self, sql, args):
        self.q.append((sql, args))
        n, d = self.n, date(2026, 10, 1)
        st = (lambda i, hidup, mati: mati if self.batal and i == 0 else hidup)
        if "DISTINCT COALESCE(NULLIF(trim(unit)" in sql:
            return [{"u": "pcs"}]
        if "SUM(quantity)" in sql:
            return Decimal("10")
        if "FROM sales_orders\n" in sql or "FROM sales_orders WHERE" in sql:
            return {"id": SO, "order_number": "SO-1", "order_code": "001-10-26", "order_title": "KAOS", "quote_id": None} if self.so else None
        if "FROM quotes" in sql:
            return _rows(n, lambda i: {"id": uuid.uuid4(), "quote_number": f"Q-{i}", "quote_date": d, "total_amount": Decimal("100"), "status": st(i, "converted", "void")})
        if "FROM proformas" in sql:
            return _rows(n, lambda i: {"id": uuid.uuid4(), "proforma_number": f"PF-{i}", "proforma_date": d, "purpose": "TERMIN", "amount": Decimal("50"), "status": st(i, "issued", "cancelled")})
        if "FROM customer_deposits" in sql:
            return _rows(n, lambda i: {"id": uuid.uuid4(), "deposit_number": f"DP-{i}", "deposit_date": d, "amount": Decimal("30"), "amount_applied": Decimal("10"),
                                       "amount_refunded": Decimal("0"), "status": st(i, "partial", "void"), "payment_method": "transfer", "proforma_number": None})
        if "compute_ar_outstanding" in sql:
            return [{"invoice_id": i, "outstanding": Decimal("5")} for i in args[1]]
        if "FROM sales_invoices" in sql and "invoice_date" in sql:
            return _rows(n, lambda i: {"id": uuid.uuid4(), "invoice_number": f"SI-{i}", "invoice_date": d, "total_amount": Decimal("70"), "status": st(i, "posted", "void")})
        if "FROM invoice_fulfillments" in sql:
            return _rows(n, lambda i: {"id": uuid.uuid4(), "fulfillment_number": f"SJ-{i}", "fulfillment_date": d, "status": "posted",
                                       "invoice_number": "SI-0", "qty": Decimal("2"), "aktif": not (self.batal and i == 0)})
        if "FROM receive_payments" in sql:
            return _rows(n, lambda i: {"id": uuid.uuid4(), "payment_number": f"RCV-{i}", "payment_date": d, "payment_method": "cash",
                                       "status": st(i, "posted", "voided"), "jumlah": Decimal("20"), "so_lain": self.so_lain or []})
        if "FROM credit_notes" in sql:
            return _rows(n, lambda i: {"id": uuid.uuid4(), "credit_note_number": f"NK-{i}", "credit_note_date": d, "total_amount": Decimal("7"),
                                       "status": st(i, "posted", "void"), "reason": "pricing_error"})
        raise AssertionError("kueri tak terduga: " + sql[:120])

    async def fetch(self, sql, *a):
        return await self._jawab(sql, a)

    async def fetchrow(self, sql, *a):
        return await self._jawab(sql, a)

    async def fetchval(self, sql, *a):
        return await self._jawab(sql, a)


@pytest.fixture(autouse=True)
def _tambal(monkeypatch):
    import app.services.kode_order as KO
    import app.services.proforma_terbayar as PT

    async def setelan(conn, tid):
        conn.q.append(("muat_setelan", (tid,)))
        return {"label": "No. SPK", "title_label": "Judul SPK"}

    async def terbayar(conn, tid, so_ids):
        conn.q.append(("terbayar_proforma", (tid,)))
        return {}
    monkeypatch.setattr(KO, "muat_setelan", setelan)
    monkeypatch.setattr(PT, "terbayar_proforma", terbayar)


def _jalan(c):
    return asyncio.run(ST.susun_terkait(c, T, SO))


def test_urutan_alur_dan_setiap_kelompok_membawa_summary_teks():
    h = _jalan(Conn(n=2))
    assert [g["key"] for g in h["groups"]] == list(ST.URUTAN)
    for g in h["groups"]:
        assert g["docs"] and isinstance(g["summary"], list) and g["summary"]
        assert all(isinstance(x["label"], str) and isinstance(x["value"], str) for x in g["summary"])
    assert h["so"] == {"id": str(SO), "number": "SO-1", "order_code": "001-10-26", "order_title": "KAOS",
                       "order_code_label": "No. SPK", "order_title_label": "Judul SPK"}


def test_bidang_dokumen_tepat_dan_tanpa_kelompok_kosong():
    h = _jalan(Conn(n=1))
    for g in h["groups"]:
        for d in g["docs"]:
            assert set(d) == {"kind", "id", "number", "date", "info", "amount", "status", "status_label", "voided"}
            assert d["kind"] == g["key"] and d["date"] == "2026-10-01" and isinstance(d["voided"], bool)
    h0 = _jalan(Conn(n=0))
    assert h0["groups"] == []


def test_dokumen_batal_ikut_bertanda_dan_tak_dihitung_summary():
    h = {g["key"]: g for g in _jalan(Conn(n=3, batal=True))["groups"]}
    for k, g in h.items():
        assert [d["voided"] for d in g["docs"]] == [True, False, False], k
    assert h["invoice"]["summary"][0] == {"label": "Total", "value": "Rp 140"}
    assert h["receipt"]["summary"][0] == {"label": "Diterima", "value": "Rp 40"}
    assert h["credit_note"]["summary"][0] == {"label": "Total", "value": "Rp 14"}
    assert h["deposit"]["summary"] == [{"label": "Diterima", "value": "Rp 60"}, {"label": "Sisa", "value": "Rp 40"}]
    assert h["delivery"]["summary"] == [{"label": "Terkirim", "value": "4/10 pcs"}]
    assert h["invoice"]["docs"][0]["status_label"] == "Batal" and h["invoice"]["docs"][0]["info"] is None
    assert h["delivery"]["docs"][0]["status"] == "voided" and h["delivery"]["docs"][0]["status_label"] == "Batal"


def test_label_layar_info_dan_jumlah_desimal_teks():
    h = {g["key"]: g for g in _jalan(Conn(n=1, so_lain=["SO-9"]))["groups"]}
    assert h["credit_note"]["docs"][0]["info"] == "Salah harga"
    assert h["receipt"]["docs"][0]["info"] == "Tunai · juga SO-9"
    assert h["receipt"]["docs"][0]["amount"] == "20.00"
    assert h["deposit"]["docs"][0]["info"] == "Transfer Bank" and h["deposit"]["docs"][0]["status_label"] == "Sebagian terpakai"
    assert h["proforma"]["docs"][0]["info"] == "Termin 1"
    assert h["invoice"]["docs"][0]["info"] == "Sisa Rp 5"
    assert h["delivery"]["docs"][0]["amount"] is None and h["delivery"]["docs"][0]["info"] == "2 pcs · Faktur SI-0"


def test_jumlah_kueri_tetap_tanpa_n_plus_1():
    a, b = Conn(n=1), Conn(n=25)
    _jalan(a), _jalan(b)
    assert len(a.q) == len(b.q)


def test_filter_tenant_eksplisit_di_setiap_kueri_dan_draf_tak_ikut():
    c = Conn(n=1)
    _jalan(c)
    for sql, args in c.q:
        assert T in args, sql[:80]
        if sql.startswith(("muat_", "terbayar_")) or "compute_ar" in sql:
            continue
        assert "tenant_id" in sql, sql[:80]
    # predikat tenant milik TABEL UTAMA (bukan sekadar kata tenant_id di JOIN) -- sabotase "buang cn.tenant_id" merah
    wajib = {"FROM quotes": "WHERE tenant_id = $1", "FROM proformas": "WHERE tenant_id = $1",
             "FROM customer_deposits cd": "cd.tenant_id = $1", "FROM invoice_fulfillments f": "f.tenant_id = $1",
             "FROM receive_payments rp": "rp.tenant_id = $1", "FROM credit_notes cn": "cn.tenant_id = $1",
             "invoice_number, invoice_date, total_amount, status FROM sales_invoices": "WHERE tenant_id = $1",
             "quote_id FROM sales_orders": "tenant_id = $2"}
    for kunci, pred in wajib.items():
        sql = next(s for s, _ in c.q if kunci in s)
        assert pred in sql, kunci
    for tabel in ("FROM quotes", "FROM proformas", "FROM customer_deposits", "FROM receive_payments", "FROM credit_notes"):
        sql = next(s for s, _ in c.q if tabel in s)
        assert "<> 'draft'" in sql, tabel
    assert any("FROM sales_invoices" in s and "status <> 'draft'" in s for s, _ in c.q)


def test_so_tak_ada_404():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        _jalan(Conn(so=False))
    assert e.value.status_code == 404


def test_rute_bentuk_terkait_aditif_bundel_lama_tetap():
    src = inspect.getsource(D.dokumen_pesanan)
    assert 'bentuk == "terkait"' in src and "susun_terkait" in src
    assert "return await susun_dokumen(conn, ctx, _uuid(order_id), sertakan_nk=(sertakan == \"nota_kredit\"))" in src
