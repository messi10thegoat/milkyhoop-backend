"""Biaya B6 uji di SALINAN (baca saja), handler ASLI lewat response_model seperti FastAPI: GET /expenses/{id}/documents untuk SETIAP biaya
tenant uji. Pohon LAMA dan BARU menulis bundel ke KELUAR; banding di luar: BARU == LAMA kecuali (a) anchor.vendor == expenses.vendor_name
atau "" (orakel: kolom DB, dibaca terpisah), (b) setiap dok paper "A4" (lama None). Tak ada perbedaan lain yang boleh."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import expenses as EX  # noqa: E402
from app.schemas import expenses as S  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
OUT = {"bundel": {}, "vendor_db": {}}


def req(tid):
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "role": "OWNER"}))


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = [(tid, r["id"], r["vendor_name"]) for tid in TENANT for r in await conn.fetch(
            "SELECT id, vendor_name FROM expenses WHERE tenant_id = $1 ORDER BY id", tid)]
    for tid, eid, vn in rows:
        h = await EX.get_expense_documents(req(tid), eid)
        OUT["bundel"][f"{tid}|{eid}"] = S.ExpenseDocumentsResponse.model_validate(h).model_dump(mode="json")["data"]
        OUT["vendor_db"][f"{tid}|{eid}"] = vn or ""
    print(f"bundel {len(OUT['bundel'])} biaya")


asyncio.run(utama())
json.dump(OUT, open(os.environ["KELUAR"], "w"), default=str)
