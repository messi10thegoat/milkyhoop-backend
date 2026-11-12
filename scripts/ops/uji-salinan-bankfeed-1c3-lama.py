"""BF-1c3 sisi LAMA: dijalankan dengan pohon sebelum ekstraksi (handler Transfer dan Uang Muka INLINE). Menjalankan operasi deterministik (uji_c3_tanda.jalankan) pada rekening kembar
per tenant di salinan DB, menyimpan tanda tangan ke /sig/lama_<tenant>.json. Sisi BARU (uji-salinan-bankfeed-1c3.py) menjalankan SAMA lalu membandingkan: HARUS identik. Tulis HANYA di salinan."""
import asyncio
import sys

from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/tanda")
import uji_c3_tanda as UT  # noqa: E402
from app.routers import bank_transfers as BTR  # noqa: E402
from app.routers import customer_deposits as CD  # noqa: E402
from app.schemas.bank_transfers import CreateBankTransferRequest, VoidBankTransferRequest  # noqa: E402
from app.schemas.customer_deposits import VoidCustomerDepositRequest  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

TENANT = ("kaos-biru-konveksi", "pt-kita-nyawit-bersama")


def req(tid):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": UT.USER, "username": "uji-bf1c3-lama"}
    return r


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_bf1c3"):
        print(f"TOLAK: bukan salinan ({db})")
        return 2
    for tid in TENANT:
        hasil = await UT.jalankan(pool, tid, req, Response, BTR, CD, CreateBankTransferRequest, VoidBankTransferRequest, CD.CreateCustomerDepositRequest, VoidCustomerDepositRequest)
        UT.simpan(f"/sig/lama_{tid}.json", hasil)
        print(f"INFO LAMA {tid}: {len(hasil)} entri tanda tangan disimpan (2 transfer + void + uang muka + void)", flush=True)
    return 0


sys.exit(asyncio.run(utama()))
