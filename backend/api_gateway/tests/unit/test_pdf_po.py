"""PDF Pesanan Pembelian (22 Okt 2026, tiket WORKSPACE "PDF PO"): /documents/purchase_order/{id}/html|pdf.
Kontrak: services/po_cetak.muat_pdf_po (tenant eksplisit, 404, baris neto) + pdf_service.render_purchase_order (stempel DRAF/DIBATALKAN)
+ routers/dokumen JENIS_CETAK (cetak) TERPISAH dari JENIS (bagikan; CHECK V379) + izin GET purchase_order R."""
import asyncio
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import dokumen as DK
from app.services import po_cetak as PC
from app.services.pdf_service import PDFService

APP = Path(__file__).resolve().parents[2] / "app"
TID = "tenant-uji"


def test_jenis_cetak_terpisah_dari_jenis_bagikan():
    assert "purchase_order" in DK.JENIS_CETAK
    assert "purchase_order" not in DK.JENIS  # bagikan butuh kind di CHECK document_shares (V379) -> migrasi tersendiri
    assert DK.JENIS_CETAK[:len(DK.JENIS)] == DK.JENIS


def test_rute_cetak_pakai_jenis_cetak_rute_bagikan_tetap_jenis():
    src = (APP / "routers" / "dokumen.py").read_text()
    for nama in ("html_dokumen", "pdf_dokumen"):
        badan = src.split(f"async def {nama}(", 1)[1].split("\n@router", 1)[0]
        assert "if kind not in JENIS_CETAK:" in badan, nama
    for nama in ("bagikan_dokumen", "pratinjau_bagikan_dokumen"):
        badan = src.split(f"async def {nama}(", 1)[1].split("\n@router", 1)[0]
        assert "if kind not in JENIS:" in badan and "JENIS_CETAK" not in badan, nama


def test_izin_cetak_po():
    src = (APP / "middleware" / "permission_middleware.py").read_text()
    assert '(r"^/api/documents/purchase_order/[^/]+/(html|pdf)$", ["GET"], "purchase_order", "R"),' in src
    pola = re.compile(r"^/api/documents/purchase_order/[^/]+/(html|pdf)$")
    assert pola.match("/api/documents/purchase_order/abc/pdf") and not pola.match("/api/documents/purchase_order/abc/share")


def test_alamat_vendor_dan_label_pajak():
    v = {"address": "Jl. Mawar 1 ", "city": "Bandung", "province": "Jawa Barat", "postal_code": "40111"}
    assert PC.alamat_vendor(v) == "Jl. Mawar 1\nBandung, Jawa Barat 40111"
    assert PC.alamat_vendor({"address": None, "city": None, "province": None, "postal_code": None}) is None
    assert PC.alamat_vendor(None) is None
    assert PC.label_pajak(Decimal("11.00")) == "Pajak 11%"
    assert PC.label_pajak(0) == "Pajak" and PC.label_pajak(None) == "Pajak"


class _Conn:
    def __init__(self, po, vendor=None, items=(), kop=None):
        self.po, self.vendor, self.items, self.sql = po, vendor, list(items), []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        if "FROM purchase_orders" in sql:
            return self.po if self.po and a[1] == TID else None
        if "FROM vendors" in sql:
            assert a[1] == TID
            return self.vendor
        if '"Tenant"' in sql:
            return {"display_name": "Usaha Uji", "address": "Jl. Pabrik 2", "phone": "0812", "logo_url": None}
        raise AssertionError(sql)

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        assert "FROM purchase_order_items" in sql
        return self.items


def _po(**k):
    d = {"id": uuid.uuid4(), "po_number": "PO-2610-0001", "status": "sent", "po_date": date(2026, 10, 22), "expected_date": date(2026, 10, 29),
         "ref_no": "REF-9", "ship_to_address": None, "warehouse_name": "Gudang Utama", "notes": "Kirim pagi", "cancelled_at": None,
         "cancelled_reason": None, "vendor_id": uuid.uuid4(), "vendor_name": "Nama Lama", "subtotal": Decimal("190000.00"),
         "discount_percent": Decimal("0"), "discount_amount": Decimal("0"), "tax_rate": Decimal("11"), "tax_amount": Decimal("20900.00"),
         "total_amount": Decimal("210900.00")}
    d.update(k)
    return d


ITEMS = [{"description": "Kain katun", "item_code": "K1", "quantity": Decimal("2"), "unit": "m", "unit_price": Decimal("50000"),
          "discount_percent": Decimal("10"), "discount_amount": Decimal("10000"), "subtotal": Decimal("100000"), "total": Decimal("99900")},
         {"description": "Benang", "item_code": None, "quantity": Decimal("1"), "unit": None, "unit_price": Decimal("100000"),
          "discount_percent": Decimal("0"), "discount_amount": Decimal("0"), "subtotal": Decimal("100000"), "total": Decimal("111000")}]
VENDOR = {"name": "CV Tekstil", "address": "Jl. Mawar 1", "city": "Bandung", "province": None, "postal_code": None, "phone": None,
          "mobile_phone": "0813", "email": None, "tax_id": "01.234"}


def test_muat_404_tenant_lain():
    c = _Conn(None)
    with pytest.raises(HTTPException) as e:
        asyncio.run(PC.muat_pdf_po(c, {"tenant_id": TID}, uuid.uuid4()))
    assert e.value.status_code == 404
    sql = c.sql[0][0]
    assert "po.tenant_id = $2" in sql and "w.tenant_id = po.tenant_id" in sql


def test_muat_baris_neto_vendor_dan_sen():
    c = _Conn(_po(), VENDOR, ITEMS)
    m = asyncio.run(PC.muat_pdf_po(c, {"tenant_id": TID}, uuid.uuid4()))
    d = m["po_data"]
    assert [b["line_total"] for b in d["items"]] == [Decimal("90000"), Decimal("100000")]  # subtotal - diskon baris
    assert sum(b["line_total"] for b in d["items"]) == d["subtotal"]
    assert d["vendor_name"] == "CV Tekstil" and d["vendor_telepon"] == "0813" and d["vendor_alamat"] == "Jl. Mawar 1\nBandung"
    assert d["label_pajak"] == "Pajak 11%" and d["has_cents"] is False
    assert m["tenant_info"]["name"] == "Usaha Uji"
    m2 = asyncio.run(PC.muat_pdf_po(_Conn(_po(total_amount=Decimal("210900.50")), VENDOR, ITEMS), {"tenant_id": TID}, uuid.uuid4()))
    assert m2["po_data"]["has_cents"] is True


def test_muat_tanpa_vendor_pakai_nama_tersimpan():
    m = asyncio.run(PC.muat_pdf_po(_Conn(_po(vendor_id=None), None, []), {"tenant_id": TID}, uuid.uuid4()))
    assert m["po_data"]["vendor_name"] == "Nama Lama" and m["po_data"]["vendor_alamat"] is None


def _html(**k):
    m = asyncio.run(PC.muat_pdf_po(_Conn(_po(**k), VENDOR, ITEMS), {"tenant_id": TID}, uuid.uuid4()))
    return PDFService().render_purchase_order(m["po_data"], m["tenant_info"]).html


def test_render_isi_dan_tanpa_stempel_saat_hidup():
    h = _html()
    for teks in ("Pesanan Pembelian", "PO-2610-0001", "CV Tekstil", "NPWP 01.234", "Kain katun", "Diskon 10%", "Pajak 11%",
                 "Rp 210.900", "Kirim pagi", "Gudang Utama", "REF-9"):
        assert teks in h, teks
    assert "DIBATALKAN" not in h and "Belum dikirim ke vendor" not in h


def test_render_draf_dan_batal():
    assert "Belum dikirim ke vendor" in _html(status="draft")
    h = _html(status="cancelled", cancelled_at=datetime(2026, 10, 22, 3, 0, tzinfo=timezone.utc), cancelled_reason="Salah vendor")
    assert "DIBATALKAN" in h and "Salah vendor" in h


def test_render_alamat_kirim_menang_atas_gudang():
    h = _html(ship_to_address="Jl. Kirim 5")
    assert "Jl. Kirim 5" in h and "Gudang Utama" not in h
