"""BUG-097: GET /purchase-orders (daftar) + /{id} (detail) menghitung draft_bills / belum_ditagih_sesudah_draf (pill "Buka faktur
draf", BUG-089) dan terima_tersedia / tagih_tersedia (PO jasa, ROW 90) -- tetapi response_model tak mendeklarasikannya, jadi FastAPI
MEMBUANGNYA DIAM-DIAM dan FE selalu menawarkan "Buat faktur pembelian". Penjaga umum: SETIAP kunci literal yang dirakit rute
untuk satu baris PO harus ada di skemanya."""
import ast
import pathlib

from app.schemas import purchase_orders as S

RUTE = pathlib.Path(__file__).resolve().parents[2] / "app" / "routers" / "purchase_orders.py"
FUNGSI = {"list_purchase_orders": S.PurchaseOrderListItem, "get_purchase_order": S.PurchaseOrderDetail}


def _kamus_baris_po(nama):
    pohon = ast.parse(RUTE.read_text())
    f = next(n for n in pohon.body if isinstance(n, ast.AsyncFunctionDef) and n.name == nama)
    hasil = [[k.value for k in n.keys if isinstance(k, ast.Constant)] for n in ast.walk(f) if isinstance(n, ast.Dict)]
    return [ks for ks in hasil if "po_number" in ks]


def test_semua_kunci_baris_po_dideklarasikan_skema():
    for nama, model in FUNGSI.items():
        kamus = _kamus_baris_po(nama)
        assert kamus, nama  # kontrol: penjaga benar-benar menemukan kamus baris PO
        for ks in kamus:
            assert [k for k in ks if k not in model.model_fields] == [], nama


def test_detail_menyampaikan_draf_dan_tersedia():
    d = S.PurchaseOrderDetailResponse.model_validate({"data": {
        "id": "p1", "po_number": "PO-1", "vendor_name": "V", "subtotal": 0, "total_amount": 0, "status": "received",
        "po_date": "2026-11-07", "created_at": "x", "updated_at": "x", "belum_ditagih_sesudah_draf": 0.0,
        "draft_bills": [{"id": "b1", "bill_number": "BILL-2611-0008"}], "terima_tersedia": False, "tagih_tersedia": True}}).model_dump()["data"]
    assert d["draft_bills"] == [{"id": "b1", "bill_number": "BILL-2611-0008"}]
    assert (d["belum_ditagih_sesudah_draf"], d["terima_tersedia"], d["tagih_tersedia"]) == (0.0, False, True)


def test_daftar_menyampaikan_draf():
    x = S.PurchaseOrderListItem.model_validate({
        "id": "p1", "po_number": "PO-1", "vendor_name": "V", "po_date": "2026-11-07", "total_amount": 0, "status": "received",
        "created_at": "x", "draft_bills": [{"id": "b1", "bill_number": "BILL-1"}], "belum_ditagih_sesudah_draf": 0.0}).model_dump()
    assert x["draft_bills"][0]["bill_number"] == "BILL-1" and x["belum_ditagih_sesudah_draf"] == 0.0
