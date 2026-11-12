"""BF-1d sisi LAMA: pohon sebelum ekstraksi E4/E5 (handler pembayaran tagihan INLINE; = c3 @c7b83d28f). Menjalankan uji_1d_tanda.jalankan per tenant di salinan DB dan menyimpan tanda tangan
ke /sig/lama_<tenant>.json; sisi BARU membandingkan. Tulis HANYA di salinan."""
import asyncio
import sys

from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/tanda")
import uji_1d_tanda as UT  # noqa: E402
import uji_c3_tanda as C3  # noqa: E402
from app.routers import bill_payments as BP  # noqa: E402
from app.schemas.bill_payments import BillAllocationInput, CreateBillPaymentRequest, VoidBillPaymentRequest  # noqa: E402
from app.services import bayar_keluar_rencana as BKR  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

TENANT = ("kaos-biru-konveksi", "pt-kita-nyawit-bersama")


def req(tid):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": C3.USER, "username": "uji-bf1d-lama"}
    return r


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_bf1d"):
        print(f"TOLAK: bukan salinan ({db})")
        return 2
    for tid in TENANT:
        hasil = await UT.jalankan(pool, tid, req, Response, BP, BKR, CreateBillPaymentRequest, BillAllocationInput, VoidBillPaymentRequest)
        C3.simpan(f"/sig/lama_{tid}.json", hasil)
        print(f"INFO LAMA {tid}: {len(hasil)} entri tanda tangan disimpan", flush=True)
    return 0


sys.exit(asyncio.run(utama()))
