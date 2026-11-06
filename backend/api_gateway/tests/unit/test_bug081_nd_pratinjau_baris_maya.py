"""BUG-081 REOPEN (6 Nov 2026, TESTER nyawit BILL-2611-0006/0007): POST /vendor-credits/preview 500 begitu ada baris barang dilacak --
rencana_retur (ROW 108) membaca it["id"] + it["total"] baris_dok dan stok_id dari item_row_id, sedangkan baris maya pratinjau-buat dulu
{item_id, quantity} saja -> KeyError 'id'. Kini baris maya == baris vendor_credit_items yang AKAN ditulis create (medan sama + id acak).
Tes PERILAKU: rute pratinjau dijalankan (penyusun _susun_nd ASLI), baris yang diteruskan ke rencana_posting ditangkap lalu dibaca rencana_retur
ASLI dengan item_row_id persis seperti NG.rencana_keluar (it.get("id")) -> bagian/semua == nilai baris yang ditulis create."""
import asyncio
import uuid
from decimal import Decimal as D

import pytest

from app.routers import vendor_credits as VC
from app.services import inventory_helpers as IH
from app.services import nota_debit as ND
from app.services import nota_debit_gudang as NG
from app.services import nota_debit_rencana as NDR

BARANG, JASA = uuid.uuid4(), uuid.uuid4()
BILL = str(uuid.uuid4())
ITEMS = [{"item_id": str(BARANG), "description": "Minyak 1L", "quantity": 4, "unit": "dus", "unit_price": 15000.5, "tax_rate": 0},
         {"item_id": str(JASA), "description": "Ongkos", "quantity": 1, "unit_price": 7333.33, "tax_rate": 0}]


class _Tr:
    async def start(self):
        pass

    async def rollback(self):
        pass


class _Conn:
    async def execute(self, q, *a):
        assert "set_config" in q

    def transaction(self, readonly=False):
        assert readonly
        return _Tr()

    async def fetchval(self, q, *a):
        assert "FROM vendors" in q
        return 1

    async def fetchrow(self, q, *a):  # rencana_retur: produk baris dilacak
        assert "FROM products" in q
        return {"id": a[0], "item_code": "M1", "nama_produk": "Minyak 1L", "track_inventory": True}


class _Acq:
    async def __aenter__(self):
        return _Conn()

    async def __aexit__(self, *a):
        return False


class _Pool:
    def acquire(self):
        return _Acq()


class _Req:
    pass


def _pasang(monkeypatch, tangkap):
    async def pool():
        return _Pool()

    async def nihil(*a, **k):
        return None

    async def tgl(conn, tid):
        return "2026-11-06"

    async def nomor(conn, tid):
        return "VC-2611-0009"

    async def rencana(conn, ctx, vc, pratinjau=False, items=None):
        tangkap["vc"], tangkap["items"] = vc, items
        return {"blocks": NDR.Blok(True), "journals": [], "stock": [], "bills": []}

    async def milik(conn, tid, bill_id, vendor_id, kunci=False):
        return {"id": bill_id, "invoice_number": "BILL-2611-0006"}

    async def rakit(conn, tid, body):
        return {"create": {"vendor_id": str(uuid.uuid4()), "vendor_name": "PT Sawit", "vendor_credit_date": body.vendor_credit_date,
                           "original_bill_id": BILL, "reason": "return", "items": ITEMS},
                "blocks": [], "penuh": False, "lines": [], "notes": [], "tagihan": {"amount": 0}}

    monkeypatch.setattr(VC, "get_user_context", lambda r: {"tenant_id": "t", "user_id": "u"})
    monkeypatch.setattr(VC, "get_pool", pool)
    monkeypatch.setattr(VC, "_ensure_role_preconditions", nihil)
    monkeypatch.setattr(VC, "tanggal_dokumen", tgl)
    monkeypatch.setattr(VC, "rakit_dari_tagihan", rakit)
    monkeypatch.setattr(ND, "tagihan_milik", milik)
    monkeypatch.setattr(NDR, "perkiraan_nomor_nd", nomor)
    monkeypatch.setattr(NDR, "rencana_posting", rencana)


@pytest.mark.parametrize("bentuk", ["items", "dari_tagihan"])
def test_pratinjau_buat_nd_barang_dilacak_baris_maya_sama_penulis(monkeypatch, bentuk):
    tangkap = {}
    _pasang(monkeypatch, tangkap)
    body = {"original_bill_id": BILL, "vendor_credit_date": "2026-11-06", "reason": "return"}
    if bentuk == "items":
        body.update({"vendor_id": str(uuid.uuid4()), "vendor_name": "PT Sawit", "items": ITEMS})
    r = asyncio.run(VC.preview_create_vendor_credit(_Req(), VC.VendorCreditPreviewRequest(**body)))
    assert r["data"]["can_proceed"] is True
    baris = tangkap["items"]
    # penulis (create_vendor_credit) menulis per baris: item_id, quantity, unit, total (calculate_item_totals) + id baris
    tulis = [VC.calculate_item_totals(VC.VendorCreditPreviewRequest(reason="return", items=ITEMS).items[i].model_dump())
             for i in range(len(ITEMS))]
    assert len(baris) == 2 and len({b["id"] for b in baris}) == 2 and all(isinstance(b["id"], uuid.UUID) for b in baris)
    assert [(b["item_id"], b["quantity"], b.get("unit"), b["total"], b["line_number"]) for b in baris] == \
        [(uuid.UUID(t["item_id"]), t["quantity"], t.get("unit"), t["total"], i) for i, t in enumerate(tulis, 1)]

    # rencana_retur ASLI atas baris maya; keluar dibentuk persis NG.rencana_keluar (item_row_id = it.get("id")), hanya baris barang dilacak
    async def biaya(conn, tid, pid):
        return {"unit_cost": D("14000"), "cost_source": "wac"}

    async def akun(conn, tid, pid):
        return {"inventory_account_id": uuid.uuid4()}

    async def peran(conn, tid, role):
        return uuid.uuid4()

    monkeypatch.setattr(IH, "get_cost_for_sale", biaya)
    monkeypatch.setattr(IH, "resolve_inventory_accounts", akun)
    monkeypatch.setattr(NDR, "resolve_account_id_by_role", peran)
    keluar = [{"item_row_id": b.get("id"), "product_id": b["item_id"], "nama": "Minyak 1L", "qty": D("4"), "gudang_id": uuid.uuid4(),
               "gudang": "Utama", "_asal": None} for b in baris if b["item_id"] == BARANG]
    rt = asyncio.run(NDR.rencana_retur(_Conn(), "t", keluar, baris))
    assert rt["bagian"] == tulis[0]["total"] == D("60002.00")
    assert rt["semua"] == tulis[0]["total"] + tulis[1]["total"]


def test_rencana_keluar_membaca_id_baris_dan_satuan():
    """Titik sambung: item_row_id dan satuan rencana_keluar dibaca dari baris (DB penulis / maya pratinjau) -- medan yang kini dibawa baris maya."""
    import inspect
    s = inspect.getsource(NG.rencana_keluar)
    assert '"item_row_id": it.get("id")' in s and 'it.get("unit")' in s
    assert 'baris = _baris_maya(s["items"])' in inspect.getsource(VC.preview_create_vendor_credit)
