"""UJI NYATA AS-G7b (24 Okt 2026): pratinjau BUAT nota kredit (/credit-notes/preview) = rencana + penentu posting atas NK maya, BACA SAJA.
kaos SAJA, NOL COMMIT.
PRATINJAU (pohon LAMA = buat + posting NYATA di savepoint lalu ROLLBACK; BARU = READ ONLY): untuk faktur kaos (<=25): "dari faktur"
  alasan retur + lain-lain, baris potongan nominal (diskon), tanpa faktur (pelanggan + baris), + blok (faktur pelanggan lain, qty berlebih).
  BANDING: LAMA == BARU di SEMUA medan, kecuali yang DIRANCANG (disetujui MASTER), masing-masing diuji terpisah:
    journals_on_post[].journal_number  LAMA bernomor, BARU null
    customer_credit_on_post.deposit_number  LAMA KRD-..., BARU null
    notes CN_EXCESS_TO_CUSTOMER_CREDIT  teks BARU = pembentuk catatan_kelebihan_nk (tanpa nomor, tanpa kurung); kode sama
    credit_note_number_estimated  medan BARU (== nomor ada)
  credit_note_number_preview TIDAK dikecualikan: perkiraan BARU WAJIB == nomor yang benar-benar diterbitkan LAMA.
KUNCI: K1 penulis buat+posting NK terbuka di A -> pratinjau di B: BARU < 1 dtk, LAMA menunggu >= 1,5 dtk (kontrol). K2 penulis||penulis:
  penantang menunggu (LAMA == BARU). JEPRET sebelum/sesudah: net 0."""
import asyncio, json, os, sys, time, uuid
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
gagal = []
BATAS = 25


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


SQL_JEPRET = {
    "nk": "SELECT count(*)::text || '/' || COALESCE(max(credit_note_number), '') FROM credit_notes WHERE tenant_id = $1",
    "urut_nk": "SELECT COALESCE(json_agg(json_build_object('m', year_month, 'n', last_number) ORDER BY year_month)::text, '[]') FROM credit_note_sequences WHERE tenant_id = $1",
    "jurnal": "SELECT count(*)::text || '/' || COALESCE(max(journal_number), '') FROM journal_entries WHERE tenant_id = $1",
    "stok": "SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1",
    "uang_muka": "SELECT count(*) FROM customer_deposits WHERE tenant_id = $1",
    "faktur": "SELECT md5(COALESCE(string_agg(id::text || status || COALESCE(amount_paid::text,'-'), ',' ORDER BY id), '')) FROM sales_invoices WHERE tenant_id = $1",
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


async def kasus(c):
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    fs = await c.fetch("""SELECT si.id, si.customer_id::text cid, si.customer_name FROM sales_invoices si
                          WHERE si.tenant_id = $1 AND si.status NOT IN ('void', 'draft') ORDER BY si.created_at DESC, si.id LIMIT $2""", T, BATAS)
    k = []
    for f in fs:
        k.append({"original_invoice_id": str(f["id"]), "reason": "return"})
        k.append({"original_invoice_id": str(f["id"]), "reason": "other"})
        b = await c.fetchrow("SELECT id, description FROM sales_invoice_items WHERE invoice_id = $1 ORDER BY line_number LIMIT 1", f["id"])
        if b:
            k.append({"original_invoice_id": str(f["id"]), "reason": "discount", "customer_id": f["cid"], "customer_name": f["customer_name"],
                      "items": [{"description": "TES E2E potongan", "quantity": 1, "unit_price": 1000}]})
            k.append({"original_invoice_id": str(f["id"]), "reason": "return", "lines": [{"original_invoice_item_id": str(b["id"]), "quantity": 999999}]})
    if fs:
        f, g = fs[0], next((x for x in fs if x["cid"] != fs[0]["cid"]), None)
        k.append({"reason": "other", "customer_id": f["cid"], "customer_name": f["customer_name"],
                  "items": [{"description": "TES E2E tanpa faktur", "quantity": 1, "unit_price": 2500}]})
        if g:
            k.append({"original_invoice_id": str(f["id"]), "reason": "discount", "customer_id": g["cid"], "customer_name": g["customer_name"],
                      "items": [{"description": "TES E2E pelanggan lain", "quantity": 1, "unit_price": 1000}]})
    return uid, k


async def pratinjau():
    from fastapi import HTTPException
    from app.routers import credit_notes as CR
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    if NAMA == "lama" or not os.path.exists("/out/kasus.json"):
        uid, k = await kasus(c)
        json.dump({"uid": str(uid), "k": k}, open("/out/kasus.json", "w"))
    d = json.load(open("/out/kasus.json"))
    CR.get_pool = pool_untuk(c)
    out = []
    for x in d["k"]:
        try:
            r = await CR.preview_credit_note(req(d["uid"]), CR.CreditNotePreviewRequest(**x))
            out.append({"ok": rapi(r["data"])})
        except HTTPException as e:
            out.append({"galat": [e.status_code, rapi(e.detail)]})
    lolos = sum(1 for y in out if y.get("ok", {}).get("ok"))
    print(f"REKAM {NAMA} {len(out)} kasus, lolos {lolos}", flush=True)
    await c.close()
    json.dump(out, open(f"/out/p-{NAMA}.json", "w"))


async def kunci():
    from app.routers import credit_notes as CR
    d = json.load(open("/out/kasus.json"))
    lama = json.load(open("/out/p-lama.json"))
    i = next((n for n, y in enumerate(lama) if y.get("ok", {}).get("ok")), None)
    out = {}
    if i is None:
        out["lewat"] = "tak ada kasus lolos"
        json.dump(out, open(f"/out/k-{NAMA}.json", "w")); print(f"KUNCI {NAMA} {out}"); return
    payload, uid = lama[i]["ok"]["payload"], d["uid"]
    ctx = {"tenant_id": T, "user_id": uid}

    async def ronde(pegang, tantang, jeda=1.5, tx_b=True):
        ca, cb = await asyncpg.connect(os.environ["DATABASE_URL"]), await asyncpg.connect(os.environ["DATABASE_URL"])
        ta = ca.transaction(); await ta.start()
        tb = None
        try:
            await pegang(ca)
            if tx_b:
                tb = cb.transaction(); await tb.start()
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
            if tb is not None: await tb.rollback()
            await ca.close(); await cb.close()

    async def penulis(cx):
        h = await CR.buat_nota_kredit(cx, ctx, CR.CreateCreditNoteRequest(**payload))
        await CR.posting_nota_kredit(cx, ctx, uuid.UUID(h["data"]["id"]))
        return "posted"

    async def pratinjau_b(cb):
        CR.get_pool = pool_untuk(cb)
        return (await CR.preview_credit_note(req(uid), CR.CreditNotePreviewRequest(**d["k"][i])))["data"]["ok"]

    out["K1"] = await ronde(penulis, pratinjau_b, tx_b=False)
    out["K2"] = await ronde(penulis, penulis)
    # K3/K4 (syarat MASTER): kunci CN_FAKTUR faktur yang SAMA saja -- penulis buat tetap menunggu; pratinjau BARU tidak (LAMA ya)
    j = next((n for n, y in enumerate(lama) if y.get("ok", {}).get("ok") and y["ok"]["payload"].get("original_invoice_id")
              and any(it.get("original_invoice_item_id") for it in y["ok"]["payload"].get("items") or [])), None)
    if j is not None:
        pj, fj = lama[j]["ok"]["payload"], lama[j]["ok"]["payload"]["original_invoice_id"]

        async def kunci_faktur(ca):
            await ca.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"CN_FAKTUR:{T}:{fj}")

        async def buat_saja(cx):
            return (await CR.buat_nota_kredit(cx, ctx, CR.CreateCreditNoteRequest(**pj)))["data"]["status"]

        async def pratinjau_j(cb):
            CR.get_pool = pool_untuk(cb)
            return (await CR.preview_credit_note(req(uid), CR.CreditNotePreviewRequest(**d["k"][j])))["data"]["ok"]
        out["K3"] = await ronde(kunci_faktur, buat_saja)
        out["K4"] = await ronde(kunci_faktur, pratinjau_j, tx_b=False)
    print(f"KUNCI {NAMA} {json.dumps(out)}", flush=True)
    json.dump(out, open(f"/out/k-{NAMA}.json", "w"))


async def jepret():
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    d = {k: str(await c.fetchval(q, T)) for k, q in SQL_JEPRET.items()}
    await c.close()
    json.dump(d, open(f"/out/j-{NAMA}.json", "w")); print(f"JEPRET {NAMA} ok", flush=True)


def normal(y):
    """Medan yang DIRANCANG beda dinolkan (masing-masing diuji terpisah di banding)."""
    if "ok" not in y:
        return y
    d = json.loads(json.dumps(y["ok"]))
    d.pop("credit_note_number_estimated", None)
    for j in d.get("journals_on_post") or []:
        j["journal_number"] = None
    if d.get("customer_credit_on_post"):
        d["customer_credit_on_post"]["deposit_number"] = None
    for n in d.get("notes") or []:
        if n.get("code") == "CN_EXCESS_TO_CUSTOMER_CREDIT":
            n["message"] = None
    return {"ok": d}


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
    from app.services import nota_kredit_rencana as NKR
    lama = json.load(open("/out/p-lama.json"))
    pemb = os.environ.get("PEMBANDING", "baru")
    baru = json.load(open(f"/out/p-{pemb}.json"))
    beda = [i for i in range(len(lama)) if normal(lama[i]) != normal(baru[i])]
    lolos = sum(1 for y in lama if y.get("ok", {}).get("ok"))
    kode = sorted({b["code"] for y in lama for b in y.get("ok", {}).get("blocks", [])})
    cek(len(lama) == len(baru) and lama and not beda,
        f"(P) pratinjau buat NK LAMA == BARU semua medan kecuali yang dirancang ({len(lama)} kasus, lolos {lolos}, blok {kode})",
        "" if not beda else f"kasus {beda[0]}: " + "; ".join(jalur_beda(normal(lama[beda[0]]), normal(baru[beda[0]]))[:6]))
    if pemb != "baru":
        return
    nl = [(y["ok"]["credit_note_number_preview"], z["ok"]["credit_note_number_preview"], z["ok"].get("credit_note_number_estimated"))
          for y, z in zip(lama, baru) if y.get("ok", {}).get("ok")]
    cek(bool(nl) and all(a == b and a and e is True for a, b, e in nl),
        f"(NOMOR) perkiraan nomor NK BARU == nomor yang diterbitkan LAMA + flag estimated ({len(nl)} kasus)", str([x for x in nl if x[0] != x[1]][:3]))
    jl = [j["journal_number"] for y in lama for j in (y.get("ok", {}).get("journals_on_post") or [])]
    jb = [j["journal_number"] for y in baru for j in (y.get("ok", {}).get("journals_on_post") or [])]
    cek(bool(jl) and all(jl) and len(jl) == len(jb) and all(x is None for x in jb), f"(N) nomor jurnal: LAMA bernomor ({len(jl)}), BARU null")
    kr = [(y["ok"]["customer_credit_on_post"], z["ok"]["customer_credit_on_post"]) for y, z in zip(lama, baru)
          if y.get("ok", {}).get("customer_credit_on_post")]
    cek(all(a["deposit_number"] and b["deposit_number"] is None for a, b in kr), f"(KRD) nomor KRD: LAMA bernomor, BARU null ({len(kr)} kasus)")
    ct = [(n, z) for y, z in zip(lama, baru) for n in (z.get("ok", {}).get("notes") or []) if n["code"] == "CN_EXCESS_TO_CUSTOMER_CREDIT"]
    cek(all(n["message"] == NKR.catatan_kelebihan_nk(Decimal(str(z["ok"]["customer_credit_on_post"]["amount"])))["message"]
            and "(" not in n["message"] for n, z in ct), f"(CATATAN) teks kelebihan BARU = pembentuk bersama, tanpa kurung ({len(ct)} kasus)")
    kl, kb = json.load(open("/out/k-lama.json")), json.load(open("/out/k-baru.json"))
    if "lewat" in kb:
        cek(False, f"(K) kasus kunci tak bisa dijalankan: {kb['lewat']}")
    else:
        cek(kl["K1"]["menunggu"] and kl["K1"]["detik"] >= 1.5, "(K1 kontrol) LAMA: pratinjau MENUNGGU penulis buat+posting >= 1,5 dtk", str(kl["K1"]))
        cek(not kb["K1"]["menunggu"] and kb["K1"]["detik"] < 1.0, "(K1) BARU: pratinjau < 1 dtk walau penulis memegang kunci", str(kb["K1"]))
        cek(kb["K2"]["menunggu"] and kl["K2"]["menunggu"], "(K2) penulis||penulis buat+posting: penantang menunggu (LAMA == BARU)", f"{kl['K2']} / {kb['K2']}")
        cek("K3" in kb and kb["K3"]["menunggu"] and kl["K3"]["menunggu"],
            "(K3) kunci CN_FAKTUR faktur sama: penulis buat MENUNGGU (LAMA == BARU; penulis tak melemah)", f"{kl.get('K3')} / {kb.get('K3')}")
        cek("K4" in kb and kl["K4"]["menunggu"] and kl["K4"]["detik"] >= 1.5 and not kb["K4"]["menunggu"] and kb["K4"]["detik"] < 1.0,
            "(K4) kunci CN_FAKTUR: pratinjau LAMA menunggu >= 1,5 dtk (kontrol), BARU < 1 dtk", f"{kl.get('K4')} / {kb.get('K4')}")
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
