"""Kontrak PO tahap 2 (22 Okt 2026): /calculate + /{id}/edit|send|receive|close|cancel|delete/preview.
Penentu = services/po_rencana, dipakai endpoint TULIS dan PRATINJAU. Uji: PARITAS (tulis menolak <=> pratinjau ber-blok, pesan = blok
pertama), SEMUA blok dilaporkan, ROLLBACK selalu (nol tulisan), angka = kalkulator create, payload siap kirim."""
import asyncio
import uuid
from datetime import date
from decimal import Decimal as D

import pytest
from fastapi import HTTPException

from app.routers import purchase_orders as PO
from app.schemas import purchase_orders as PS
from app.services import po_hitung as PH
from app.services import po_rencana as PR

T = "kaos-biru-konveksi"
PID, VID = uuid.uuid4(), uuid.uuid4()
I1, I2 = uuid.uuid4(), uuid.uuid4()
HARI = date(2026, 10, 22)


class _Tr:
    def __init__(self, kon, readonly=False): self.kon = kon; kon.readonly.append(readonly)
    async def start(self): self.kon.mulai += 1
    async def rollback(self): self.kon.rollback += 1
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Req:
    class state:
        user = {"tenant_id": T, "user_id": str(uuid.uuid4())}


class Kon:
    def __init__(self, status="sent", items=None, vendor=True, deposit=(), po_date=date(2026, 10, 1)):
        self.po = {"id": PID, "tenant_id": T, "status": status, "po_number": "PO-2610-0001", "po_date": po_date,
                   "discount_percent": D("0"), "discount_amount": D("0"), "tax_rate": D("0"), "total_amount": D("1000"),
                   "notes": None, "vendor_id": VID}
        self.items = items if items is not None else [
            {"id": I1, "description": "Kain", "quantity": D("10"), "quantity_received": D("3"), "quantity_billed": D("0"), "unit": "m",
             "total": D("1000"), "subtotal": D("1000"), "discount_amount": D("0"), "tax_amount": D("0")}]
        self.vendor, self.deposit = vendor, list(deposit)
        self.writes, self.mulai, self.rollback, self.readonly, self.q = [], 0, 0, [], []

    def transaction(self, readonly=False): return _Tr(self, readonly)

    async def execute(self, sql, *a):
        q = " ".join(sql.split()); self.q.append(q)
        if q.startswith(("INSERT", "UPDATE", "DELETE")):
            self.writes.append(q[:70])
        return "OK"

    async def fetchrow(self, sql, *a):
        q = " ".join(sql.split()); self.q.append(q)
        if "FROM vendors" in q: return {"id": VID, "name": "Vendor X"} if self.vendor else None
        if "FROM purchase_orders" in q: return self.po
        if "SUM(quantity_received)" in q and "AS diterima" in q:
            return {"diterima": sum((i["quantity_received"] for i in self.items), D(0)),
                    "ditagih": sum((i["quantity_billed"] for i in self.items), D(0))}
        if "AS sisa_terima" in q:
            return {"sisa_terima": sum((max(i["quantity"] - i["quantity_received"], D(0)) for i in self.items), D(0)),
                    "belum_ditagih": sum((max(i["quantity_received"] - i["quantity_billed"], D(0)) for i in self.items), D(0))}
        if "FROM purchase_order_items" in q and "WHERE id = $1" in q:
            return next((i for i in self.items if i["id"] == a[0]), None)
        return None

    async def fetch(self, sql, *a):
        q = " ".join(sql.split()); self.q.append(q)
        if "FROM purchase_order_items" in q: return self.items
        if "FROM vendor_deposits" in q: return self.deposit
        return []

    async def fetchval(self, sql, *a):
        return None


@pytest.fixture
def kon(monkeypatch):
    k = {"v": None}
    def pasang(kn):
        k["v"] = kn
        class CM:
            async def __aenter__(s): return kn
            async def __aexit__(s, *a): return False
        class P:
            def acquire(s): return CM()
        async def gp(): return P()
        monkeypatch.setattr(PO, "get_pool", gp)
        async def hari(conn, tid): return HARI
        monkeypatch.setattr(PO, "tanggal_dokumen", hari)
        async def catat(*a, **kw): return None
        monkeypatch.setattr(PO, "catat_riwayat", catat)
        return kn
    return pasang


def run(c): return asyncio.run(c)


# ---------------------------------------------------------------- calculate
def _body(**kw):
    d = dict(vendor_id=str(VID), vendor_name="Vendor X", po_date=HARI,
             items=[PS.PurchaseOrderItemCreate(description="Kain", quantity=D("3"), unit_price=D("33.34"), tax_rate=11, discount_percent=5)],
             discount_percent=10, tax_rate=0)
    d.update(kw)
    return PS.CreatePurchaseOrderRequest(**d)


def test_calculate_sama_dengan_kalkulator_create(kon):
    kon(Kon())
    b = _body()
    r = run(PO.calculate_purchase_order(_Req(), b))["data"]
    baris = [PH.hitung_baris(i.model_dump()) for i in b.items]
    t = PH.hitung_header(baris, b.discount_percent, b.discount_amount, b.tax_rate)
    assert r["ok"] is True and r["blocks"] == []
    assert (r["subtotal"], r["discount_amount"], r["tax_amount"], r["total_amount"]) == tuple(
        float(t[k]) for k in ("subtotal", "discount_amount", "tax_amount", "total_amount"))
    assert r["items"][0]["total"] == float(baris[0]["total"]) and r["items"][0]["line_number"] == 1
    assert r["payload"]["vendor_id"] == str(VID) and r["payload"]["items"][0]["unit_price"] == "33.34"


def test_calculate_vendor_bukan_tenant_blok_bukan_galat(kon):
    kon(Kon(vendor=False))
    r = run(PO.calculate_purchase_order(_Req(), _body()))["data"]
    assert r["ok"] is False and r["blocks"] == [{"code": "VENDOR_TIDAK_DITEMUKAN", "message": "Vendor tidak ditemukan di usaha ini."}]
    assert r["total_amount"] is not None  # angka tetap dihitung


# ---------------------------------------------------------------- paritas tulis <-> pratinjau
TULIS = {
    "send": (lambda: PO.send_purchase_order(_Req(), PID), lambda: PO.preview_send_purchase_order(_Req(), PID)),
    "close": (lambda: PO.close_purchase_order(_Req(), PID), lambda: PO.preview_close_purchase_order(_Req(), PID)),
    "cancel": (lambda: PO.cancel_purchase_order(_Req(), PID, PS.CancelPurchaseOrderRequest(reason="salah")),
               lambda: PO.preview_cancel_purchase_order(_Req(), PID, PS.CancelPreviewRequest(reason="salah"))),
    "delete": (lambda: PO.delete_purchase_order(_Req(), PID), lambda: PO.preview_delete_purchase_order(_Req(), PID)),
    "edit": (lambda: PO.update_purchase_order(_Req(), PID, PS.UpdatePurchaseOrderRequest(notes="x")),
             lambda: PO.preview_edit_purchase_order(_Req(), PID, PS.UpdatePurchaseOrderRequest(notes="x"))),
}
STATUS = ["draft", "sent", "partial_received", "received", "partial_billed", "billed", "closed", "cancelled"]


@pytest.mark.parametrize("aksi", list(TULIS))
@pytest.mark.parametrize("status", STATUS)
def test_paritas_tulis_menolak_iff_pratinjau_berblok(kon, aksi, status):
    items = [{"id": I1, "description": "Kain", "quantity": D("10"), "unit": "m", "total": D("1000"), "subtotal": D("1000"),
              "discount_amount": D("0"), "tax_amount": D("0"),
              "quantity_received": D("0") if status in ("draft", "sent", "cancelled") else D("10"),
              "quantity_billed": D("10") if status in ("billed", "partial_billed") else D("0")}]
    tulis, pratinjau = TULIS[aksi]
    k = kon(Kon(status=status, items=[dict(i) for i in items]))
    p = run(pratinjau())["data"]
    assert k.writes == [] and k.rollback == 1, "pratinjau wajib ROLLBACK tanpa tulisan"
    assert k.readonly == [True], "pratinjau = transaksi READ ONLY"
    assert not any("FOR UPDATE" in q or "pg_advisory" in q for q in k.q), "pratinjau tak mengunci"
    kon(Kon(status=status, items=[dict(i) for i in items]))
    if p["blocks"]:
        with pytest.raises(HTTPException) as e:
            run(tulis())
        detail = e.value.detail
        pesan = detail if isinstance(detail, str) else detail["message"]
        assert pesan == p["blocks"][0]["message"], (aksi, status)
    else:
        assert run(tulis())["success"] is True, (aksi, status)


# ---------------------------------------------------------------- terima
def test_terima_bawaan_semua_sisa(kon):
    k = kon(Kon(status="partial_received"))
    d = run(PO.preview_receive_goods(_Req(), PID, None))["data"]
    assert d["ok"] and d["status_sesudah"] == "received" and d["position_text_sesudah"]
    assert d["items"][0] == {"po_item_id": str(I1), "description": "Kain", "unit": "m", "qty": 10.0, "sudah_diterima": 3.0, "sisa": 7.0,
                             "akan_diterima": 7.0}
    assert d["nilai_diterima"] == 700.0 and d["amount_received_sesudah"] == 1000.0
    assert d["payload"] == {"receive_date": "2026-10-22", "items": [{"po_item_id": str(I1), "quantity_received": "7"}]}
    assert k.writes == [] and k.rollback == 1


def test_terima_semua_blok_dilaporkan_dan_tulis_menolak_dengan_blok_pertama(kon):
    kon(Kon(status="sent"))
    body = PS.ReceivePreviewRequest(receive_date=date(2026, 10, 30), items=[PS.ReceiveItemRequest(po_item_id=str(I1), quantity_received="8"),
                                                                            PS.ReceiveItemRequest(po_item_id=str(I2), quantity_received="1")])
    d = run(PO.preview_receive_goods(_Req(), PID, body))["data"]
    assert [b["code"] for b in d["blocks"]] == ["TANGGAL_TERIMA_MASA_DEPAN", "QTY_MELEBIHI_SISA", "BARIS_TIDAK_DITEMUKAN"]
    kon(Kon(status="sent"))
    with pytest.raises(HTTPException) as e:
        run(PO.receive_goods(_Req(), PID, PS.ReceiveGoodsRequest(receive_date=date(2026, 10, 30), items=body.items)))
    assert e.value.detail == d["blocks"][0]["message"]


def test_terima_status_salah_dan_tak_ada_sisa(kon):
    kon(Kon(status="received", items=[{"id": I1, "description": "Kain", "quantity": D("10"), "quantity_received": D("10"),
                                       "quantity_billed": D("0"), "unit": None, "total": D("1000")}]))
    d = run(PO.preview_receive_goods(_Req(), PID, None))["data"]
    assert [b["code"] for b in d["blocks"]] == ["PO_TAK_BISA_DITERIMA", "TAK_ADA_SISA_TERIMA"] and d["payload"]["items"] == []


@pytest.mark.parametrize("status", ["partial_billed", "billed"])
def test_terima_po_sudah_ditagih_hanya_aturan_d(kon, status):
    """GRN aturan D (pemilik 23 Okt): tagihan boleh lebih dulu -> PO ber-status tagih tetap bisa diterima HANYA saat flag GRN nyala; status
    penagihan TIDAK diturunkan. Flag mati = perilaku lama (blok PO_TAK_BISA_DITERIMA; jalur lama memasukkan stok saat tagihan)."""
    baris = [{"id": I1, "description": "Kain", "quantity": D("10"), "quantity_received": D("0"), "quantity_billed": D("10"), "unit": None,
              "total": D("1000")}]
    kon(Kon(status=status, items=baris))
    d = run(PO.preview_receive_goods(_Req(), PID, None))["data"]
    assert [b["code"] for b in d["blocks"]] == ["PO_TAK_BISA_DITERIMA"]

    class KonD(Kon):
        async def fetchval(self, sql, *a):
            return 1 if "tenant_features" in sql else None
    from app.services import grn_rencana as G

    async def grn_kosong(*a, **kw):  # sisi GRN diuji di test_grn; di sini hanya penentu status PO
        return {"blocks": [], "baris": [], "gudang": None, "jurnal": [], "nilai_grni": D("0")}
    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(G, "rencana_grn", grn_kosong)
    kon(KonD(status=status, items=baris))
    d = run(PO.preview_receive_goods(_Req(), PID, None))["data"]
    mp.undo()
    assert "PO_TAK_BISA_DITERIMA" not in [b["code"] for b in d["blocks"]] and d["status_sesudah"] == status
    import inspect
    src = inspect.getsource(PO.receive_goods)
    assert 'po["status"] in PR.STATUS_DITAGIH' in src and "PD.sinkron_tagih(conn, ctx[\"tenant_id\"], po_id)" in src


# ---------------------------------------------------------------- tutup / batal / ubah
def test_tutup_sisa_ditagih_sesudah_nol_dan_catatan(kon):
    kon(Kon(status="partial_received"))
    d = run(PO.preview_close_purchase_order(_Req(), PID))["data"]
    assert d["ok"] and d["status_sesudah"] == "closed" and d["sisa_ditagih_sesudah"] == 0.0
    assert {n["code"] for n in d["notes"]} == {"SISA_BARANG_TAK_DITERIMA", "DITERIMA_BELUM_DITAGIH"}


def test_batal_semua_blok_dan_uang_muka_dicatat(kon):
    kon(Kon(status="partial_received", deposit=[{"deposit_number": "VD-2610-0001"}]))
    d = run(PO.preview_cancel_purchase_order(_Req(), PID, None))["data"]
    assert [b["code"] for b in d["blocks"]] == ["PO_SUDAH_DITERIMA", "ALASAN_WAJIB"]
    assert d["notes"][0]["code"] == "UANG_MUKA_TETAP" and "VD-2610-0001" in d["notes"][0]["message"]
    assert d["payload"] == {"reason": None}


def test_ubah_angka_sesudah_dan_perubahan(kon):
    kon(Kon(status="draft", items=[{"id": I1, "description": "Kain", "quantity": D("10"), "quantity_received": D("0"), "quantity_billed": D("0"),
                                    "unit": "m", "total": D("1000"), "subtotal": D("1000"), "discount_amount": D("0"), "tax_amount": D("0")}]))
    body = PS.UpdatePurchaseOrderRequest(items=[PS.PurchaseOrderItemUpdate(description="Kain", quantity=D("2"), unit_price=D("600.50"))],
                                         notes="baru")
    d = run(PO.preview_edit_purchase_order(_Req(), PID, body))["data"]
    assert d["ok"] and d["total_amount_sesudah"] == 1201.0 and d["items"][0]["total"] == 1201.0
    medan = {p["field"] for p in d["perubahan"]}
    assert {"items", "notes", "total_amount"} <= medan
    assert d["payload"] == body.model_dump(mode="json", exclude_unset=True)


def test_ubah_baris_sudah_diterima_diblok():
    async def jalan():
        class C(Kon):
            pass
        k = C(status="sent")
        body = PS.UpdatePurchaseOrderRequest(items=[PS.PurchaseOrderItemUpdate(description="Kain", quantity=D("1"), unit_price=D("1"))])
        return await PR.rencana_ubah(k, T, k.po, body)
    r = run(jalan())
    assert [b["code"] for b in r["blocks"]] == ["PO_BARIS_SUDAH_DITERIMA"]


def test_pratinjau_404(kon):
    k = Kon(); k.po = None
    kon(k)
    with pytest.raises(HTTPException) as e:
        run(PO.preview_send_purchase_order(_Req(), PID))
    assert e.value.status_code == 404 and k.rollback == 1


# ---------------------------------------------------------------- read-only & tagih
PRATINJAU_SEMUA = [
    lambda: PO.preview_send_purchase_order(_Req(), PID), lambda: PO.preview_close_purchase_order(_Req(), PID),
    lambda: PO.preview_cancel_purchase_order(_Req(), PID, None), lambda: PO.preview_delete_purchase_order(_Req(), PID),
    lambda: PO.preview_receive_goods(_Req(), PID, None), lambda: PO.preview_convert_to_bill(_Req(), PID, None),
    lambda: PO.preview_edit_purchase_order(_Req(), PID, PS.UpdatePurchaseOrderRequest(notes="x")),
]


@pytest.mark.parametrize("i", range(len(PRATINJAU_SEMUA)))
def test_semua_pratinjau_read_only_tanpa_kunci(kon, i):
    k = kon(Kon(status="partial_received", items=[_baris_tagih(quantity_received=D("3"), quantity_billed=D("0"))]))
    run(PRATINJAU_SEMUA[i]())
    assert k.readonly == [True] and k.rollback == 1 and k.writes == []
    assert not any("FOR UPDATE" in q or "pg_advisory" in q for q in k.q), [q for q in k.q if "FOR UPDATE" in q]


def _baris_tagih(**kw):
    d = {"id": I1, "item_id": None, "item_code": None, "description": "Kain", "quantity": D("10"), "quantity_received": D("10"),
         "quantity_billed": D("4"), "unit": "m", "unit_price": D("100.50"), "discount_percent": D("0"), "discount_amount": D("0"),
         "tax_rate": D("11"), "total": D("1115.55"), "subtotal": D("1005"), "tax_amount": D("110.55")}
    d.update(kw)
    return d


def test_tagih_bawaan_sisa_belum_tertagih_dan_angka(kon):
    kon(Kon(status="partial_billed", items=[_baris_tagih()]))
    d = run(PO.preview_convert_to_bill(_Req(), PID, None))["data"]
    assert d["ok"] and d["bill_date"] == "2026-10-22" and d["due_date"] == "2026-10-22"
    it = d["items"][0]
    assert it["quantity"] == 6.0 and it["subtotal"] == 603.0 and it["tax_amount"] == 66.33
    assert d["bill_subtotal"] == 603.0 and d["bill_tax"] == 66.33 and d["bill_total"] == 669.33
    assert d["payload"]["items"] == [{"po_item_id": str(I1), "quantity_to_bill": "6"}]


def test_tagih_semua_blok_dan_tulis_menolak_dengan_blok_pertama(kon):
    k = Kon(status="closed", items=[_baris_tagih()])
    kon(k)
    body = PS.ToBillPreviewRequest(items=[PS.ToBillItemRequest(po_item_id=str(I1), quantity_to_bill="7"),
                                          PS.ToBillItemRequest(po_item_id=str(I2))])
    d = run(PO.preview_convert_to_bill(_Req(), PID, body))["data"]
    assert [b["code"] for b in d["blocks"]] == ["PO_TAK_BISA_DITAGIH", "QTY_MELEBIHI_SISA_TAGIH", "BARIS_TIDAK_DITEMUKAN"]
    kon(Kon(status="closed", items=[_baris_tagih()]))
    with pytest.raises(HTTPException) as e:
        run(PO.convert_to_bill(_Req(), PID, PS.ConvertToBillRequest(bill_date=HARI, items=body.items)))
    assert e.value.detail == d["blocks"][0]["message"]


def test_tagih_tak_ada_sisa(kon):
    kon(Kon(status="received", items=[]))
    d = run(PO.preview_convert_to_bill(_Req(), PID, None))["data"]
    assert [b["code"] for b in d["blocks"]] == ["TAK_ADA_SISA_TAGIH"] and d["bill_total"] == 0.0


def test_jalur_tulis_memakai_kunci_true():
    import inspect
    src = inspect.getsource(PO)
    assert "PR.rencana_tagih(conn, po, [i.model_dump() for i in body.items] if body.items else None, kunci=True)" in src
    assert "rencana_terima(conn, po, body.receive_date, [i.model_dump() for i in body.items], _hari_ini)" in src  # bawaan kunci=True
    assert inspect.signature(PR.rencana_terima).parameters["kunci"].default is True
    assert inspect.signature(PR.rencana_tagih).parameters["kunci"].default is True
