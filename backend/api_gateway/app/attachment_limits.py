"""Satu sumber batas & jenis lampiran (Unit B, 16 Sep 2026).

Sebelumnya tiga jalur unggah punya batas berbeda: hub generik 50 MB / 14 tipe,
endpoint faktur 5 MB / 4 tipe, endpoint uang muka 5 MB / 4 tipe. FE punya 3 batas
(5/10/10 MB). Modul ini menyatukan: 10 MB + daftar tipe acuan (14 tipe hub generik).
Pakai `enforce_attachment_limits(len(content), content_type)` di SEMUA jalur unggah.
"""

from fastapi import HTTPException

ATTACHMENT_MAX_MB = 10
ATTACHMENT_MAX_BYTES = ATTACHMENT_MAX_MB * 1024 * 1024

# DAFTAR TIPE RESMI (putusan MASTER 24 Sep 2026, L1): jpg/jpeg (alias image/jpg
# hanya lewat tentukan_tipe_lampiran),
# png, webp, gif, heic, heif, pdf, doc, docx, xls, xlsx, csv, txt. DIBUANG: svg
# (risiko skrip), zip/rar (wadah tak terperiksa) -- diukur 0/124 baris documents
# prod bertipe itu, jadi tak ada unduhan lama yang rusak.
ATTACHMENT_ALLOWED_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        # Foto iPhone (L1, 24 Sep 2026). Tak dirender browser -> selalu diunduh.
        "image/heic",
        "image/heif",
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
    }
)


def enforce_attachment_limits(content_length: int, content_type: str | None) -> None:
    """Tolak 400 bila tipe tak sah atau ukuran > batas. Pesan seragam di semua jalur.

    Urutan: tipe dulu (pesan menyebut daftar), lalu ukuran.
    """
    if content_type not in ATTACHMENT_ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Jenis berkas {content_type} tidak didukung. Gunakan gambar "
                "(JPEG/PNG/GIF/WebP/HEIC), PDF, Word, Excel, atau teks/CSV."
            ),
        )
    if content_length > ATTACHMENT_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Ukuran berkas melebihi batas {ATTACHMENT_MAX_MB} MB.",
        )


# --------------------------------------------------------------------------
# L1 lampiran SO (24 Sep 2026): kuota jumlah + penggolongan galat per berkas.
# --------------------------------------------------------------------------

# Batas JUMLAH lampiran per dokumen (acuan Xero: 10 berkas x 10 MB). Dipakai
# lampiran SO di L1; beban tetap _EXP_ATT_MAKS=5 sampai diputuskan; uang muka +
# hub attach menyusul di L2.
MAKS_LAMPIRAN_PER_DOKUMEN = 10

# Tipe ditentukan dari EKSTENSI nama berkas (allowlist), BUKAN dari
# content_type klien saja: HEIC/CSV sering datang sebagai "" atau
# application/octet-stream. Nilai = tipe kanonik yang disimpan & disajikan.
EKSTENSI_LAMPIRAN = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
}

# content_type yang berarti "klien tak tahu" -> diputus oleh ekstensi.
_CTYPE_NETRAL = {"", "application/octet-stream", "binary/octet-stream"}

# Alias content_type klien yang diterima tentukan_tipe_lampiran SAJA. SENGAJA
# tidak di ATTACHMENT_ALLOWED_TYPES: rute SI/uang muka meneruskan content_type
# mentah ke storage.upload_file (whitelist sendiri) -> alias di sana = 500 (L2).
_CTYPE_ALIAS = {"image/jpg"}


def _cocok_magic(tipe: str, isi: bytes):
    """True/False untuk tipe web + PDF (tanda tangan byte awal); None = tak
    diperiksa (HEIC/office/teks: ekstensi cukup, tanpa pustaka baru)."""
    if tipe == "image/jpeg":
        return isi[:3] == b"\xff\xd8\xff"
    if tipe == "image/png":
        return isi[:8] == b"\x89PNG\r\n\x1a\n"
    if tipe == "image/gif":
        return isi[:6] in (b"GIF87a", b"GIF89a")
    if tipe == "image/webp":
        return isi[:4] == b"RIFF" and isi[8:12] == b"WEBP"
    if tipe == "application/pdf":
        return b"%PDF-" in isi[:1024]
    return None


def tentukan_tipe_lampiran(nama: str | None, content_type: str | None, isi: bytes):
    """(tipe_kanonik, ext, kode_galat) untuk SATU berkas; kode_galat None = boleh.

    Urutan: ekstensi -> content_type klien -> ukuran -> tanda tangan byte.
      tipe_ditolak   ekstensi di luar allowlist, ATAU content_type klien
                     spesifik yang tidak resmi (mis. text/html)
      kosong         0 byte
      terlalu_besar  > ATTACHMENT_MAX_BYTES
      isi_tak_cocok  jpg/png/gif/webp/pdf yang byte awalnya bukan tipe itu
    """
    n = (nama or "").strip().lower()
    ext = "." + n.rsplit(".", 1)[-1] if "." in n else ""
    if ext == ".jpeg":
        ext = ".jpg"
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if not ext and (ct in ATTACHMENT_ALLOWED_TYPES or ct in _CTYPE_ALIAS):
        # L2: nama TANPA ekstensi (kamera/canvas: "blob", "image") -> tipe dari
        # content_type resmi; tanda tangan byte di bawah tetap berlaku. Ekstensi
        # yang ADA tapi tak resmi (.svg/.exe) tetap ditolak.
        kanon = "image/jpeg" if ct in _CTYPE_ALIAS else ct
        ext = next(e for e, t in EKSTENSI_LAMPIRAN.items() if t == kanon and e != ".jpeg")
    tipe = EKSTENSI_LAMPIRAN.get(ext)
    if tipe is None:
        return None, None, "tipe_ditolak"
    if ct not in _CTYPE_NETRAL and ct not in _CTYPE_ALIAS and ct not in ATTACHMENT_ALLOWED_TYPES:
        return None, None, "tipe_ditolak"
    if len(isi) <= 0:
        return None, None, "kosong"
    if len(isi) > ATTACHMENT_MAX_BYTES:
        return None, None, "terlalu_besar"
    if _cocok_magic(tipe, isi) is False:
        return None, None, "isi_tak_cocok"
    return tipe, ext, None


def lampiran_tersedia(storage_type, deleted_at=None) -> bool:
    """Lampiran MEMAKAN kuota bila bisa diunduh: di storage objek (s3) dan
    dokumennya belum dihapus. Baris 'local' lama (berkas hilang 23 Sep) tidak."""
    return (storage_type or "").lower() == "s3" and deleted_at is None


# Hitung lampiran tersedia satu entitas. Predikat tenant eksplisit (gateway =
# peran BYPASSRLS). WAJIB dipanggil DI DALAM transaksi yang sudah memegang
# advisory lock entitas (lihat kunci_kuota_lampiran), kalau tidak dua unggahan
# paralel sama-sama melihat sisa kuota yang sama.
SQL_HITUNG_LAMPIRAN_TERSEDIA = """
    SELECT count(*)
    FROM document_attachments da
    JOIN documents d ON d.id = da.document_id
    WHERE da.tenant_id = $1 AND d.tenant_id = $1
      AND da.entity_type = $2 AND da.entity_id = $3
      AND lower(coalesce(d.storage_type, '')) = 's3'
      AND d.deleted_at IS NULL
"""


def kunci_kuota_lampiran(tenant_id: str, entity_type: str, entity_id) -> str:
    """Nama advisory lock (hashtext) kuota lampiran satu entitas."""
    return f"LAMPIRAN_KUOTA:{tenant_id}:{entity_type}:{entity_id}"


async def hitung_lampiran_tersedia(conn, tenant_id: str, entity_type: str, entity_id) -> int:
    return int(
        await conn.fetchval(SQL_HITUNG_LAMPIRAN_TERSEDIA, tenant_id, entity_type, entity_id)
        or 0
    )


# --------------------------------------------------------------------------
# L2 (24 Sep 2026): satu jalur validasi untuk SEMUA pengunggah lampiran
# (storage.upload_file, rute uang muka/SI/bills, hub /api/documents/upload).
# Dulu: enforce_attachment_limits (content_type) lalu storage.upload_file dgn
# whitelist SENDIRI 6 tipe -> gif/docx/xlsx/csv/txt ke rute DP = 500.
# --------------------------------------------------------------------------
PESAN_GALAT_LAMPIRAN = {
    "tipe_ditolak": (
        "Jenis berkas tidak didukung. Gunakan gambar (JPG/PNG/WebP/GIF/HEIC), "
        "PDF, Word, Excel, atau teks/CSV."
    ),
    "kosong": "Berkas kosong.",
    "terlalu_besar": f"Ukuran berkas melebihi batas {ATTACHMENT_MAX_MB} MB.",
    "isi_tak_cocok": "Isi berkas tidak cocok dengan jenisnya.",
    "kuota_penuh": (
        f"Lampiran penuh: maksimal {MAKS_LAMPIRAN_PER_DOKUMEN} berkas per dokumen."
    ),
}


class LampiranDitolak(ValueError):
    """Berkas ditolak aturan lampiran. `kode` = kunci PESAN_GALAT_LAMPIRAN."""

    def __init__(self, kode: str):
        self.kode = kode
        super().__init__(PESAN_GALAT_LAMPIRAN.get(kode, kode))


def periksa_lampiran(nama, content_type, isi: bytes) -> str:
    """Tipe kanonik, atau lempar LampiranDitolak."""
    tipe, _ext, kode = tentukan_tipe_lampiran(nama, content_type, isi)
    if kode:
        raise LampiranDitolak(kode)
    return tipe


def http_400_lampiran(kode: str) -> HTTPException:
    return HTTPException(status_code=400, detail=PESAN_GALAT_LAMPIRAN.get(kode, kode))


async def baca_lampiran_atau_400(file) -> tuple:
    """Baca UploadFile, validasi (ekstensi + byte awal + ukuran), kembalikan
    (tipe_kanonik, isi) lalu seek(0). Ditolak -> HTTP 400 berpesan jujur."""
    isi = await file.read()
    try:
        tipe = periksa_lampiran(file.filename, file.content_type, isi)
    except LampiranDitolak as e:
        raise http_400_lampiran(e.kode)
    await file.seek(0)
    return tipe, isi
