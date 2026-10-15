"""Lampiran Pesanan Penjualan (Unit L1, 24 Sep 2026).

Permintaan pemilik: form SO perlu lampiran -- maks 10 berkas per dokumen,
10 MB per berkas (acuan Xero), unggah banyak berkas sekaligus. Sebelum unit
ini SO sama sekali TIDAK punya lampiran (tanpa kolom/tabel/rute; nol baris
document_attachments 'sales_order').

Kontrak (disepakati MASTER + FE):
  POST   /api/sales-orders/{id}/attachments          multipart, medan `files` (1..n)
         -> {hasil: [{nama, ok, id?, galat?}], kuota: {terpakai, maks}}
         Sukses SEBAGIAN boleh: satu berkas gagal tidak membatalkan yang lain.
  GET    /api/sales-orders/{id}/attachments          -> {data: [...], kuota}
  GET    /api/sales-orders/{id}/attachments/{aid}/download   stream
  DELETE /api/sales-orders/{id}/attachments/{aid}    lepas tautan (SO bukan
         dokumen buku -> boleh dihapus; berkas `documents` tidak dihapus)

Penyimpanan: pola U1 -- objek MinIO `<tenant>/uploads/lampiran/<sha256><ext>`
(ext dari content-type, metadata kosong -> nama non-ASCII aman), baris
`documents` storage_type='s3', tautan `document_attachments` entity_type
'sales_order'. `id` lampiran = documents.id (sama di daftar/unduh/DELETE).

Kuota: dihitung per berkas BERURUTAN di DALAM satu transaksi yang memegang
advisory lock per SO (kunci_kuota_lampiran) -> dua unggahan paralel tak bisa
bersama-sama melewati 10. Tiap berkas di savepoint sendiri.

Pagar: setiap SQL JOIN/menyaring `sales_orders` + tenant JWT (gateway memakai
peran BYPASSRLS) -> SO tenant lain / tak ada = 404. Izin modul `sales_order`
(R/U/D) di permission_middleware.

NAMA per TAUTAN (12 Okt 2026, temuan WORKSPACE uji nyata kaos; pola NetSuite/SAP): berkas dideduplikasi per isi (sha256)
di `documents`, tetapi nama yang dilihat pengguna = nama UNGGAHAN tautan ini (`document_attachments.display_name`, V411).
Dulu PNG baru "tes-e2e-resi-....png" tampil sebagai dokumen lama "uji-r139-sah.png". Tautan lama (display_name NULL)
memakai nama dokumen.
"""
import hashlib
import logging
from typing import List
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from ..attachment_limits import (
    MAKS_LAMPIRAN_PER_DOKUMEN,
    hitung_lampiran_tersedia,
    kunci_kuota_lampiran,
    lampiran_tersedia,
    tentukan_tipe_lampiran,
)
from ..services.storage_service import get_storage_service
from ..utils.chat_file_path import kunci_unggahan, simpan_objek_unggahan
from ..utils.lampiran_unduh import stream_lampiran, url_unduh_lampiran

logger = logging.getLogger(__name__)
router = APIRouter()

# Penanda unik (window_item.sh: harus ada di kode BERJALAN).
PENANDA_LAMPIRAN_SO = "lampiran-so-l1-kuota-dalam-txn"

_MODUL_URL = "sales-orders"
_ENTITAS = "sales_order"
_SUBDIR = "lampiran"


async def get_pool():
    """Singleton pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def _ctx(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Perlu masuk (login).")
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Konteks pengguna tidak sah.")
    user_id = user.get("user_id") or user.get("id")
    return {"tenant_id": tenant_id, "user_id": str(user_id) if user_id else None}


_SQL_SO = "SELECT id FROM sales_orders WHERE id = $1 AND tenant_id = $2"

async def _pastikan_so(conn, order_id: UUID, tenant_id: str) -> None:
    if not await conn.fetchval(_SQL_SO, order_id, tenant_id):
        raise HTTPException(status_code=404, detail="Pesanan penjualan tidak ditemukan")



# LAMPIRAN-SO-SJ-PABRIK (15 Okt 2026, putusan MASTER): unggah/daftar/unduh/lepas lampiran SO kini lewat SATU pabrik
# routers/lampiran_induk.buat_router (pola sama dengan NK/penawaran/SJ; tenant di tiap SQL -- cek "sudah tertaut" dulu tanpa
# tenant). Kontrak HTTP LAMA persis: rute, bentuk respons, pesan 404 induk, nama fungsi. Tambahan: lepas kini juga diaudit.
from . import lampiran_induk as _LI  # noqa: E402

SPEC_LAMPIRAN_SO = _LI.SpecLampiran(
    "sales_order", _MODUL_URL, "sales_orders", "order_number", "Pesanan", "sales_orders",
    pesan_404="Pesanan penjualan tidak ditemukan", ringkas_lepas="Lampiran pesanan dilepas ({nomor})",
    sumber_audit="api:sales-orders.attachments", modul_dep=__name__,
    nama_endpoint={"unggah": "unggah_lampiran_so", "daftar": "daftar_lampiran_so", "unduh": "unduh_lampiran_so",
                   "lepas": "lepas_lampiran_so"})
_router_lampiran = _LI.buat_router(SPEC_LAMPIRAN_SO)
router.include_router(_router_lampiran)
_EP = {r.endpoint.__name__: r.endpoint for r in _router_lampiran.routes}
unggah_lampiran_so = _EP["unggah_lampiran_so"]
daftar_lampiran_so = _EP["daftar_lampiran_so"]
unduh_lampiran_so = _EP["unduh_lampiran_so"]
lepas_lampiran_so = _EP["lepas_lampiran_so"]
_SQL = _LI.sql_spec(SPEC_LAMPIRAN_SO)
_SQL_TAUT, _SQL_DAFTAR, _SQL_UNDUH, _SQL_LEPAS = _SQL["taut"], _SQL["daftar"], _SQL["unduh"], _SQL["lepas"]
_SQL_URUTAN, _SQL_SUDAH_TERTAUT, _SQL_INDUK = _SQL["urutan"], _SQL["sudah"], _SQL["induk"]
_SQL_DOK_SAMA, _SQL_DOK_BARU = _LI._SQL_DOK_SAMA, _LI._SQL_DOK_BARU


# =============================================================================
# W-20261013-LAMPIRAN-SO: tab "Lampiran (N)" panel SO -- SEMUA lampiran keluarga
# SO, dikelompokkan per dokumen (pola NetSuite subtab Files / SAP Attachments).
# Tahap 1 BACA saja.
#
# Temuan pemilik grapgrap (SO-2610-0019): bukti transfer DP "tak bisa dilihat
# lagi". Diukur: berkas utuh (149/149 objek MinIO ada); panel SO hanya membaca
# lampiran milik SO sendiri, padahal bukti tertaut ke DOKUMEN ANAK (DP,
# penerimaan, SJ, faktur) -> 103 lampiran hub + 20 lampiran faktur lama di 72 SO
# tak terlihat dari panel.
#
# Pagar:
#  * SO bukan milik tenant JWT -> 404. SETIAP tabel di SQL keluarga disaring
#    tenant eksplisit (gateway = peran BYPASSRLS; RLS bukan pagar).
#  * Izin per GRUP = izin rute /download modul grup itu (ROUTE_PERMISSIONS +
#    PolicyEngine yang sama dengan middleware). Tanpa izin -> grup diganti
#    {jenis, tersembunyi_izin, jumlah} tanpa nomor dokumen/nama berkas.
#  * url = rute /download modul yang SUDAH ADA (pagar induk+tenant tiap modul
#    tetap berlaku); berkas tak tersedia (baris 'local' lama, musnah 23 Sep)
#    tetap dikirim dengan tersedia=false + url null, bukan dihilangkan diam.
# =============================================================================
PENANDA_LAMPIRAN_KELUARGA = "lampiran-so-keluarga-w20261013"

# jenis -> (segmen URL modul untuk /download, jalur izin bila TAK ada rute download)
JENIS_KELUARGA = {
    "sales_order": ("sales-orders", None),
    "quote": ("quotes", None),   # LAMPIRAN-NK-QUOTE 15 Okt: rute lampiran penawaran ada
    "proforma": (None, "/api/proformas/{id}"),
    "customer_deposit": ("customer-deposits", None),
    "receive_payment": ("receive-payments", None),
    "delivery": ("deliveries", None),
    "sales_invoice": ("sales-invoices", None),
    "credit_note": ("credit-notes", None),   # LAMPIRAN-NK-QUOTE 15 Okt: rute lampiran NK ada
}
URUTAN_JENIS = list(JENIS_KELUARGA)
STATUS_VOID = frozenset({"void", "voided", "cancelled", "canceled"})


def jalur_izin_jenis(jenis: str) -> str:
    """Jalur contoh yang izinnya mewakili grup `jenis` (id tiruan, pola [^/]+)."""
    modul, cadangan = JENIS_KELUARGA[jenis]
    if modul:
        return url_unduh_lampiran(modul, "x", "x")
    return cadangan.format(id="x")


# Keluarga SO. $1 = SO id (boleh NULL), $2 = tenant. Tiap tabel berpredikat tenant_id = $2.
#  - penawaran: sales_orders.quote_id
#  - proforma: proformas.sales_order_id
#  - DP: customer_deposits.sales_order_id / quote_id penawaran SO / proforma_id keluarga
#  - faktur: sales_invoices.sales_order_id
#  - penerimaan: alokasi ke faktur keluarga, ATAU source/created_deposit_id = DP keluarga
#  - SJ: COALESCE(invoice_fulfillments.sales_order_id, faktur.sales_order_id) -- SJ asal 'faktur' tak punya
#    sales_order_id (chk_if_asal_kolom: kolom itu khusus asal 'so'); SO-nya lewat fakturnya (14 Okt 2026, SJ-FAKTUR-SO-ID)
#  - NK (LAMPIRAN-TANPA-SO 15 Okt): credit_notes.original_invoice_id = faktur keluarga
#  Alias tiap tabel UNIK (penjaga tes membaca predikat tenant per alias).
#  entity_type 'payment' dipakai BERSAMA penerimaan & pembayaran-keluar -> hanya id yang
#  ADA di receive_payments tenant ini yang dicocokkan.
#
# BENIH (LAMPIRAN-TANPA-SO, 15 Okt 2026, putusan MASTER): dokumen TANPA SO (faktur langsung, penerimaan, DP/proforma tanpa SO,
# SJ, NK) memakai SQL yang SAMA dengan SO NULL dan benih: $3 faktur[], $4 DP[], $5 penerimaan[], $6 proforma[], $7 SJ[], $8 NK[],
# $9 penawaran[] (LAMPIRAN-NK-QUOTE: penawaran -> DP ber-quote_id + faktur ber-quote_id + kerabatnya).
# Penutupan satu langkah yang sama: faktur -> penerimaan teralokasi + SJ + NK; penerimaan -> faktur teralokasi (+ kerabatnya) + DP
# sumber/hasil; DP -> proforma + penerimaan; proforma -> DP + penerimaan; SJ -> fakturnya; NK -> faktur asal. Benih kosong = keluarga
# SO PERSIS seperti sebelumnya (pasangan diuji). Benih penerimaan HANYA id yang ada di receive_payments (bukan bill_payments_v2).
_SQL_META_KELUARGA = """
WITH so AS (
    SELECT id, quote_id FROM sales_orders WHERE id = $1 AND tenant_id = $2
), rp0 AS (
    SELECT rp0x.id, rp0x.source_deposit_id, rp0x.created_deposit_id FROM receive_payments rp0x
    WHERE rp0x.tenant_id = $2 AND rp0x.id = ANY($5::uuid[])
), dp0 AS (
    SELECT dp0x.id, dp0x.proforma_id FROM customer_deposits dp0x WHERE dp0x.tenant_id = $2 AND dp0x.id = ANY($4::uuid[])
), sj0 AS (
    SELECT sj0x.id, sj0x.invoice_id FROM invoice_fulfillments sj0x WHERE sj0x.tenant_id = $2 AND sj0x.id = ANY($7::uuid[])
), nk0 AS (
    SELECT nk0x.id, nk0x.original_invoice_id FROM credit_notes nk0x WHERE nk0x.tenant_id = $2 AND nk0x.id = ANY($8::uuid[])
), pf AS (
    SELECT pfx.id FROM proformas pfx WHERE pfx.tenant_id = $2
      AND (pfx.sales_order_id IN (SELECT id FROM so) OR pfx.id = ANY($6::uuid[]) OR pfx.id IN (SELECT proforma_id FROM dp0))
), dp AS (
    SELECT cdx.id FROM customer_deposits cdx
    WHERE cdx.tenant_id = $2
      AND (cdx.sales_order_id IN (SELECT id FROM so)
           OR cdx.quote_id IN (SELECT quote_id FROM so WHERE quote_id IS NOT NULL)
           OR cdx.proforma_id IN (SELECT id FROM pf)
           OR cdx.id IN (SELECT id FROM dp0)
           OR cdx.id IN (SELECT source_deposit_id FROM rp0)
           OR cdx.id IN (SELECT created_deposit_id FROM rp0)
           OR cdx.quote_id = ANY($9::uuid[]))
), si AS (
    SELECT six.id FROM sales_invoices six
    WHERE six.tenant_id = $2
      AND (six.sales_order_id IN (SELECT id FROM so)
           OR six.id = ANY($3::uuid[])
           OR six.id IN (SELECT invoice_id FROM sj0)
           OR six.id IN (SELECT original_invoice_id FROM nk0)
           OR six.quote_id = ANY($9::uuid[])
           OR six.id IN (SELECT al0x.invoice_id FROM receive_payment_allocations al0x
                         WHERE al0x.tenant_id = $2 AND al0x.payment_id IN (SELECT id FROM rp0)))
), rp AS (
    SELECT rpx.id FROM receive_payments rpx
    WHERE rpx.tenant_id = $2
      AND (EXISTS (SELECT 1 FROM receive_payment_allocations alx
                   WHERE alx.payment_id = rpx.id AND alx.tenant_id = $2
                     AND alx.invoice_id IN (SELECT id FROM si))
           OR rpx.source_deposit_id IN (SELECT id FROM dp)
           OR rpx.created_deposit_id IN (SELECT id FROM dp)
           OR rpx.id IN (SELECT id FROM rp0))
), meta AS (
    SELECT 'sales_order'::text AS jenis, 'sales_order'::text AS et, s.id,
           s.order_number::text AS nomor, s.order_date::date AS tanggal, s.status::text AS status
    FROM sales_orders s JOIN so ON s.id = so.id WHERE s.tenant_id = $2
    UNION ALL
    SELECT 'quote', 'quote', q.id, q.quote_number, q.quote_date::date, q.status
    FROM quotes q WHERE q.tenant_id = $2 AND (q.id IN (SELECT quote_id FROM so) OR q.id = ANY($9::uuid[]))
    UNION ALL
    SELECT 'proforma', 'proforma', p.id, p.proforma_number, p.proforma_date::date, p.status
    FROM proformas p WHERE p.tenant_id = $2 AND p.id IN (SELECT id FROM pf)
    UNION ALL
    SELECT 'customer_deposit', 'customer_deposit', c.id, c.deposit_number, c.deposit_date::date, c.status
    FROM customer_deposits c WHERE c.tenant_id = $2 AND c.id IN (SELECT id FROM dp)
    UNION ALL
    SELECT 'receive_payment', 'payment', r.id, r.payment_number, r.payment_date::date, r.status
    FROM receive_payments r WHERE r.tenant_id = $2 AND r.id IN (SELECT id FROM rp)
    UNION ALL
    SELECT 'delivery', 'delivery', f.id, f.fulfillment_number, f.fulfillment_date::date, f.status
    FROM invoice_fulfillments f
    LEFT JOIN sales_invoices fsi ON fsi.id = f.invoice_id AND fsi.tenant_id = $2
    WHERE f.tenant_id = $2
      AND (COALESCE(f.sales_order_id, fsi.sales_order_id) IN (SELECT id FROM so)
           OR f.invoice_id IN (SELECT id FROM si)
           OR f.id IN (SELECT id FROM sj0))
    UNION ALL
    SELECT 'sales_invoice', 'sales_invoice', i.id, i.invoice_number, i.invoice_date::date, i.status
    FROM sales_invoices i WHERE i.tenant_id = $2 AND i.id IN (SELECT id FROM si)
    UNION ALL
    SELECT 'credit_note', 'credit_note', n.id, n.credit_note_number, n.credit_note_date::date, n.status
    FROM credit_notes n WHERE n.tenant_id = $2 AND (n.original_invoice_id IN (SELECT id FROM si) OR n.id IN (SELECT id FROM nk0))
"""
_SQL_KELUARGA = _SQL_META_KELUARGA + """)
SELECT m.jenis, m.id AS dok_id, m.nomor, m.tanggal, m.status,
       d.id AS lampiran_id, COALESCE(da.display_name, d.file_name) AS nama,
       d.file_type AS mime, d.file_size AS ukuran, d.storage_type, d.uploaded_at,
       COALESCE(NULLIF(trim(up.display_name), ''), NULLIF(trim(u.fullname), ''),
                NULLIF(trim(u.name), ''), u.email) AS pengunggah,
       'hub'::text AS sumber
FROM meta m
JOIN document_attachments da
  ON da.entity_type = m.et AND da.entity_id = m.id AND da.tenant_id = $2
JOIN documents d ON d.id = da.document_id AND d.tenant_id = $2 AND d.deleted_at IS NULL
LEFT JOIN "User" u ON u.id = d.uploaded_by::text
LEFT JOIN user_profiles up ON up.user_id = u.id
UNION ALL
SELECT m.jenis, m.id, m.nomor, m.tanggal, m.status,
       sa.id, sa.filename, sa.mime_type, sa.file_size, NULL, sa.uploaded_at,
       COALESCE(NULLIF(trim(up.display_name), ''), NULLIF(trim(u.fullname), ''),
                NULLIF(trim(u.name), ''), u.email),
       'faktur_lama'
FROM meta m
JOIN sales_invoice_attachments sa ON sa.invoice_id = m.id AND sa.tenant_id = $2
LEFT JOIN "User" u ON u.id = sa.uploaded_by::text
LEFT JOIN user_profiles up ON up.user_id = u.id
WHERE m.jenis = 'sales_invoice'
"""

_SQL_ANGGOTA_KELUARGA = _SQL_META_KELUARGA + """)
SELECT EXISTS (SELECT 1 FROM meta WHERE jenis = $10 AND id = $11)
"""

# urutan benih $3..$8 (jenis dokumen -> posisi parameter)
BENIH_JENIS = ("sales_invoice", "customer_deposit", "receive_payment", "proforma", "delivery", "credit_note", "quote")


def param_keluarga(so_id, tenant_id: str, benih: dict = None) -> tuple:
    """($1 SO|None, $2 tenant, $3..$9 benih uuid[] per BENIH_JENIS; $9 = penawaran, LAMPIRAN-NK-QUOTE)."""
    b = benih or {}
    return (so_id, tenant_id, *[list(b.get(j, [])) for j in BENIH_JENIS])


async def baris_keluarga(conn, tenant_id: str, so_id=None, benih: dict = None):
    return await conn.fetch(_SQL_KELUARGA, *param_keluarga(so_id, tenant_id, benih))


async def anggota_keluarga(conn, tenant_id: str, so_id, jenis: str, dok_id) -> bool:
    return bool(await conn.fetchval(_SQL_ANGGOTA_KELUARGA, *param_keluarga(so_id, tenant_id), jenis, dok_id))


async def susun_dengan_izin(request, rows) -> dict:
    """Izin per GRUP (izin rute /download modul grup itu) lalu susun. Satu jalur untuk keluarga SO & dokumen."""
    from ..middleware.permission_middleware import izin_rute_pengguna
    jenis_ada = sorted({r["jenis"] for r in rows}, key=URUTAN_JENIS.index)
    jalur = {j: jalur_izin_jenis(j) for j in jenis_ada}
    izin = await izin_rute_pengguna(request.state.user, list(jalur.values())) if jalur else {}
    boleh = {j: izin.get(p, False) for j, p in jalur.items()}
    return susun_grup_lampiran(rows, boleh)


def _tersedia_baris(r) -> bool:
    # Tabel lama faktur: rute download faktur menyajikannya (tersedia=True di GET faktur).
    if r["sumber"] == "faktur_lama":
        return True
    return lampiran_tersedia(r["storage_type"])


def susun_grup_lampiran(rows, boleh: dict) -> dict:
    """Baris SQL keluarga -> respons. `boleh` = {jenis: bool} hasil izin rute."""
    grup = {}
    for r in rows:
        kunci = (r["jenis"], str(r["dok_id"]))
        g = grup.get(kunci)
        if g is None:
            status = (r["status"] or "").lower()
            g = grup[kunci] = {
                "dokumen": {
                    "jenis": r["jenis"],
                    "id": str(r["dok_id"]),
                    "nomor": r["nomor"],
                    "tanggal": r["tanggal"].isoformat() if r["tanggal"] else None,
                    "status": r["status"],
                    "void": status in STATUS_VOID,
                },
                "lampiran": [],
                "_kunci_lampiran": set(),
            }
        lid = str(r["lampiran_id"])
        if lid in g["_kunci_lampiran"]:
            continue
        g["_kunci_lampiran"].add(lid)
        modul = JENIS_KELUARGA[r["jenis"]][0]
        tersedia = _tersedia_baris(r)
        g["lampiran"].append({
            "id": lid,
            "display_name": r["nama"],
            "mime": r["mime"],
            "ukuran": r["ukuran"],
            "diunggah_oleh": r["pengunggah"],
            "diunggah_pada": r["uploaded_at"].isoformat() if r["uploaded_at"] else None,
            "tersedia": tersedia,
            # berkas musnah (baris 'local' lama) / modul tanpa rute unduh -> null, bukan tautan mati
            "url": url_unduh_lampiran(modul, r["dok_id"], lid) if (tersedia and modul) else None,
        })

    terlihat, tersembunyi = [], {}
    for (jenis, _), g in grup.items():
        g.pop("_kunci_lampiran")
        g["lampiran"].sort(key=lambda x: (x["diunggah_pada"] or "", x["id"]))
        if boleh.get(jenis):
            terlihat.append(g)
        else:
            tersembunyi[jenis] = tersembunyi.get(jenis, 0) + len(g["lampiran"])
    terlihat.sort(key=lambda g: (
        URUTAN_JENIS.index(g["dokumen"]["jenis"]),
        g["dokumen"]["tanggal"] or "",
        g["dokumen"]["nomor"] or "",
    ))
    data = terlihat + [
        {"dokumen": {"jenis": j}, "tersembunyi_izin": True, "jumlah": n}
        for j, n in sorted(tersembunyi.items(), key=lambda x: URUTAN_JENIS.index(x[0]))
    ]
    # Satu berkas bisa tertaut ke >1 dokumen keluarga: tampil di tiap grup, dihitung sekali.
    unik = {lp["id"] for g in terlihat for lp in g["lampiran"]}
    return {"success": True, "data": data, "jumlah": len(unik)}


@router.get("/{order_id}/lampiran")
async def lampiran_keluarga_so(request: Request, order_id: UUID):
    ctx = _ctx(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await _pastikan_so(conn, order_id, tenant_id)
            rows = await baris_keluarga(conn, tenant_id, order_id)
    return await susun_dengan_izin(request, rows)
