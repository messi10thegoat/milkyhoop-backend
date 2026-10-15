"""Harness NYATA BAGIKAN-BATAL (15 Okt 2026): kaos = _bagikan nyata di tx-ROLLBACK (batal -> 409 DOKUMEN_BATAL, aktif -> tautan
dibuat lalu dibatalkan); grapgrap = BACA saja (_dokumen_ada + tolak_batal, tanpa tulis). Juga hitung tautan publik AKTIF yang sudah
ada untuk dokumen batal (informasi; tak diubah)."""
import asyncio, os, sys
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from fastapi import HTTPException
from backend.api_gateway.app.routers import dokumen as DK

SQL = {"invoice": ("sales_invoices", "void"), "delivery": ("invoice_fulfillments", "voided"), "credit_note": ("credit_notes", "void"),
       "proforma": ("proformas", "cancelled"), "rekap": ("sales_orders", "cancelled"), "receipt": ("customer_deposits", "void")}
gagal = 0
def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else "")); gagal += 0 if ok else 1

async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', "kaos-biru-konveksi")
        ctx = {"tenant_id": "kaos-biru-konveksi", "user_id": uid}
        tx = c.transaction(); await tx.start()
        try:
            # kwitansi uang muka memeriksa izin E lewat request nyata -> di tx kaos pakai kwitansi PENERIMAAN batal (tanpa cek itu)
            for kind, (tabel, st) in {**SQL, "receipt": ("receive_payments", "voided")}.items():
                v = await c.fetchval(f"SELECT id FROM {tabel} WHERE tenant_id=$1 AND status=$2 LIMIT 1", ctx["tenant_id"], st)
                if v is None:
                    print("JSON kaos tak ada", kind, st); continue
                try:
                    await DK._bagikan(c, object(), ctx, kind, v, "link"); cek(False, f"kaos {kind} {st} ditolak")
                except HTTPException as e:
                    cek(e.status_code == 409 and e.detail.get("code") == "DOKUMEN_BATAL", f"kaos {kind} {st} -> DOKUMEN_BATAL", e.detail.get("message"))
            a = await c.fetchval("SELECT id FROM sales_invoices WHERE tenant_id=$1 AND status IN ('posted','paid') LIMIT 1", ctx["tenant_id"])
            h, info = await DK._bagikan(c, object(), ctx, "invoice", a, "link")
            cek(info["boleh"] and h["id"], "kaos kontrol faktur aktif -> tautan dibuat (lalu ROLLBACK)")
        finally:
            await tx.rollback()
        await c.execute("BEGIN READ ONLY")
        try:
            for kind, (tabel, st) in SQL.items():
                rows = await c.fetch(f"SELECT id FROM {tabel} WHERE tenant_id='grapgrap-manado' AND status=$1", st)
                ok = 0
                for r in rows:
                    dok = await DK._dokumen_ada(c, "grapgrap-manado", kind, r["id"])
                    try:
                        DK.tolak_batal(kind, dok)
                    except HTTPException as e:
                        ok += e.detail["code"] == "DOKUMEN_BATAL"
                cek(ok == len(rows), f"grapgrap {kind}: {len(rows)} dokumen {st} semuanya akan ditolak")
            n = await c.fetch("""SELECT ds.tenant_id, ds.kind, count(*) FROM document_shares ds
                WHERE ds.revoked_at IS NULL AND ds.expires_at > now() AND ds.tenant_id IN ('grapgrap-manado','kaos-biru-konveksi')
                  AND ((ds.kind='invoice' AND EXISTS (SELECT 1 FROM sales_invoices x WHERE x.id=ds.doc_id AND x.status='void'))
                    OR (ds.kind='delivery' AND EXISTS (SELECT 1 FROM invoice_fulfillments x WHERE x.id=ds.doc_id AND x.status='voided'))
                    OR (ds.kind='proforma' AND EXISTS (SELECT 1 FROM proformas x WHERE x.id=ds.doc_id AND x.status='cancelled'))
                    OR (ds.kind='receipt' AND EXISTS (SELECT 1 FROM customer_deposits x WHERE x.id=ds.doc_id AND x.status='void')))
                GROUP BY 1,2""")
            print("JSON tautan publik aktif untuk dokumen batal:", [tuple(r) for r in n])
        finally:
            await c.execute("ROLLBACK")
        print("JSON sisa tautan uji:", await c.fetchval("SELECT count(*) FROM document_shares WHERE sent_at > now() - interval '5 minutes' AND tenant_id='kaos-biru-konveksi'"))
    finally:
        await c.close()
    print("RINGKAS gagal=%d" % gagal)
asyncio.run(main())
