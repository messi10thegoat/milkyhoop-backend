#!/usr/bin/env python3
"""Gerbang GANTI NOMOR FAKTUR DRAFT (tiket A).

⚠️ CAKUPAN YANG JUJUR — baca sebelum percaya hijaunya.
Gerbang ini memanggil fungsi handler `update_invoice` LANGSUNG, bukan lewat
HTTP. Yang TIDAK diuji karenanya: lapis izin (dekorator/middleware) dan
serialisasi FastAPI. Yang DIUJI: skema Pydantic, pagar draft/posted, pembangun
UPDATE dinamis, pagar salinan nomor, dan pemetaan nomor kembar ke 409 --
yakni seluruh bagian yang rusak.

Kenapa bukan HTTP: akun uji berperan Collaborator dan PATCH faktur dijawab
403 PERMISSION_DENIED (diukur 6 Sep 2026, kelima kalinya batas ini
menghalangi). Menaikkan perannya adalah keputusan pemilik, dan menempa JWT
sendiri adalah pengganti token -- keduanya tidak kulakukan.

Seluruh tulisan dibungkus SATU transaksi yang selalu ROLLBACK: pembukuan
pemilik tidak berubah sedikit pun, dan tak ada baris audit penghapusan.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend/api_gateway")

import asyncpg  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import sales_invoices as R  # noqa: E402
from app.schemas.sales_invoices import UpdateInvoiceRequest  # noqa: E402

TENANT = "kaos-biru-konveksi"
gagal = []


def cek(nama, syarat, catatan=""):
    print(f"  {'OK  ' if syarat else 'GAGAL'} {nama}{(' — ' + catatan) if catatan else ''}")
    if not syarat:
        gagal.append(nama)


class PoolPalsu:
    """Menyodorkan SATU koneksi yang sudah ada di dalam transaksi kami."""

    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _Ctx()


async def buat_draft(conn, nomor):
    iid = uuid.uuid4()
    # created_by NOT NULL: dipinjam dari faktur yang sudah ada di tenant ini,
    # bukan uuid karangan -- ada FK ke "User".
    pembuat = await conn.fetchval(
        "SELECT created_by FROM sales_invoices WHERE tenant_id=$1 "
        "AND created_by IS NOT NULL LIMIT 1", TENANT)
    await conn.execute(
        """
        INSERT INTO sales_invoices (
            id, tenant_id, invoice_number, customer_name, invoice_date,
            due_date, subtotal, tax_amount, total_amount, status, created_by
        ) VALUES ($1, $2, $3, 'PELANGGAN UJI GERBANG', CURRENT_DATE,
                  CURRENT_DATE, 0, 0, 0, 'draft', $4)
        """,
        iid, TENANT, nomor, pembuat,
    )
    return iid


async def panggil(iid, **medan):
    """Panggil handler dan kembalikan (kode_http, hasil). 200 bila lolos."""
    try:
        hasil = await R.update_invoice(
            request=None, invoice_id=iid, body=UpdateInvoiceRequest(**medan)
        )
        return 200, hasil
    except HTTPException as e:
        return e.status_code, e.detail


async def nomor_di_db(conn, iid):
    return await conn.fetchval("SELECT invoice_number FROM sales_invoices WHERE id=$1", iid)


async def utama():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = conn.transaction()
    await tx.start()
    R.get_pool = lambda: asyncio.sleep(0, result=PoolPalsu(conn))
    R.get_user_context = lambda _req: {"tenant_id": TENANT, "user_id": str(uuid.uuid4())}
    try:
        A = await buat_draft(conn, "UJI-GERBANG-A")
        B = await buat_draft(conn, "UJI-GERBANG-B")

        # 1. INTI TIKET: nomor draft benar-benar berubah di BASIS DATA.
        kode, _ = await panggil(A, invoice_number="UJI-GERBANG-BARU")
        sesudah = await nomor_di_db(conn, A)
        cek("ganti nomor draft -> tersimpan", kode == 200 and sesudah == "UJI-GERBANG-BARU",
            f"kode={kode} nomor={sesudah!r}")

        # 2. KONTROL POSITIF: medan lain tetap bisa diubah bersamaan.
        kode, _ = await panggil(A, invoice_number="UJI-GERBANG-C", ref_no="REF-UJI")
        ref = await conn.fetchval("SELECT ref_no FROM sales_invoices WHERE id=$1", A)
        cek("medan lain tetap bisa diubah", kode == 200 and ref == "REF-UJI",
            f"kode={kode} ref_no={ref!r}")

        # 3. Nomor KEMBAR -> 409, bukan 500.
        kode, pesan = await panggil(B, invoice_number="UJI-GERBANG-C")
        cek("nomor kembar -> 409", kode == 409, f"kode={kode} {str(pesan)[:70]}")

        # 4. Nomor KOSONG ditolak di skema (kolomnya NOT NULL).
        try:
            UpdateInvoiceRequest(invoice_number="   ")
            cek("nomor kosong ditolak", False, "diterima")
        except Exception as e:
            cek("nomor kosong ditolak", "tidak boleh kosong" in str(e), type(e).__name__)

        # 5. Faktur POSTED tak bisa ganti nomor (jejak audit).
        await conn.execute("UPDATE sales_invoices SET status='paid' WHERE id=$1", B)
        kode, pesan = await panggil(B, invoice_number="UJI-GERBANG-D")
        nomor_b = await nomor_di_db(conn, B)
        cek("faktur non-draft ditolak", kode == 400 and nomor_b == "UJI-GERBANG-B",
            f"kode={kode} nomor={nomor_b!r}")

        # 6. KONTROL MERAH: tanpa medan di skema, nilai TAK PERNAH sampai ke DB.
        #    Inilah cacat aslinya. Kalau kontrol ini gagal, gerbang di atas
        #    hijau karena alasan lain dan tidak membuktikan perbaikan ini.
        buang = UpdateInvoiceRequest(**{"medan_karangan": "X"}).model_dump(exclude_unset=True)
        cek("kontrol merah: kunci tak dikenal memang DIBUANG diam-diam",
            "medan_karangan" not in buang, f"model_dump={buang}")
    finally:
        await tx.rollback()
        n = await conn.fetchval(
            "SELECT count(*) FROM sales_invoices WHERE invoice_number LIKE 'UJI-GERBANG%'")
        print(f"\n  rollback selesai — sisa baris uji di DB: {n} (harus 0)")
        if n:
            gagal.append("ROLLBACK GAGAL: baris uji tertinggal")
        await conn.close()

    if gagal:
        print("\nGAGAL:")
        for g in gagal:
            print("  - " + g)
        return 1
    print("\nHIJAU: nomor faktur draft bisa diubah, dan penjaganya bekerja.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(utama()))
