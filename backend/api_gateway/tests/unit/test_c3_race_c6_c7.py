"""Audit CW SO (26 Sep 2026): C3 proforma kunci/idempotensi, race confirm/cancel/DELETE/to-invoice,
C6 masukan buruk -> 404/422 (bukan 500), C7 uang muka mengunci baris SO + status SO."""
import inspect
import os
from types import SimpleNamespace
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import proformas as PF  # noqa: E402
from app.routers import sales_orders as SO  # noqa: E402
from app.routers import customer_deposits as CD  # noqa: E402

T = "kaos-biru-konveksi"
USER = "00000000-0000-0000-0000-0000000000a1"
SOID = UUID("10000000-0000-0000-0000-000000000001")


def _req(headers=None):
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": USER, "tenant_id": T, "role": "OWNER"}),
                           headers=headers or {})


def _src(fn):
    return " ".join(inspect.getsource(fn).split())


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return _Acq(self.c)


# ---------------- C3 proforma ----------------

class _PConn:
    def __init__(self):
        self.jejak = []

    def transaction(self):
        return _Acq(self)

    async def execute(self, sql, *a):
        self.jejak.append(("exec", " ".join(sql.split()), a))

    async def fetchval(self, sql, *a):
        self.jejak.append(("val", sql, a))
        return "PRO-1"

    async def fetchrow(self, sql, *a):
        self.jejak.append(("row", " ".join(sql.split()), a))
        return {"id": UUID("20000000-0000-0000-0000-000000000001"), "proforma_number": "PRO-1"}


@pytest.fixture
def pf(monkeypatch):
    c = _PConn()
    simpan, replay = [], {}

    async def pool():
        return _Pool(c)

    async def order(conn, tid, oid):
        c.jejak.append(("order",))
        return {"status": "confirmed", "total_amount": 1000000, "customer_id": None, "customer_name": "X",
                "order_number": "SO-1", "payment_bank_name": None, "payment_account_number": None,
                "payment_account_holder": None}

    async def plafon(*a, **k):
        c.jejak.append(("plafon",))

    async def tgl(conn, tid):
        return None

    async def ambil(conn, tid, kunci, sidik):
        if kunci in replay:
            s_lama, resp = replay[kunci]
            if s_lama != sidik:
                e = LookupError("beda")
                e.respons = resp
                raise e
            return resp
        return None

    async def simpan_(conn, tid, kunci, st, sidik, resp, rid=None, ttl_hours=24):
        simpan.append(kunci)
        replay[kunci] = (sidik, resp)

    monkeypatch.setattr(PF, "get_pool", pool)
    monkeypatch.setattr(PF, "fetch_order_or_404", order)
    monkeypatch.setattr(PF, "assert_within_order_total", plafon)
    monkeypatch.setattr(PF, "tanggal_dokumen", tgl)
    monkeypatch.setattr(PF, "ambil_replay_klien", ambil)
    monkeypatch.setattr(PF, "simpan_replay_klien", simpan_)
    monkeypatch.setattr(PF, "serialize_proforma", lambda row, n, p: {"id": str(row["id"]), "proforma_number": row["proforma_number"]})
    return c, simpan


def _badan(pct=50):
    return PF.CreateProformaRequest(sales_order_id=str(SOID), purpose="DP", percent_of_order=pct)


def _insert(c):
    return [j for j in c.jejak if j[0] == "row" and "INSERT INTO proformas" in j[1]]


@pytest.mark.asyncio
async def test_c3_kunci_so_sebelum_baca_pesanan_dan_plafon(pf):
    c, _ = pf
    await PF.create_proforma(_req(), _badan())
    urut = [j[0] if j[0] != "exec" else ("KUNCI_SO" if f"PROFORMA_SO:{T}:{SOID}" in j[2] else "exec") for j in c.jejak]
    assert urut.index("KUNCI_SO") < urut.index("order") < urut.index("plafon")


@pytest.mark.asyncio
async def test_c3_kunci_klien_ulang_isi_sama_replay_tanpa_insert_kedua(pf):
    c, simpan = pf
    h = {"X-Idempotency-Key": "form-1"}
    r1 = await PF.create_proforma(_req(h), _badan())
    r2 = await PF.create_proforma(_req(h), _badan())
    assert r1 == r2 and len(_insert(c)) == 1 and simpan == [f"PROFORMA_CREATE:{USER}:form-1"]


@pytest.mark.asyncio
async def test_c3_kunci_klien_isi_beda_409(pf):
    c, _ = pf
    h = {"X-Idempotency-Key": "form-1"}
    await PF.create_proforma(_req(h), _badan(50))
    with pytest.raises(HTTPException) as e:
        await PF.create_proforma(_req(h), _badan(60))
    assert e.value.status_code == 409 and e.value.detail["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert e.value.detail["proforma_number"] == "PRO-1" and len(_insert(c)) == 1


@pytest.mark.asyncio
async def test_c3_tanpa_kunci_tetap_jalan_tanpa_catatan_replay(pf):
    c, simpan = pf
    await PF.create_proforma(_req(), _badan())
    assert len(_insert(c)) == 1 and simpan == []


def test_c3_terbit_mengunci_so_sebelum_plafon_dalam_tx():
    s = _src(PF.issue_proforma)
    assert s.index("async with conn.transaction()") < s.index("PROFORMA_SO:") < s.index("assert_within_order_total(")


# ---------------- race SO ----------------

def test_race_delete_kunci_guard_dan_bersyarat_di_dalam_tx():
    s = _src(SO.delete_sales_order)
    tx = s.index("async with conn.transaction()")
    assert tx < s.index("FOR UPDATE") < s.index("\"dihapus\")", tx) < s.index("AND status = 'draft' RETURNING id")


def test_race_cancel_kunci_dan_guard_dp_di_dalam_tx():
    s = _src(SO.cancel_sales_order)
    tx = s.index("async with conn.transaction()")
    assert tx < s.index("FOR UPDATE", tx) < s.index("\"dibatalkan\")", tx) < s.index("UPDATE sales_orders SET status = 'cancelled'")


def test_race_patch_dan_to_invoice_mengunci_baris_so():
    assert "WHERE id = $1 AND tenant_id = $2 FOR UPDATE" in _src(SO.update_sales_order)
    assert "SELECT * FROM sales_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE" in _src(SO.convert_to_invoice)


class _DConn:
    def __init__(self, hapus_ok):
        self.hapus_ok, self.q = hapus_ok, []

    def transaction(self):
        return _Acq(self)

    async def fetchrow(self, sql, *a):
        return {"id": SOID, "status": "draft", "order_number": "SO-1"}

    async def execute(self, sql, *a):
        self.q.append(sql)

    async def fetchval(self, sql, *a):
        self.q.append(sql)
        return SOID if self.hapus_ok else None


@pytest.mark.asyncio
async def test_race_delete_berubah_di_antaranya_409(monkeypatch):
    c = _DConn(False)

    async def pool():
        return _Pool(c)

    async def tanpa_dp(*a, **k):
        return None
    monkeypatch.setattr(SO, "get_pool", pool)
    monkeypatch.setattr(SO, "_tolak_bila_ada_uang_muka_aktif", tanpa_dp)
    with pytest.raises(HTTPException) as e:
        await SO.delete_sales_order(_req(), str(SOID))
    assert e.value.status_code == 409


# ---------------- C6 ----------------

@pytest.mark.asyncio
@pytest.mark.parametrize("fn,args", [
    ("get_sales_order_detail", ()), ("confirm_sales_order", ()), ("delete_sales_order", ()),
    ("cancel_sales_order", (None,)), ("close_sales_order", (None,)), ("convert_to_invoice", (None,)),
])
async def test_c6_id_jalur_buruk_404_tanpa_db(monkeypatch, fn, args):
    async def pool():
        class P:
            def acquire(self):
                raise AssertionError("DB tersentuh")
        return P()
    monkeypatch.setattr(SO, "get_pool", pool)
    with pytest.raises(HTTPException) as e:
        await getattr(SO, fn)(_req(), "bukan-uuid", *args)
    assert e.value.status_code == 404


SID = "30000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize("baris", [
    {}, {"quantity": 1}, {"so_item_id": "x"}, {"so_item_id": SID, "quantity": "abc"},
    {"so_item_id": SID, "quantity": 0}, {"so_item_id": SID, "quantity": -1}, {"so_item_id": SID, "quantity": True},
    {"so_item_id": SID, "quantity": float("nan")}, "bukan-dict",
])
def test_c6_baris_to_invoice_buruk_422(baris):
    with pytest.raises(HTTPException) as e:
        SO._baris_tagih(baris)
    assert e.value.status_code == 422


def test_c6_baris_to_invoice_sah():
    assert SO._baris_tagih({"so_item_id": SID}) == (UUID(SID), None)
    assert SO._baris_tagih({"so_item_id": SID, "quantity": "3"}) == (UUID(SID), 3.0)


@pytest.mark.parametrize("ubah", [{"customer_id": "x"}, {"quote_id": "x"}, {"shipping_tax_code_id": "x"},
                                  {"items": [{"item_id": "x"}]}, {"items": [{"warehouse_id": "x"}]}])
def test_c6_badan_buat_so_uuid_buruk_422(ubah):
    b = {"customer_id": SID, "quote_id": None, "shipping_tax_code_id": None, "items": [{}]}
    b.update(ubah)
    body = SimpleNamespace(**{**b, "items": [SimpleNamespace(**{"item_id": None, "warehouse_id": None, **i}) for i in b["items"]]})
    with pytest.raises(HTTPException) as e:
        SO._cek_uuid_badan_so(body)
    assert e.value.status_code == 422


def test_c6_badan_buat_so_sah_lolos_dan_tax_id_bukan_urusannya():
    body = SimpleNamespace(customer_id=SID, quote_id=None, shipping_tax_code_id=None,
                           items=[SimpleNamespace(item_id=SID, warehouse_id=None, tax_id="bukan-uuid")])
    SO._cek_uuid_badan_so(body)  # tax_id: kontrak t34 (400 dari jalur kode pajak)


# ---------------- C7 ----------------

class _CConn:
    def __init__(self, status):
        self.status, self.sql = status, []

    async def fetchrow(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        return {"order_number": "SO-1", "total_amount": 1000000, "status": self.status}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "cancelled", "completed"])
async def test_c7_so_tak_berjalan_ditolak(monkeypatch, status):
    async def sudah(*a, **k):
        return 0.0
    monkeypatch.setattr(CD, "received_total_for_order", sudah)
    c = _CConn(status)
    with pytest.raises(HTTPException) as e:
        await CD.assert_deposit_within_order_total(c, T, SOID, 100)
    assert e.value.status_code == 400 and e.value.detail["code"] == "SO_NOT_ACCEPTING_DEPOSIT"


@pytest.mark.asyncio
async def test_c7_kunci_baris_so_dan_berjalan_lolos(monkeypatch):
    async def sudah(*a, **k):
        return 0.0
    monkeypatch.setattr(CD, "received_total_for_order", sudah)
    c = _CConn("confirmed")
    await CD.assert_deposit_within_order_total(c, T, SOID, 100)
    assert c.sql[0].endswith("WHERE id = $1 AND tenant_id = $2 FOR UPDATE")


@pytest.mark.asyncio
async def test_c6_buat_so_customer_id_buruk_422_tanpa_db(monkeypatch):
    async def pool():
        raise AssertionError("DB tersentuh")
    monkeypatch.setattr(SO, "get_pool", pool)
    body = SO.CreateSalesOrderRequest(customer_id="bukan-uuid", customer_name="X", order_date="2026-09-26",
                                      items=[{"description": "a", "quantity": 1, "unit_price": 1}])
    with pytest.raises(HTTPException) as e:
        await SO.create_sales_order(_req(), body, SimpleNamespace(headers={}))
    assert e.value.status_code == 422 and "customer_id" in str(e.value.detail)
