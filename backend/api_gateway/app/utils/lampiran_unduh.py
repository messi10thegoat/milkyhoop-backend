"""Lampiran yang DIUNDUH LEWAT GATEWAY (Unit 1a, 24 Sep 2026).

KENAPA ADA: medan `url` lampiran dulu diisi URL presign MinIO
(`storage.generate_signed_url`). Host presign = `MINIO_PUBLIC_ENDPOINT`
(159.89.202.160:9000), dan sejak port publik ditutup (23 Sep) MinIO hanya
mendengar di 127.0.0.1:9000 -> tiap URL presign MATI dari luar. Sebagian jalur
lain malah mengirim `documents.file_url` mentah, yang NULL untuk baris s3.

ARAH (disetujui): `url` = PATH RELATIF gateway
`/api/<modul>/<induk_id>/attachments/<lampiran_id>/download`, dan rute itu
men-STREAM isi objek dari MinIO lewat gateway. MinIO tetap tertutup; env tak
diubah. Path relatif = izin modul (permission_middleware) + pagar tenant rute
download berlaku pada tiap unduhan, bukan tanda tangan yang bisa diteruskan.

Rute download tiap modul WAJIB memverifikasi INDUK di tabel modulnya sendiri
+ tenant (JOIN), karena entity_type 'payment' dipakai BERSAMA
receive_payments dan bill_payments_v2: tanpa JOIN induk, id lampiran
pembayaran-keluar bisa diunduh lewat rute penerimaan dan sebaliknya.
"""
import logging
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

# Penanda unik (dipakai window_item.sh untuk memastikan kode ini terpasang).
PENANDA_LAMPIRAN_1A = "lampiran-1a-unduh-lewat-gateway"

PESAN_BERKAS_TAK_TERSEDIA = "Berkas tidak tersedia"


def url_unduh_lampiran(modul: str, induk_id, lampiran_id) -> str:
    """Path relatif gateway untuk mengunduh satu lampiran.

    `modul` = segmen URL modul (mis. 'customer-deposits'). Sengaja TIDAK
    menerima file_path/kunci storage: url tak boleh membocorkan kunci objek.
    """
    return f"/api/{modul}/{induk_id}/attachments/{lampiran_id}/download"


# Penanda unik Unit 1b (faktur penjualan, faktur pembelian, beban).
PENANDA_LAMPIRAN_1B = "lampiran-1b-unduh-lewat-gateway"

def url_lampiran_dokumen(
    modul: str, induk_id, lampiran_id, storage_type=None, file_url=None
) -> str:
    """url untuk lampiran yang barisnya di `documents` (Unit 1b).

    SEMUA baris -> rute download modul (`url_unduh_lampiran`), yang men-stream
    dari storage (baris s3) atau menjawab 404 bersih (baris local lama yang
    berkasnya sudah hilang). Sejak unggahan-persisten (Unit U1) unggahan form
    ditulis sebagai baris s3, jadi cabang sementara "baris local -> path
    berkas-chat" sudah dihapus. `storage_type` / `file_url` dipertahankan di
    tanda tangan supaya pemanggil (bills, expenses) tak berubah, tapi SENGAJA
    tak dipakai: url tak pernah berasal dari file_url tersimpan.
    """
    return url_unduh_lampiran(modul, induk_id, lampiran_id)


def content_disposition_lampiran(nama: str | None) -> str:
    """`inline; filename="..."; filename*=UTF-8''...` yang aman untuk header.

    Buang `"`, CR, LF (pemecah header / pemutus kutip); nama non-ASCII utuh di
    filename*, fallback ASCII di filename=.
    """
    bersih = (nama or "").replace('"', "").replace("\r", "").replace("\n", "").strip()
    bersih = bersih or "lampiran"
    ascii_fallback = bersih.encode("ascii", "replace").decode("ascii")
    return (
        f"inline; filename=\"{ascii_fallback}\"; "
        f"filename*=UTF-8''{quote(bersih, safe='')}"
    )


def stream_lampiran(row, storage) -> StreamingResponse:
    """Stream satu baris `documents` (file_name, file_path, file_type,
    storage_type) dari storage. Baris non-s3 / tanpa file_path / objek hilang
    -> 404 bersih, bukan 500. Disk lokal SENGAJA tidak dibaca (berkas lokal
    lampiran lama sudah hilang; membaca path dari DB ke disk = permukaan
    traversal).
    """
    if (row["storage_type"] or "").lower() != "s3" or not row["file_path"]:
        raise HTTPException(status_code=404, detail=PESAN_BERKAS_TAK_TERSEDIA)
    try:
        obj = storage.client.get_object(
            Bucket=storage.config.bucket, Key=row["file_path"]
        )
    except Exception as e:  # noqa: BLE001
        kode = ""
        try:
            kode = e.response["Error"]["Code"]  # botocore ClientError
        except Exception:  # noqa: BLE001
            pass
        if kode in ("NoSuchKey", "404", "NotFound"):
            raise HTTPException(status_code=404, detail=PESAN_BERKAS_TAK_TERSEDIA)
        logger.error(f"Gagal mengambil objek lampiran: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Gagal mengunduh lampiran")

    body = obj["Body"]

    def iter_body():
        try:
            while chunk := body.read(65536):
                yield chunk
        finally:
            body.close()

    return StreamingResponse(
        iter_body(),
        media_type=row["file_type"] or "application/octet-stream",
        headers={
            "Content-Disposition": content_disposition_lampiran(row["file_name"]),
            "Cache-Control": "private, max-age=3600",
        },
    )
