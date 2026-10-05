"""U1b F5 (5 Okt 2026, MASTER GO): PDF massal (ZIP per dokumen, <=25) + tautan bagikan massal (+pratinjau) untuk 8 modul.

Penjaga: (1) ZIP: nama berkas aman+unik, _RINGKASAN.txt, id tak ada/gagal/waktu habis DILEWATI (tak menggagalkan semua), batas 25, audit;
(2) bagikan: draf/dibatalkan ditolak PER ITEM (penawaran ikut aturan penentunya), jalur dokumen._bagikan yang sama dengan share tunggal,
URL tautan = RAHASIA (tak disimpan di tabel idempotensi, replay tanpa url), kunci idempotensi WAJIB, batas 50; (3) rute per modul
hanya pdf/share/share-preview + daftar putih; (4) izin = izin PDF tunggal (R) / share tunggal (E)."""
import asyncio
import inspect
import io
import uuid
import zipfile
from datetime import date

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import bulk_dokumen as BD
from app.routers import dokumen as D
from app.services import bulk as B
from app.services.bulk_export import SPEC

T = "t1"
U = uuid.UUID("0bccdb25-fdf0-4e99-9024-b9a20846f76c")
CTX = {"tenant_id": T, "user_id": U}


def _jalan(c): return asyncio.run(c)


def _id(): return uuid.uuid4()


# ---------------- ZIP ----------------
def test_zip_nama_aman_unik_dan_ringkasan():
    z = zipfile.ZipFile(io.BytesIO(BD.buat_zip([("INV/2610 0001", b"%PDF-a"), ("INV/2610 0001", b"%PDF-b"), ("../../etc/passwd", b"x"), (None, b"y")], "ringkas")))
    nama = z.namelist()
    assert len(nama) == len(set(n.lower() for n in nama)) == 5 and "_RINGKASAN.txt" in nama
    assert all("/" not in n and ".." not in n and n.endswith((".pdf", ".txt")) for n in nama)
    assert z.read("_RINGKASAN.txt") == b"ringkas" and z.read(nama[0]) == b"%PDF-a"


# ---------------- bagikan: pemeriksaan per item ----------------
class _K:
    def __init__(self, dok=None): self.dok = dok


@pytest.fixture
def dok(monkeypatch):
    s = {"v": {"nomor": "X-1", "status": "posted"}, "exc": None}

    async def ada(conn, tid, kind, uid):
        if s["exc"]:
            raise s["exc"]
        return s["v"]
    monkeypatch.setattr(D, "_dokumen_ada", ada)
    return s


def _periksa(kind): return _jalan(BD._periksa_bagikan(_K(), CTX, kind, _id()))


def test_periksa_draf_dan_batal_ditolak_per_item(dok):
    assert _periksa("invoice")["status"] == "posted"
    for st, kode in (("draft", "DOKUMEN_DRAF"), ("void", "DOKUMEN_BATAL"), ("voided", "DOKUMEN_BATAL"), ("cancelled", "DOKUMEN_BATAL")):
        dok["v"] = {"nomor": "X-1", "status": st}
        for kind in ("invoice", "receipt", "delivery", "rekap", "credit_note", "proforma"):
            with pytest.raises(HTTPException) as e:
                _periksa(kind)
            assert e.value.status_code == 409 and e.value.detail["code"] == kode, (kind, st)
            assert "X-1" in e.value.detail["message"]


def test_periksa_penawaran_ikut_penentunya_dan_404_berkode(dok):
    dok["v"] = {"nomor": "QUO-1", "status": "draft"}
    assert _periksa("quotation")["status"] == "draft"  # draf penawaran BOLEH (ditandai terkirim oleh jalur share)
    dok["exc"] = HTTPException(status_code=404, detail="Dokumen tidak ditemukan")
    with pytest.raises(HTTPException) as e:
        _periksa("invoice")
    assert e.value.status_code == 404 and e.value.detail["code"] == "DOKUMEN_TAK_ADA"
    dok["exc"] = HTTPException(status_code=409, detail={"code": "DOKUMEN_DRAF", "message": "Dokumen draf belum bisa dibagikan."})
    with pytest.raises(HTTPException) as e2:
        _periksa("proforma")
    assert e2.value.detail["code"] == "DOKUMEN_DRAF"  # galat 409 jalur tunggal diteruskan apa adanya


# ---------------- bagikan: fungsi item ----------------
def test_tulis_memisah_rahasia_url_dan_memakai_jalur_bagikan_yang_sama(dok, monkeypatch):
    panggil = []

    async def bagikan(conn, request, ctx, kind, uid, channel):
        panggil.append((kind, uid, channel))
        return ({"id": "S1", "url": "https://x/d/TOKEN", "token": "TOKEN", "sent_at": "t1", "expires_at": "t2"},
                {"number": "X-1", "status_now": "posted", "akan_ditandai_terkirim": False})
    monkeypatch.setattr(D, "_bagikan", bagikan)
    tulis, pratinjau = BD._fungsi_bagikan(object(), "invoice", "wa")
    i = _id()
    r = _jalan(tulis(_K(), CTX, i))
    assert r["share_id"] == "S1" and r["_rahasia"] == {"url": "https://x/d/TOKEN"} and "token" not in r and "url" not in r
    assert panggil == [("invoice", i, "wa")]
    blok, ring = _jalan(pratinjau(_K(), CTX, i))
    assert blok == [] and ring == {"number": "X-1", "status_now": "posted", "channel": "wa"} and "url" not in str(ring)  # pratinjau tanpa url
    rq = _jalan(BD._fungsi_bagikan(object(), "quotation", "link")[0](_K(), CTX, i))
    assert rq["marked_sent"] is False


def test_infra_rahasia_tak_disimpan_di_tabel_idempotensi(monkeypatch):
    from app.services import idem_buat as IB
    tersimpan = []

    async def mulai(conn, ctx, kunci, prefix, doc, isi, response=None): return "kp", "sd", None

    async def simpan(conn, ctx, kp, sd, src, resp, rid):
        tersimpan.append(dict(resp))
        return dict(resp)
    monkeypatch.setattr(IB, "mulai_aksi", mulai)
    monkeypatch.setattr(IB, "simpan", simpan)

    class Tx:
        async def __aenter__(s): return None
        async def __aexit__(s, *e): return False

    class Kn:
        def transaction(s): return Tx()

        async def execute(s, *a): pass

    class P:
        def acquire(s):
            class A:
                async def __aenter__(a): return Kn()
                async def __aexit__(a, *e): return False
            return A()

    async def fn(conn, ctx, i): return {"share_id": "S1", "_rahasia": {"url": "https://x/d/TOKEN"}}
    out = _jalan(B.jalankan_per_item(P(), CTX, "share", "quotes", [_id()], fn, kunci_batch="K"))
    assert out["items"][0]["result"] == {"share_id": "S1", "url": "https://x/d/TOKEN"}  # pemanggil MENERIMA url
    assert tersimpan == [{"share_id": "S1"}] and "TOKEN" not in str(tersimpan)  # tabel idempotensi TIDAK menyimpannya


# ---------------- rute ----------------
def test_rute_per_modul_dan_daftar_putih():
    assert set(BD.ROUTERS) == set(SPEC) == set(BD.MODUL_KIND) and len(BD.ROUTERS) == 8
    for modul, r in BD.ROUTERS.items():
        jalur = sorted((x.path, tuple(sorted(x.methods))) for x in r.routes)
        assert jalur == [("/bulk/pdf", ("POST",)), ("/bulk/share", ("POST",)), ("/bulk/share/preview", ("POST",))], modul
    assert {"pdf", "share"} <= B.AKSI_DIIZINKAN
    assert BD.MODUL_KIND["customer-deposits"] == BD.MODUL_KIND["receive-payments"] == "receipt" and BD.MODUL_KIND["deliveries"] == "delivery"


class _Req:
    def __init__(self, kunci=None, user=True):
        self.headers = {"X-Idempotency-Key": kunci} if kunci else {}
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": str(U)} if user else None})()


def _ep(modul, nama):
    return next(r.endpoint for r in BD.ROUTERS[modul].routes if r.endpoint.__name__ == nama)


def test_bagikan_wajib_kunci_batas_50_login_dan_kanal():
    bodi = BD.BulkShareRequest(ids=[str(_id())])
    with pytest.raises(HTTPException) as e:
        _jalan(_ep("quotes", "bulk_share")(_Req(), bodi))
    assert e.value.detail["code"] == "BULK_KUNCI_WAJIB"
    with pytest.raises(HTTPException) as e2:
        _jalan(_ep("quotes", "bulk_share")(_Req("K"), BD.BulkShareRequest(ids=[str(_id()) for _ in range(51)])))
    assert e2.value.detail["code"] == "BULK_TERLALU_BANYAK" and e2.value.detail["batas"] == 50
    with pytest.raises(HTTPException) as e3:
        _jalan(_ep("quotes", "bulk_share")(_Req("K", user=False), bodi))
    assert e3.value.status_code == 401
    with pytest.raises(Exception):
        BD.BulkShareRequest(ids=[str(_id())], channel="sms")  # kanal di luar link|wa|email ditolak skema


# ---------------- PDF handler ----------------
class _Tx:
    async def __aenter__(self): return None
    async def __aexit__(self, *e): return False


class _Kn:
    def __init__(self): self.exec = []
    def transaction(self): return _Tx()
    async def execute(self, sql, *a): self.exec.append((" ".join(sql.split()), a))


class _Pool:
    def __init__(self): self.k = _Kn()

    def acquire(self):
        pool = self

        class A:
            async def __aenter__(s): return pool.k
            async def __aexit__(s, *e): return False
        return A()


@pytest.fixture
def pdf_env(monkeypatch):
    pool = _Pool()

    async def gp(): return pool

    async def hari(conn, tid): return date(2026, 10, 5)
    monkeypatch.setattr(BD, "get_pool", gp)
    monkeypatch.setattr(BD, "tanggal_dokumen", hari)
    st = {"gagal": set(), "waktu": None}

    async def render(conn, ctx, request, kind, did):
        if did in st["gagal"]:
            raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan")
        return ("R-" + did, "NO-" + did[:4])

    async def tercache(tid, r): return b"%PDF-" + r.encode()
    monkeypatch.setattr(D, "_render", render)
    monkeypatch.setattr(D, "pdf_tercache", tercache)
    monkeypatch.setattr(D, "_ctx", lambda request: CTX)
    return pool, st


def test_pdf_zip_header_audit_dan_melewati_yang_gagal(pdf_env):
    pool, st = pdf_env
    a, b, c = _id(), _id(), _id()
    st["gagal"].add(str(b))
    resp = _jalan(_ep("sales-invoices", "bulk_pdf")(_Req(), BD.BulkIdsRequest(ids=[str(a), str(b), str(c)])))
    assert resp.media_type == "application/zip" and resp.headers["X-Bulk-Total"] == "3" and resp.headers["X-Bulk-Ditemukan"] == "2" \
        and resp.headers["X-Bulk-Dilewati"] == "1"
    assert resp.headers["Content-Disposition"] == 'attachment; filename="faktur-2026-10-05.zip"' and resp.headers["Cache-Control"] == "no-store"
    z = zipfile.ZipFile(io.BytesIO(resp.body))
    pdfs = [n for n in z.namelist() if n.endswith(".pdf")]
    assert len(pdfs) == 2 and "DILEWATI" in z.read("_RINGKASAN.txt").decode() and str(b) in z.read("_RINGKASAN.txt").decode()
    assert z.read(pdfs[0]).startswith(b"%PDF-")
    assert any("INSERT INTO audit_logs" in s and a_[0] == "BULK_PDF" for s, a_ in pool.k.exec)


def test_pdf_batas_25_404_bila_tak_satu_pun_dan_waktu_habis(pdf_env, monkeypatch):
    pool, st = pdf_env
    with pytest.raises(HTTPException) as e:
        _jalan(_ep("quotes", "bulk_pdf")(_Req(), BD.BulkIdsRequest(ids=[str(_id()) for _ in range(26)])))
    assert e.value.detail["code"] == "BULK_TERLALU_BANYAK" and e.value.detail["batas"] == 25
    x = _id()
    st["gagal"].add(str(x))
    with pytest.raises(HTTPException) as e2:
        _jalan(_ep("quotes", "bulk_pdf")(_Req(), BD.BulkIdsRequest(ids=[str(x)])))
    assert e2.value.status_code == 404 and e2.value.detail["code"] == "BULK_TAK_ADA" and not pool.k.exec  # tanpa audit bila kosong
    # waktu habis: dokumen sesudah batas waktu dilewati (tercatat), bukan menggantung
    t = {"n": 0}

    def jam():
        t["n"] += 1
        return 0.0 if t["n"] <= 2 else 100.0  # pemeriksaan 1 lolos, sesudahnya melampaui BATAS_WAKTU_PDF
    import types
    monkeypatch.setattr(BD, "time", types.SimpleNamespace(monotonic=jam))  # BUKAN time.monotonic global (dipakai event loop)
    ids = [_id(), _id(), _id()]
    resp = _jalan(_ep("quotes", "bulk_pdf")(_Req(), BD.BulkIdsRequest(ids=[str(i) for i in ids])))
    assert resp.headers["X-Bulk-Ditemukan"] == "1" and resp.headers["X-Bulk-Dilewati"] == "2"
    assert "waktu habis" in zipfile.ZipFile(io.BytesIO(resp.body)).read("_RINGKASAN.txt").decode()


# ---------------- izin ----------------
def _izin(metode, jalur):
    return PM.PermissionMiddleware(lambda *a: None, False)._find_permission(jalur, metode)


@pytest.mark.parametrize("prefiks,modul", [("sales-orders", "sales_order"), ("quotes", "quote"), ("proformas", "proforma"),
                                           ("sales-invoices", "sales_invoice"), ("customer-deposits", "customer_deposit"),
                                           ("receive-payments", "receive_payment"), ("deliveries", "sales_invoice"),
                                           ("credit-notes", "credit_note")])
def test_izin_pdf_R_dan_share_E(prefiks, modul):
    assert _izin("POST", f"/api/{prefiks}/bulk/pdf") == (modul, "R")
    assert _izin("POST", f"/api/{prefiks}/bulk/share") == (modul, "E") == _izin("POST", f"/api/{prefiks}/bulk/share/preview")


def test_izin_sama_dengan_aksi_tunggal_di_documents():
    # pdf massal = R seperti GET /documents/{kind}/{id}/pdf; share massal = E seperti POST /documents/{kind}/{id}/share
    pasangan = {"sales-orders": "rekap", "quotes": "quotation", "proformas": "proforma", "receive-payments": "receipt",
                "deliveries": "delivery", "sales-invoices": "invoice", "credit-notes": "credit_note"}
    for prefiks, kind in pasangan.items():
        assert _izin("POST", f"/api/{prefiks}/bulk/pdf") == _izin("GET", f"/api/documents/{kind}/X/pdf")
        assert _izin("POST", f"/api/{prefiks}/bulk/share") == _izin("POST", f"/api/documents/{kind}/X/share")
