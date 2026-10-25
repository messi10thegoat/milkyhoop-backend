"""UJI NYATA VENDOR-IDEM (25 Okt 2026) -- buat/status/hapus vendor LAMA vs BARU + ulang ber-kunci, ROLLBACK-ONLY di kaos. argv: <keluaran.jsonl> <lama|baru>
(1) SEMUA vendor kaos x {status nonaktif, hapus} tanpa kunci: respons + keadaan WAJIB identik LAMA vs BARU.
(2) vendor TES E2E dibuat di transaksi: buat/status/hapus masing-masing DUA kali dengan kunci sama -> catat kedua jawaban + keadaan.
Keadaan = kolom bisnis (name, code, is_active) + jumlah vendor_activities + ada/tidaknya baris; id vendor baru diganti token <BARU>."""
import asyncio, json, os, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException, Response
from starlette.requests import Request

T = "kaos-biru-konveksi"
KELUAR, MODE = sys.argv[1], sys.argv[2]
BARU = MODE == "baru"


def js(v):
    return json.loads(json.dumps(v, default=str))


async def main():
    from app.routers import vendors as V
    from app.schemas.vendors import CreateVendorRequest
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")
    uid = await c.fetchval("SELECT created_by FROM vendors WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    class Kon:
        def __getattr__(s, n): return getattr(c, n)

    class CMA:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class PA:
        def acquire(s): return CMA()
    async def gpa(): return PA()
    V.get_pool = gpa
    import app.services.db_pool as DBP
    DBP.get_db_pool = gpa

    def req(kunci=None):
        h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
        return r

    tok = {}
    def norm(v):
        s = json.dumps(js(v), sort_keys=True)
        for asli, t in tok.items():
            s = s.replace(asli, t)
        return json.loads(s)

    async def panggil(f):
        try:
            r = await f()
            return norm({"ok": r.model_dump() if hasattr(r, "model_dump") else r})
        except HTTPException as e:
            return norm({"galat": [e.status_code, e.detail]})

    async def snap(vid):
        v = await c.fetchrow("SELECT name, code, is_active FROM vendors WHERE id = $1", vid)
        return norm({"v": dict(v) if v else None,
                     "aktivitas": await c.fetchval("SELECT count(*) FROM vendor_activities WHERE vendor_id = $1", vid)})

    def T_(f, kw):  # rute BARU menerima response=; LAMA tidak
        return (lambda r, *a: f(r, *a, response=Response())) if BARU else f

    status = T_(V.toggle_vendor_status, None)
    hapus = T_(V.delete_vendor, None)
    buat = T_(V.create_vendor, None)
    n = 0
    with open(KELUAR, "w") as out:
        for v in await c.fetch("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY name, id", T):
            for kasus, f in (("status", lambda: status(req(), v["id"], "inactive")), ("hapus", lambda: hapus(req(), v["id"]))):
                tx = c.transaction(); await tx.start()
                try:
                    catat = {"v": v["name"], "kasus": kasus, "respons": await panggil(f), "sesudah": await snap(v["id"])}
                finally:
                    await tx.rollback()
                out.write(json.dumps(catat, sort_keys=True) + "\n"); n += 1
        tx = c.transaction(); await tx.start()
        try:
            s = uuid.uuid4().hex[:8]
            body = CreateVendorRequest(name=f"TES E2E Vendor idem {s}", code=f"TESIDEM{s}")
            tok[s] = "<S>"
            k = f"vi-{uuid.uuid4().hex}"
            r1 = await buat(req(k), body)
            vid = r1["data"]["id"]; tok[vid] = "<BARU>"
            rec = {"v": "<TES>", "kasus": "ulang", "buat_1": norm({"ok": r1}), "buat_2": await panggil(lambda: buat(req(k), body)),
                   "jumlah_nama": await c.fetchval("SELECT count(*) FROM vendors WHERE tenant_id = $1 AND name = $2", T, body.name)}
            k2 = f"vi-{uuid.uuid4().hex}"
            rec["status_1"] = await panggil(lambda: status(req(k2), uuid.UUID(vid), "inactive"))
            rec["status_2"] = await panggil(lambda: status(req(k2), uuid.UUID(vid), "inactive"))
            rec["sesudah_status"] = await snap(uuid.UUID(vid))
            k3 = f"vi-{uuid.uuid4().hex}"
            rec["hapus_1"] = await panggil(lambda: hapus(req(k3), uuid.UUID(vid)))
            rec["hapus_2"] = await panggil(lambda: hapus(req(k3), uuid.UUID(vid)))
            rec["sesudah_hapus"] = await snap(uuid.UUID(vid))
        finally:
            await tx.rollback()
        rec["sisa_sesudah_rollback"] = await c.fetchval("SELECT count(*) FROM vendors WHERE tenant_id = $1 AND name LIKE 'TES E2E Vendor idem %'", T)
        out.write(json.dumps(rec, sort_keys=True) + "\n"); n += 1
    print(f"SELESAI kasus={n}")
    await c.close()


asyncio.run(main())
