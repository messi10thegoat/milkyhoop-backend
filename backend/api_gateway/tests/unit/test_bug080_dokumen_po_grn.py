"""BUG-080: bundel dokumen PO (services/po_daftar.dokumen_po = detail /{id}/documents + kolom Dok daftar) memuat Penerimaan Barang (GRN)
bertaut purchase_order_id -- dulu hanya PO + faktur + UM + bayar, padahal Riwayat menulis "Barang diterima". Urutan alur: po -> grn -> faktur
(== bundel berjangkar GRN, grn_daftar.kelompok_grn_banyak); dokumen GRN = grn_daftar.dok_goods_receipt (SATU pembentuk); GRN batal tetap
tampil bertanda batal (== faktur draf/void tampil)."""
import asyncio
import uuid
from datetime import date

from app.services import po_daftar as PD

PO, G1, G2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class Conn:
    def __init__(self):
        self.sql = []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "FROM purchase_orders" in sql:
            return [{"id": PO, "po_number": "PO-2611-0004", "po_date": date(2026, 11, 5), "total_amount": 1000, "status": "received", "sent_at": None}]
        if "FROM goods_receipts" in sql:
            assert "tenant_id = $1" in sql and a[0] == "t1"
            return [{"id": G1, "purchase_order_id": PO, "receipt_number": "GRN-2611-0004", "receipt_date": date(2026, 11, 6), "status": "posted", "amount": 1000},
                    {"id": G2, "purchase_order_id": PO, "receipt_number": "GRN-2611-0009", "receipt_date": date(2026, 11, 7), "status": "void", "amount": 0}]
        return []


def test_bundel_po_memuat_grn_sesudah_po():
    c = Conn()
    kel = asyncio.run(PD.dokumen_po(c, "t1", [PO]))[PO]
    assert [g["key"] for g in kel] == ["po", "grn"]
    grn = kel[1]
    assert grn["label"] == "Penerimaan Barang"
    assert [(d["kind"], d["number"], d["status"]) for d in grn["docs"]] == [
        ("goods_receipt", "GRN-2611-0004", "terbit"), ("goods_receipt", "GRN-2611-0009", "batal")]


def test_documents_baris_po_menghitung_grn():
    kel = asyncio.run(PD.dokumen_po(Conn(), "t1", [PO]))[PO]
    assert [d["type"] for d in PD.documents_baris(kel)] == ["purchase_order", "goods_receipt", "goods_receipt"]
