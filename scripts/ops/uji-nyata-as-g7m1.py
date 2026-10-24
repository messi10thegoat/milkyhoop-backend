"""UJI NYATA AS-G7-M1 (24 Okt 2026): pratinjau massal hapus SO / hapus penawaran / batal proforma BACA SAJA. kaos SAJA, NOL COMMIT.
PRATINJAU (pohon LAMA = inti penulis per item di transaksi luar ROLLBACK; BARU = penentu baca saja, transaksi luar READ ONLY):
  /sales-orders/bulk/delete/preview, /quotes/bulk/delete/preview, /proformas/bulk/cancel/preview atas <=50 dokumen kaos (semua status:
  draf yang lolos + bukan draf / berdokumen turunan yang diblok) + id acak. BANDING: LAMA == BARU di SEMUA medan (nol pengecualian).
KUNCI: kunci penulis dipegang koneksi A (FOR UPDATE baris SO draf / kunci QUOTE + FOR UPDATE penawaran / kunci PROFORMA_SO) -> pratinjau
  massal di B: BARU < 1 dtk, LAMA menunggu >= 1,5 dtk (kontrol). K-tulis: penulis batal-draf proforma (_blok_batal kunci=True) di bawah
  kunci PROFORMA_SO MENUNGGU (LAMA == BARU). JEPRET sebelum/sesudah: net 0."""
import asyncio, json, os, sys, time, uuid
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


SQL_JEPRET = {
    "so": "SELECT count(*)::text || '/' || md5(COALESCE(string_agg(id::text || status, ',' ORDER BY id), '')) FROM sales_orders WHERE tenant_id = $1",
    "quote": "SELECT count(*)::text || '/' || md5(COALESCE(string_agg(id::text || status, ',' ORDER BY id), '')) FROM quotes WHERE tenant_id = $1",
    "proforma": "SELECT count(*)::text || '/' || md5(COALESCE(string_agg(id::text || status, ',' ORDER BY id), '')) FROM proformas WHERE tenant_id = $1",
    "audit": "SELECT count(*) FROM audit_logs WHERE tenant_id = $1",
}


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid), "id": str(uid), "role": "OWNER"}; return r


def pool_untuk(conn):
    class CM:
        async def __aenter__(s): return conn
        async def __aexit__(s, *a): return False
    class Pool:
        def acquire(s): return CM()
    async def gp(): return Pool()
    return gp


def rapi(d):
    return json.loads(json.dumps(d, default=str))


async def kasus(c):
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    acak = str(uuid.uuid4())

    async def ids(q):
        return [str(r["id"]) for r in await c.fetch(q, T)]
    return uid, {
        "so": await ids("SELECT id FROM sales_orders WHERE tenant_id = $1 ORDER BY (status = 'draft') DESC, created_at DESC, id LIMIT 49") + [acak],
        "quote": await ids("SELECT id FROM quotes WHERE tenant_id = $1 ORDER BY (status = 'draft') DESC, created_at DESC, id LIMIT 49") + [acak],
        "proforma": await ids("SELECT id FROM proformas WHERE tenant_id = $1 ORDER BY (status = 'draft') DESC, created_at DESC, id LIMIT 49") + [acak],
    }


def rute(mod, nama):
    return next(r.endpoint for r in mod.router.routes if r.endpoint.__name__ == nama)


async def pratinjau_semua(cx, uid, k):
    from app.routers import bulk_so as BS, bulk_quote as BQ, bulk_proforma as BP
    BS.get_pool = BQ.get_pool = BP.get_pool = pool_untuk(cx)
    return {
        "so": (await rute(BS, "bulk_delete_preview")(req(uid), BS.BulkAksiRequest(ids=k["so"])))["data"],
        "quote": (await rute(BQ, "bulk_delete_preview")(req(uid), BQ.BulkAksiRequest(ids=k["quote"])))["data"],
        "proforma": (await BP.bulk_cancel_preview(req(uid), BP.BulkBatalRequest(ids=k["proforma"], reason="TES E2E AS-G7-M1")))["data"],
    }


async def pratinjau():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    if NAMA == "lama" or not os.path.exists("/out/kasus.json"):
        uid, k = await kasus(c)
        json.dump({"uid": str(uid), "k": k}, open("/out/kasus.json", "w"))
    d = json.load(open("/out/kasus.json"))
    out = rapi(await pratinjau_semua(c, d["uid"], d["k"]))
    print(f"REKAM {NAMA} " + ", ".join(f"{a}: {v['total']} (lolos {v['ok_count']})" for a, v in out.items()), flush=True)
    await c.close()
    json.dump(out, open(f"/out/p-{NAMA}.json", "w"))


async def kunci():
    from app.routers import bulk_so as BS, bulk_quote as BQ, bulk_proforma as BP
    d = json.load(open("/out/kasus.json"))
    lama = json.load(open("/out/p-lama.json"))
    uid, k = d["uid"], d["k"]
    ctx = {"tenant_id": T, "user_id": uid}

    def lolos(a):
        return next((x["id"] for x in lama[a]["items"] if x["ok"]), None)

    async def ronde(pegang, tantang, jeda=1.5):
        ca, cb = await asyncpg.connect(os.environ["DATABASE_URL"]), await asyncpg.connect(os.environ["DATABASE_URL"])
        ta = ca.transaction(); await ta.start()
        try:
            await pegang(ca)
            t0, selesai = time.monotonic(), {}

            async def catat():
                try:
                    r = await tantang(cb)
                except Exception as e:  # noqa: BLE001
                    r = f"GALAT {type(e).__name__}: {str(e)[:120]}"
                selesai["t"] = time.monotonic()
                return r
            tugas = asyncio.ensure_future(catat())
            await asyncio.sleep(jeda)
            menunggu = not tugas.done()
            await ta.rollback(); ta = None
            r = await asyncio.wait_for(tugas, 30)
            return {"menunggu": menunggu, "detik": round(selesai["t"] - t0, 2), "hasil": str(r)[:80]}
        finally:
            if ta is not None: await ta.rollback()
            await ca.close(); await cb.close()

    out = {}
    so, q, pf = lolos("so"), lolos("quote"), lolos("proforma")
    if so:
        async def kunci_so(ca):
            await ca.execute("SELECT 1 FROM sales_orders WHERE id = $1 FOR UPDATE", uuid.UUID(so))

        async def pv_so(cb):
            BS.get_pool = pool_untuk(cb)
            return (await rute(BS, "bulk_delete_preview")(req(uid), BS.BulkAksiRequest(ids=[so])))["data"]["ok_count"]
        out["K_so"] = await ronde(kunci_so, pv_so)
    if q:
        async def kunci_q(ca):
            await ca.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:{T}:{q}")
            await ca.execute("SELECT 1 FROM quotes WHERE id = $1 FOR UPDATE", uuid.UUID(q))

        async def pv_q(cb):
            BQ.get_pool = pool_untuk(cb)
            return (await rute(BQ, "bulk_delete_preview")(req(uid), BQ.BulkAksiRequest(ids=[q])))["data"]["ok_count"]
        out["K_quote"] = await ronde(kunci_q, pv_q)
    if pf:
        async def kunci_pf(ca):
            so_pf = await ca.fetchval("SELECT sales_order_id FROM proformas WHERE id = $1", uuid.UUID(pf))
            await ca.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"PROFORMA_SO:{T}:{so_pf}")

        async def pv_pf(cb):
            BP.get_pool = pool_untuk(cb)
            return (await BP.bulk_cancel_preview(req(uid), BP.BulkBatalRequest(ids=[pf], reason="TES E2E AS-G7-M1")))["data"]["ok_count"]

        async def tulis_pf(cb):  # penulis: penentu BERKUNCI (kunci=True / jalur lama) di transaksi yang di-rollback
            tx = cb.transaction(); await tx.start()
            try:
                return await BP._blok_batal(cb, ctx, pf, "TES E2E AS-G7-M1")
            finally:
                await tx.rollback()
        out["K_proforma"] = await ronde(kunci_pf, pv_pf)
        out["K_tulis_proforma"] = await ronde(kunci_pf, tulis_pf)
    print(f"KUNCI {NAMA} {json.dumps(out)}", flush=True)
    json.dump(out, open(f"/out/k-{NAMA}.json", "w"))


async def jepret():
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    d = {k: str(await c.fetchval(q, T)) for k, q in SQL_JEPRET.items()}
    await c.close()
    json.dump(d, open(f"/out/j-{NAMA}.json", "w")); print(f"JEPRET {NAMA} ok", flush=True)


def jalur_beda(a, b, jalur=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += jalur_beda(a.get(k), b.get(k), f"{jalur}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += jalur_beda(x, y, f"{jalur}[{i}]")
        return out
    return [] if a == b else [f"{jalur}: {json.dumps(a, default=str)[:90]} != {json.dumps(b, default=str)[:90]}"]


def banding():
    lama = json.load(open("/out/p-lama.json"))
    pemb = os.environ.get("PEMBANDING", "baru")
    baru = json.load(open(f"/out/p-{pemb}.json"))
    for a in ("so", "quote", "proforma"):
        bd = jalur_beda(lama[a], baru[a])
        kode = sorted({b["code"] for x in lama[a]["items"] for b in x["blocks"]})
        cek(not bd and lama[a]["total"] > 1, f"(P-{a}) pratinjau massal LAMA == BARU semua medan ({lama[a]['total']} item, lolos {lama[a]['ok_count']}, blok {kode})",
            "; ".join(bd[:5]))
    if pemb != "baru":
        return
    kl, kb = json.load(open("/out/k-lama.json")), json.load(open("/out/k-baru.json"))
    for k in ("K_so", "K_quote", "K_proforma"):
        if k not in kb:
            cek(False, f"({k}) tak ada dokumen lolos untuk uji kunci")
            continue
        cek(kl[k]["menunggu"] and kl[k]["detik"] >= 1.5 and not kb[k]["menunggu"] and kb[k]["detik"] < 1.0,
            f"({k}) kunci penulis dipegang: pratinjau LAMA menunggu >= 1,5 dtk (kontrol), BARU < 1 dtk", f"{kl[k]} / {kb[k]}")
    if "K_tulis_proforma" in kb:
        cek(kl["K_tulis_proforma"]["menunggu"] and kb["K_tulis_proforma"]["menunggu"],
            "(K-tulis) penentu penulis batal proforma (kunci=True) MENUNGGU kunci PROFORMA_SO (LAMA == BARU)",
            f"{kl['K_tulis_proforma']} / {kb['K_tulis_proforma']}")
    s, e = json.load(open("/out/j-sebelum.json")), json.load(open("/out/j-sesudah.json"))
    for k in SQL_JEPRET:
        cek(s[k] == e[k], f"NET 0 sesudah proses: {k}", "" if s[k] == e[k] else f"{s[k]} / {e[k]}")


async def main():
    if MODE == "JEPRET":
        return await jepret()
    if MODE == "PRATINJAU":
        return await pratinjau()
    if MODE == "KUNCI":
        return await kunci()
    banding()
    print(f"RINGKAS gagal={len(gagal)} {gagal}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
