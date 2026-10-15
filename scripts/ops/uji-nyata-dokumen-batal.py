"""Harness NYATA DOKUMEN-BATAL-FIELD (15 Okt 2026): untuk SETIAP SO kaos + grapgrap, tiap dokumen bundel /documents:
batal == (share/preview -> DOKUMEN_BATAL). kaos: _bagikan NYATA di savepoint (ROLLBACK; kwitansi uang muka lewat jalur
_dokumen_ada+tolak_batal karena cek izin E butuh request). grapgrap: BACA saja (_dokumen_ada + tolak_batal, nol tulis)."""
import asyncio, os, sys
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from fastapi import HTTPException
from uuid import UUID
from backend.api_gateway.app.routers import dokumen as DK

gagal = 0
def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else "")); gagal += 0 if ok else 1

async def tolak_baca(c, t, kind, uid):
    if kind == "quotation":
        return False
    try:
        DK.tolak_batal(kind, await DK._dokumen_ada(c, t, kind, uid)); return False
    except HTTPException as e:
        return isinstance(e.detail, dict) and e.detail.get("code") == "DOKUMEN_BATAL"

async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        for t, tulis in (("kaos-biru-konveksi", True), ("grapgrap-manado", False)):
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
            ctx = {"tenant_id": t, "user_id": uid}
            tx = c.transaction(readonly=not tulis); await tx.start()
            try:
                n = beda = nbatal = 0
                for s in await c.fetch("SELECT id FROM sales_orders WHERE tenant_id=$1", t):
                    b = await DK.susun_dokumen(c, ctx, s["id"])
                    for gr in b["groups"]:
                        for d in gr["docs"]:
                            n += 1
                            nbatal += d["batal"]
                            k, did = d["kind"], UUID(d["id"])
                            if tulis and k != "receipt":
                                sp = c.transaction(); await sp.start()
                                try:
                                    await DK._bagikan(c, object(), ctx, k, did, "link"); hasil = False
                                except HTTPException as e:
                                    hasil = isinstance(e.detail, dict) and e.detail.get("code") == "DOKUMEN_BATAL"
                                finally:
                                    await sp.rollback()
                            else:
                                hasil = await tolak_baca(c, t, k, did)
                            if hasil != d["batal"]:
                                beda += 1; print("JSON beda", t, k, d.get("number"), d["batal"], hasil)
                cek(beda == 0, f"{t}: {n} dokumen bundel, batal == share DOKUMEN_BATAL", f"batal={nbatal} beda={beda}")
            finally:
                await tx.rollback()
        print("JSON sisa tautan uji:", await c.fetchval("SELECT count(*) FROM document_shares WHERE sent_at > now() - interval '10 minutes'"))
    finally:
        await c.close()
    print("RINGKAS gagal=%d" % gagal)
asyncio.run(main())
