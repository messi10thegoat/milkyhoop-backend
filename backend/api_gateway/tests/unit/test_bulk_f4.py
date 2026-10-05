"""U1b F4 (5 Okt 2026, MASTER GO): Proforma BATAL draf MASSAL. Terbit proforma massal TIDAK ADA (putusan MASTER).

Penjaga: (1) batal_proforma_core = langkah rute /cancel lama (kunci proforma -> kunci baris SO -> _rencana_batal -> blok pertama ->
_tulis_batal) + hanya_draf; rute tunggal hanya membungkusnya (idempotensi tetap di rute); (2) bulk: hanya DRAF, alasan wajib untuk tulis,
kunci idempotensi WAJIB, batas 50; (3) TIDAK ada rute terbit massal; (4) izin = izin /cancel tunggal; (5) tulis memakai penentu yang sama
dgn pratinjau (kode galat sama)."""
import asyncio
import inspect
import uuid

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import bulk_proforma as BP
from app.routers import proformas as PF
from app.services import bulk as B

T = "t1"
U = uuid.UUID("0bccdb25-fdf0-4e99-9024-b9a20846f76c")
CTX = {"tenant_id": T, "user_id": U}


def _jalan(c): return asyncio.run(c)


def _id(): return uuid.uuid4()


class _K:
    def __init__(self): self.sql = []

    async def execute(self, sql, *a): self.sql.append(("exec", " ".join(sql.split()), a))


def _cur(status="draft"):
    return {"id": _id(), "status": status, "sales_order_id": _id(), "proforma_number": "PRO-1", "amount": 100}


@pytest.fixture
def pf(monkeypatch):
    jejak = []
    state = {"cur": _cur(), "blocks": [], "tulis_dipanggil": 0}

    async def kunci(conn, ctx, pid):
        jejak.append("kunci")
        c = state["cur"]
        if c is None:
            raise HTTPException(status_code=404, detail="Proforma tidak ditemukan.")
        return c

    async def rencana(conn, ctx, cur, alasan):
        jejak.append("rencana")
        return {"order": {"order_number": "SO-1", "id": _id()}, "blocks": state["blocks"], "linked_deposits": [], "impact": {}}

    def angkat(r):
        jejak.append("angkat")
        if r["blocks"]:
            b = r["blocks"][0]
            raise HTTPException(status_code=b.get("status", 400), detail={"code": b["code"], "message": b["message"]})

    async def tulis(conn, ctx, cur, alasan):
        jejak.append("tulis")
        state["tulis_dipanggil"] += 1
        return {"id": cur["id"], "proforma_number": "PRO-1", "status": "cancelled"}

    monkeypatch.setattr(PF, "_kunci_proforma", kunci)
    monkeypatch.setattr(PF, "_rencana_batal", rencana)
    monkeypatch.setattr(PF, "_angkat_blok_pertama", angkat)
    monkeypatch.setattr(PF, "_tulis_batal", tulis)
    monkeypatch.setattr(PF, "serialize_proforma", lambda row, nomor_so, paid, *a: {"proforma_number": row["proforma_number"], "status": row["status"], "so": nomor_so})
    state["jejak"] = jejak
    return state


# ---------------- (1) inti ----------------
def test_inti_urutan_langkah_rute_lama(pf):
    k = _K()
    out = _jalan(PF.batal_proforma_core(k, CTX, _id(), "salah ketik"))
    assert out == {"proforma_number": "PRO-1", "status": "cancelled", "so": "SO-1"}
    assert pf["jejak"] == ["kunci", "rencana", "angkat", "tulis"]
    s, a = k.sql[0][1], k.sql[0][2]
    assert "SELECT 1 FROM sales_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE" in s  # kunci baris SO
    assert a[1] == T


def test_inti_hanya_draf_menolak_sebelum_menulis(pf):
    pf["cur"] = _cur("issued")
    k = _K()
    with pytest.raises(HTTPException) as e:
        _jalan(PF.batal_proforma_core(k, CTX, _id(), "x", hanya_draf=True))
    assert e.value.status_code == 400 and e.value.detail["code"] == "PROFORMA_BUKAN_DRAF" and "Terbit" in e.value.detail["message"]
    assert pf["tulis_dipanggil"] == 0 and not k.sql  # tak sempat mengunci SO / menulis
    # tanpa hanya_draf (rute tunggal) proforma terbit tetap bisa dibatalkan -> perilaku lama utuh
    assert _jalan(PF.batal_proforma_core(_K(), CTX, _id(), "x"))["status"] == "cancelled"


def test_inti_blok_penentu_diangkat_tanpa_menulis(pf):
    pf["blocks"] = [{"code": "PROFORMA_HAS_PAYMENT", "status": 400, "message": "Sudah menerima pembayaran."}]
    with pytest.raises(HTTPException) as e:
        _jalan(PF.batal_proforma_core(_K(), CTX, _id(), "x"))
    assert e.value.detail["code"] == "PROFORMA_HAS_PAYMENT" and pf["tulis_dipanggil"] == 0


def test_rute_tunggal_membungkus_inti_dan_idempotensi_tetap():
    src = " ".join(inspect.getsource(PF.cancel_proforma).split())
    assert "data = await batal_proforma_core(conn, ctx, pid, body.reason)" in src
    assert '"PROFORMA_CANCEL"' in src and "_idem_aksi(conn, ctx, request, \"CANCEL\"" in src
    assert "_tulis_batal(" not in src and "_rencana_batal(" not in src  # rute tak menulis/menentukan sendiri


# ---------------- bulk ----------------
class _Req:
    def __init__(self, kunci=None, user=True):
        self.headers = {"X-Idempotency-Key": kunci} if kunci else {}
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": str(U)} if user else None})()


def test_rute_hanya_cancel_dan_TIDAK_ada_terbit_massal():
    jalur = sorted((r.path, tuple(sorted(r.methods))) for r in BP.router.routes)
    assert jalur == [("/bulk/cancel", ("POST",)), ("/bulk/cancel/preview", ("POST",))]
    assert BP.AKSI == ("cancel",) and set(BP.AKSI) <= B.AKSI_DIIZINKAN
    assert "issue" not in B.AKSI_DIIZINKAN and "terbit" not in B.AKSI_DIIZINKAN
    src = (inspect.getsource(BP)).lower()
    assert "_tulis_terbit" not in src and "/issue" not in src and "_rencana_terbit" not in src  # putusan MASTER: terbit tak massal


def test_tulis_wajib_kunci_alasan_batas_dan_login():
    bodi = BP.BulkBatalRequest(ids=[str(_id())], reason="x")
    with pytest.raises(HTTPException) as e:
        _jalan(BP.bulk_cancel(_Req(), bodi))
    assert e.value.detail["code"] == "BULK_KUNCI_WAJIB"
    with pytest.raises(HTTPException) as e2:
        _jalan(BP.bulk_cancel(_Req("K"), BP.BulkBatalRequest(ids=[str(_id())], reason="   ")))
    assert e2.value.detail["code"] == "BULK_ALASAN_WAJIB"
    with pytest.raises(HTTPException) as e3:
        _jalan(BP.bulk_cancel(_Req("K"), BP.BulkBatalRequest(ids=[str(_id()) for _ in range(51)], reason="x")))
    assert e3.value.detail["code"] == "BULK_TERLALU_BANYAK" and e3.value.detail["batas"] == 50
    with pytest.raises(HTTPException) as e4:
        _jalan(BP.bulk_cancel(_Req("K", user=False), bodi))
    assert e4.value.status_code == 401
    with pytest.raises(HTTPException) as e5:  # pratinjau tanpa alasan TIDAK ditolak di gerbang (muncul sebagai blok per item)
        _jalan(BP.bulk_cancel_preview(_Req(), BP.BulkBatalRequest(ids=[str(_id()) for _ in range(51)])))
    assert e5.value.detail["code"] == "BULK_TERLALU_BANYAK"


def test_blok_semua_penghalang_bukan_draf_plus_penentu(pf):
    pf["cur"] = _cur("issued")
    pf["blocks"] = [{"code": "PROFORMA_HAS_PAYMENT", "status": 400, "message": "Sudah menerima pembayaran."},
                    {"code": "CANCEL_REASON_REQUIRED", "status": 422, "message": "Alasan pembatalan wajib diisi."}]
    blok = _jalan(BP._blok_batal(_K(), CTX, _id(), ""))
    assert [b["code"] for b in blok] == ["PROFORMA_BUKAN_DRAF", "PROFORMA_HAS_PAYMENT", "CANCEL_REASON_REQUIRED"]
    pf["cur"], pf["blocks"] = _cur("draft"), []
    assert _jalan(BP._blok_batal(_K(), CTX, _id(), "x")) == []
    pf["cur"] = None
    with pytest.raises(HTTPException) as e:
        _jalan(BP._blok_batal(_K(), CTX, _id(), "x"))
    assert e.value.status_code == 404 and e.value.detail["code"] == "PROFORMA_TAK_ADA"


def test_tulis_dan_pratinjau_memakai_penentu_dan_inti_yang_sama(pf):
    tulis, pratinjau = BP._fungsi("alasan")
    # pratinjau: bersih -> ringkasan dari inti; terblok -> blok tanpa menulis
    blok, ringkas = _jalan(pratinjau(_K(), CTX, _id()))
    assert blok == [] and ringkas == {"proforma_number": "PRO-1", "status": "cancelled"} and pf["tulis_dipanggil"] == 1
    pf["blocks"] = [{"code": "PROFORMA_HAS_PAYMENT", "status": 400, "message": "m"}]
    pf["tulis_dipanggil"] = 0
    blok2, r2 = _jalan(pratinjau(_K(), CTX, _id()))
    assert blok2[0]["code"] == "PROFORMA_HAS_PAYMENT" and r2 is None and pf["tulis_dipanggil"] == 0
    # tulis: kode galat == kode blok pratinjau
    with pytest.raises(HTTPException) as e:
        _jalan(tulis(_K(), CTX, _id()))
    assert e.value.detail["code"] == "PROFORMA_HAS_PAYMENT" and e.value.status_code == 400
    pf["blocks"], pf["cur"] = [], _cur("cancelled")
    with pytest.raises(HTTPException) as e2:
        _jalan(tulis(_K(), CTX, _id()))
    assert e2.value.detail["code"] == "PROFORMA_BUKAN_DRAF"
    src = inspect.getsource(BP._fungsi)
    assert "PF.batal_proforma_core(" in src and "hanya_draf=True" in src


def test_tulis_meneruskan_alasan_kunci_dan_modul(monkeypatch):
    tangkap = {}

    async def jalan(pool, ctx, aksi, modul, ids, fn, kunci_batch=None, payload=None, nomor=None):
        tangkap.update(aksi=aksi, modul=modul, ids=ids, kunci=kunci_batch, payload=payload, nomor=nomor)
        return {"action": aksi}

    class P:
        def acquire(s):
            class A:
                async def __aenter__(a): return _K()
                async def __aexit__(a, *e): return False
            return A()

    async def gp(): return P()

    async def nomor(pool, tid, ids): return {"n": 1}
    monkeypatch.setattr(B, "jalankan_per_item", jalan)
    monkeypatch.setattr(BP, "get_pool", gp)
    monkeypatch.setattr(BP, "_nomor", nomor)
    i = str(_id())
    out = _jalan(BP.bulk_cancel(_Req("K-9"), BP.BulkBatalRequest(ids=[i, i], reason="  salah  ")))
    assert out == {"success": True, "data": {"action": "cancel"}}
    assert tangkap["kunci"] == "K-9" and tangkap["modul"] == "proformas" and len(tangkap["ids"]) == 1
    assert tangkap["payload"] == {"reason": "salah"} and tangkap["nomor"] == {"n": 1}  # alasan masuk sidik idempotensi per item


def _izin(metode, jalur):
    return PM.PermissionMiddleware(lambda *a: None, False)._find_permission(jalur, metode)


def test_izin_massal_sama_dengan_cancel_tunggal():
    assert _izin("POST", "/api/proformas/bulk/cancel") == _izin("POST", "/api/proformas/X/cancel") == ("proforma", "C")
    assert _izin("POST", "/api/proformas/bulk/cancel/preview") == ("proforma", "C")
