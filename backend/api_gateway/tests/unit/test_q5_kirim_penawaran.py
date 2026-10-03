"""Q5 (3 Okt 2026): kirim dokumen penawaran. Share draf = tautan + DITANDAI terkirim (status, sent_at, audit QUOTE_SENT)
dalam SATU transaksi; terkirim/dilihat/disetujui/jadi pesanan = tautan saja; batal/ditolak/kedaluwarsa = 409
QUOTE_NOT_SHAREABLE nol tulis; pratinjau = jalur sama lalu ROLLBACK. Koneksi tiruan, tanpa DB."""
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import dokumen as D
from app.services import penawaran_bagikan as PB
from app.utils import tanggal_tenant as TT

T, QID = "t-uji", uuid.uuid4()
HARI = date(2026, 10, 3)


class _Conn:
    def __init__(self, status, expiry=date(2026, 10, 17)):
        self.status, self.expiry = status, expiry
        self.dalam, self.tulis, self.log = 0, [], []

    def transaction(self):
        c = self

        class _T:
            async def __aenter__(s):
                c.dalam += 1

            async def __aexit__(s, et, e, tb):
                c.dalam -= 1
                c.log.append("rollback" if et else "commit")
                return False
        return _T()

    async def execute(self, q, *a):
        s = " ".join(q.split())
        if not s.startswith("SELECT pg_advisory"):
            self.tulis.append((s[:40], self.dalam > 0, a))
        return "UPDATE 1"

    async def fetchrow(self, q, *a):
        s = " ".join(q.split())
        if s.startswith("SELECT id, quote_number, status, expiry_date FROM quotes"):
            assert a == (QID, T) and "FOR UPDATE" in s
            return {"id": QID, "quote_number": "QUO-1", "status": self.status, "expiry_date": self.expiry}
        if s.startswith("SELECT quote_number AS nomor, status FROM quotes"):
            return {"nomor": "QUO-1", "status": self.status}
        if s.startswith("INSERT INTO document_shares"):
            self.tulis.append((s[:40], self.dalam > 0, a))
            n = datetime(2026, 10, 3, tzinfo=timezone.utc)
            return {"id": uuid.uuid4(), "sent_at": n, "expires_at": n + timedelta(days=30)}
        raise AssertionError(s)

    async def fetchval(self, q, *a):
        raise AssertionError(q)


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class _A:
            async def __aenter__(s):
                return c

            async def __aexit__(s, *a):
                return False
        return _A()


def _req(body=None):
    async def js():
        return body or {"channel": "wa"}
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": str(uuid.uuid4()), "tenant_id": T}), json=js)


@pytest.fixture
def pasang(monkeypatch):
    async def tgl(conn, tid):
        return HARI
    monkeypatch.setattr(TT, "tanggal_dokumen", tgl)

    def _p(conn):
        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(D, "_pool", pool)
        return conn
    return _p


def _jenis(c):
    return [s.split(" ")[0] + " " + s.split(" ")[2] if s.startswith("INSERT") else s.split(" ")[1]
            for s, _, _ in c.tulis]


@pytest.mark.asyncio
async def test_share_draf_tautan_dan_ditandai_terkirim_satu_transaksi(pasang):
    c = pasang(_Conn("draft"))
    r = await D.bagikan_dokumen(_req(), "quotation", str(QID))
    assert r["marked_sent"] is True and r["document"]["number"] == "QUO-1" and r["url"]
    jenis = [s for s, _, _ in c.tulis]
    assert any(s.startswith("UPDATE quotes SET status = 'sent'") for s in jenis), jenis
    audit = [a for s, _, a in c.tulis if s.startswith("INSERT INTO audit_logs")]
    assert len(audit) == 1 and audit[0][1] == "QUOTE_SENT"
    assert any(s.startswith("INSERT INTO document_shares") for s in jenis)
    assert all(dalam for _, dalam, _ in c.tulis), "SEMUA tulisan di SATU transaksi"
    assert c.log == ["commit"]


@pytest.mark.asyncio
@pytest.mark.parametrize("st", ["sent", "viewed", "accepted", "converted"])
async def test_share_bukan_draf_hanya_tautan(pasang, st):
    c = pasang(_Conn(st))
    r = await D.bagikan_dokumen(_req(), "quotation", str(QID))
    assert r["marked_sent"] is False
    assert [s[:27] for s, _, _ in c.tulis] == ["INSERT INTO document_shares"]


@pytest.mark.asyncio
@pytest.mark.parametrize("st,exp", [("void", date(2026, 10, 17)), ("declined", date(2026, 10, 17)),
                                    ("expired", date(2026, 9, 1)), ("sent", date(2026, 10, 2)),
                                    ("viewed", date(2026, 10, 2)), ("draft", date(2026, 10, 2))])
async def test_share_tak_boleh_409_nol_tulis(pasang, st, exp):
    c = pasang(_Conn(st, exp))
    with pytest.raises(HTTPException) as e:
        await D.bagikan_dokumen(_req(), "quotation", str(QID))
    assert e.value.status_code == 409 and e.value.detail["code"] == "QUOTE_NOT_SHAREABLE" and e.value.detail["message"]
    assert c.tulis == []


@pytest.mark.asyncio
async def test_pratinjau_draf_rollback_dan_bentuk(pasang):
    c = pasang(_Conn("draft"))
    r = await D.pratinjau_bagikan_dokumen(_req(), "quotation", str(QID))
    assert r == {"success": True, "data": {"status_now": "draft", "akan_ditandai_terkirim": True, "boleh": True,
                                           "code": None, "message": None, "number": "QUO-1"}}
    assert c.log == ["rollback"], c.log  # jalur sama dijalankan, lalu dibatalkan: nol tulis


@pytest.mark.asyncio
async def test_pratinjau_batal_200_boleh_false(pasang):
    c = pasang(_Conn("void"))
    r = await D.pratinjau_bagikan_dokumen(_req(), "quotation", str(QID))
    d = r["data"]
    assert d["boleh"] is False and d["code"] == "QUOTE_NOT_SHAREABLE" and d["status_now"] == "void"
    assert d["number"] == "QUO-1" and d["akan_ditandai_terkirim"] is False and c.tulis == []


def test_penentu_tanggal_hari_ini_masih_berlaku():
    assert PB.penentu("draft", "Q", HARI, HARI)["boleh"] is True  # berlaku s.d. hari ini = sah
    assert PB.penentu("sent", "Q", None, HARI) == {"boleh": True, "akan_ditandai_terkirim": False, "code": None,
                                                   "message": None}


def test_send_quote_dan_share_memakai_penanda_yang_sama():
    import inspect
    from app.routers import quotes as Q
    assert "tandai_terkirim(" in inspect.getsource(Q.send_quote)
    assert "tandai_terkirim(" in inspect.getsource(D._bagikan)


def test_rute_terpasang():
    r = {(x.path, tuple(sorted(x.methods))): x.endpoint.__name__ for x in D.router.routes}
    assert r[("/documents/{kind}/{doc_id}/share", ("POST",))] == "bagikan_dokumen"
    assert r[("/documents/{kind}/{doc_id}/share/preview", ("POST",))] == "pratinjau_bagikan_dokumen"


def test_detail_penawaran_membawa_kontak_dan_nama_usaha():
    from app.schemas.quotes import QuoteDetail
    f = QuoteDetail.model_fields
    assert f["customer_phone"].default is None and f["business_name"].default is None
