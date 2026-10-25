"""UJI NYATA GABUNG-VENDOR (25 Okt 2026) -- ROLLBACK-ONLY di kaos. argv: <keluaran.jsonl> <lama|baru>
SEMUA pasangan berurut (sumber, tujuan) vendor kaos AKTIF; tiap pasangan transaksi SENDIRI lalu ROLLBACK. DUA SUMBER independen per kasus:
 (1) compute_ap_outstanding per vendor (subledger), (2) GL: jumlah debit/kredit journal_lines POSTED per akun tenant + jumlah jurnal.
Mode baru: pratinjau (READ ONLY) dulu -> NOL tulis; lalu POST /merge; ulang dengan kunci sama -> jawaban pertama.
Mode lama: catat respons POST /merge saja (dulu 500 kolom hantu / kini 409 parkir)."""
import asyncio, json, os, sys, uuid
from decimal import Decimal
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
    from app.schemas.vendors import MergeVendorRequest
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")
    uid = await c.fetchval("SELECT created_by FROM vendors WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()  # readonly pratinjau -> savepoint; NOL tulis dibuktikan snapshot

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

    async def panggil(f):
        try:
            r = await f()
            return {"ok": js(r.model_dump() if hasattr(r, "model_dump") else r)}
        except HTTPException as e:
            return {"galat": [e.status_code, js(e.detail)]}

    async def ap():
        rows = await c.fetch("SELECT vendor_id, SUM(outstanding) AS o FROM compute_ap_outstanding($1) GROUP BY vendor_id", T)
        return {str(r["vendor_id"]): str(Decimal(r["o"]).quantize(Decimal("0.01"))) for r in rows if r["o"]}

    async def gl():
        rows = await c.fetch("""SELECT jl.account_id, SUM(jl.debit) AS d, SUM(jl.credit) AS k FROM journal_lines jl
                                JOIN journal_entries je ON je.id = jl.journal_id WHERE je.tenant_id = $1 AND je.status = 'POSTED'
                                GROUP BY jl.account_id ORDER BY 1""", T)
        return {"akun": {str(r["account_id"]): [str(r["d"]), str(r["k"])] for r in rows},
                "jurnal": await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", T)}

    async def tulis_snap():
        return {"ap": await ap(), "gl": await gl(),
                "vendor": js([dict(r) for r in await c.fetch("SELECT id, is_active, notes FROM vendors WHERE tenant_id = $1 ORDER BY id", T)]),
                "jejak": await c.fetchval("""SELECT (SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND "eventType" = 'VENDOR_MERGED')
                                             + (SELECT count(*) FROM vendor_activities WHERE tenant_id = $1 AND type = 'merged')""", T)}

    merge = (lambda r, b: V.merge_vendors(r, b, response=Response())) if BARU else V.merge_vendors
    aktif = await c.fetch("SELECT id, name FROM vendors WHERE tenant_id = $1 AND is_active ORDER BY name, id", T)
    n = 0
    with open(KELUAR, "w") as out:
        for s in aktif:
            for t in aktif:
                if s["id"] == t["id"]:
                    continue
                body = MergeVendorRequest(source_vendor_id=str(s["id"]), target_vendor_id=str(t["id"]))
                catat = {"sumber": s["name"], "tujuan": t["name"]}
                tx = c.transaction(); await tx.start()
                try:
                    sebelum = await tulis_snap()
                    catat["ap_sebelum"] = {k: sebelum["ap"].get(k, "0.00") for k in (str(s["id"]), str(t["id"]))}
                    catat["ap_total_sebelum"] = str(sum(Decimal(v) for v in sebelum["ap"].values()))
                    if BARU:
                        catat["pratinjau"] = await panggil(lambda: V.preview_merge_vendors(req(), body))
                        catat["pratinjau_tanpa_tulis"] = (await tulis_snap()) == sebelum
                    k = f"gv-{uuid.uuid4().hex}"
                    catat["respons"] = await panggil(lambda: merge(req(k), body))
                    sesudah = await tulis_snap()
                    catat["ap_sesudah"] = {k2: sesudah["ap"].get(k2, "0.00") for k2 in (str(s["id"]), str(t["id"]))}
                    catat["ap_total_sesudah"] = str(sum(Decimal(v) for v in sesudah["ap"].values()))
                    catat["ap_lain_sama"] = {k2: v for k2, v in sesudah["ap"].items() if k2 not in (str(s["id"]), str(t["id"]))} == \
                                            {k2: v for k2, v in sebelum["ap"].items() if k2 not in (str(s["id"]), str(t["id"]))}
                    catat["gl_sama"] = sesudah["gl"] == sebelum["gl"]
                    catat["jejak_baru"] = sesudah["jejak"] - sebelum["jejak"]
                    catat["sumber_aktif_sesudah"] = await c.fetchval("SELECT is_active FROM vendors WHERE id = $1", s["id"])
                    catat["sisa_di_sumber"] = await c.fetchval("SELECT count(*) FROM bills WHERE tenant_id = $1 AND vendor_id = $2", T, s["id"])
                    if BARU:
                        catat["ulang"] = await panggil(lambda: merge(req(k), body))
                        catat["ulang_tanpa_tulis"] = (await tulis_snap()) == sesudah
                finally:
                    await tx.rollback()
                out.write(json.dumps(catat, sort_keys=True) + "\n"); n += 1
    print(f"SELESAI kasus={n} vendor_aktif={len(aktif)}")
    await c.close()


asyncio.run(main())
