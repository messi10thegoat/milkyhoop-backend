"""U1b F2 (5 Okt 2026, MASTER GO): SO Konfirmasi draf + Hapus draf MASSAL, dengan pratinjau per item.

Penjaga: (1) hapus_so_draf_core = isi DELETE tunggal (urutan: baca/404/draf -> guard DP -> kunci baris -> guard ulang -> set_config
user -> DELETE bersyarat draft -> 409 bila berubah; tenant eksplisit) dan rute tunggal hanya membungkusnya di transaksi;
(2) rencana_hapus_so mengumpulkan penghalang tanpa menulis; (3) pratinjau_per_item: satu transaksi luar SELALU di-rollback, savepoint
per item, efek berurutan terlihat, galat internal tak bocor; (4) rute bulk SO: kunci idempotensi WAJIB untuk tulis, batas 50,
daftar putih, izin = izin aksi tunggal (confirm = POST SO, hapus = DELETE SO)."""
import asyncio
import inspect
import uuid

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import bulk_so as BS
from app.routers import sales_orders as SO
from app.services import bulk as B

T = "t1"
U = uuid.UUID("0bccdb25-fdf0-4e99-9024-b9a20846f76c")
CTX = {"tenant_id": T, "user_id": U}


def _jalan(c): return asyncio.run(c)


def _id(): return uuid.uuid4()


class _Tx:
    def __init__(self, k, nama): self.k, self.nama = k, nama

    async def __aenter__(self): self.k.log.append(("mulai", self.nama)); return self

    async def start(self): self.k.log.append(("mulai", self.nama))
    async def rollback(self): self.k.log.append(("rollback", self.nama))
    async def __aexit__(self, tipe, *e):
        self.k.log.append(("rollback" if tipe else "commit", self.nama)); return False


class _K:
    def __init__(self, order=None, delete_ok=True):
        self.order, self.delete_ok, self.log, self.sql = order, delete_ok, [], []
        self.n = 0

    def transaction(self): self.n += 1; return _Tx(self, f"tx{self.n}")

    async def fetchrow(self, sql, *a): self.sql.append(("row", " ".join(sql.split()), a)); return self.order
    async def fetchval(self, sql, *a): self.sql.append(("val", " ".join(sql.split()), a)); return uuid.uuid4() if self.delete_ok else None
    async def execute(self, sql, *a): self.sql.append(("exec", " ".join(sql.split()), a))
    async def fetch(self, sql, *a): self.sql.append(("fetch", " ".join(sql.split()), a)); return []


def _so(status="draft", nomor="SO-1"):
    return {"id": _id(), "status": status, "order_number": nomor}


class _Rekam(list):
    tolak = False


@pytest.fixture
def guard(monkeypatch):
    panggilan = _Rekam()

    async def g(conn, order_id, tid, aksi):
        panggilan.append((order_id, tid, aksi))
        if panggilan.tolak:
            raise HTTPException(status_code=400, detail="Pesanan punya uang muka aktif.")
    monkeypatch.setattr(SO, "_tolak_bila_ada_uang_muka_aktif", g)
    return panggilan


# ---------------- (1)(2) hapus ----------------
def test_inti_hapus_urutan_tenant_dan_hasil(guard):
    k = _K(order=_so())
    oid = str(_id())
    out = _jalan(SO.hapus_so_draf_core(k, CTX, oid))
    assert out == {"order_number": "SO-1"}
    jenis = [(j, s) for j, s, a in k.sql]
    kunci = next(i for i, (j, s) in enumerate(jenis) if "FOR UPDATE" in s)
    setcfg = next(i for i, (j, s) in enumerate(jenis) if "set_config('app.user_id'" in s)
    hapus = next(i for i, (j, s) in enumerate(jenis) if s.startswith("DELETE FROM sales_orders"))
    assert kunci < setcfg < hapus  # kunci baris -> set user -> DELETE
    assert len(guard) == 2  # guard DP sebelum DAN sesudah mengunci (race)
    for j, s, a in k.sql:
        if "sales_orders" in s:
            assert "tenant_id = $" in s and T in a
    delete = next(s for j, s, a in k.sql if s.startswith("DELETE"))
    assert "status = 'draft'" in delete and "RETURNING id" in delete  # bersyarat draf


def test_inti_hapus_penolakan_dan_balapan(guard):
    with pytest.raises(HTTPException) as e:
        _jalan(SO.hapus_so_draf_core(_K(order=None), CTX, str(_id())))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e2:
        _jalan(SO.hapus_so_draf_core(_K(order=_so("confirmed")), CTX, str(_id())))
    assert e2.value.status_code == 400 and "Draf" in e2.value.detail
    k = _K(order=_so())
    guard.clear()
    with pytest.raises(HTTPException) as e3:
        _jalan(SO.hapus_so_draf_core(_K(order=_so(), delete_ok=False), CTX, str(_id())))
    assert e3.value.status_code == 409
    with pytest.raises(HTTPException) as e4:
        _jalan(SO.hapus_so_draf_core(_K(order=None), CTX, "bukan-uuid"))
    assert e4.value.status_code == 404  # id tak sah = 404 sebelum DB


def test_inti_hapus_dp_aktif_ditolak_tanpa_menghapus(guard):
    guard.tolak = True
    k = _K(order=_so())
    with pytest.raises(HTTPException):
        _jalan(SO.hapus_so_draf_core(k, CTX, str(_id())))
    assert not [s for j, s, a in k.sql if s.startswith("DELETE")]


def test_rute_tunggal_hanya_membungkus_inti_di_transaksi():
    src = " ".join(inspect.getsource(SO.delete_sales_order).split())
    assert "async with conn.transaction(): data = await hapus_so_draf_core(conn, ctx, order_id)" in src
    assert 'message="Sales order deleted"' in src and "DELETE FROM" not in src  # rute tak menulis sendiri


def test_rencana_hapus_semua_penghalang(guard):
    oid = str(_id())
    assert _jalan(SO.rencana_hapus_so(_K(order=None), CTX, oid))[0]["code"] == "SO_TAK_ADA"
    b = _jalan(SO.rencana_hapus_so(_K(order=_so("confirmed")), CTX, oid))
    assert [x["code"] for x in b] == ["SO_BUKAN_DRAF"]
    assert _jalan(SO.rencana_hapus_so(_K(order=_so()), CTX, oid)) == []
    guard.tolak = True
    b2 = _jalan(SO.rencana_hapus_so(_K(order=_so()), CTX, oid))
    assert b2[0]["code"] == "SO_ADA_UANG_MUKA" and "uang muka" in b2[0]["message"]


# ---------------- (3) pratinjau_per_item ----------------
class _Pool:
    def __init__(self, k): self.k = k

    def acquire(self):
        k = self.k

        class A:
            async def __aenter__(s): return k
            async def __aexit__(s, *e): return False
        return A()


def test_pratinjau_satu_transaksi_luar_selalu_rollback_dan_efek_berurutan():
    k = _K()
    ids = [_id(), _id(), _id()]
    lihat = []

    async def fn(conn, ctx, i):
        lihat.append(len(lihat))
        if i == ids[1]:
            raise HTTPException(status_code=400, detail={"code": "SO_BUKAN_DRAF", "message": "Hanya draf."})
        if i == ids[2]:
            raise RuntimeError("rahasia")
        return [], {"urut": len(lihat)}
    out = _jalan(B.pratinjau_per_item(_Pool(k), CTX, "confirm", ids, fn, {str(ids[0]): "SO-1"}))
    assert out["total"] == 3 and out["ok_count"] == 1 and out["rejected_count"] == 2 and out["can_run_any"] is True and out["preview"] is True
    a, b, c = out["items"]
    assert a["ok"] and a["number"] == "SO-1" and a["preview"] == {"urut": 1} and a["blocks"] == []
    assert not b["ok"] and b["blocks"] == [{"code": "SO_BUKAN_DRAF", "message": "Hanya draf."}] and b["preview"] is None
    assert not c["ok"] and c["blocks"][0]["code"] == "BULK_ITEM_GALAT" and "rahasia" not in str(c)  # galat internal tak bocor
    # tx1 = luar (rollback di akhir), tx2.. = savepoint per item
    assert k.log[0] == ("mulai", "tx1") and k.log[-1] == ("rollback", "tx1")
    assert ("commit", "tx2") in k.log and ("rollback", "tx3") in k.log and ("rollback", "tx4") in k.log
    assert sum(1 for x in k.log if x[0] == "commit") == 1  # TIDAK ada commit selain savepoint item sukses; luar rollback


def test_pratinjau_blok_dari_penentu_mengosongkan_ringkasan():
    k = _K()

    async def fn(conn, ctx, i):
        return [{"code": "A", "message": "a", "status": 400}, {"code": "B", "message": "b"}], {"x": 1}
    out = _jalan(B.pratinjau_per_item(_Pool(k), CTX, "delete", [_id()], fn))
    it = out["items"][0]
    assert it["blocks"] == [{"code": "A", "message": "a"}, {"code": "B", "message": "b"}] and it["preview"] is None and out["can_run_any"] is False


def test_jalankan_per_item_membawa_nomor_ke_hasil(monkeypatch):
    from app.services import idem_buat as IB

    async def mulai(*a, **k): return "kp", "sd", None

    async def simpan(conn, ctx, kp, sd, src, resp, rid): return resp
    monkeypatch.setattr(IB, "mulai_aksi", mulai)
    monkeypatch.setattr(IB, "simpan", simpan)
    k = _K()
    i = _id()

    async def fn(conn, ctx, x): return {"ok": 1}
    out = _jalan(B.jalankan_per_item(_Pool(k), CTX, "delete", "sales-orders", [i], fn, kunci_batch="K", nomor={str(i): "SO-9"}))
    assert out["items"][0]["number"] == "SO-9" and out["items"][0]["status"] == "ok"


# ---------------- (4) rute ----------------
class _Req:
    def __init__(self, kunci=None, user=True):
        self.headers = {"X-Idempotency-Key": kunci} if kunci else {}
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": str(U)} if user else None})()


def _endpoint(nama): return next(r.endpoint for r in BS.router.routes if r.endpoint.__name__ == nama)


def test_rute_bulk_so_hanya_aksi_daftar_putih():
    jalur = sorted((r.path, tuple(sorted(r.methods))) for r in BS.router.routes)
    assert jalur == [("/bulk/confirm", ("POST",)), ("/bulk/confirm/preview", ("POST",)), ("/bulk/delete", ("POST",)),
                     ("/bulk/delete/preview", ("POST",))]
    assert set(BS.AKSI) == {"confirm", "delete"} and set(BS.AKSI) <= B.AKSI_DIIZINKAN


def test_tulis_massal_wajib_kunci_batas_50_dan_login(monkeypatch):
    bodi = BS.BulkAksiRequest(ids=[str(_id())])
    with pytest.raises(HTTPException) as e:
        _jalan(_endpoint("bulk_confirm")(_Req(), bodi))
    assert e.value.status_code == 400 and e.value.detail["code"] == "BULK_KUNCI_WAJIB"
    with pytest.raises(HTTPException) as e2:
        _jalan(_endpoint("bulk_delete")(_Req("K1"), BS.BulkAksiRequest(ids=[str(_id()) for _ in range(51)])))
    assert e2.value.status_code == 400 and e2.value.detail["code"] == "BULK_TERLALU_BANYAK" and e2.value.detail["batas"] == 50
    with pytest.raises(HTTPException) as e3:
        _jalan(_endpoint("bulk_confirm")(_Req("K1", user=False), bodi))
    assert e3.value.status_code == 401
    # pratinjau tak butuh kunci tetapi tetap berbatas 50
    with pytest.raises(HTTPException) as e4:
        _jalan(_endpoint("bulk_confirm_preview")(_Req(), BS.BulkAksiRequest(ids=[str(_id()) for _ in range(51)])))
    assert e4.value.detail["code"] == "BULK_TERLALU_BANYAK"


def test_tulis_meneruskan_kunci_batch_nomor_dan_fungsi_tunggal(monkeypatch):
    tangkap = {}

    async def jalan(pool, ctx, aksi, modul, ids, fn, kunci_batch=None, payload=None, nomor=None):
        tangkap.update(aksi=aksi, modul=modul, ids=ids, fn=fn, kunci=kunci_batch, nomor=nomor)
        return {"action": aksi}

    async def gp(): return _Pool(_K())

    async def nomor(pool, tid, ids): return {"n": 1}
    monkeypatch.setattr(B, "jalankan_per_item", jalan)
    monkeypatch.setattr(BS, "get_pool", gp)
    monkeypatch.setattr(BS, "_nomor", nomor)
    i = str(_id())
    out = _jalan(_endpoint("bulk_confirm")(_Req("K-777"), BS.BulkAksiRequest(ids=[i, i])))
    assert out == {"success": True, "data": {"action": "confirm"}}
    assert tangkap["kunci"] == "K-777" and tangkap["modul"] == "sales-orders" and len(tangkap["ids"]) == 1  # duplikat dibuang
    assert tangkap["fn"] is BS._tulis_konfirmasi and tangkap["nomor"] == {"n": 1}
    # fungsi item = jalur tunggal yang SAMA
    assert "SO._konfirmasi_so(" in inspect.getsource(BS._konfirmasi) and "SO.hapus_so_draf_core(" in inspect.getsource(BS._tulis_hapus)
    assert "_konfirmasi(" in inspect.getsource(BS._tulis_konfirmasi) and "_konfirmasi(" in inspect.getsource(BS._pratinjau_konfirmasi)
    assert "SO.rencana_hapus_so(" in inspect.getsource(BS._tulis_hapus)  # tulis memakai penentu yang sama dgn pratinjau
    ph = inspect.getsource(BS._pratinjau_hapus)
    assert "SO.rencana_hapus_so(" in ph and "SO.hapus_so_draf_core(" in ph


# ---------------- izin ----------------
def _izin(metode, jalur):
    return PM.PermissionMiddleware(lambda *a: None, False)._find_permission(jalur, metode)


def test_izin_massal_sama_dengan_aksi_tunggal():
    assert _izin("POST", "/api/sales-orders/bulk/confirm") == _izin("POST", "/api/sales-orders/X/confirm") == ("sales_order", "C")
    assert _izin("POST", "/api/sales-orders/bulk/confirm/preview") == ("sales_order", "C")
    assert _izin("POST", "/api/sales-orders/bulk/delete") == _izin("DELETE", "/api/sales-orders/X") == ("sales_order", "D")
    assert _izin("POST", "/api/sales-orders/bulk/delete/preview") == ("sales_order", "D")


def test_galat_konfirmasi_diberi_kode_stabil_dan_hapus_tulis_sekode_dengan_pratinjau(monkeypatch, guard):
    async def konfirmasi_so(conn, ctx, oid):
        raise HTTPException(status_code=int(oid.split("-")[0]), detail="pesan teks")
    monkeypatch.setattr(SO, "_konfirmasi_so", konfirmasi_so)
    for status, kode in ((404, "SO_TAK_ADA"), (400, "SO_BUKAN_DRAF"), (409, "SO_BERUBAH"), (500, "HTTP_500")):
        with pytest.raises(HTTPException) as e:
            _jalan(BS._konfirmasi(_K(), CTX, f"{status}-x"))
        assert e.value.status_code == status and e.value.detail == {"code": kode, "message": "pesan teks"}
    # hapus: kode galat tulis == kode blok pratinjau (penentu yang sama)
    oid = _id()
    for order, kode in ((None, "SO_TAK_ADA"), (_so("confirmed"), "SO_BUKAN_DRAF")):
        with pytest.raises(HTTPException) as e:
            _jalan(BS._tulis_hapus(_K(order=order), CTX, oid))
        assert e.value.detail["code"] == kode
        blok = _jalan(SO.rencana_hapus_so(_K(order=order), CTX, str(oid)))
        assert blok[0]["code"] == kode
