"""Nomor pesanan di faktur penjualan (permintaan pemilik 25 Sep 2026, kontrak MASTER 1-4).

Diukur sebelum: detail GET sudah membawa sales_order_number (JOIN tanpa pagar tenant); daftar TIDAK;
pencarian tak mencocokkan nomor SO; PDF memilih sales_order_number tapi TAK meneruskannya ->
tak pernah tercetak di template a maupun b.
"""
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routers import sales_invoices as SI
from app.schemas.sales_invoices import InvoiceListItem
from app.services.pdf_service import TEMPLATE_FAKTUR, get_pdf_service

TENANT = "grapgrap-manado"
SRC = Path(SI.__file__).read_text()


def _faktur(**k):
    d = {"invoice_number": "INV-2609-0002", "customer_name": "Budi", "invoice_date": "2026-09-10",
         "due_date": "2026-09-17", "status": "paid", "items": [], "subtotal": 0, "total_amount": 0,
         "ref_no": None, "purchase_order_no": None, "delivery_order_no": None}
    d.update(k)
    return d


def _html(template, **k):
    svc = get_pdf_service()
    return svc.jinja_env.get_template(TEMPLATE_FAKTUR[template]).render(**svc._konteks_faktur(_faktur(**k)))


# ---------- PDF: template a & b ----------

@pytest.mark.parametrize("tpl,label", [("a", "No. Pesanan"), ("b", "Sales Order No.")])
def test_pdf_mencetak_nomor_pesanan_bila_tertaut(tpl, label):
    h = _html(tpl, sales_order_number="002-09-26")
    assert re.search(re.escape(label) + r"</td>\s*<td[^>]*>\s*002-09-26\s*</td>", h), label


@pytest.mark.parametrize("tpl,label", [("a", "No. Pesanan"), ("b", "Sales Order No.")])
def test_pdf_tanpa_tautan_tanpa_baris(tpl, label):
    assert label not in _html(tpl, sales_order_number=None)
    assert label not in _html(tpl)


def test_pdf_a_referensi_tetap_terpisah():
    h = _html("a", sales_order_number="002-09-26", ref_no="REF-9")
    assert "No. Pesanan" in h and "Referensi" in h and "REF-9" in h


def test_pdf_b_po_pelanggan_bukan_nomor_so():
    h = _html("b", sales_order_number="002-09-26", purchase_order_no="PO-CUST-1")
    po = re.search(r"Purchase Order No\.</td><td[^>]*>([^<]*)</td>", h).group(1)
    assert po == "PO-CUST-1"


def test_data_pdf_meneruskan_nomor_so():
    # invoice_data PDF harus memuat sales_order_number (dulu dipilih kueri tapi tak diteruskan)
    blok = SRC[SRC.index("invoice_data = {"):SRC.index("pdf_bytes = pdf_service.generate_sales_invoice_pdf")]
    assert '"sales_order_number": invoice["sales_order_number"]' in blok


# ---------- pagar tenant JOIN SO ----------

def test_semua_join_so_berpagar_tenant():
    semua = re.findall(r"LEFT JOIN sales_orders so ON so\.id = si\.sales_order_id[^\n]*", SRC)
    assert len(semua) >= 3                                  # daftar, detail, PDF
    assert all("AND so.tenant_id = si.tenant_id" in j for j in semua), semua


# ---------- daftar: medan + pencarian ----------

def test_skema_daftar_mendeklarasikan_medan():
    assert {"sales_order_id", "sales_order_number"} <= set(InvoiceListItem.model_fields)


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


def _baris(so_id, so_no):
    return {"id": uuid.uuid4(), "invoice_number": "INV-1", "customer_id": None, "customer_name": "Budi",
            "invoice_date": date(2026, 9, 10), "due_date": date(2026, 9, 17), "total_amount": 100,
            "journal_paid": 100, "status": "paid", "operational_status": "PAID", "accounting_status": "POSTED",
            "fulfillment_status": None, "revenue_status": None, "created_at": datetime(2026, 9, 10),
            "sales_order_id": so_id, "sales_order_number": so_no, "is_overdue": False}


async def _daftar(monkeypatch, rows, search=None):
    db = DB(rows)

    async def _pool():
        return Pool(db)
    monkeypatch.setattr(SI, "get_pool", _pool)

    async def _hari(conn, tid):          # Q-014: daftar kini selalu membaca tanggal bisnis -> tanpa kueri zona
        return date(2026, 9, 25)
    monkeypatch.setattr(SI, "tanggal_dokumen", _hari)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": None}), headers={})
    out = await SI.list_invoices(req, skip=0, limit=20, search=search, status=None, customer_id=None,
                                 start_date=None, end_date=None, sort_by="created_at", sort_order="desc",
                                 amount_min=None, amount_max=None)
    return db, out


@pytest.mark.asyncio
async def test_daftar_membawa_nomor_so_lewat_response_model(monkeypatch):
    so = uuid.uuid4()
    db, out = await _daftar(monkeypatch, [_baris(so, "002-09-26"), _baris(None, None)])
    items = [InvoiceListItem.model_validate(i).model_dump() for i in out["items"]]
    assert (items[0]["sales_order_id"], items[0]["sales_order_number"]) == (str(so), "002-09-26")
    assert (items[1]["sales_order_id"], items[1]["sales_order_number"]) == (None, None)
    q = db.sql[-1]
    assert "so.order_number AS sales_order_number" in q
    assert "LEFT JOIN sales_orders so ON so.id = si.sales_order_id AND so.tenant_id = si.tenant_id" in q


@pytest.mark.asyncio
@pytest.mark.parametrize("search,n_kata", [("002-09-26", 1), ("budi 002-09", 2)])
async def test_pencarian_cocok_nomor_so_berpagar_tenant(monkeypatch, search, n_kata):
    db, _ = await _daftar(monkeypatch, [], search=search)
    for sql in db.sql:                                          # COUNT dan SELECT memakai WHERE yang sama
        klausa = re.findall(r"si\.sales_order_id IN \(SELECT so2\.id FROM sales_orders so2 WHERE "
                            r"so2\.tenant_id = \$1 AND so2\.order_number ILIKE \$(\d+)\)", sql)
        assert len(klausa) == n_kata, sql
    args = db.args[0]
    assert args[0] == TENANT
    for nomor in re.findall(r"so2\.order_number ILIKE \$(\d+)", db.sql[0]):
        assert args[int(nomor) - 1].strip("%") in search.split()   # $N menunjuk kata yang benar
