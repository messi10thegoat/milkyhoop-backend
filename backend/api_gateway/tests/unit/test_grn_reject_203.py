"""BUG-203: barang ditolak TERSIMPAN di GRN (V477) dan tampil di API; respons terima memuat sisa + petunjuk + pointer tutup PO; items kosong: pratinjau = bawaan semua sisa
(bawaan_semua_sisa), tulis = 422 berkode ITEMS_KOSONG. Paritas nyata (stok/tagihan/sisa PO tak berubah) = uji salinan."""
import asyncio
import datetime as dt
import inspect
import uuid
from decimal import Decimal as D

import pytest
from starlette.requests import Request

import app.routers.goods_receipts as GR
import app.routers.purchase_orders as PO
from app.schemas.purchase_orders import ReceiveGoodsRequest
from app.services import grn_ditolak as GT
from app.services.galat_berkode import GalatBerkode

T = "kaos-biru-konveksi"
PID = uuid.uuid4()


def req():
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = {"tenant_id": T, "user_id": "00000000-0000-0000-0000-000000000001"}
    return r


def jalan(aw):
    return asyncio.run(aw)


@pytest.mark.parametrize("qty,unit,alasan,teks", [(2, "Pcs", "rusak", "Ditolak 2 Pcs · rusak"), (D("1.5000"), "Kg", None, "Ditolak 1.5 Kg"), (3, None, " bocor ", "Ditolak 3 · bocor"),
                                                 (0, "Pcs", "x", ""), (None, "Pcs", "x", ""), (D("0.0000"), "Pcs", None, "")])
def test_ditolak_text(qty, unit, alasan, teks):
    assert GT.ditolak_text(qty, unit, alasan) == teks


def test_petunjuk_sisa_menyebut_aksi_tutup():
    t = GT.petunjuk_sisa(2)
    assert "2 barang" in t and "menunggu kirim ulang" in t and "tutup pesanan" in t


def test_detail_grn_memuat_reject_dari_kolom_tersimpan():
    gr = {"id": uuid.uuid4(), "receipt_number": "GRN-1", "purchase_order_id": uuid.uuid4(), "receipt_date": dt.date(2026, 11, 13), "status": "posted"}
    baris = {"id": uuid.uuid4(), "purchase_order_item_id": uuid.uuid4(), "description": "Kain", "unit": "Pcs", "quantity": D("6"), "quantity_returned": D("0"),
             "quantity_billed": D("0"), "unit_cost": D("1000"), "amount": D("6000"), "ber_stok": True, "quantity_rejected": D("2"), "reject_reason": "rusak"}
    it = GR._bentuk(gr, [baris])["items"][0]
    assert (it["quantity_rejected"], it["reject_reason"], it["ditolak_text"]) == ("2", "rusak", "Ditolak 2 Pcs · rusak")
    tanpa = GR._bentuk(gr, [{**baris, "quantity_rejected": D("0"), "reject_reason": None}])["items"][0]
    assert (tanpa["quantity_rejected"], tanpa["reject_reason"], tanpa["ditolak_text"]) == ("0", None, "")
    assert it["quantity"] == "6" and it["amount"] == "6000.00"                       # angka lama tak berubah (stok/nilai hanya dari yang diterima)


def test_tulis_items_kosong_422_berkode_dan_skema_menerima_kosong():
    assert ReceiveGoodsRequest(receive_date=dt.date(2026, 11, 13), items=[]).items == []
    with pytest.raises(GalatBerkode) as e:
        jalan(PO.receive_goods(req(), PID, ReceiveGoodsRequest(receive_date=dt.date(2026, 11, 13), items=[])))
    assert (e.value.status_code, e.value.code, e.value.detail) == (422, "ITEMS_KOSONG", "Pilih barang yang diterima; pratinjau memberi daftar sisa.")


@pytest.fixture
def pratinjau(monkeypatch):
    async def fp(request, po_id, isi):
        return await isi(None, {"tenant_id": T}, {"tenant_id": T, "status": "sent"})

    async def hari(conn, tid):
        return dt.date(2026, 11, 13)

    async def rencana(conn, po, receive_date, items, hari_ini, **kw):
        return {"blocks": [], "status_sesudah": "received", "payload": {"receive_date": "2026-11-13", "items": []}, "baris": [], "toleransi_persen": D("0"),
                "nilai_diterima": D("0"), "amount_received_sesudah": D("0"), "notes": []}

    async def grn(conn, tid, po, r, wid, sj=None):
        return {}
    monkeypatch.setattr(PO, "_pratinjau", fp)
    monkeypatch.setattr(PO, "tanggal_dokumen", hari)
    monkeypatch.setattr(PO.PR, "rencana_terima", rencana)
    monkeypatch.setattr(PO, "_pratinjau_grn", grn)


def test_pratinjau_kosong_menandai_bawaan_semua_sisa(pratinjau):
    from app.schemas.purchase_orders import ReceivePreviewRequest, ReceiveItemRequest
    assert jalan(PO.preview_receive_goods(req(), PID, None))["bawaan_semua_sisa"] is True
    assert jalan(PO.preview_receive_goods(req(), PID, ReceivePreviewRequest(items=[])))["bawaan_semua_sisa"] is True
    eksplisit = ReceivePreviewRequest(items=[ReceiveItemRequest(po_item_id=str(uuid.uuid4()), quantity_received=D("1"))])
    assert jalan(PO.preview_receive_goods(req(), PID, eksplisit))["bawaan_semua_sisa"] is False


def test_respons_terima_memuat_sisa_ditolak_dan_pointer_tutup():
    src = inspect.getsource(PO.receive_goods)
    for jangkar in ('"sisa_belum_diterima": _sisa', '"ditolak": _ditolak', 'GT.petunjuk_sisa(len(_sisa))', '"tutup_po": {"rute": f"/api/purchase-orders/{po_id}/close"',
                    "if _sisa else {}"):
        assert jangkar in src
    assert "if not body.items:" in src and src.index("if not body.items:") < src.index("get_user_context(request)")   # penolakan sebelum menyentuh DB/pengguna
