"""Unit L2 -- satu sumber aturan lampiran untuk SEMUA pengunggah, kuota uang
muka, dan hub /api/documents berpagar entitas + izin per-doctype.

Latar (24 Sep 2026):
  * storage.upload_file punya whitelist SENDIRI (6 tipe). Rute uang muka/SI
    meloloskan tipe resmi lain (enforce_attachment_limits) lalu ValueError di
    storage -> 500 "Failed to upload attachment". Form DP = data NYATA.
  * storage.upload_file memasang metadata original_filename -> nama berkas
    non-ASCII gagal PutObject (#17).
  * hub attach: entity_id tak diverifikasi; izin hanya "anggota aktif".
  * hub download: tipe tersimpan + inline + nama mentah di header.

Rute diuji dengan StorageService.upload_file ASLI (klien boto palsu), jadi
satu sumber validasinya teruji dari rute sampai storage.
"""
import io
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from app import attachment_limits as AL
from app.routers import bills as BL
from app.routers import customer_deposits as CD
from app.routers import documents as DOC
from app.routers import sales_invoices as SI
from app.services import storage_service as SS
from app.utils import lampiran_unduh as LU

TENANT = "kaos-biru-konveksi"
USER = "22222222-2222-2222-2222-222222222222"
INDUK = uuid.UUID("11111111-1111-1111-1111-111111111111")
DOKID = uuid.UUID("33333333-3333-3333-3333-333333333333")
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
GIF = b"GIF89a" + b"x" * 20
DOCX = b"PK\x03\x04" + b"x" * 20
SVG = b"<svg><script>alert(1)</script></svg>"


# ------------------------------------------------------------------ storage asli
class FakeS3:
    def __init__(self):
        self.put = []

    def head_bucket(self, **kw):
        return {}

    def put_object(self, **kw):
        # botocore menolak metadata non-ASCII (validate_ascii_metadata); tiru.
        for k, v in (kw.get("Metadata") or {}).items():
            v.encode("ascii")
        self.put.append(kw)
        return {}


def storage_asli():
    svc = SS.StorageService(config=SimpleNamespace(bucket="b"))
    svc._client = FakeS3()

    async def _url(*a, **k):
        return "http://presign-tak-dipakai"

    svc.generate_signed_url = _url
    return svc


def berkas(nama, isi, tipe):
    return UploadFile(file=io.BytesIO(isi), filename=nama, headers=Headers({"content-type": tipe}))


# ------------------------------------------------------------------ 1. storage.upload_file
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nama,isi,ctype,harap",
    [
        ("anim.gif", GIF, "image/gif", "image/gif"),
        ("surat.docx", DOCX, "application/octet-stream",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("data.csv", b"a,b\n1,2", "application/vnd.ms-excel", "text/csv"),
        ("foto.heic", b"ftypheic", "", "image/heic"),
        ("nota Bapak Ö ñ.png", PNG, "image/png", "image/png"),  # #17 non-ASCII
    ],
)
async def test_storage_upload_file_tipe_resmi_penuh(nama, isi, ctype, harap):
    svc = storage_asli()
    r = await svc.upload_file(berkas(nama, isi, ctype), TENANT, "deposit-attachments")
    assert r.content_type == harap
    put = svc._client.put[0]
    assert put["ContentType"] == harap
    assert "original_filename" not in put["Metadata"]
    assert all(v.isascii() for v in put["Metadata"].values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nama,isi,ctype,kode",
    [
        ("x.svg", SVG, "image/svg+xml", "tipe_ditolak"),
        ("x.zip", b"PK", "application/zip", "tipe_ditolak"),
        ("html.png", b"<html>", "image/png", "isi_tak_cocok"),
        ("besar.pdf", b"%PDF-" + b"x" * (10 * 1024 * 1024), "application/pdf", "terlalu_besar"),
    ],
)
async def test_storage_upload_file_menolak_dengan_kode(nama, isi, ctype, kode):
    svc = storage_asli()
    with pytest.raises(AL.LampiranDitolak) as ei:
        await svc.upload_file(berkas(nama, isi, ctype), TENANT, "x")
    assert ei.value.kode == kode
    assert isinstance(ei.value, ValueError)
    assert svc._client.put == []


# ------------------------------------------------------------------ 2. rute uang muka
class Conn:
    """DB palsu untuk rute DP/SI/bills: mencatat urutan txn/lock/hitung."""

    def __init__(self, terpakai=0):
        self.terpakai = terpakai
        self.ev = []
        self.kedalaman = 0

    def transaction(self):
        conn = self

        class T:
            async def __aenter__(self_):
                conn.kedalaman += 1
                conn.ev.append(("txn", conn.kedalaman))

            async def __aexit__(self_, *a):
                conn.kedalaman -= 1
                return False

        return T()

    async def execute(self, sql, *args):
        if "pg_advisory_xact_lock" in sql:
            self.ev.append(("lock", args[0], self.kedalaman))
        elif "INSERT INTO documents" in sql:
            self.ev.append(("dok", args))
        elif "INSERT INTO document_attachments" in sql:
            self.ev.append(("taut", args))
        else:
            self.ev.append(("exec", sql[:40]))

    async def fetchrow(self, sql, *args):
        return {"id": INDUK}

    async def fetchval(self, sql, *args):
        if sql == AL.SQL_HITUNG_LAMPIRAN_TERSEDIA:
            self.ev.append(("hitung", self.kedalaman, args))
            return self.terpakai
        raise AssertionError(f"fetchval tak dikenal: {sql}")


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def pasang(monkeypatch, mod, conn, svc):
    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq(conn))

    monkeypatch.setattr(mod, "get_pool", _pool)
    monkeypatch.setattr(mod, "get_storage_service", lambda: svc)


def req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": USER}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nama,isi,ctype,harap",
    [
        ("anim.gif", GIF, "image/gif", "image/gif"),
        ("surat.docx", DOCX, "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("rekap.xlsx", DOCX, "application/octet-stream",
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("data.csv", b"a,b", "text/csv", "text/csv"),
        ("catatan.txt", b"halo", "text/plain", "text/plain"),
        ("foto.heic", b"ftypheic", "application/octet-stream", "image/heic"),
    ],
)
async def test_dp_menerima_daftar_tipe_resmi_penuh(monkeypatch, nama, isi, ctype, harap):
    """Dulu: lolos enforce, ditolak storage -> 500. Kini 201 + tipe kanonik."""
    conn, svc = Conn(), storage_asli()
    pasang(monkeypatch, CD, conn, svc)
    out = await CD.upload_deposit_attachment(req(), INDUK, berkas(nama, isi, ctype))
    assert out["data"]["mime_type"] == harap
    dok = [e for e in conn.ev if e[0] == "dok"][0][1]
    assert dok[3] == harap  # documents.file_type kanonik
    assert svc._client.put and svc._client.put[0]["ContentType"] == harap
    assert out["kuota"] == {"terpakai": 1, "maks": 10}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nama,isi,ctype",
    [("x.svg", SVG, "image/svg+xml"), ("x.png", b"<html>", "image/png"), ("x.exe", b"MZ", "application/octet-stream")],
)
async def test_dp_menolak_400_jujur_bukan_500(monkeypatch, nama, isi, ctype):
    conn, svc = Conn(), storage_asli()
    pasang(monkeypatch, CD, conn, svc)
    with pytest.raises(HTTPException) as ei:
        await CD.upload_deposit_attachment(req(), INDUK, berkas(nama, isi, ctype))
    assert ei.value.status_code == 400
    assert ei.value.detail in AL.PESAN_GALAT_LAMPIRAN.values()
    assert svc._client.put == []


@pytest.mark.asyncio
async def test_dp_kuota_penuh_400_tanpa_menyimpan(monkeypatch):
    conn, svc = Conn(terpakai=10), storage_asli()
    pasang(monkeypatch, CD, conn, svc)
    with pytest.raises(HTTPException) as ei:
        await CD.upload_deposit_attachment(req(), INDUK, berkas("a.png", PNG, "image/png"))
    assert ei.value.status_code == 400
    assert ei.value.detail == AL.PESAN_GALAT_LAMPIRAN["kuota_penuh"]
    assert svc._client.put == []
    assert not [e for e in conn.ev if e[0] in ("dok", "taut")]


@pytest.mark.asyncio
async def test_dp_kuota_sembilan_masih_boleh(monkeypatch):
    conn, svc = Conn(terpakai=9), storage_asli()
    pasang(monkeypatch, CD, conn, svc)
    out = await CD.upload_deposit_attachment(req(), INDUK, berkas("a.png", PNG, "image/png"))
    assert out["kuota"] == {"terpakai": 10, "maks": 10}


@pytest.mark.asyncio
async def test_dp_kuota_dihitung_di_dalam_txn_sesudah_lock(monkeypatch):
    conn, svc = Conn(), storage_asli()
    pasang(monkeypatch, CD, conn, svc)
    await CD.upload_deposit_attachment(req(), INDUK, berkas("a.png", PNG, "image/png"))
    jenis = [e[0] for e in conn.ev]
    i_lock, i_hitung, i_dok = jenis.index("lock"), jenis.index("hitung"), jenis.index("dok")
    assert i_lock < i_hitung < i_dok
    assert conn.ev[i_lock][1] == AL.kunci_kuota_lampiran(TENANT, "customer_deposit", INDUK)
    assert conn.ev[i_lock][2] >= 1 and conn.ev[i_hitung][1] >= 1  # di dalam txn


# ------------------------------------------------------------------ 3. SI & bills
@pytest.mark.asyncio
@pytest.mark.parametrize("mod,fungsi", [(SI, "upload_invoice_attachment"), (BL, "upload_attachment")])
async def test_si_bills_tipe_resmi_dan_400_jujur(monkeypatch, mod, fungsi):
    for nama, isi, ctype, ok in [
        ("anim.gif", GIF, "image/gif", True),
        ("surat.docx", DOCX, "application/octet-stream", True),
        ("x.svg", SVG, "image/svg+xml", False),
    ]:
        conn, svc = Conn(), storage_asli()
        pasang(monkeypatch, mod, conn, svc)
        f = getattr(mod, fungsi)
        if ok:
            out = await f(req(), INDUK, berkas(nama, isi, ctype))
            assert out["data"]["mime_type"] == AL.EKSTENSI_LAMPIRAN["." + nama.rsplit(".", 1)[1]]
        else:
            with pytest.raises(HTTPException) as ei:
                await f(req(), INDUK, berkas(nama, isi, ctype))
            assert ei.value.status_code == 400


# ------------------------------------------------------------------ 4. hub attach/detach
class HubConn(Conn):
    def __init__(self, entitas_ada=None, terpakai=0):
        super().__init__(terpakai)
        self.entitas_ada = entitas_ada or {}  # tabel -> bool

    async def fetchval(self, sql, *args):
        if sql.startswith("SELECT 1 FROM "):
            tabel = sql.split()[3]
            self.ev.append(("entitas", tabel, args))
            return 1 if self.entitas_ada.get(tabel) else None
        if "FROM documents WHERE id" in sql:
            return DOKID
        if "FROM document_attachments" in sql and "document_id = $1" in sql:
            return None
        return await super().fetchval(sql, *args)

    async def fetchrow(self, sql, *args):
        if "INSERT INTO document_attachments" in sql:
            self.ev.append(("taut", args))
            return {
                "id": uuid.uuid4(), "tenant_id": args[0], "document_id": args[1],
                "entity_type": args[2], "entity_id": args[3], "attachment_type": args[4],
                "display_order": args[5], "attached_at": datetime.now(timezone.utc), "attached_by": None,
            }
        raise AssertionError(sql)


class Eng:
    def __init__(self, peran, izin=()):
        self.peran, self.izin = peran, set(izin)

    async def get_user_context(self, uid, tid, role):
        return SimpleNamespace(business_role_code=self.peran, membership_active=True)

    async def can(self, c, aksi, modul):
        return (modul, aksi) in self.izin


def pasang_hub(monkeypatch, conn, eng):
    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq(conn))

    monkeypatch.setattr(DOC, "get_pool", _pool)
    from app.services import policy_engine_client as pec

    monkeypatch.setattr(pec, "get_policy_engine", lambda: eng)


def body(et="quote", eid=INDUK):
    return DOC.AttachDocumentRequest(entity_type=et, entity_id=eid)


@pytest.mark.asyncio
async def test_hub_attach_entitas_tak_ada_atau_tenant_lain_404(monkeypatch):
    conn = HubConn(entitas_ada={})
    pasang_hub(monkeypatch, conn, Eng("OWNER"))
    with pytest.raises(HTTPException) as ei:
        await DOC.attach_document(req(), DOKID, body("quote"))
    assert ei.value.status_code == 404
    e = [x for x in conn.ev if x[0] == "entitas"][0]
    assert e[1] == "quotes" and e[2] == (INDUK, TENANT)  # predikat tenant eksplisit
    assert not [x for x in conn.ev if x[0] == "taut"]


@pytest.mark.asyncio
@pytest.mark.parametrize("et", ["other", "project", "contract", "delivery"])
async def test_hub_attach_jenis_tak_didukung_422(monkeypatch, et):
    pasang_hub(monkeypatch, HubConn(), Eng("OWNER"))
    with pytest.raises(HTTPException) as ei:
        await DOC.attach_document(req(), DOKID, body(et))
    assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_hub_attach_payment_ke_pembayaran_keluar(monkeypatch):
    conn = HubConn(entitas_ada={"bill_payments_v2": True})
    pasang_hub(monkeypatch, conn, Eng("STAFF", {("send_payment", "U")}))
    out = await DOC.attach_document(req(), DOKID, body("payment"))
    assert out.attachment.entity_type == "payment"
    assert [x[1] for x in conn.ev if x[0] == "entitas"] == ["receive_payments", "bill_payments_v2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "peran,izin,lolos",
    [
        ("OWNER", set(), True),
        ("STAFF", {("quote", "U")}, True),
        ("STAFF", {("quote", "C")}, True),       # baru membuat quote lalu melampirkan
        ("STAFF", {("quote", "R")}, False),
        ("STAFF", {("receive_payment", "U")}, False),  # izin modul LAIN tak berlaku
    ],
)
async def test_hub_attach_izin_per_doctype(monkeypatch, peran, izin, lolos):
    conn = HubConn(entitas_ada={"quotes": True})
    pasang_hub(monkeypatch, conn, Eng(peran, izin))
    if lolos:
        out = await DOC.attach_document(req(), DOKID, body("quote"))
        assert out.attachment.entity_id == INDUK
    else:
        with pytest.raises(HTTPException) as ei:
            await DOC.attach_document(req(), DOKID, body("quote"))
        assert ei.value.status_code == 403
        assert not [x for x in conn.ev if x[0] == "taut"]


@pytest.mark.asyncio
async def test_hub_attach_kuota_penuh_dan_urutan_lock(monkeypatch):
    conn = HubConn(entitas_ada={"quotes": True}, terpakai=10)
    pasang_hub(monkeypatch, conn, Eng("OWNER"))
    with pytest.raises(HTTPException) as ei:
        await DOC.attach_document(req(), DOKID, body("quote"))
    assert ei.value.detail == AL.PESAN_GALAT_LAMPIRAN["kuota_penuh"]
    jenis = [e[0] for e in conn.ev]
    assert jenis.index("lock") < jenis.index("hitung")
    assert conn.ev[jenis.index("hitung")][1] >= 1


@pytest.mark.asyncio
async def test_hub_detach_wajib_izin(monkeypatch):
    conn = HubConn(entitas_ada={"quotes": True})
    pasang_hub(monkeypatch, conn, Eng("STAFF", {("quote", "R")}))
    with pytest.raises(HTTPException) as ei:
        await DOC.detach_document(req(), DOKID, DOC.DetachDocumentRequest(entity_type="quote", entity_id=INDUK))
    assert ei.value.status_code == 403


def test_peta_hub_memakai_izin_rute_entitas():
    assert DOC._izin_modul("quotes", INDUK) == ("quote", "U")
    assert DOC._izin_modul("receive-payments", INDUK) == ("receive_payment", "U")
    assert DOC._izin_modul("bill-payments", INDUK) == ("send_payment", "U")


# ------------------------------------------------------------------ 5. hub download
def test_hub_download_sajian_aman(monkeypatch):
    import asyncio

    class Body:
        def read(self, n=-1):
            return b""

        def close(self):
            pass

    class C2:
        async def execute(self, *a):
            pass

        async def fetchrow(self, sql, *a):
            return {"file_name": 'x"\r\nSet-Cookie: a=b.svg', "file_path": "k", "file_type": "image/svg+xml",
                    "storage_type": "s3"}  # #25: kueri unduh hub memilih storage_type

    async def _pool():
        return SimpleNamespace(acquire=lambda: _Acq(C2()))

    monkeypatch.setattr(DOC, "get_pool", _pool)
    monkeypatch.setattr(DOC, "get_storage_service", lambda: SimpleNamespace(
        client=SimpleNamespace(get_object=lambda **k: {"Body": Body()}), config=SimpleNamespace(bucket="b")))
    r = asyncio.run(DOC.download_document(req(), DOKID))
    assert r.media_type == "application/octet-stream"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment; ") and "\r" not in cd and "\n" not in cd
    assert r.headers["x-content-type-options"] == "nosniff"
