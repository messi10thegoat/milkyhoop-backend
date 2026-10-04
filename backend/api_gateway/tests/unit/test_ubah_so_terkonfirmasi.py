"""Ubah SO TERKONFIRMASI (4 Okt 2026, pola NetSuite, putusan pemilik LANGSUNG). Penjaga baris diuji dengan koneksi
tiruan TANPA DB: galat keluar SEBELUM satu pun tulis. Perilaku nyata (hitung ulang, status, riwayat, tautan faktur utuh,
pratinjau nol tulis, uang muka/proforma) dibuktikan harness kaos di transaksi yang selalu di-ROLLBACK."""
import inspect
import re
import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.routers import sales_orders as SO
from app.schemas.sales_orders import UpdateSalesOrderRequest as U
from app.services import so_kirim
from app.services import so_ubah_terkonfirmasi as SUT

T = "t-uji"
SOID, L1, L2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _baris(i, desk, qty):
    return {"id": i, "item_id": None, "description": desk, "quantity": Decimal(qty), "unit": None,
            "unit_price": Decimal("100000"), "discount_percent": Decimal("0"), "tax_id": None, "tax_rate": Decimal("0"),
            "tax_amount": Decimal("0"), "line_total": Decimal(qty) * 100000, "dpp": Decimal("0"), "warehouse_id": None,
            "sort_order": 0}


class _Conn:
    """Menjawab menurut potongan SQL; mencatat setiap tulis."""

    def __init__(self, terfaktur=4, draf=(), batal=()):
        self.terfaktur, self.draf, self.batal, self.tulis = terfaktur, list(draf), list(batal), []

    async def execute(self, q, *a):
        if not q.lstrip().upper().startswith("SELECT"):
            self.tulis.append(q)
        return "OK"

    async def fetch(self, q, *a):
        assert a[0] == T, "tenant WAJIB parameter pertama"
        if "AS tertaut" in q:
            return [{"id": L1, "quantity_invoiced": Decimal(self.terfaktur), "tertaut": Decimal(self.terfaktur)},
                    {"id": L2, "quantity_invoiced": Decimal(0), "tertaut": Decimal(0)}]
        if "si.status = 'draft'" in q:
            return self.draf
        if "si.status = 'void'" in q:
            return self.batal
        if "FROM sales_order_items soi JOIN sales_orders" in q:
            return [_baris(L1, "Kemeja", 10), _baris(L2, "Topi", 2)]
        raise AssertionError(q)

    async def fetchval(self, q, *a):
        if "status <> 'void' LIMIT 1" in q:
            return 1
        raise AssertionError(q)


SO_ROW = {"id": SOID, "status": "partial_invoiced", "order_number": "SO-1", "customer_id": None, "customer_name": "A",
          "discount_amount": 0, "shipping_amount": 0, "shipping_tax_code_id": None, "total_amount": Decimal("1200000"),
          "dp_percent": None, "dp_amount": None, "dp_amount_source": None}
CTX = {"tenant_id": T, "user_id": str(uuid.uuid4())}


@pytest.fixture(autouse=True)
def _kirim(monkeypatch):
    async def nol(conn, tid, ids):
        return {}, {}
    monkeypatch.setattr(so_kirim, "terkirim_per_baris", nol)


async def _hitung(*a):
    raise AssertionError("penjaga baris WAJIB menolak sebelum menghitung")


async def _galat(conn, body):
    with pytest.raises(HTTPException) as e:
        await SUT.ubah(conn, CTX, dict(SO_ROW), U(**body), _hitung)
    assert conn.tulis == [], f"tulis sebelum penjaga: {conn.tulis}"
    return e.value.status_code, e.value.detail


@pytest.mark.asyncio
async def test_qty_di_bawah_terfaktur_ditolak():
    st, d = await _galat(_Conn(), {"items": [{"id": str(L1), "quantity": 3}, {"id": str(L2)}]})
    assert st == 409 and d["code"] == "SO_LINE_QTY_BELOW_USED" and d["minimum"] == 4


@pytest.mark.asyncio
async def test_harga_baris_terpakai_terkunci():
    st, d = await _galat(_Conn(), {"items": [{"id": str(L1), "unit_price": 1}, {"id": str(L2)}]})
    assert d["code"] == "SO_LINE_LOCKED" and d["fields"] == ["unit_price"]


@pytest.mark.asyncio
async def test_hapus_baris_terpakai_ditolak():
    st, d = await _galat(_Conn(), {"items": [{"id": str(L2)}]})
    assert d["code"] == "SO_LINE_LOCKED" and d["line_id"] == str(L1)


@pytest.mark.asyncio
async def test_baris_faktur_draf_terkunci_dan_menyebut_fakturnya():
    draf = [{"soi_id": L1, "id": uuid.uuid4(), "invoice_number": "INV-9", "quantity": Decimal(2)}]
    st, d = await _galat(_Conn(terfaktur=0, draf=draf), {"items": [{"id": str(L1), "unit_price": 1}, {"id": str(L2)}]})
    assert d["code"] == "SO_LINE_LOCKED" and d["draft_invoices"][0]["invoice_number"] == "INV-9"
    st, d = await _galat(_Conn(terfaktur=0, draf=draf), {"items": [{"id": str(L1), "quantity": 1}, {"id": str(L2)}]})
    assert d["code"] == "SO_LINE_QTY_BELOW_USED" and d["minimum"] == 2


@pytest.mark.asyncio
async def test_baris_faktur_batal_tak_bisa_dihapus():
    batal = [{"soi_id": L2, "id": uuid.uuid4(), "invoice_number": "INV-8"}]
    st, d = await _galat(_Conn(batal=batal), {"items": [{"id": str(L1)}]})
    assert d["code"] == "SO_LINE_HAS_VOID_INVOICE" and d["void_invoices"][0]["invoice_number"] == "INV-8"


@pytest.mark.asyncio
async def test_pelanggan_terkunci_dan_status_selesai_ditolak():
    st, d = await _galat(_Conn(), {"customer_name": "B"})
    assert d["code"] == "SO_FIELD_LOCKED"
    with pytest.raises(HTTPException) as e:
        await SUT.ubah(_Conn(), CTX, {**SO_ROW, "status": "completed"}, U(notes="x"), _hitung)
    assert e.value.detail["code"] == "SO_NOT_EDITABLE"


@pytest.mark.asyncio
async def test_diskon_dokumen_terkunci_bila_sudah_ada_faktur():
    st, d = await _galat(_Conn(), {"discount_amount": 5000})
    assert d["code"] == "SO_DOC_MONEY_LOCKED"


def test_tak_pernah_hapus_semua_baris():
    """FK faktur ON DELETE SET NULL = tautan putus DIAM; jalur ini wajib ubah di tempat per id."""
    src = inspect.getsource(SUT)
    assert not re.search(r"DELETE FROM sales_order_items\s+WHERE sales_order_id", src)
    assert "DELETE FROM sales_order_items WHERE id = $1 AND sales_order_id = $2" in src
    assert "UPDATE sales_order_items SET" in src and "WHERE id = $1 AND sales_order_id = $2" in src


def test_rute_terpasang_ke_handler_yang_benar():
    """Pelajaran 27 Sep: helper di bawah dekorator = rute pindah ke helper."""
    rute = {(r.path, tuple(sorted(r.methods))): r.endpoint.__name__ for r in SO.router.routes}
    assert rute[("/{order_id}/edit/preview", ("POST",))] == "preview_edit_sales_order"
    assert rute[("/{order_id}", ("PATCH",))] == "update_sales_order"


def test_patch_terkonfirmasi_hanya_lewat_flag():
    src = inspect.getsource(SO.update_sales_order)
    i_flag, i_lama = src.index("_sut.flag_aktif"), src.index("Hanya pesanan berstatus Draf yang bisa diubah.")
    assert i_flag < i_lama and SUT.FLAG == "so_edit_confirmed"


def test_teks_angka():
    assert SUT._q(Decimal("60.00")) == "60" and SUT._q(Decimal("2.5000")) == "2.5"
    assert SUT._rp(Decimal("1234567.5")) == "Rp 1.234.567,50"  # Rupiah baku, tak dibulatkan (5 Okt)


# ---- X-Idempotency-Key PATCH SO (permintaan WORKSPACE 4 Okt; Law 14: kunci men-SERIAL-kan, idempotensi men-DEDUP) ----

class _IConn:
    def __init__(self, simpan=None):
        self.simpan, self.kunci = simpan, []

    async def fetchrow(self, q, *a):
        if "FROM idempotency_keys" in q:
            return {"result": self.simpan} if self.simpan else None
        raise AssertionError("tak boleh menyentuh SO saat replay: " + q)

    async def execute(self, q, *a):
        self.kunci.append(a)
        return "OK"

    def transaction(self):
        c = self

        class _T:
            async def __aenter__(s): return c
            async def __aexit__(s, *x): return False
        return _T()


class _IPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        c = self.conn

        class _A:
            async def __aenter__(s): return c
            async def __aexit__(s, *x): return False
        return _A()


UID = str(uuid.uuid4())


def _req(kunci, if_match=None):
    from types import SimpleNamespace
    h = {"X-Idempotency-Key": kunci}
    if if_match:
        h["if-match"] = if_match
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": UID, "tenant_id": T, "role": "OWNER"}), headers=h)


@pytest.mark.asyncio
async def test_kunci_sama_badan_sama_replay_tanpa_tulis_bahkan_dengan_if_match_basi(monkeypatch):
    from starlette.responses import Response
    from app.utils.idempotency import hash_payload
    body = U(notes="x")
    sidik = hash_payload(body.model_dump(mode="json", exclude_unset=True))
    lama = {"success": True, "message": "Sales order updated", "data": {"id": str(SOID), "new_total": 5}}
    conn = _IConn({"payload_hash": sidik, "sidik": sidik, "response": lama, **lama})
    async def gp(): return _IPool(conn)
    monkeypatch.setattr(SO, "get_pool", gp)
    async def basi(*a, **k):
        raise AssertionError("If-Match diperiksa SEBELUM replay -> balasan hilang lalu diulang = 412, bukan replay")
    import app.services.optimistic_concurrency as OC
    monkeypatch.setattr(OC, "assert_if_match_row", basi)
    import app.routers.sales_orders as _m
    async def replay(c, t, k, s):
        assert k == f"SO_UPDATE:{UID}:{SOID}:k-1" and s == sidik
        return lama
    monkeypatch.setattr(_m, "ambil_replay_klien", replay)
    resp = Response()
    r = await SO.update_sales_order(_req("k-1", if_match='W/"basi"'), str(SOID), body, resp)
    assert r.data == lama["data"] and resp.headers.get("X-Idempotent-Replay") == "true"


@pytest.mark.asyncio
async def test_kunci_sama_badan_beda_409(monkeypatch):
    import app.routers.sales_orders as _m
    async def gp(): return _IPool(_IConn())
    monkeypatch.setattr(SO, "get_pool", gp)
    async def beda(*a):
        raise LookupError("isi beda")
    monkeypatch.setattr(_m, "ambil_replay_klien", beda)
    with pytest.raises(HTTPException) as e:
        await SO.update_sales_order(_req("k-1"), str(SOID), U(notes="y"))
    assert e.value.status_code == 409 and e.value.detail["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_patch_menyimpan_replay_di_setiap_jalan_sukses():
    src = inspect.getsource(SO.update_sales_order)
    assert src.count("return SalesOrderResponse(") == 1  # satu-satunya = bentuk replay
    assert src.count("_simpan_idem_patch(") == 3          # judul-saja, terkonfirmasi, draf
    assert src.index("IDEM:") < src.index("FOR UPDATE")  # kunci idempotensi sebelum baris SO
