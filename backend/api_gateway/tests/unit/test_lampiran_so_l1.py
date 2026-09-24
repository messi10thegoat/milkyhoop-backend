"""Unit L1 -- lampiran Pesanan Penjualan: 10 berkas x 10 MB, unggah banyak.

Kontrak yang dijaga (lihat app/routers/sales_order_lampiran.py):
  * sukses SEBAGIAN: satu berkas gagal (tipe/ukuran/kuota/galat DB) tidak
    membatalkan berkas lain;
  * kuota dihitung DI DALAM transaksi yang memegang advisory lock per SO ->
    dua unggahan PARALEL tak bisa bersama-sama melewati 10 (diuji dengan dua
    coroutine sungguhan di atas DB palsu yang meniru advisory lock);
  * url lampiran = path RELATIF gateway .../{aid}/download;
  * SO tenant lain / tak ada -> 404 tanpa objek tersimpan;
  * izin sales_order: POST=U, GET=R, download=R, DELETE=D;
  * sajian unduhan: svg/heic/html -> application/octet-stream + attachment +
    nosniff (blob: URL mewarisi origin aplikasi).

DB palsu menjawab tiap SQL sesuai SEMANTIKNYA terhadap keadaan bersama, dan
mencatat urutan peristiwa (txn/savepoint/lock/hitung) untuk tes urutan.
"""
import asyncio
import hashlib
import io
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app import attachment_limits as AL
from app.middleware import permission_middleware as pm
from app.routers import sales_order_lampiran as SL
from app.utils import chat_file_path as CFP
from app.utils import lampiran_unduh as LU

TENANT = "kaos-biru-konveksi"
USER_ID = "22222222-2222-2222-2222-222222222222"
SO = uuid.UUID("11111111-1111-1111-1111-111111111111")
WAKTU = datetime(2026, 9, 24, tzinfo=timezone.utc)
MB = 1024 * 1024


# ------------------------------------------------------------------ DB palsu
class Keadaan:
    """Keadaan BERSAMA antar-koneksi (dua unggahan paralel melihat yang sama)."""

    def __init__(self, so_ada=True, tautan=None):
        self.so_ada = so_ada
        # tautan: list of dict(document_id, storage_type, deleted_at)
        self.tautan = list(tautan or [])
        self.dokumen = {}  # (sha, kunci) -> id
        self.kunci_lock = {}  # nama -> asyncio.Lock
        self.gagal_taut_untuk = set()  # sha yang INSERT tautannya meledak

    def lock(self, nama):
        return self.kunci_lock.setdefault(nama, asyncio.Lock())


class Txn:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.kedalaman += 1
        self.conn.peristiwa.append(("txn_masuk", self.conn.kedalaman))
        return self

    async def __aexit__(self, et, ev, tb):
        self.conn.peristiwa.append(("txn_keluar", self.conn.kedalaman, et is not None))
        if self.conn.kedalaman == 1:
            # akhir transaksi terluar -> lepas semua advisory xact lock
            for lk in self.conn.dipegang:
                lk.release()
            self.conn.dipegang = []
        self.conn.kedalaman -= 1
        return False


class Conn:
    def __init__(self, k: Keadaan):
        self.k = k
        self.kedalaman = 0
        self.dipegang = []
        self.peristiwa = []

    def transaction(self):
        return Txn(self)

    async def execute(self, sql, *args):
        if "pg_advisory_xact_lock" in sql:
            assert self.kedalaman >= 1, "advisory xact lock di luar transaksi = NO-OP"
            lk = self.k.lock(args[0])
            # Postgres: advisory lock REENTRANT dalam satu sesi (dua berkas
            # identik di satu permintaan mengambil kunci berkas yang sama).
            if lk not in self.dipegang:
                await lk.acquire()
                self.dipegang.append(lk)
            self.peristiwa.append(("lock", args[0], self.kedalaman))
            return
        if "set_config" in sql:
            return
        if "INSERT INTO document_attachments" in sql:
            tenant, doc_id, so_id, urutan, user = args
            sha = next(s for (s, _), d in self.k.dokumen.items() if d == doc_id)
            if sha in self.k.gagal_taut_untuk:
                raise RuntimeError("galat DB buatan")
            await asyncio.sleep(0)
            self.k.tautan.append(
                {"document_id": doc_id, "storage_type": "s3", "deleted_at": None, "urutan": urutan}
            )
            self.peristiwa.append(("taut", doc_id))
            return
        raise AssertionError(f"execute tak dikenal: {sql}")

    async def fetchval(self, sql, *args):
        if sql == SL._SQL_SO:
            return SO if self.k.so_ada and args[0] == SO and args[1] == TENANT else None
        if sql == AL.SQL_HITUNG_LAMPIRAN_TERSEDIA:
            self.peristiwa.append(("hitung", self.kedalaman, bool(self.dipegang)))
            await asyncio.sleep(0)  # titik sela: tanpa lock, pesaing menyusup di sini
            return sum(1 for t in self.k.tautan if AL.lampiran_tersedia(t["storage_type"], t["deleted_at"]))
        if sql == SL._SQL_URUTAN:
            return len(self.k.tautan)
        if sql == SL._SQL_DOK_SAMA:
            return self.k.dokumen.get((args[1], args[2]))
        if sql == SL._SQL_DOK_BARU:
            doc_id = uuid.uuid4()
            self.k.dokumen[(args[6], args[5])] = doc_id
            return doc_id
        if sql == SL._SQL_SUDAH_TERTAUT:
            return 1 if any(t["document_id"] == args[0] for t in self.k.tautan) else None
        if sql == SL._SQL_LEPAS:
            for t in list(self.k.tautan):
                if t["document_id"] == args[0] and self.k.so_ada:
                    self.k.tautan.remove(t)
                    return uuid.uuid4()
            return None
        raise AssertionError(f"fetchval tak dikenal: {sql}")

    async def fetch(self, sql, *args):
        if sql == SL._SQL_DAFTAR:
            return [
                {
                    "id": t["document_id"],
                    "file_name": t.get("nama", "x.jpg"),
                    "file_size": 10,
                    "file_type": "image/jpeg",
                    "storage_type": t["storage_type"],
                    "deleted_at": t["deleted_at"],
                    "uploaded_at": WAKTU,
                }
                for t in self.k.tautan
                if t["deleted_at"] is None
            ]
        raise AssertionError(f"fetch tak dikenal: {sql}")

    async def fetchrow(self, sql, *args):
        raise AssertionError(f"fetchrow tak dikenal: {sql}")


class _Acq:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


def pasang(monkeypatch, k: Keadaan):
    """Tiap acquire() = koneksi BARU atas keadaan bersama (seperti pool)."""
    conns = []

    def acquire():
        c = Conn(k)
        conns.append(c)
        return _Acq(c)

    async def _pool():
        return SimpleNamespace(acquire=acquire)

    tersimpan = []

    async def _simpan(storage, kunci, isi):
        await asyncio.sleep(0)
        tersimpan.append(kunci)

    monkeypatch.setattr(SL, "get_pool", _pool)
    monkeypatch.setattr(SL, "simpan_objek_unggahan", _simpan)
    monkeypatch.setattr(SL, "get_storage_service", lambda: object())
    return conns, tersimpan


def req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": USER_ID}))


class Berkas(UploadFile):
    dibaca = 0

    async def read(self, *a, **kw):
        Berkas.dibaca += 1
        return await super().read(*a, **kw)


def berkas(nama, isi: bytes, tipe="image/jpeg"):
    return Berkas(file=io.BytesIO(isi), filename=nama, headers=Headers({"content-type": tipe}))


JPG = b"\xff\xd8\xff\xe0"  # tanda tangan JPEG sungguhan
PNG = b"\x89PNG\r\n\x1a\n"


def unik(n, awal=0, tipe="image/jpeg"):
    return [berkas(f"f{i}.jpg", JPG + f"isi-{i}-{uuid.uuid4()}".encode(), tipe) for i in range(awal, awal + n)]


def s3(n):
    return [{"document_id": uuid.uuid4(), "storage_type": "s3", "deleted_at": None} for _ in range(n)]


def local(n):
    return [{"document_id": uuid.uuid4(), "storage_type": "local", "deleted_at": None} for _ in range(n)]


# ------------------------------------------------------------------ helper bersama
def test_konstanta_bersama():
    assert AL.MAKS_LAMPIRAN_PER_DOKUMEN == 10
    assert AL.ATTACHMENT_MAX_BYTES == 10 * MB


def test_ekstensi_resmi_kuncinya_sah_dan_tipenya_resmi():
    sha = "a" * 64
    for ext, tipe in AL.EKSTENSI_LAMPIRAN.items():
        assert tipe in AL.ATTACHMENT_ALLOWED_TYPES, ext
        kunci = CFP.kunci_unggahan(TENANT, "lampiran", sha, ext)
        assert CFP.kunci_sah_milik_tenant(TENANT, kunci), ext
        assert not CFP.kunci_sah_milik_tenant("tenant-lain", kunci)
    # tiap tipe resmi (kecuali alias) punya ekstensi
    assert set(AL.EKSTENSI_LAMPIRAN.values()) == AL.ATTACHMENT_ALLOWED_TYPES
    assert "image/jpg" not in AL.ATTACHMENT_ALLOWED_TYPES  # alias: hanya rute SO
    for dibuang in (".svg", ".zip", ".rar", ".html", ".exe"):
        assert dibuang not in AL.EKSTENSI_LAMPIRAN


T = AL.tentukan_tipe_lampiran


@pytest.mark.parametrize(
    "nama,ctype,isi,harap",
    [
        ("nota.JPG", "image/jpeg", JPG + b"x", ("image/jpeg", ".jpg", None)),
        ("nota.jpeg", "image/jpg", JPG + b"x", ("image/jpeg", ".jpg", None)),
        ("foto.heic", "", b"ftypheic", ("image/heic", ".heic", None)),
        ("foto.HEIF", "application/octet-stream", b"ftyp", ("image/heif", ".heif", None)),
        ("data.csv", "application/octet-stream", b"a,b\n1,2", ("text/csv", ".csv", None)),
        ("data.csv", "application/vnd.ms-excel", b"a,b", ("text/csv", ".csv", None)),
        ("surat.docx", "application/octet-stream", b"PK\x03\x04", (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx", None)),
        ("faktur.pdf", "application/pdf; charset=binary", b"%PDF-1.7", ("application/pdf", ".pdf", None)),
        # ditolak
        ("gambar.svg", "image/svg+xml", b"<svg", (None, None, "tipe_ditolak")),
        ("arsip.zip", "application/zip", b"PK", (None, None, "tipe_ditolak")),
        ("tanpa-ekstensi", "image/jpeg", JPG, (None, None, "tipe_ditolak")),
        ("nota.jpg", "text/html", JPG, (None, None, "tipe_ditolak")),
        ("kosong.png", "image/png", b"", (None, None, "kosong")),
        ("besar.pdf", "application/pdf", b"%PDF-" + b"x" * (10 * MB), (None, None, "terlalu_besar")),
        ("png-menyamar.jpg", "image/jpeg", PNG + b"x", (None, None, "isi_tak_cocok")),
        ("html-menyamar.pdf", "application/pdf", b"<html>", (None, None, "isi_tak_cocok")),
        ("html-menyamar.png", "application/octet-stream", b"<script>", (None, None, "isi_tak_cocok")),
    ],
)
def test_tentukan_tipe(nama, ctype, isi, harap):
    assert T(nama, ctype, isi) == harap


def test_tersedia_hanya_s3_tak_dihapus():
    assert AL.lampiran_tersedia("s3") and AL.lampiran_tersedia("S3")
    assert not AL.lampiran_tersedia("local")
    assert not AL.lampiran_tersedia("s3", WAKTU)


# ------------------------------------------------------------------ POST
@pytest.mark.asyncio
async def test_unggah_tiga_berkas_semua_masuk(monkeypatch):
    k = Keadaan()
    _, tersimpan = pasang(monkeypatch, k)
    out = await SL.unggah_lampiran_so(req(), SO, unik(3))
    assert [h["ok"] for h in out["hasil"]] == [True, True, True]
    assert out["kuota"] == {"terpakai": 3, "maks": 10}
    assert len(tersimpan) == 3
    assert all(re.fullmatch(rf"{TENANT}/uploads/lampiran/[0-9a-f]{{64}}\.jpg", kk) for kk in tersimpan)
    assert [t["urutan"] for t in k.tautan] == [0, 1, 2]


@pytest.mark.asyncio
async def test_kuota_sebagian_local_mati_tak_dihitung(monkeypatch):
    k = Keadaan(tautan=s3(8) + local(2))
    _, tersimpan = pasang(monkeypatch, k)
    out = await SL.unggah_lampiran_so(req(), SO, unik(3))
    assert [h.get("galat") for h in out["hasil"]] == [None, None, "kuota_penuh"]
    assert out["kuota"]["terpakai"] == 10
    assert len(tersimpan) == 2  # berkas kuota_penuh TIDAK disimpan


@pytest.mark.asyncio
async def test_berkas_buruk_tak_membatalkan_yang_baik(monkeypatch):
    k = Keadaan()
    _, tersimpan = pasang(monkeypatch, k)
    fs = [
        berkas("besar.pdf", b"%PDF-" + b"x" * (10 * MB), "application/pdf"),
        berkas("skrip.html", b"<script>", "text/html"),
        berkas("foto.heic", b"heic-isi", "image/heic"),
    ]
    out = await SL.unggah_lampiran_so(req(), SO, fs)
    assert [h.get("galat") for h in out["hasil"]] == ["terlalu_besar", "tipe_ditolak", None]
    assert out["hasil"][2]["ok"] is True
    assert len(tersimpan) == 1 and tersimpan[0].endswith(".heic")


@pytest.mark.asyncio
async def test_png_menyamar_jpg_ditolak_yang_lain_masuk(monkeypatch):
    k = Keadaan()
    _, tersimpan = pasang(monkeypatch, k)
    fs = [berkas("a.jpg", PNG + b"x"), berkas("b.csv", b"a,b", "application/octet-stream")]
    out = await SL.unggah_lampiran_so(req(), SO, fs)
    assert [h.get("galat") for h in out["hasil"]] == ["isi_tak_cocok", None]
    assert tersimpan == [k2 for k2 in tersimpan if k2.endswith(".csv")] and len(tersimpan) == 1


@pytest.mark.asyncio
async def test_galat_db_satu_berkas_tak_membatalkan_yang_lain(monkeypatch):
    """Sabotase 'satu gagal membatalkan semua' (tanpa savepoint/try per berkas)
    -> galat merambat -> 500 / tak ada hasil -> MERAH."""
    k = Keadaan()
    pasang(monkeypatch, k)
    fs = unik(3)
    isi_kedua = fs[1].file.getvalue()
    k.gagal_taut_untuk.add(hashlib.sha256(isi_kedua).hexdigest())
    out = await SL.unggah_lampiran_so(req(), SO, fs)
    assert [h.get("galat") for h in out["hasil"]] == [None, "gagal_simpan", None]
    assert out["kuota"]["terpakai"] == 2
    assert len(k.tautan) == 2


@pytest.mark.asyncio
async def test_kuota_dihitung_di_dalam_txn_sesudah_lock(monkeypatch):
    k = Keadaan()
    conns, _ = pasang(monkeypatch, k)
    await SL.unggah_lampiran_so(req(), SO, unik(1))
    ev = conns[0].peristiwa
    i_lock = next(i for i, e in enumerate(ev) if e[0] == "lock" and e[1].startswith("LAMPIRAN_KUOTA:"))
    i_hitung = next(i for i, e in enumerate(ev) if e[0] == "hitung")
    assert i_lock < i_hitung
    assert ev[i_hitung][1] >= 1 and ev[i_hitung][2] is True  # di dalam txn, lock dipegang
    assert ev[i_lock][1] == AL.kunci_kuota_lampiran(TENANT, "sales_order", SO)


@pytest.mark.asyncio
async def test_dua_unggahan_paralel_tak_melewati_sepuluh(monkeypatch):
    """Dua coroutine SUNGGUHAN, masing-masing 6 berkas, SO kosong -> total 10.
    Sabotase (hitung di luar txn/sebelum lock, atau lock dibuang) -> 12."""
    k = Keadaan()
    pasang(monkeypatch, k)
    a, b = await asyncio.gather(
        SL.unggah_lampiran_so(req(), SO, unik(6, 0)),
        SL.unggah_lampiran_so(req(), SO, unik(6, 100)),
    )
    total = sum(1 for t in k.tautan if t["storage_type"] == "s3")
    assert total == 10, total
    ok = sum(h["ok"] for h in a["hasil"] + b["hasil"])
    penuh = sum(h.get("galat") == "kuota_penuh" for h in a["hasil"] + b["hasil"])
    assert (ok, penuh) == (10, 2)


@pytest.mark.asyncio
async def test_lebih_dari_maks_berkas_sisanya_tak_dibaca(monkeypatch):
    k = Keadaan()
    pasang(monkeypatch, k)
    Berkas.dibaca = 0
    out = await SL.unggah_lampiran_so(req(), SO, unik(12))
    assert sum(h["ok"] for h in out["hasil"]) == 10
    assert [h.get("galat") for h in out["hasil"][10:]] == ["kuota_penuh", "kuota_penuh"]
    assert Berkas.dibaca == 10


@pytest.mark.asyncio
async def test_berkas_sama_dua_kali_duplikat(monkeypatch):
    k = Keadaan()
    pasang(monkeypatch, k)
    out = await SL.unggah_lampiran_so(req(), SO, [berkas("a.jpg", JPG + b"sama"), berkas("b.jpg", JPG + b"sama")])
    assert [h.get("galat") for h in out["hasil"]] == [None, "duplikat"]
    assert out["kuota"]["terpakai"] == 1


@pytest.mark.asyncio
async def test_so_tak_ada_atau_tenant_lain_404_tanpa_menyimpan(monkeypatch):
    k = Keadaan(so_ada=False)
    _, tersimpan = pasang(monkeypatch, k)
    with pytest.raises(HTTPException) as ei:
        await SL.unggah_lampiran_so(req(), SO, unik(2))
    assert ei.value.status_code == 404
    assert tersimpan == [] and k.tautan == []


# ------------------------------------------------------------------ GET / DELETE
@pytest.mark.asyncio
async def test_daftar_url_relatif_dan_kuota(monkeypatch):
    k = Keadaan(tautan=s3(2) + local(1))
    pasang(monkeypatch, k)
    out = await SL.daftar_lampiran_so(req(), SO)
    assert out["kuota"] == {"terpakai": 2, "maks": 10}
    for d, t in zip(out["data"], k.tautan):
        assert d["url"] == f"/api/sales-orders/{SO}/attachments/{t['document_id']}/download"
        assert "http" not in d["url"] and ":9000" not in d["url"]
        assert set(d) >= {"id", "nama", "ukuran", "mime", "url", "tersedia"}
    assert [d["tersedia"] for d in out["data"]] == [True, True, False]


@pytest.mark.asyncio
async def test_daftar_so_tak_ada_404(monkeypatch):
    pasang(monkeypatch, Keadaan(so_ada=False))
    with pytest.raises(HTTPException) as ei:
        await SL.daftar_lampiran_so(req(), SO)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_lepas_mengurangi_kuota_dan_404_bila_tak_ada(monkeypatch):
    k = Keadaan(tautan=s3(3))
    pasang(monkeypatch, k)
    out = await SL.lepas_lampiran_so(req(), SO, k.tautan[0]["document_id"])
    assert out["kuota"]["terpakai"] == 2
    with pytest.raises(HTTPException) as ei:
        await SL.lepas_lampiran_so(req(), SO, uuid.uuid4())
    assert ei.value.status_code == 404


def test_sql_berpagar_tenant_dan_induk_so():
    for sql in (SL._SQL_DAFTAR, SL._SQL_UNDUH, SL._SQL_LEPAS):
        assert "sales_orders so" in sql
        assert re.search(r"so\.tenant_id = \$\d", sql)
        assert "da.tenant_id" in sql
        assert "'sales_order'" in sql
    assert "d.tenant_id" in SL._SQL_UNDUH and "d.deleted_at IS NULL" in SL._SQL_UNDUH
    assert "tenant_id = $2" in SL._SQL_SO


# ------------------------------------------------------------------ izin
def test_izin_rute_lampiran_so():
    mw = pm.PermissionMiddleware(app=None)
    a = uuid.uuid4()
    assert mw._find_permission(f"/api/sales-orders/{SO}/attachments", "POST") == ("sales_order", "U")
    assert mw._find_permission(f"/api/sales-orders/{SO}/attachments", "GET") == ("sales_order", "R")
    unduh = f"/api/sales-orders/{SO}/attachments/{a}/download"
    assert mw._find_permission(unduh, "GET") == ("sales_order", "R")
    assert mw._find_permission(f"/api/sales-orders/{SO}/attachments/{a}", "DELETE") == ("sales_order", "D")
    assert not any(p.match(unduh) for p in mw._compiled_read_open)
    assert not any(p.match(unduh) for p in mw._compiled_skip)
    # rute SO lama tak bergeser
    assert mw._find_permission("/api/sales-orders", "POST") == ("sales_order", "C")
    assert mw._find_permission(f"/api/sales-orders/{SO}", "DELETE") == ("sales_order", "D")


# ------------------------------------------------------------------ sajian unduhan
class _Body:
    def __init__(self, b):
        self.b = io.BytesIO(b)

    def read(self, n=-1):
        return self.b.read(n)

    def close(self):
        pass


def _stream(file_type, nama="berkas"):
    storage = SimpleNamespace(
        client=SimpleNamespace(get_object=lambda **kw: {"Body": _Body(b"isi")}),
        config=SimpleNamespace(bucket="b"),
    )
    row = {"file_name": nama, "file_path": "k", "file_type": file_type, "storage_type": "s3"}
    return LU.stream_lampiran(row, storage)


@pytest.mark.parametrize("tipe", ["image/svg+xml", "image/heic", "text/html", "application/zip", None])
def test_tipe_tak_aman_diunduh_sebagai_oktet(tipe):
    r = _stream(tipe, "gambar.svg")
    assert r.media_type == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment; ")
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("tipe", ["image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf"])
def test_tipe_aman_tetap_inline(tipe):
    r = _stream(tipe)
    assert r.media_type == tipe
    assert r.headers["content-disposition"].startswith("inline; ")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_router_terdaftar_di_main():
    import pathlib

    main = (pathlib.Path(SL.__file__).parents[1] / "main.py").read_text()
    assert "sales_order_lampiran.router" in main
    assert re.search(r'sales_order_lampiran\.router,\s*prefix="/api/sales-orders"', main)
