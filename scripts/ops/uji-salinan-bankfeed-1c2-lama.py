"""BF-1c2 sisi LAMA: dijalankan dengan pohon MASTER (handler Uang Masuk/Keluar dan void INLINE, sebelum ekstraksi). Menjalankan OPS+VOIDS deterministik pada rekening kembar
per tenant di salinan DB dan menyimpan tanda tangan jurnal/bank_transactions ke /sig/lama_<tenant>.json. Sisi BARU (uji-salinan-bankfeed-1c2.py, pohon c2) menjalankan operasi
SAMA lalu membandingkan: ekstraksi penulis harus menghasilkan jurnal IDENTIK. Tulis HANYA di salinan."""
import asyncio
import sys

from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/tanda")
import uji_c2_tanda as UT  # noqa: E402
from app.routers import bank_accounts as BAR  # noqa: E402
from app.routers import kasbank_v2 as KV2  # noqa: E402
from app.schemas.bank_accounts import CreateManualTransactionRequest  # noqa: E402
from app.schemas.kasbank_v2 import VoidTransactionRequest  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

TENANT = ("kaos-biru-konveksi", "pt-kita-nyawit-bersama")


def req(tid):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": UT.USER, "username": "uji-bf1c2-lama"}
    return r


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if not db.startswith("scratch_bf1c2"):
        print(f"TOLAK: bukan salinan ({db})")
        return 2
    for tid in TENANT:
        async with pool.acquire() as c:
            rid, coa = await UT.rekening_kembar(c, tid, "8888880001")
        hasil = await UT.jalankan(pool, tid, coa, rid, BAR.create_manual_transaction, KV2.void_transaction, req, Response, CreateManualTransactionRequest, VoidTransactionRequest)
        UT.simpan(f"/sig/lama_{tid}.json", hasil)
        print(f"INFO LAMA {tid}: {len(hasil)} entri tanda tangan disimpan ({len(UT.OPS)} Uang Masuk/Keluar + {len(UT.VOIDS)} void + cermin)", flush=True)
    return 0


sys.exit(asyncio.run(utama()))
