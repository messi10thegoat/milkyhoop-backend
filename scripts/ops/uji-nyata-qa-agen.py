"""UJI NYATA QA-AGEN (25 Okt 2026) -- aksi penawaran LAMA vs BARU + pratinjau == tulis + ulang ber-kunci, ROLLBACK-ONLY di kaos.
argv: <keluaran.jsonl> <lama|baru>. SEMUA penawaran kaos x 5 kasus (kirim, kirim+surel, terima, tolak, hapus); tiap kasus transaksi SENDIRI lalu ROLLBACK."""
import asyncio, json, os, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException, Response
from starlette.requests import Request

T = "kaos-biru-konveksi"
KELUAR, MODE = sys.argv[1], sys.argv[2]
BARU = MODE == "baru"
ABAI = {"updated_at", "search_text"}


def js(v):
    return json.loads(json.dumps(v, default=str))


async def main():
    from app.routers import quotes as Q
    from app.schemas.quotes import DeclineQuoteRequest, SendQuoteRequest
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")
    uid = await c.fetchval("SELECT created_by FROM quotes WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()  # readonly pratinjau -> savepoint di transaksi kasus; NOL tulis dibuktikan snapshot

    class CMA:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class PA:
        def acquire(s): return CMA()
    async def gpa(): return PA()
    Q.get_pool = gpa
    import app.services.db_pool as DBP
    DBP.get_db_pool = gpa

    def req(kunci=None):
        h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
        return r

    async def panggil(f):
        try:
            r = await f()
            return {"ok": js(r.model_dump() if hasattr(r, "model_dump") else r)}
        except HTTPException as e:
            return {"galat": [e.status_code, js(e.detail)]}

    async def snap(qid):
        q = await c.fetchrow("SELECT * FROM quotes WHERE id = $1", qid)
        return js({"q": {k: v for k, v in dict(q).items() if k not in ABAI} if q else None,
                   "riwayat": await c.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2", T, str(qid)),
                   "bagikan": await c.fetchval("SELECT count(*) FROM document_shares WHERE tenant_id = $1 AND doc_id = $2", T, qid)})

    tulis = {
        "kirim": lambda r, i: Q.send_quote(r, i, SendQuoteRequest(send_email=False), Response()) if BARU else Q.send_quote(r, i, SendQuoteRequest(send_email=False)),
        "kirim_surel": lambda r, i: Q.send_quote(r, i, SendQuoteRequest(send_email=True), Response()) if BARU else Q.send_quote(r, i, SendQuoteRequest(send_email=True)),
        "terima": lambda r, i: Q.accept_quote(r, i, Response()) if BARU else Q.accept_quote(r, i),
        "tolak": lambda r, i: Q.decline_quote(r, i, DeclineQuoteRequest(reason="TES E2E QA-AGEN"), Response()) if BARU else Q.decline_quote(r, i, DeclineQuoteRequest(reason="TES E2E QA-AGEN")),
        "hapus": lambda r, i: Q.delete_quote(r, i, Response()) if BARU else Q.delete_quote(r, i),
    }
    pratinjau = {
        "kirim": lambda r, i: Q.preview_send_quote(r, i, SendQuoteRequest(send_email=False)),
        "kirim_surel": lambda r, i: Q.preview_send_quote(r, i, SendQuoteRequest(send_email=True)),
        "terima": lambda r, i: Q.preview_accept_quote(r, i),
        "tolak": lambda r, i: Q.preview_decline_quote(r, i, DeclineQuoteRequest(reason="TES E2E QA-AGEN")),
        "hapus": lambda r, i: Q.preview_delete_quote(r, i),
    }
    quotes = await c.fetch("SELECT id, quote_number, status FROM quotes WHERE tenant_id = $1 ORDER BY quote_number", T)
    n = 0
    with open(KELUAR, "w") as f:
        for qu in quotes:
            qid = str(qu["id"])
            for kasus in tulis:
                catat = {"q": qu["quote_number"], "status_awal": qu["status"], "kasus": kasus}
                tx = c.transaction(); await tx.start()
                try:
                    sebelum = await snap(qu["id"])
                    if BARU:
                        catat["pratinjau"] = await panggil(lambda: pratinjau[kasus](req(), qid))
                        catat["pratinjau_tanpa_tulis"] = (await snap(qu["id"])) == sebelum
                    k = f"qa-{uuid.uuid4().hex}"
                    catat["respons"] = await panggil(lambda: tulis[kasus](req(k), qid))
                    catat["sesudah"] = await snap(qu["id"])
                    catat["riwayat_baru"] = catat["sesudah"]["riwayat"] - sebelum["riwayat"]
                    if BARU:
                        catat["ulang"] = await panggil(lambda: tulis[kasus](req(k), qid))
                        catat["sesudah_ulang"] = await snap(qu["id"])
                finally:
                    await tx.rollback()
                f.write(json.dumps(catat, sort_keys=True) + "\n"); n += 1
    print(f"SELESAI kasus={n} penawaran={len(quotes)}")
    await c.close()


asyncio.run(main())
