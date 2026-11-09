"""VOID-NULL -- uji di SALINAN: SATU dump, DUA klon, handler ASLI pohon LAMA (master) dan BARU. Semua tagihan kaos+nyawit:
 POST /bills/{id}/void (penulis, transaksi LUAR di-ROLLBACK), POST /bills/{id}/void/preview (READ ONLY), [baru] aksi_tersedia fpbatal.
Banding: status_v2 NULL ATAU jurnal sudah dibalik (bukan void) -> lama 500, baru 400 dgn kalimat == blok pratinjau == alasan aksi_tersedia; lainnya lama == baru PERSIS."""
import asyncio
import json
import os
import sys

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bills as BL  # noqa: E402
from app.schemas.bills import VoidBillRequest  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
ASLI = {n: getattr(BL, n) for n in ("get_pool",)}


def req(tid):
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-void-null", "role": "OWNER"}
    return r


def j(v):
    return json.loads(json.dumps(v, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


async def coba(f):
    try:
        return {"ok": True, "hasil": j(await f)}
    except HTTPException as e:
        return {"ok": False, "status": e.status_code, "detail": j(e.detail)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": 500, "detail": repr(e)[:300]}


class Kolam:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class Cm:
            async def __aenter__(self):
                return c

            async def __aexit__(self, *e):
                return False
        return Cm()

    def __getattr__(self, n):
        return getattr(self.c, n)


async def main():
    from app.services import lampiran_milik as LM

    async def hapus_tiruan(paths, konteks):
        return {"dihapus": 0, "gagal": []}
    LM.hapus_objek_sesudah_commit = hapus_tiruan
    pool = await ASLI["get_pool"]()
    out = {}
    async with pool.acquire() as conn:
        for tid in TENANT:
            rows = await conn.fetch(
                "SELECT b.id, b.invoice_number, b.status, b.status_v2, (je.reversed_by_id IS NOT NULL) AS dibalik FROM bills b "
                "LEFT JOIN journal_entries je ON je.id = b.journal_id AND je.tenant_id = b.tenant_id WHERE b.tenant_id = $1 ORDER BY b.invoice_number", tid)
            print(f"[{tid}] tagihan {len(rows)}; status_v2 NULL {sum(1 for r in rows if r['status_v2'] is None)}", flush=True)
            for b in rows:
                k = f"{tid}:{b['invoice_number']}:{b['status']}:{b['status_v2']}"
                tr = conn.transaction()
                await tr.start()

                async def palsu(*a, **kw):
                    return Kolam(conn)
                BL.get_pool = palsu
                try:
                    tulis = await coba(BL.void_bill(req(tid), b["id"], VoidBillRequest(reason="UJI void-null"), Response()))
                finally:
                    BL.get_pool = ASLI["get_pool"]
                    await tr.rollback()
                pv = await coba(BL.preview_void_bill(req(tid), b["id"], None))
                at = None
                if hasattr(BL, "aksi_tersedia_fpb"):
                    at = (await BL.aksi_tersedia_fpb(conn, {"tenant_id": tid, "user_id": None}, b["id"]))["fpbatal"]
                blok = ((pv.get("hasil") or {}).get("data") or {}).get("blocks") if pv["ok"] else None
                out[k] = {"null": b["status_v2"] is None or (b["dibalik"] and (b["status_v2"] or "") != "void"),
                          "tulis": {"ok": tulis["ok"], "status": tulis.get("status"), "detail": tulis.get("detail")} if not tulis["ok"]
                          else {"ok": True}, "pratinjau": blok, "aksi": at}
    with open(os.environ["KELUAR"], "w") as fo:
        json.dump(out, fo, default=str)
    print(f"[selesai] {len(out)} tagihan", flush=True)


asyncio.run(main())
