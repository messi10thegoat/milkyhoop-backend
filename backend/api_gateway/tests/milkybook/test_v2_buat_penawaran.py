"""Milkybook v2: buat penawaran ATOMIK dengan master baru (03 §3, 1 Nov 2026).

Inti tulis (tulis_pelanggan_baru / tulis_barang_baru / buat_penawaran_inti) = jalur POST /customers, /items, /quotes; di sini
diganti perekam supaya urutan, rujukan temp_id, dan batal-semua terlihat. Bukti DB nyata (tanpa data parsial) = gerbang 05 §6
di kaos, bukan berkas ini."""
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.milkybook.v2 import buat_penawaran as BP
from app.milkybook.v2.router import buat_router_v2
from app.routers import customers as RC
from app.routers import items as RI
from app.routers import quotes as RQ
from app.services import idem_buat

CUST_LAMA = "11111111-1111-4111-8111-111111111111"
ITEM_LAMA = "22222222-2222-4222-8222-222222222222"
USER = {"tenant_id": "kaos-biru-konveksi", "user_id": "33333333-3333-4333-8333-333333333333", "email": "o@x"}


def _baris(item_id=None, **kw):
    b = {"description": "Kaos Combed 30s Hitam", "quantity": 10, "unit_price": 85000}
    if item_id:
        b["item_id"] = item_id
    b.update(kw)
    return b


def _penawaran(customer_id="c-baru", items=None, **kw):
    p = {"quote_date": "2026-11-01", "customer_id": customer_id, "items": items or [_baris("i-baru")]}
    p.update(kw)
    return p


def _badan(master=None, **kw):
    if master is None:
        master = [{"kind": "customer", "temp_id": "c-baru", "data": {"name": "TES E2E Toko Baru", "phone": "0812"}},
                  {"kind": "item", "temp_id": "i-baru", "data": {"name": "TES E2E Kaos Baru", "item_type": "non_inventory",
                                                                 "track_inventory": False, "base_unit": "pcs",
                                                                 "sales_price": 85000}}]
    return BP.BuatPenawaranV2(master_baru=master, penawaran=_penawaran(**kw))


# ---------------------------------------------------------------- rencanakan: semua penolakan SEBELUM transaksi

def test_rencana_sah_mengurai_master():
    pel, bar = BP.rencanakan(_badan())
    assert [t for t, _ in pel] == ["c-baru"] and pel[0][1].name == "TES E2E Toko Baru"
    assert [t for t, _ in bar] == ["i-baru"] and bar[0][1].item_type == "non_inventory"


def test_rencana_tanpa_master_baru_sah():
    pel, bar = BP.rencanakan(_badan(master=[], customer_id=CUST_LAMA, items=[_baris(ITEM_LAMA)], customer_name="Lama"))
    assert pel == [] and bar == []


@pytest.mark.parametrize("badan,kode", [
    (lambda: _badan(master=[{"kind": "item", "temp_id": "x", "data": {"name": "A", "base_unit": "pcs"}},
                            {"kind": "item", "temp_id": "x", "data": {"name": "B", "base_unit": "pcs"}}],
                    customer_id=CUST_LAMA, customer_name="L", items=[_baris("x")]), "TEMP_ID_GANDA"),
    (lambda: _badan(master=[{"kind": "customer", "temp_id": "a", "data": {"name": "A"}},
                            {"kind": "customer", "temp_id": "b", "data": {"name": "B"}}],
                    customer_id="a", items=[_baris(ITEM_LAMA)]), "PELANGGAN_BARU_LEBIH_DARI_SATU"),
    (lambda: _badan(customer_id=CUST_LAMA, customer_name="L"), "MASTER_BARU_TAK_DIRUJUK"),       # pelanggan baru yatim
    (lambda: _badan(items=[_baris(ITEM_LAMA)]), "MASTER_BARU_TAK_DIRUJUK"),                     # barang baru yatim
    (lambda: _badan(items=[_baris("i-baru"), _baris("c-baru")]), "TEMP_ID_SALAH_TEMPAT"),       # pelanggan di baris
    (lambda: _badan(master=[{"kind": "customer", "temp_id": "c-baru", "data": {}}]), "DATA_TIDAK_SAH"),  # nama wajib
    (lambda: _badan(master=[], customer_id=CUST_LAMA, customer_name="L", items=[]), "DATA_TIDAK_SAH"),  # tanpa baris
])
def test_rencana_menolak_422(badan, kode):
    with pytest.raises(HTTPException) as e:
        BP.rencanakan(badan())
    assert e.value.status_code == 422 and e.value.detail["code"] == kode


def test_rencana_data_tidak_sah_menyebut_letak():
    with pytest.raises(HTTPException) as e:
        BP.rencanakan(_badan(master=[], customer_id=CUST_LAMA, customer_name="L", items=[]))
    assert e.value.detail["letak"] == "penawaran"


def test_ganti_rujukan_mengisi_id_dan_nama_pelanggan_baru():
    pel, _ = BP.rencanakan(_badan())
    p = BP.ganti_rujukan(_penawaran(), {"c-baru": CUST_LAMA, "i-baru": ITEM_LAMA}, pel)
    assert p["customer_id"] == CUST_LAMA and p["customer_name"] == "TES E2E Toko Baru"
    assert p["items"][0]["item_id"] == ITEM_LAMA
    p2 = BP.ganti_rujukan(_penawaran(customer_name="Nama Kiriman"), {"c-baru": CUST_LAMA, "i-baru": ITEM_LAMA}, pel)
    assert p2["customer_name"] == "Nama Kiriman"


# ---------------------------------------------------------------- buat: urutan, satu transaksi, batal semua

class _Tx:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        self.log.append("BEGIN")

    async def __aexit__(self, t, e, tb):
        self.log.append("ROLLBACK" if t else "COMMIT")
        return False


class _Conn:
    def __init__(self):
        self.log = []

    def transaction(self):
        return _Tx(self.log)


@pytest.fixture
def rekam(monkeypatch):
    conn = _Conn()
    st = {"lama": None, "gagal_barang": None, "izin": {"/api/customers": True, "/api/items": True}, "izin_dipanggil": []}

    async def mulai(c, ctx, kunci, prefix, body, tabel, nama, response=None):
        c.log.append(("IDEM", prefix, kunci))
        return "kp", "sd", st["lama"]

    async def simpan(c, ctx, kp, sd, source_type, resp, result_id):
        c.log.append(("SIMPAN", source_type, str(result_id)))
        return resp

    async def pelanggan(c, ctx, body, uid, uname):
        c.log.append(("PELANGGAN", body.name, uid))
        return "aaaaaaaa-0000-4000-8000-000000000001"

    async def barang(c, ctx, body, uid, uname):
        c.log.append(("BARANG", body.name))
        if st["gagal_barang"]:
            raise st["gagal_barang"]
        return "bbbbbbbb-0000-4000-8000-000000000002"

    async def penawaran(c, ctx, body):
        c.log.append(("PENAWARAN", body.customer_id, body.customer_name, [i.item_id for i in body.items]))
        return "qqqqqqqq-0000-4000-8000-000000000003", {"id": "q", "quote_number": "QUO-UJI-0001"}

    async def izin(user, jalur, metode):
        st["izin_dipanggil"].append((tuple(jalur), metode))
        return {j: st["izin"].get(j, False) for j in jalur}

    monkeypatch.setattr(idem_buat, "mulai", mulai)
    monkeypatch.setattr(idem_buat, "simpan", simpan)
    monkeypatch.setattr(RC, "tulis_pelanggan_baru", pelanggan)
    monkeypatch.setattr(RI, "tulis_barang_baru", barang)
    monkeypatch.setattr(RQ, "buat_penawaran_inti", penawaran)
    st["conn"], st["izin_fn"] = conn, izin
    return st


async def _buat(st, badan=None, kunci="draf-1"):
    return await BP.buat(st["conn"], USER, badan or _badan(), kunci, izin_fn=st["izin_fn"])


@pytest.mark.asyncio
async def test_buat_urutan_satu_transaksi_id_nyata_ke_penawaran(rekam):
    hasil = await _buat(rekam)
    assert rekam["conn"].log == [
        "BEGIN",
        ("IDEM", "MB2_QUOTE_CREATE", "draf-1"),
        ("PELANGGAN", "TES E2E Toko Baru", USER["user_id"]),
        ("BARANG", "TES E2E Kaos Baru"),
        ("PENAWARAN", "aaaaaaaa-0000-4000-8000-000000000001", "TES E2E Toko Baru", ["bbbbbbbb-0000-4000-8000-000000000002"]),
        ("SIMPAN", "MB2_QUOTE_CREATE", "qqqqqqqq-0000-4000-8000-000000000003"),
        "COMMIT",
    ]
    assert hasil["data"]["master_baru"] == [
        {"kind": "customer", "temp_id": "c-baru", "id": "aaaaaaaa-0000-4000-8000-000000000001"},
        {"kind": "item", "temp_id": "i-baru", "id": "bbbbbbbb-0000-4000-8000-000000000002"}]
    assert rekam["izin_dipanggil"] == [(("/api/customers", "/api/items"), "POST")]


@pytest.mark.asyncio
async def test_barang_gagal_penawaran_tak_ditulis_transaksi_batal(rekam):
    rekam["gagal_barang"] = HTTPException(409, detail={"code": "NAMA_BARANG_DIPAKAI"})
    with pytest.raises(HTTPException) as e:
        await _buat(rekam)
    assert e.value.status_code == 409
    log = rekam["conn"].log
    assert log[-1] == "ROLLBACK" and not any(isinstance(x, tuple) and x[0] in ("PENAWARAN", "SIMPAN") for x in log)


@pytest.mark.asyncio
async def test_bentrok_unik_db_dipetakan_seperti_post_items(rekam, monkeypatch):
    rekam["gagal_barang"] = RuntimeError("duplicate key products_nama")
    monkeypatch.setattr(RI, "galat_bentrok_unik", lambda e, b: HTTPException(409, detail={"code": "NAMA_BARANG_DIPAKAI"}))
    with pytest.raises(HTTPException) as e:
        await _buat(rekam)
    assert e.value.status_code == 409 and rekam["conn"].log[-1] == "ROLLBACK"


@pytest.mark.asyncio
async def test_kirim_ulang_draf_sama_replay_tanpa_tulis(rekam):
    rekam["lama"] = {"success": True, "data": {"id": "q-lama"}}
    hasil = await _buat(rekam)
    assert hasil["data"]["id"] == "q-lama"
    assert [x for x in rekam["conn"].log if isinstance(x, tuple)] == [("IDEM", "MB2_QUOTE_CREATE", "draf-1")]


@pytest.mark.asyncio
@pytest.mark.parametrize("kunci", [None, ""])
async def test_tanpa_kunci_422_sebelum_transaksi(rekam, kunci):
    with pytest.raises(HTTPException) as e:
        await _buat(rekam, kunci=kunci)
    assert e.value.detail["code"] == "IDEMPOTENCY_KEY_WAJIB" and rekam["conn"].log == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tolak", ["/api/customers", "/api/items"])
async def test_tanpa_izin_master_403_sebelum_transaksi(rekam, tolak):
    rekam["izin"][tolak] = False
    with pytest.raises(HTTPException) as e:
        await _buat(rekam)
    assert e.value.status_code == 403 and e.value.detail["jalur"] == [tolak] and rekam["conn"].log == []


@pytest.mark.asyncio
async def test_tanpa_master_baru_tanpa_cek_izin_tambahan(rekam):
    await _buat(rekam, _badan(master=[], customer_id=CUST_LAMA, customer_name="L", items=[_baris(ITEM_LAMA)]))
    assert rekam["izin_dipanggil"] == []
    assert not any(isinstance(x, tuple) and x[0] in ("PELANGGAN", "BARANG") for x in rekam["conn"].log)


@pytest.mark.asyncio
async def test_rencana_ditolak_nol_transaksi(rekam):
    with pytest.raises(HTTPException):
        await _buat(rekam, _badan(items=[_baris(ITEM_LAMA)]))  # barang baru yatim
    assert rekam["conn"].log == [] and rekam["izin_dipanggil"] == []


# ---------------------------------------------------------------- rute: flag, token, header kunci

def _app(rekam, flag=True):
    @asynccontextmanager
    async def koneksi():
        yield rekam["conn"]

    async def flag_fn(t):
        return flag

    app = FastAPI()

    @app.middleware("http")
    async def pengguna(request: Request, lanjut):
        if request.headers.get("X-Uji-Tenant"):
            request.state.user = dict(USER)
        return await lanjut(request)

    app.include_router(buat_router_v2(flag_fn=flag_fn, conn_fn=koneksi, izin_fn=rekam["izin_fn"]), prefix="/api/milkybook/v2")
    return TestClient(app)


def _json():
    return _badan().model_dump()


def test_rute_flag_mati_404_tanpa_token_401(rekam):
    assert _app(rekam, flag=False).post("/api/milkybook/v2/penawaran", json=_json(),
                                        headers={"X-Uji-Tenant": "1", "X-Idempotency-Key": "k"}).status_code == 404
    assert _app(rekam).post("/api/milkybook/v2/penawaran", json=_json()).status_code == 401
    assert rekam["conn"].log == []


def test_rute_header_kunci_sampai_ke_idem(rekam):
    r = _app(rekam).post("/api/milkybook/v2/penawaran", json=_json(), headers={"X-Uji-Tenant": "1", "X-Idempotency-Key": "draf-7"})
    assert r.status_code == 200, r.text
    assert ("IDEM", "MB2_QUOTE_CREATE", "draf-7") in rekam["conn"].log


def test_rute_tanpa_header_kunci_422(rekam):
    r = _app(rekam).post("/api/milkybook/v2/penawaran", json=_json(), headers={"X-Uji-Tenant": "1"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "IDEMPOTENCY_KEY_WAJIB"


# ---------------------------------------------------------------- jalur lama memakai inti yang SAMA (tanpa aturan kedua)

def test_handler_lama_memanggil_inti_bersama():
    import inspect
    assert "buat_penawaran_inti(conn, ctx, body)" in inspect.getsource(RQ.create_quote)
    assert "tulis_pelanggan_baru(conn, ctx, body" in inspect.getsource(RC.create_customer)
    assert "tulis_barang_baru(conn, ctx, body" in inspect.getsource(RI.create_item)
