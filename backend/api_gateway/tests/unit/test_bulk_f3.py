"""U1b F3 (5 Okt 2026, MASTER GO): Penawaran Hapus draf + Kirim (tandai terkirim) MASSAL, dengan pratinjau per item.

Penjaga: (1) hapus_penawaran_core / kirim_penawaran_core = isi rute tunggal (urutan kunci advisory -> FOR UPDATE -> aturan -> tulis;
tenant eksplisit) dan rute tunggal hanya membungkusnya di transaksi; (2) rencana_hapus_penawaran = penentu baca (pola Xero via
alasan_tak_bisa_hapus); (3) surel TETAP ditolak sebelum menandai terkirim; (4) rute bulk: kunci idempotensi WAJIB, batas 50, daftar
putih, izin = izin aksi tunggal, kode galat stabil, tulis memakai penentu yang sama dgn pratinjau."""
import asyncio
import inspect
import uuid

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import bulk_quote as BQ
from app.routers import quotes as Q
from app.services import bulk as B

T = "t1"
U = uuid.UUID("0bccdb25-fdf0-4e99-9024-b9a20846f76c")
CTX = {"tenant_id": T, "user_id": U}


def _jalan(c): return asyncio.run(c)


def _id(): return uuid.uuid4()


class _K:
    def __init__(self, quote=None, status_val=None):
        self.quote, self.status_val, self.sql = quote, status_val, []

    async def fetchrow(self, sql, *a): self.sql.append(("row", " ".join(sql.split()), a)); return self.quote
    async def fetchval(self, sql, *a): self.sql.append(("val", " ".join(sql.split()), a)); return self.status_val
    async def execute(self, sql, *a): self.sql.append(("exec", " ".join(sql.split()), a))


def _q(status="draft", nomor="QUO-1"):
    return {"id": _id(), "status": status, "quote_number": nomor, "sent_at": None}


@pytest.fixture
def alasan(monkeypatch):
    s = {"v": None}

    async def f(conn, tid, quote): return s["v"]
    monkeypatch.setattr(Q, "alasan_tak_bisa_hapus", f)
    return s


@pytest.fixture
def terkirim(monkeypatch):
    rek = []

    async def f(conn, tid, qid, nomor, user, sumber): rek.append((tid, qid, nomor, user, sumber))
    import app.services.penawaran_bagikan as PB
    monkeypatch.setattr(PB, "tandai_terkirim", f)
    return rek


# ---------------- hapus ----------------
def test_inti_hapus_urutan_tenant_dan_hasil(alasan):
    k = _K(quote=_q())
    qid = str(_id())
    out = _jalan(Q.hapus_penawaran_core(k, CTX, qid))
    assert out == {"quote_number": "QUO-1"}
    s = [x[1] for x in k.sql]
    assert "pg_advisory_xact_lock" in s[0] and k.sql[0][2] == (f"QUOTE:{T}:{qid}",)
    assert "FOR UPDATE" in s[1] and k.sql[1][2][1] == T
    assert "set_config('app.user_id'" in s[2] and s[3].startswith("DELETE FROM quotes") and k.sql[3][2][1] == T


def test_inti_hapus_404_dan_409_kode_stabil(alasan):
    with pytest.raises(HTTPException) as e:
        _jalan(Q.hapus_penawaran_core(_K(quote=None), CTX, str(_id())))
    assert e.value.status_code == 404
    alasan["v"] = "Penawaran QUO-1 sudah pernah dibagikan ke pelanggan. Batalkan saja."
    k = _K(quote=_q())
    with pytest.raises(HTTPException) as e2:
        _jalan(Q.hapus_penawaran_core(k, CTX, str(_id())))
    assert e2.value.status_code == 409 and e2.value.detail["code"] == "QUOTE_NOT_DELETABLE" and "dibagikan" in e2.value.detail["message"]
    assert not [x for x in k.sql if x[1].startswith("DELETE")]  # tak menghapus bila ditolak


def test_rute_hapus_tunggal_membungkus_inti_di_transaksi():
    src = " ".join(inspect.getsource(Q.delete_quote).split())
    assert "async with conn.transaction(): data = await hapus_penawaran_core(conn, ctx, quote_id)" in src
    assert 'message="Quote deleted successfully"' in src and "DELETE FROM" not in src


def test_rencana_hapus(alasan):
    qid = str(_id())
    assert _jalan(Q.rencana_hapus_penawaran(_K(quote=None), CTX, qid))[0]["code"] == "QUOTE_TAK_ADA"
    assert _jalan(Q.rencana_hapus_penawaran(_K(quote=_q()), CTX, qid)) == []
    alasan["v"] = "sudah dikirim"
    b = _jalan(Q.rencana_hapus_penawaran(_K(quote=_q()), CTX, qid))
    assert b == [{"code": "QUOTE_NOT_DELETABLE", "status": 409, "message": "sudah dikirim"}]
    k = _K(quote=_q())
    _jalan(Q.rencana_hapus_penawaran(k, CTX, qid))
    assert all(x[0] != "exec" for x in k.sql)  # penentu BACA: nol tulisan


# ---------------- kirim ----------------
def test_inti_kirim_urutan_dan_penanda(terkirim):
    k = _K(quote=_q("draft"))
    qid = str(_id())
    out = _jalan(Q.kirim_penawaran_core(k, CTX, qid))
    assert out == {"quote_number": "QUO-1", "status": "sent"}
    assert "pg_advisory_xact_lock" in k.sql[0][1] and k.sql[0][2] == (f"QUOTE:{T}:{qid}",) and "FOR UPDATE" in k.sql[1][1]
    assert terkirim == [(T, k.quote["id"], "QUO-1", U, "api:quotes")]
    # status 'sent' boleh dikirim ulang (tunggal pun)
    assert _jalan(Q.kirim_penawaran_core(_K(quote=_q("sent")), CTX, qid))["status"] == "sent"


def test_inti_kirim_penolakan_surel_tetap_sebelum_menandai(terkirim):
    qid = str(_id())
    with pytest.raises(HTTPException) as e:
        _jalan(Q.kirim_penawaran_core(_K(quote=None), CTX, qid))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e2:
        _jalan(Q.kirim_penawaran_core(_K(quote=_q("accepted")), CTX, qid))
    assert e2.value.status_code == 400 and "dikirim" in e2.value.detail
    with pytest.raises(HTTPException) as e3:
        _jalan(Q.kirim_penawaran_core(_K(quote=_q("draft")), CTX, qid, send_email=True))
    assert e3.value.status_code == 422 and "TIDAK ditandai terkirim" in e3.value.detail
    assert terkirim == []  # tak satu pun penolakan menandai terkirim


def test_rute_kirim_tunggal_membungkus_inti():
    src = " ".join(inspect.getsource(Q.send_quote).split())
    assert "kirim_penawaran_core(conn, ctx, quote_id, bool(body and body.send_email))" in src
    assert 'message="Quote sent successfully"' in src and "tandai_terkirim(" not in src


# ---------------- rute bulk ----------------
class _Req:
    def __init__(self, kunci=None, user=True):
        self.headers = {"X-Idempotency-Key": kunci} if kunci else {}
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": str(U)} if user else None})()


def _ep(nama): return next(r.endpoint for r in BQ.router.routes if r.endpoint.__name__ == nama)


def test_rute_dan_daftar_putih():
    jalur = sorted((r.path, tuple(sorted(r.methods))) for r in BQ.router.routes)
    assert jalur == [("/bulk/delete", ("POST",)), ("/bulk/delete/preview", ("POST",)), ("/bulk/send", ("POST",)),
                     ("/bulk/send/preview", ("POST",))]
    assert set(BQ.AKSI) == {"delete", "send"} and set(BQ.AKSI) <= B.AKSI_DIIZINKAN


def test_kunci_wajib_batas_50_login():
    bodi = BQ.BulkAksiRequest(ids=[str(_id())])
    with pytest.raises(HTTPException) as e:
        _jalan(_ep("bulk_send")(_Req(), bodi))
    assert e.value.detail["code"] == "BULK_KUNCI_WAJIB"
    with pytest.raises(HTTPException) as e2:
        _jalan(_ep("bulk_delete")(_Req("K"), BQ.BulkAksiRequest(ids=[str(_id()) for _ in range(51)])))
    assert e2.value.detail["code"] == "BULK_TERLALU_BANYAK" and e2.value.detail["batas"] == 50
    with pytest.raises(HTTPException) as e3:
        _jalan(_ep("bulk_send")(_Req("K", user=False), bodi))
    assert e3.value.status_code == 401
    with pytest.raises(HTTPException) as e4:
        _jalan(_ep("bulk_send_preview")(_Req(), BQ.BulkAksiRequest(ids=[str(_id()) for _ in range(51)])))
    assert e4.value.detail["code"] == "BULK_TERLALU_BANYAK"


def test_tulis_meneruskan_kunci_modul_dan_fungsi(monkeypatch):
    tangkap = {}

    async def jalan(pool, ctx, aksi, modul, ids, fn, kunci_batch=None, payload=None, nomor=None):
        tangkap.update(aksi=aksi, modul=modul, ids=ids, fn=fn, kunci=kunci_batch, nomor=nomor)
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
    monkeypatch.setattr(BQ, "get_pool", gp)
    monkeypatch.setattr(BQ, "_nomor", nomor)
    i = str(_id())
    out = _jalan(_ep("bulk_send")(_Req("K-1"), BQ.BulkAksiRequest(ids=[i, i])))
    assert out == {"success": True, "data": {"action": "send"}} and tangkap["kunci"] == "K-1" and tangkap["modul"] == "quotes"
    assert len(tangkap["ids"]) == 1 and tangkap["fn"] is BQ._tulis_kirim and tangkap["nomor"] == {"n": 1}
    assert "Q.kirim_penawaran_core(" in inspect.getsource(BQ._kirim)
    th = inspect.getsource(BQ._tulis_hapus)
    assert "Q.rencana_hapus_penawaran(" in th and "Q.hapus_penawaran_core(" in th
    ph = inspect.getsource(BQ._pratinjau_hapus)
    assert "Q.rencana_hapus_penawaran(" in ph and "Q.hapus_penawaran_core(" in ph


def test_kode_galat_kirim_stabil_dan_hapus_tulis_sekode_pratinjau(monkeypatch, alasan, terkirim):
    for status, kode, q in ((404, "QUOTE_TAK_ADA", None), (400, "QUOTE_TAK_BISA_DIKIRIM", _q("accepted"))):
        with pytest.raises(HTTPException) as e:
            _jalan(BQ._kirim(_K(quote=q), CTX, _id()))
        assert e.value.status_code == status and e.value.detail["code"] == kode
    # hapus: tulis memakai penentu yang sama -> kode sama
    oid = _id()
    with pytest.raises(HTTPException) as e:
        _jalan(BQ._tulis_hapus(_K(quote=None), CTX, oid))
    assert e.value.detail["code"] == "QUOTE_TAK_ADA" and e.value.status_code == 404
    alasan["v"] = "sudah dikirim"
    with pytest.raises(HTTPException) as e2:
        _jalan(BQ._tulis_hapus(_K(quote=_q()), CTX, oid))
    assert e2.value.detail["code"] == "QUOTE_NOT_DELETABLE" and e2.value.status_code == 409


def test_pratinjau_kirim_memberi_tahu_bila_sudah_terkirim(terkirim):
    blok, ringkas = _jalan(BQ._pratinjau_kirim(_K(quote=_q("sent"), status_val="sent"), CTX, _id()))
    assert blok == [] and ringkas["status"] == "sent" and ringkas["sudah_terkirim_sebelumnya"] is True
    blok2, r2 = _jalan(BQ._pratinjau_kirim(_K(quote=_q("draft"), status_val="draft"), CTX, _id()))
    assert r2["sudah_terkirim_sebelumnya"] is False


def _izin(metode, jalur):
    return PM.PermissionMiddleware(lambda *a: None, False)._find_permission(jalur, metode)


def test_izin_massal_sama_dengan_aksi_tunggal():
    assert _izin("POST", "/api/quotes/bulk/send") == _izin("POST", "/api/quotes/X/send") == ("quote", "C")
    assert _izin("POST", "/api/quotes/bulk/send/preview") == ("quote", "C")
    assert _izin("POST", "/api/quotes/bulk/delete") == _izin("DELETE", "/api/quotes/X") == ("quote", "D")
    assert _izin("POST", "/api/quotes/bulk/delete/preview") == ("quote", "D")
