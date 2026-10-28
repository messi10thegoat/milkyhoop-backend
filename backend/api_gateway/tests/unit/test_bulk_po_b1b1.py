"""B1b-1 (28 Okt 2026): aksi massal Pesanan Pembelian -- KIRIM + HAPUS draf + BATAL (kembar Penjualan U1b F2-F4, tanpa uang).

Spek: (1) rute = 3 aksi x {tulis, pratinjau}, POST, daftar putih, tanpa receive/to-bill/close massal; (2) izin = izin aksi tunggal
(P/D/V); (3) jalur TUNGGAL: rute tunggal dan massal memanggil inti yang sama (kirim_po_core / hapus_po_draf_core / batal_po_core), handler
tak lagi menulis sendiri; (4) tulis per item: hasil per item, kode stabil, idem BULK_{AKSI}, pesan = pesan rute tunggal; (5) pratinjau =
BACA SAJA (transaksi readonly, tanpa kunci, nol tulis) dengan penentu yang sama; (6) pagar permintaan: kunci wajib, alasan wajib (batal),
401 tanpa user (kirim/batal), batas 50."""
import asyncio
import inspect
import os
import uuid
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.middleware import permission_middleware as PM  # noqa: E402
from app.routers import bulk_po as BP  # noqa: E402
from app.routers import purchase_orders as PO  # noqa: E402
from app.schemas import purchase_orders as PS  # noqa: E402
from app.services import bulk as B  # noqa: E402
from app.services import idem_buat as IB  # noqa: E402

APP = Path(__file__).resolve().parents[2] / "app"
T = "kaos-biru-konveksi"
UID = uuid.UUID("99999999-8888-7777-6666-555555555555")
D = Decimal


def _n(sql):
    return " ".join(sql.split())


def _jalan(c):
    return asyncio.run(c)


# ---------------- (1) rute ----------------
def test_rute_tiga_aksi_tulis_dan_pratinjau_post_daftar_putih():
    jalur = sorted((r.path, tuple(sorted(r.methods))) for r in BP.router.routes)
    assert jalur == sorted((f"/bulk/{a}{s}", ("POST",)) for a in ("send", "delete", "cancel") for s in ("", "/preview"))
    assert set(BP.AKSI) == {"send", "delete", "cancel"} and set(BP.AKSI) <= B.AKSI_DIIZINKAN
    for terlarang in ("receive", "to-bill", "close", "void", "post", "refund", "apply"):
        assert not any(terlarang in r.path for r in BP.router.routes), terlarang


# ---------------- (2) izin ----------------
@pytest.mark.parametrize("aksi,metode,tunggal,huruf", [
    ("send", "POST", "/api/purchase-orders/X/send", "P"),
    ("cancel", "POST", "/api/purchase-orders/X/cancel", "V"),
    ("delete", "DELETE", "/api/purchase-orders/X", "D"),
])
def test_izin_massal_sama_dengan_aksi_tunggal(aksi, metode, tunggal, huruf):
    assert PM.izin_untuk_jalur(tunggal, metode) == ("purchase_order", huruf)
    assert PM.izin_untuk_jalur(f"/api/purchase-orders/bulk/{aksi}", "POST") == ("purchase_order", huruf)
    assert PM.izin_untuk_jalur(f"/api/purchase-orders/bulk/{aksi}/preview", "POST") == ("purchase_order", huruf)


# ---------------- (3) jalur tunggal ----------------
INTI = {"send": ("send_purchase_order", "kirim_po_core"), "delete": ("delete_purchase_order", "hapus_po_draf_core"),
        "cancel": ("cancel_purchase_order", "batal_po_core")}


@pytest.mark.parametrize("aksi", sorted(INTI))
def test_rute_tunggal_memakai_inti_dan_tak_menulis_sendiri(aksi):
    handler, inti = INTI[aksi]
    src = inspect.getsource(getattr(PO, handler))
    assert f"{inti}(conn, ctx, po_id" in src
    for tulis in ("UPDATE purchase_orders", "DELETE FROM purchase_orders", "_kunci_po(", "rencana_"):
        assert tulis not in src, (handler, tulis)
    src_inti = inspect.getsource(getattr(PO, inti))
    assert "_kunci_po(" in src_inti and "_tolak(" in src_inti


def test_massal_tulis_memanggil_inti_yang_sama():
    src = inspect.getsource(BP._fungsi)
    for inti in ("PO.kirim_po_core(", "PO.hapus_po_draf_core(", "PO.batal_po_core(", "_blok(conn, ctx, i, aksi, alasan, kunci=True)",
                 "_blok(conn, ctx, i, aksi, alasan, kunci=False)"):
        assert inti in src, inti
    src_blok = inspect.getsource(BP._blok)  # penentu: kunci inti yang sama + po_rencana yang sama dengan rute tunggal
    for inti in ("PO._kunci_po(", "PR.rencana_kirim(", "PR.rencana_hapus(", "PR.rencana_batal("):
        assert inti in src_blok, inti
    for tulis in ("UPDATE purchase_orders", "DELETE FROM", "INSERT INTO", "conn.execute("):
        assert tulis not in inspect.getsource(BP).replace('conn.execute("SELECT set_config(', ""), tulis


# ---------------- tiruan DB ----------------
class _Tx:
    def __init__(self, kon, ro=False): self.kon, self.ro = kon, ro
    async def start(self): self.kon.log.append(("tx", "BEGIN RO" if self.ro else "BEGIN", ()))
    async def rollback(self): self.kon.log.append(("tx", "ROLLBACK", ()))
    async def __aenter__(self):
        await self.start()
        return self
    async def __aexit__(self, et, *a):
        self.kon.log.append(("tx", "ROLLBACK" if et else "COMMIT", ()))
        return False


class Kon:
    def __init__(self, pos: dict, diterima=None, deposit=None):
        self.pos, self.diterima, self.deposit = pos, diterima or {}, deposit or {}
        self.log = []

    def transaction(self, readonly=False, **kw): return _Tx(self, readonly)

    def _c(self, j, sql, a):
        q = _n(sql)
        self.log.append((j, q, a))
        return q

    async def execute(self, sql, *a):
        q = self._c("exec", sql, a)
        if q.startswith("UPDATE purchase_orders SET status = 'sent'"):
            self.pos[a[0]]["status"] = "sent"
        elif q.startswith("UPDATE purchase_orders SET status = 'cancelled'"):
            self.pos[a[0]]["status"] = "cancelled"
        elif q.startswith("DELETE FROM purchase_orders"):
            self.pos.pop(a[0])
        return "OK"

    async def fetchrow(self, sql, *a):
        q = self._c("fetchrow", sql, a)
        if "SUM(quantity_received)" in q:
            r, t = self.diterima.get(a[0], ("0", "0"))
            return {"diterima": D(r), "ditagih": D(t)}
        if "FROM purchase_orders" in q:
            po = self.pos.get(a[0])
            return dict(po) if po and a[1] == T else None
        return None

    async def fetch(self, sql, *a):
        q = self._c("fetch", sql, a)
        if "FROM vendor_deposits" in q:
            return [{"deposit_number": n} for n in self.deposit.get(a[1], [])]
        if "SELECT id, po_number FROM purchase_orders" in q:
            return [{"id": i, "po_number": p["po_number"]} for i, p in self.pos.items() if i in a[1]]
        return []

    async def fetchval(self, sql, *a):
        self._c("fetchval", sql, a)
        return None

    def tulis(self):
        return [(q, a) for j, q, a in self.log if j == "exec" and q.startswith(("UPDATE purchase_orders", "DELETE FROM purchase_orders"))]


class Pool:
    def __init__(self, kon): self.kon = kon

    def acquire(self):
        kon = self.kon

        class CM:
            async def __aenter__(s): return kon
            async def __aexit__(s, *a): return False
        return CM()


class Idem:
    def __init__(self): self.mulai, self.disimpan, self.lama = [], [], {}

    def pasang(self, mp):
        async def mulai_aksi(conn, ctx, kunci, prefix, doc_id, isi, response=None):
            if not kunci:
                return None, None, None
            kp = f"{prefix}:{ctx['user_id']}:{doc_id}:{kunci}"
            self.mulai.append((kp, isi))
            return kp, "S", self.lama.get(kp)

        async def simpan(conn, ctx, kp, sd, st, resp, rid):
            self.disimpan.append((kp, st, rid))
            self.lama[kp] = resp
            return resp
        mp.setattr(IB, "mulai_aksi", mulai_aksi)
        mp.setattr(IB, "simpan", simpan)
        return self


def _ids(n): return [uuid.UUID(int=i + 1) for i in range(n)]


def _pos(*status):
    return {i: {"id": i, "status": s, "po_number": f"PO-{k + 1:04d}"} for k, (i, s) in enumerate(zip(_ids(len(status)), status))}


class _Req:
    def __init__(self, kunci="K-BATCH", user=UID):
        self.headers = {"X-Idempotency-Key": kunci} if kunci else {}
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": str(user) if user else None}})()


def _endpoint(path):
    return next(r.endpoint for r in BP.router.routes if r.path == path)


def _pasang_pool(mp, kon):
    async def gp(): return Pool(kon)
    mp.setattr(BP, "get_pool", gp)


# ---------------- (4) tulis per item ----------------
def test_kirim_massal_hasil_per_item_kode_stabil_dan_idem_per_item(monkeypatch):
    kon = Kon(_pos("draft", "sent", "draft"))
    ids = _ids(3) + [uuid.UUID(int=99)]
    _pasang_pool(monkeypatch, kon)
    idem = Idem().pasang(monkeypatch)
    r = _jalan(_endpoint("/bulk/send")(_Req(), BP.BulkAksiRequest(ids=[str(i) for i in ids])))["data"]
    st = [(x["status"], x["http"], x["code"]) for x in r["items"]]
    assert st == [("ok", 200, None), ("rejected", 400, "PO_TAK_BISA_DIKIRIM"), ("ok", 200, None), ("rejected", 404, "PO_TAK_ADA")]
    assert r["items"][0]["result"] == {"id": str(ids[0]), "po_number": "PO-0001", "status": "sent"}
    assert r["items"][1]["message"] == "Pesanan pembelian berstatus 'sent' tidak bisa dikirim. Hanya Draf."
    assert [kp for kp, _ in idem.mulai] == [f"BULK_SEND:{UID}:{i}:K-BATCH" for i in ids]
    assert [a for _, a in kon.tulis()] == [(ids[0], UID), (ids[2], UID)]
    assert any(q.startswith("INSERT INTO audit_logs") and a[0] == "BULK_SEND" for j, q, a in kon.log if j == "exec")
    # ulang dengan kunci sama: replay, nol tulis baru
    n = len(kon.tulis())
    r2 = _jalan(_endpoint("/bulk/send")(_Req(), BP.BulkAksiRequest(ids=[str(i) for i in ids])))["data"]
    assert [x["replay"] for x in r2["items"]][:1] == [True] and len(kon.tulis()) == n


def test_hapus_massal_hanya_draf_app_user_id_dan_delete(monkeypatch):
    kon = Kon(_pos("draft", "cancelled"))
    _pasang_pool(monkeypatch, kon)
    Idem().pasang(monkeypatch)
    ids = _ids(2)
    r = _jalan(_endpoint("/bulk/delete")(_Req(), BP.BulkAksiRequest(ids=[str(i) for i in ids])))["data"]
    assert [(x["status"], x["code"]) for x in r["items"]] == [("ok", None), ("rejected", "PO_BUKAN_DRAF")]
    assert r["items"][0]["result"] == {"id": str(ids[0]), "po_number": "PO-0001"}
    i_user = max(k for k, x in enumerate(kon.log) if "set_config('app.user_id'" in x[1] and x[2] == (str(UID),))
    i_del = next(k for k, x in enumerate(kon.log) if x[1].startswith("DELETE FROM purchase_orders"))
    assert i_user < i_del and kon.tulis() == [("DELETE FROM purchase_orders WHERE id = $1 AND tenant_id = $2", (ids[0], T))]


def test_batal_massal_alasan_ke_inti_blok_diterima_ditagih(monkeypatch):
    pos = _pos("sent", "partial_received", "billed", "closed")
    ids = _ids(4)
    kon = Kon(pos, diterima={ids[1]: ("2", "0"), ids[2]: ("0", "3")})
    _pasang_pool(monkeypatch, kon)
    idem = Idem().pasang(monkeypatch)
    r = _jalan(_endpoint("/bulk/cancel")(_Req(), BP.BulkAksiRequest(ids=[str(i) for i in ids], reason="  Salah vendor ")))["data"]
    assert [(x["status"], x["code"]) for x in r["items"]] == [
        ("ok", None), ("rejected", "PO_SUDAH_DITERIMA"), ("rejected", "PO_SUDAH_DITAGIH"), ("rejected", "PO_TAK_BISA_DIBATALKAN")]
    assert kon.tulis() == [(_n("""UPDATE purchase_orders SET status = 'cancelled', cancelled_at = NOW(), cancelled_by = $2,
                                  cancelled_reason = $3, updated_at = NOW() WHERE id = $1"""), (ids[0], UID, "Salah vendor"))]
    assert r["items"][0]["result"] == {"id": str(ids[0]), "status": "cancelled"}
    assert idem.mulai[0][1] == {"aksi": "cancel", "payload": {"reason": "Salah vendor"}}


@pytest.mark.parametrize("aksi,status", [("send", "sent"), ("send", "closed"), ("delete", "sent"), ("delete", "received"),
                                         ("cancel", "cancelled"), ("cancel", "closed")])
def test_pesan_tolak_massal_sama_dengan_rute_tunggal(monkeypatch, aksi, status):
    kon = Kon(_pos(status))
    (pid,) = _ids(1)
    _pasang_pool(monkeypatch, kon)

    async def gp(): return Pool(kon)
    monkeypatch.setattr(PO, "get_pool", gp)
    Idem().pasang(monkeypatch)
    with pytest.raises(HTTPException) as e:
        if aksi == "send":
            _jalan(PO.send_purchase_order(_Req(kunci=None), pid))
        elif aksi == "delete":
            _jalan(PO.delete_purchase_order(_Req(kunci=None), pid))
        else:
            _jalan(PO.cancel_purchase_order(_Req(kunci=None), pid, PS.CancelPurchaseOrderRequest(reason="x")))
    r = _jalan(_endpoint(f"/bulk/{aksi}")(_Req(), BP.BulkAksiRequest(ids=[str(pid)], reason="x")))["data"]["items"][0]
    assert (r["http"], r["message"]) == (e.value.status_code, e.value.detail)


# ---------------- (5) pratinjau ----------------
@pytest.mark.parametrize("aksi", ["send", "delete", "cancel"])
def test_pratinjau_baca_saja_tanpa_kunci_nol_tulis(monkeypatch, aksi):
    kon = Kon(_pos("draft", "sent", "partial_received"), deposit={})
    _pasang_pool(monkeypatch, kon)
    ids = _ids(3)
    r = _jalan(_endpoint(f"/bulk/{aksi}/preview")(_Req(kunci=None), BP.BulkAksiRequest(ids=[str(i) for i in ids], reason="x")))["data"]
    assert r["preview"] is True and r["total"] == 3
    assert ("tx", "BEGIN RO", ()) in kon.log and kon.log[-1] == ("tx", "ROLLBACK", ())
    assert kon.tulis() == [] and not any("pg_advisory_xact_lock" in x[1] or "FOR UPDATE" in x[1] for x in kon.log)
    harap = {"send": [True, False, False], "delete": [True, False, False], "cancel": [True, True, True]}[aksi]
    assert [x["ok"] for x in r["items"]] == harap


def test_pratinjau_batal_alasan_kosong_blok_dan_catatan_uang_muka(monkeypatch):
    ids = _ids(1)
    kon = Kon(_pos("sent"), deposit={ids[0]: ["VD-2026-00007"]})
    _pasang_pool(monkeypatch, kon)
    r = _jalan(_endpoint("/bulk/cancel/preview")(_Req(kunci=None), BP.BulkAksiRequest(ids=[str(ids[0])])))["data"]["items"][0]
    assert r["ok"] is False and r["blocks"] == [{"code": "ALASAN_WAJIB", "message": "Alasan pembatalan wajib diisi."}]
    r = _jalan(_endpoint("/bulk/cancel/preview")(_Req(kunci=None), BP.BulkAksiRequest(ids=[str(ids[0])], reason="ok")))["data"]["items"][0]
    assert r["ok"] is True and r["preview"]["status"] == "cancelled"
    assert [c["code"] for c in r["preview"]["notes"]] == ["UANG_MUKA_TETAP"]


def test_pratinjau_kirim_ringkasan_bentuk_sama_dengan_inti(monkeypatch):
    kon = Kon(_pos("draft"))
    _pasang_pool(monkeypatch, kon)
    (pid,) = _ids(1)
    r = _jalan(_endpoint("/bulk/send/preview")(_Req(kunci=None), BP.BulkAksiRequest(ids=[str(pid)])))["data"]["items"][0]
    assert r["preview"] == {"id": str(pid), "po_number": "PO-0001", "status": "sent"}  # == kirim_po_core


# ---------------- (6) pagar permintaan ----------------
@pytest.mark.parametrize("aksi", ["send", "delete", "cancel"])
def test_tanpa_kunci_400(monkeypatch, aksi):
    _pasang_pool(monkeypatch, Kon(_pos("draft")))
    with pytest.raises(HTTPException) as e:
        _jalan(_endpoint(f"/bulk/{aksi}")(_Req(kunci=None), BP.BulkAksiRequest(ids=[str(_ids(1)[0])], reason="x")))
    assert e.value.status_code == 400 and e.value.detail["code"] == "BULK_KUNCI_WAJIB"


@pytest.mark.parametrize("alasan", [None, "", "   "])
def test_batal_tanpa_alasan_400_sebelum_db(monkeypatch, alasan):
    kon = Kon(_pos("sent"))
    _pasang_pool(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(_endpoint("/bulk/cancel")(_Req(), BP.BulkAksiRequest(ids=[str(_ids(1)[0])], reason=alasan)))
    assert e.value.status_code == 400 and e.value.detail["code"] == "BULK_ALASAN_WAJIB" and kon.log == []


@pytest.mark.parametrize("aksi,harap", [("send", 401), ("cancel", 401), ("delete", 200)])
def test_tanpa_user_id_kirim_batal_401_hapus_jalan(monkeypatch, aksi, harap):
    kon = Kon(_pos("draft" if aksi != "cancel" else "sent"))
    _pasang_pool(monkeypatch, kon)
    Idem().pasang(monkeypatch)
    ep = _endpoint(f"/bulk/{aksi}")
    if harap == 401:
        with pytest.raises(HTTPException) as e:
            _jalan(ep(_Req(user=None), BP.BulkAksiRequest(ids=[str(_ids(1)[0])], reason="x")))
        assert e.value.status_code == 401 and e.value.detail == "ID pengguna wajib ada." and kon.log == []
    else:
        r = _jalan(ep(_Req(user=None), BP.BulkAksiRequest(ids=[str(_ids(1)[0])])))["data"]
        assert r["ok_count"] == 1


def test_batas_lima_puluh(monkeypatch):
    _pasang_pool(monkeypatch, Kon({}))
    with pytest.raises(HTTPException) as e:
        _jalan(_endpoint("/bulk/send")(_Req(), BP.BulkAksiRequest(ids=[str(i) for i in _ids(51)])))
    assert e.value.status_code == 400 and e.value.detail["code"] == "BULK_TERLALU_BANYAK" and e.value.detail["batas"] == 50
