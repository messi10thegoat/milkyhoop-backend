"""UJI NYATA AS-G7a (24 Okt 2026): pratinjau konfirmasi SO = penentu bersama BACA SAJA. kaos SAJA, nol COMMIT.
PRATINJAU (pohon LAMA = penulis+ROLLBACK, BARU = READ ONLY): /confirm/preview tiap draf SO kaos (<=40) + bukan-draf (<=5) + id acak,
  lalu /bulk/confirm/preview 6 draf. BANDING: LAMA == BARU di SEMUA medan (nomor, status, kode, terbit, label, galat) kecuali
  order_code_estimated (medan BARU, diuji terpisah: == order_code_issued). Kode LAMA = kode yang benar-benar diterbitkan penulis.
KUNCI (dua pohon): K1 koneksi A memegang kunci penulis (advisory kode periode + FOR UPDATE baris SO) -> pratinjau di koneksi B:
  BARU selesai < 1,5 dtk, LAMA MENUNGGU (kontrol merah alat ukur). K2 penulis||penulis SO SAMA dan K3 SO lain periode sama:
  penantang MENUNGGU sampai A di-ROLLBACK (penulis tak melemah), LAMA == BARU. Semua transaksi ROLLBACK.
JEPRET sebelum/sesudah (koneksi READ ONLY): SO kaos, penghitung kode, peristiwa kode, audit -> net 0 sesudah proses."""
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
    "so": "SELECT md5(COALESCE(string_agg(id::text || status || COALESCE(order_code, '-') || COALESCE(confirmed_at::text, '-'), ',' ORDER BY id), '')) FROM sales_orders WHERE tenant_id = $1",
    "penghitung": "SELECT COALESCE(json_agg(json_build_object('p', period_key, 's', last_seq) ORDER BY period_key)::text, '[]') FROM order_code_counters WHERE tenant_id = $1",
    "peristiwa": "SELECT count(*) FROM order_code_events WHERE tenant_id = $1",
    "audit": "SELECT count(*) FROM audit_logs WHERE tenant_id = $1",
}


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER"}; return r


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


async def siapkan(c):
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    draf = [str(r["id"]) for r in await c.fetch(
        "SELECT id FROM sales_orders WHERE tenant_id = $1 AND status = 'draft' ORDER BY created_at DESC, id LIMIT 40", T)]
    lain = [str(r["id"]) for r in await c.fetch(
        "SELECT id FROM sales_orders WHERE tenant_id = $1 AND status <> 'draft' ORDER BY created_at DESC, id LIMIT 5", T)]
    return uid, draf, lain


async def pratinjau():
    from fastapi import HTTPException
    from app.routers import sales_orders as SO, bulk_so as BS
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    uid, draf, lain = await siapkan(c)
    assert len(draf) >= 2, f"draf kaos kurang: {len(draf)}"
    SO.get_pool = BS.get_pool = pool_untuk(c)
    hasil = {}
    for i in draf + lain + [str(uuid.UUID(int=0))]:
        try:
            r = await SO.preview_confirm_sales_order(req(uid), i)
            hasil[i] = {"ok": rapi(r.data)}
        except HTTPException as e:
            hasil[i] = {"galat": [e.status_code, rapi(e.detail)]}
    pv = next(r.endpoint for r in BS.router.routes if r.endpoint.__name__ == "bulk_confirm_preview")
    massal = (await pv(req(uid), BS.BulkAksiRequest(ids=draf[:6])))["data"]
    out = {"tunggal": hasil, "massal": rapi(massal), "draf": draf, "lain": lain}
    print(f"REKAM {NAMA} draf={len(draf)} lain={len(lain)} kode_terbit={sum(1 for v in hasil.values() if v.get('ok', {}).get('order_code_issued'))}", flush=True)
    await c.close()
    json.dump(out, open(f"/out/p-{NAMA}.json", "w"))


async def kunci():
    from app.routers import sales_orders as SO
    from app.services import kode_order as KO
    from app.utils.tanggal_tenant import tanggal_dokumen
    adm = await asyncpg.connect(os.environ["DATABASE_URL"])
    uid, draf, _ = await siapkan(adm)
    s1, s2 = draf[0], draf[1]
    ctx = {"tenant_id": T, "user_id": str(uid)}
    st = await KO.muat_setelan(adm, T)
    pk = KO.kunci_periode(st["reset"], await tanggal_dokumen(adm, T))
    await adm.close()
    out = {"kode_aktif": bool(st["enabled"] and st["trigger"] == "so_confirmed")}

    async def ronde(pegang, tantang, jeda=1.5, tx_b=True):
        # tx_b=False: pratinjau membuka transaksinya SENDIRI (READ ONLY tak bisa sebagai savepoint)
        ca, cb = await asyncpg.connect(os.environ["DATABASE_URL"]), await asyncpg.connect(os.environ["DATABASE_URL"])
        ta = ca.transaction(); await ta.start()
        tb = None
        try:
            await pegang(ca)
            if tx_b:
                tb = cb.transaction(); await tb.start()
            t0 = time.monotonic()
            tugas = asyncio.ensure_future(tantang(cb))
            await asyncio.sleep(jeda)
            menunggu = not tugas.done()
            await ta.rollback(); ta = None
            r = await asyncio.wait_for(tugas, 20)
            return {"menunggu": menunggu, "detik": round(time.monotonic() - t0, 2), "hasil": r}
        finally:
            if ta is not None: await ta.rollback()
            if tb is not None: await tb.rollback()
            await ca.close(); await cb.close()

    async def kunci_penulis(ca):  # persis kunci yang dipegang penulis yang sedang menerbitkan kode untuk s1
        await ca.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ORDER_CODE:{T}:{pk}")
        await ca.fetchval("SELECT id FROM sales_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE", uuid.UUID(s1), T)

    async def pratinjau_b(cb):
        SO.get_pool = pool_untuk(cb)
        r = await SO.preview_confirm_sales_order(req(uid), s1)
        return r.data["order_code"]

    async def penulis(sid):
        async def f(cx):
            return (await SO._konfirmasi_so(cx, ctx, sid))["status"]
        return f

    out["K1"] = await ronde(kunci_penulis, pratinjau_b, tx_b=False)
    out["K2"] = await ronde(await penulis(s1), await penulis(s1))
    out["K3"] = await ronde(await penulis(s1), await penulis(s2))
    print(f"KUNCI {NAMA} {json.dumps(out, default=str)}", flush=True)
    json.dump(out, open(f"/out/k-{NAMA}.json", "w"), default=str)


async def jepret():
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    d = {k: str(await c.fetchval(q, T)) for k, q in SQL_JEPRET.items()}
    await c.close()
    json.dump(d, open(f"/out/j-{NAMA}.json", "w")); print(f"JEPRET {NAMA} ok", flush=True)


def tanpa_perkiraan(d):
    if isinstance(d, dict):
        return {k: tanpa_perkiraan(v) for k, v in d.items() if k != "order_code_estimated"}
    if isinstance(d, list):
        return [tanpa_perkiraan(x) for x in d]
    return d


def perkiraan_jujur(d):
    """order_code_estimated hadir di SETIAP pratinjau sukses BARU dan == order_code_issued."""
    ok = [v["ok"] for v in d["tunggal"].values() if "ok" in v] + [x["preview"] for x in d["massal"]["items"] if x["preview"]]
    return bool(ok) and all(o.get("order_code_estimated") == o.get("order_code_issued") for o in ok)


def banding():
    lama, baru = json.load(open("/out/p-lama.json")), json.load(open("/out/p-baru.json"))
    pemb = os.environ.get("PEMBANDING", "baru")
    if pemb != "baru":
        baru = json.load(open(f"/out/p-{pemb}.json"))
    cek(lama["draf"] == baru["draf"], "himpunan draf sama di kedua jalan (tak ada konfirmasi pengguna di sela)", f"{len(lama['draf'])}")
    beda = [i for i in lama["tunggal"] if lama["tunggal"][i] != tanpa_perkiraan(baru["tunggal"].get(i))]
    terbit = sum(1 for v in lama["tunggal"].values() if v.get("ok", {}).get("order_code_issued"))
    galat = sorted({str(v["galat"][0]) for v in lama["tunggal"].values() if "galat" in v})
    cek(not beda, f"(P) /confirm/preview LAMA == BARU semua medan ({len(lama['tunggal'])} SO; kode terbit {terbit}; galat {galat})",
        "" if not beda else json.dumps({i: [lama["tunggal"][i], baru["tunggal"].get(i)] for i in beda[:3]}))
    cek(lama["massal"] == tanpa_perkiraan(baru["massal"]), "(M) /bulk/confirm/preview LAMA == BARU (kode berurutan antar item)",
        "" if lama["massal"] == tanpa_perkiraan(baru["massal"]) else json.dumps([lama["massal"]["items"], baru["massal"]["items"]])[:600])
    cek(perkiraan_jujur(baru), "(E) order_code_estimated == order_code_issued di setiap pratinjau BARU")
    if pemb != "baru":
        return
    kl, kb = json.load(open("/out/k-lama.json")), json.load(open("/out/k-baru.json"))
    cek(kl["K1"]["menunggu"] is True, "(K1 kontrol) LAMA: pratinjau MENUNGGU kunci penulis (alat ukur bisa merah)", str(kl["K1"]))
    cek(kb["K1"]["menunggu"] is False and kb["K1"]["detik"] < 1.5, "(K1) BARU: pratinjau TIDAK menunggu kunci penulis", str(kb["K1"]))
    cek(kb["K2"]["menunggu"] is True and kb["K2"]["hasil"] == "confirmed" and kl["K2"]["menunggu"] is True,
        "(K2) penulis||penulis SO sama: penantang menunggu lalu jalan sesudah A rollback (LAMA == BARU)", f"{kl['K2']} / {kb['K2']}")
    if kb["kode_aktif"]:
        cek(kb["K3"]["menunggu"] is True and kl["K3"]["menunggu"] is True,
            "(K3) penulis||penulis SO lain periode sama: penantang menunggu kunci kode periode (LAMA == BARU)", f"{kl['K3']} / {kb['K3']}")
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
