"""UJI NYATA AS-G7-M2 (24 Okt 2026): pratinjau kirim penawaran massal + bagikan dokumen (tunggal + massal) BACA SAJA. kaos SAJA, NOL COMMIT.
PRATINJAU (pohon LAMA = inti/_bagikan lalu ROLLBACK; BARU = penentu baca saja, transaksi READ ONLY):
  /quotes/bulk/send/preview (<=49 penawaran semua status + id acak); /documents/{kind}/{id}/share/preview untuk 7 jenis (<=8 dokumen per
  jenis); /{modul}/bulk/share/preview untuk 8 modul (<=10 dokumen). BANDING: LAMA == BARU di SEMUA medan (nol pengecualian).
KUNCI: kunci QUOTE + FOR UPDATE penawaran dipegang koneksi A -> pratinjau bagikan tunggal + kirim massal di B: BARU < 1 dtk, LAMA menunggu
  >= 1,5 dtk (kontrol). K-tulis: inti kirim_penawaran_core di bawah kunci itu MENUNGGU (LAMA == BARU). JEPRET sebelum/sesudah: net 0."""
import asyncio, json, os, sys, time, uuid
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
gagal = []
SQL_KIND = {
    "quotation": "SELECT id FROM quotes WHERE tenant_id = $1 ORDER BY created_at DESC, id LIMIT 8",
    "invoice": "SELECT id FROM sales_invoices WHERE tenant_id = $1 ORDER BY created_at DESC, id LIMIT 8",
    "proforma": "SELECT id FROM proformas WHERE tenant_id = $1 ORDER BY created_at DESC, id LIMIT 8",
    "delivery": "SELECT id FROM invoice_fulfillments WHERE tenant_id = $1 ORDER BY created_at DESC, id LIMIT 8",
    "credit_note": "SELECT id FROM credit_notes WHERE tenant_id = $1 ORDER BY created_at DESC, id LIMIT 8",
    "rekap": "SELECT id FROM sales_orders WHERE tenant_id = $1 ORDER BY created_at DESC, id LIMIT 8",
    "receipt": "SELECT id FROM customer_deposits WHERE tenant_id = $1 ORDER BY created_at DESC, id LIMIT 8",
}
SQL_MODUL = {
    "sales-orders": "sales_orders", "quotes": "quotes", "proformas": "proformas", "sales-invoices": "sales_invoices",
    "customer-deposits": "customer_deposits", "receive-payments": "receive_payments", "deliveries": "invoice_fulfillments",
    "credit-notes": "credit_notes",
}
SQL_JEPRET = {
    "quote": "SELECT count(*)::text || '/' || md5(COALESCE(string_agg(id::text || status || COALESCE(sent_at::text, '-'), ',' ORDER BY id), '')) FROM quotes WHERE tenant_id = $1",
    "tautan": "SELECT count(*) FROM document_shares WHERE tenant_id = $1",
    "audit": "SELECT count(*) FROM audit_logs WHERE tenant_id = $1",
}


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def req(uid, body=b'{"channel": "link"}'):
    async def receive(): return {"type": "http.request", "body": body, "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"content-type", b"application/json")], "query_string": b""}, receive)
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
    k = {"send": [str(r["id"]) for r in await c.fetch(
        "SELECT id FROM quotes WHERE tenant_id = $1 ORDER BY (status IN ('draft','sent')) DESC, created_at DESC, id LIMIT 49", T)] + [acak]}
    k["share1"] = {kind: [str(r["id"]) for r in await c.fetch(q, T)] for kind, q in SQL_KIND.items()}
    k["share1"]["quotation"].append(acak)
    k["sharem"] = {m: [str(r["id"]) for r in await c.fetch(f"SELECT id FROM {t} WHERE tenant_id = $1 ORDER BY created_at DESC, id LIMIT 10", T)]
                   for m, t in SQL_MODUL.items()}
    return uid, k


async def semua(cx, uid, k):
    from fastapi import HTTPException
    from app.routers import bulk_quote as BQ, bulk_dokumen as BD, dokumen as D
    BQ.get_pool = BD.get_pool = pool_untuk(cx)
    D._pool = pool_untuk(cx)
    # izin kuitansi (_wajib_izin) butuh PolicyEngineClient yang tak dinyalakan di kontainer harness -> PEREKAM (dua pohon SAMA): apakah
    # pemeriksaan dipanggil + aksi/modulnya ikut DIBANDINGKAN LAMA == BARU (M2 tak mengubah izin; hanya memindahkannya ke penentu).
    izin = []

    async def rekam_izin(request, aksi, modul):
        izin.append([aksi, modul])
    D._wajib_izin = rekam_izin

    async def coba(aw):
        try:
            return {"ok": rapi(await aw)}
        except HTTPException as e:
            return {"galat": [e.status_code, rapi(e.detail)]}
    pv_send = next(r.endpoint for r in BQ.router.routes if r.endpoint.__name__ == "bulk_send_preview")
    out = {"send": await coba(pv_send(req(uid), BQ.BulkAksiRequest(ids=k["send"])))}
    out["share1"] = {kind: [await coba(D.pratinjau_bagikan_dokumen(req(uid), kind, i)) for i in ids] for kind, ids in k["share1"].items()}
    out["sharem"] = {}
    for m, ids in k["sharem"].items():
        if not ids:
            continue
        pv = next(r.endpoint for r in BD.ROUTERS[m].routes if r.endpoint.__name__ == "bulk_share_preview")
        out["sharem"][m] = await coba(pv(req(uid), BD.BulkShareRequest(ids=ids, channel="link")))
    out["izin"] = izin
    return out


VARIAN_TES = [  # (status, selisih hari masa berlaku | None) -- syarat MASTER: jalur SUKSES kirim lebih dari satu kasus
    ("draft", None), ("draft", 10), ("draft", 0), ("draft", -1), ("sent", 5), ("sent", -1)]


async def penawaran_tes(c, uid):
    """Penawaran TES E2E dibuat DI DALAM transaksi pemanggil (SELALU di-ROLLBACK; nol commit): klon baris penawaran kaos dengan id
    deterministik (sama di pohon LAMA & BARU) + nomor/status/masa berlaku per varian. -> [id]."""
    hari = await c.fetchval("SELECT tanggal_bisnis($1)", T)
    dasar = await c.fetchval("SELECT id FROM quotes WHERE tenant_id = $1 ORDER BY created_at, id LIMIT 1", T)
    assert dasar, "kaos tanpa penawaran dasar"
    ids = []
    for n, (st, sel) in enumerate(VARIAN_TES, 1):
        qid = uuid.uuid5(uuid.NAMESPACE_URL, f"as-g7m2-tes-{n}")
        await c.execute(
            """INSERT INTO quotes SELECT (jsonb_populate_record(NULL::quotes, to_jsonb(q) || jsonb_build_object(
                   'id', $2::uuid, 'quote_number', $3::text, 'status', $4::text, 'expiry_date', $5::date,
                   'sent_at', CASE WHEN $4 = 'sent' THEN now() END, 'converted_to_id', NULL))).*
               FROM quotes q WHERE q.id = $1""",
            dasar, qid, f"TES-E2E-G7M2-{n}", st, (hari + __import__("datetime").timedelta(days=sel)) if sel is not None else None)
        ids.append(str(qid))
    return ids


async def pratinjau():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    if NAMA == "lama" or not os.path.exists("/out/kasus.json"):
        uid, k = await kasus(c)
        json.dump({"uid": str(uid), "k": k}, open("/out/kasus.json", "w"))
    d = json.load(open("/out/kasus.json"))
    out = await semua(c, d["uid"], d["k"])
    # jalur SUKSES kirim: penawaran TES E2E di transaksi luar yang SELALU di-ROLLBACK. Catatan: di dalam transaksi luar, transaksi READ ONLY
    # pratinjau menjadi SAVEPOINT (asyncpg mengabaikan READ ONLY untuk transaksi bersarang) -- penjaga READ ONLY diuji di kasus utama di atas.
    from fastapi import HTTPException
    from app.routers import bulk_quote as BQ, dokumen as D
    tx = c.transaction(); await tx.start()
    try:
        ids = await penawaran_tes(c, d["uid"])
        pv = next(r.endpoint for r in BQ.router.routes if r.endpoint.__name__ == "bulk_send_preview")
        tes = {"send": rapi(await pv(req(d["uid"]), BQ.BulkAksiRequest(ids=ids)))}
        tes["share1"] = []
        for i in ids:
            try:
                tes["share1"].append({"ok": rapi(await D.pratinjau_bagikan_dokumen(req(d["uid"]), "quotation", i))})
            except HTTPException as e:
                tes["share1"].append({"galat": [e.status_code, rapi(e.detail)]})
        out["tes"] = tes
    finally:
        await tx.rollback()
    s = out["send"].get("ok", {}).get("data", {})
    print(f"REKAM {NAMA} send {s.get('total')} (lolos {s.get('ok_count')}); share1 " +
          ", ".join(f"{kd}:{len(v)}" for kd, v in out["share1"].items()) + f"; sharem {len(out['sharem'])} modul", flush=True)
    await c.close()
    json.dump(out, open(f"/out/p-{NAMA}.json", "w"))


async def kunci():
    from app.routers import bulk_quote as BQ, dokumen as D, quotes as Q
    d = json.load(open("/out/kasus.json"))
    lama = json.load(open("/out/p-lama.json"))
    uid = d["uid"]
    items = lama["send"].get("ok", {}).get("data", {}).get("items", [])
    q = next((x["id"] for x in items if x["ok"]), None)
    out = {}
    if not q:
        out["lewat"] = "tak ada penawaran lolos kirim"
        json.dump(out, open(f"/out/k-{NAMA}.json", "w")); print(f"KUNCI {NAMA} {out}"); return

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

    async def kunci_q(ca):
        await ca.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:{T}:{q}")
        await ca.execute("SELECT 1 FROM quotes WHERE id = $1 FOR UPDATE", uuid.UUID(q))

    async def pv_share(cb):
        D._pool = pool_untuk(cb)
        return (await D.pratinjau_bagikan_dokumen(req(uid), "quotation", q))["data"]["boleh"]

    async def pv_send(cb):
        BQ.get_pool = pool_untuk(cb)
        ep = next(r.endpoint for r in BQ.router.routes if r.endpoint.__name__ == "bulk_send_preview")
        return (await ep(req(uid), BQ.BulkAksiRequest(ids=[q])))["data"]["ok_count"]

    async def tulis_kirim(cb):
        tx = cb.transaction(); await tx.start()
        try:
            return (await Q.kirim_penawaran_core(cb, {"tenant_id": T, "user_id": uid}, q))["status"]
        finally:
            await tx.rollback()
    out["K_share"] = await ronde(kunci_q, pv_share)
    out["K_send"] = await ronde(kunci_q, pv_send)
    out["K_tulis"] = await ronde(kunci_q, tulis_kirim)
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
    tl, tb = lama["tes"], baru["tes"]
    lolos_tes = tl["send"]["data"]["ok_count"]
    bd = jalur_beda(tl, tb)
    kode = sorted({b["code"] for x in tl["send"]["data"]["items"] for b in x["blocks"]})
    cek(not bd and lolos_tes >= 3, f"(P-send-tes) {len(VARIAN_TES)} penawaran TES E2E (ROLLBACK): kirim massal + bagikan tunggal LAMA == BARU "
        f"semua medan (lolos kirim {lolos_tes}, blok {kode})", "; ".join(bd[:5]))
    s = lama["send"].get("ok", {}).get("data", {})
    bd = jalur_beda(lama["send"], baru["send"])
    cek(not bd and s.get("total", 0) > 1, f"(P-send) pratinjau kirim massal LAMA == BARU semua medan ({s.get('total')} item, lolos {s.get('ok_count')})",
        "; ".join(bd[:5]))
    n1 = sum(len(v) for v in lama["share1"].values())
    bd = jalur_beda(lama["share1"], baru["share1"])
    galat = sorted({str(x["galat"][0]) for v in lama["share1"].values() for x in v if "galat" in x})
    cek(not bd and n1 > 1, f"(P-share1) pratinjau bagikan TUNGGAL LAMA == BARU semua medan ({n1} dokumen, 7 jenis, galat {galat})", "; ".join(bd[:5]))
    cek(lama["izin"] == baru["izin"] and len(lama["izin"]) > 0,
        f"(IZIN) pemeriksaan izin kuitansi dipanggil SAMA (LAMA == BARU, {len(lama['izin'])} panggilan {sorted({tuple(x) for x in lama['izin']})})",
        f"{len(lama['izin'])} / {len(baru['izin'])}")
    bd = jalur_beda(lama["sharem"], baru["sharem"])
    cek(not bd and lama["sharem"], f"(P-sharem) pratinjau bagikan MASSAL LAMA == BARU semua medan ({len(lama['sharem'])} modul)", "; ".join(bd[:5]))
    if pemb != "baru":
        return
    kl, kb = json.load(open("/out/k-lama.json")), json.load(open("/out/k-baru.json"))
    if "lewat" in kb:
        cek(False, f"(K) {kb['lewat']}")
    else:
        for k in ("K_share", "K_send"):
            cek(kl[k]["menunggu"] and kl[k]["detik"] >= 1.5 and not kb[k]["menunggu"] and kb[k]["detik"] < 1.0,
                f"({k}) kunci QUOTE dipegang: pratinjau LAMA menunggu >= 1,5 dtk (kontrol), BARU < 1 dtk", f"{kl[k]} / {kb[k]}")
        cek(kl["K_tulis"]["menunggu"] and kb["K_tulis"]["menunggu"], "(K-tulis) inti kirim_penawaran_core di bawah kunci QUOTE MENUNGGU (LAMA == BARU)",
            f"{kl['K_tulis']} / {kb['K_tulis']}")
    s0, e0 = json.load(open("/out/j-sebelum.json")), json.load(open("/out/j-sesudah.json"))
    for k in SQL_JEPRET:
        cek(s0[k] == e0[k], f"NET 0 sesudah proses: {k}", "" if s0[k] == e0[k] else f"{s0[k]} / {e0[k]}")


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
