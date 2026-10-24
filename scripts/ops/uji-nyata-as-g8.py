"""UJI NYATA AS-G8 (24 Okt 2026): idempotensi aksi Faktur Penjualan (ubah / posting / void / hapus) + race hapus ditutup. kaos SAJA.
TULIS (pohon LAMA & BARU): SATU transaksi luar SELALU di-ROLLBACK (get_pool tertambal), tiap skenario savepoint sendiri:
  U ubah draf INV-2610-0003 kunci K 2x (BARU replay) + badan beda (409)    P posting kunci K2 2x (BARU replay, jurnal SEKALI; LAMA 400)
  X kunci K2 dipakai VOID sesudah posting (aksi lain) = JALAN; void ulang = replay; alasan beda = 409
  D hapus draf INV-2609-0136 (dari SO) kunci K3 2x: BARU replay (LAMA 404); qty SO dilepas SEKALI; log hapus SEKALI
  G hapus faktur POSTED INV-2610-0002 berkunci 2x: 400 dua-duanya (galat tak dicatat)
  Efek per aksi (jurnal faktur, qty SO, audit, status) sesudah panggilan pertama & ulangan; LAMA == BARU sesudah panggilan pertama.
KONK (pohon BARU, COMMIT 'TES E2E', izin MASTER opsi a): SATU draf TES E2E, dua koneksi: R1 hapus menang (pegang FOR UPDATE 2 dtk, ROLLBACK) ||
  posting menunggu lalu berhasil (ROLLBACK) ; R2 posting menang (COMMIT) || hapus menunggu -> 400 "sudah diterbitkan". Batas 20 dtk (macet = FAIL).
  Pembersihan: void lewat aplikasi (finally). Bukti: GL per akun + AR (compute_ar_rincian) sebelum == sesudah; delta tepat dilaporkan.
MERAH=1: harapan X dibalik -> WAJIB gagal (KONK dilewati)."""
import asyncio, inspect, json, os, sys, time, uuid
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
SI_U, SI_D, SI_G = "2ef2e9d3-3165-439f-96ff-99a37c7a0e5f", "9464fa9f-24d5-4c4b-a9e8-01520e33e24e", "9f700180-02ed-4483-9e94-6d1579628004"
PELANGGAN = ("d2d6d24b-1de1-4be0-8ef8-4cd057ca5617", "Toko Merdeka")
FIX = [SI_U, SI_D, SI_G]
MODE, NAMA, MERAH = os.environ["MODE"], os.environ.get("NAMA", ""), os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


SQL_FIX = {
    "si": "SELECT json_agg(json_build_object('n', invoice_number, 's', status, 'nt', notes, 't', total_amount) ORDER BY invoice_number)::text FROM sales_invoices WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0)::text FROM journal_entries WHERE tenant_id = $1 AND source_id = ANY($2::uuid[])",
    "so_qty": """SELECT COALESCE(sum(soi.quantity_invoiced),0)::text FROM sales_order_items soi WHERE soi.id IN
                 (SELECT sales_order_item_id FROM sales_invoice_items WHERE invoice_id = ANY($2::uuid[])) AND $1::text IS NOT NULL""",
    "audit": "SELECT count(*) FROM audit_logs WHERE entity_id::text = ANY($2::uuid[]::text[]) AND $1::text IS NOT NULL",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1 AND $2::uuid[] IS NOT NULL",
}
SQL_UANG = {
    "gl": """SELECT json_object_agg(k, s ORDER BY k)::text FROM (SELECT coa.account_code k, sum(jl.debit - jl.credit)::text s FROM journal_lines jl
             JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts coa ON coa.id = jl.account_id
             WHERE je.tenant_id = $1 AND je.status = 'POSTED' GROUP BY 1) x""",
    "ar": "SELECT COALESCE(sum(outstanding),0)::text, count(*) FROM compute_ar_rincian($1)",
    "jurnal_semua": "SELECT count(*), COALESCE(sum(total_debit),0)::text FROM journal_entries WHERE tenant_id = $1",
    "si_semua": "SELECT count(*) FROM sales_invoices WHERE tenant_id = $1",
}


async def jepret(c, ids=None):
    ids = [uuid.UUID(x) for x in (ids or FIX)]
    return {k: [str(v) for v in (await c.fetchrow(q, T, ids)).values()] for k, q in SQL_FIX.items()}


async def jepret_uang(c):
    return {k: [str(v) for v in (await c.fetchrow(q, T)).values()] for k, q in SQL_UANG.items()}


def req(uid, kunci=None):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


async def tulis():
    from fastapi import HTTPException
    from starlette.responses import Response
    from app.routers import sales_invoices as SR
    from app.schemas.sales_invoices import UpdateInvoiceRequest as UR, VoidInvoiceRequest as VR
    baru = "response" in inspect.signature(SR.post_invoice).parameters
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    for si, st in ((SI_U, "draft"), (SI_D, "draft"), (SI_G, "posted")):
        assert await c.fetchval("SELECT status FROM sales_invoices WHERE id = $1 AND tenant_id = $2", uuid.UUID(si), T) == st, f"prasyarat {si} {st}"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)

    class Kon:
        def __getattr__(s, a): return getattr(c, a)
        def transaction(s, **kw): return c.transaction()
        async def close(s): return None
    k = Kon()
    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False
    class Pool:
        def acquire(s): return CM()
    async def gp(): return Pool()
    SR.get_pool = gp
    kw = lambda: {"response": Response()} if baru else {}

    async def coba(aw):
        sp = c.transaction(); await sp.start()
        try:
            await aw; await sp.commit(); return "OK"
        except HTTPException as e:
            await sp.rollback(); return str(e.status_code)
        except Exception as e:  # noqa: BLE001
            await sp.rollback(); return f"GALAT {type(e).__name__}: {str(e)[:100]}"

    def beda(a, b):
        return [kk for kk in a if a[kk] != b[kk]]

    h = {"baru": baru}
    tx = c.transaction(); await tx.start()
    try:
        sp = c.transaction(); await sp.start()
        try:
            e0 = await jepret(c, [SI_U])
            u1 = await coba(SR.update_invoice(req(uid, "TESG8-K"), uuid.UUID(SI_U), UR(notes="TES E2E ubah A"), **kw()))
            u2 = await coba(SR.update_invoice(req(uid, "TESG8-K"), uuid.UUID(SI_U), UR(notes="TES E2E ubah A"), **kw()))
            u3 = await coba(SR.update_invoice(req(uid, "TESG8-K"), uuid.UUID(SI_U), UR(notes="TES E2E ubah B"), **kw()))
            e1 = await jepret(c, [SI_U])
            h["U"] = {"r": [u1, u2, u3], "notes": await c.fetchval("SELECT notes FROM sales_invoices WHERE id = $1", uuid.UUID(SI_U))}
        finally:
            await sp.rollback()
        sp = c.transaction(); await sp.start()
        try:
            e1 = await jepret(c, [SI_U])
            p1 = await coba(SR.post_invoice(req(uid, "TESG8-K2"), uuid.UUID(SI_U), **kw()))
            e2 = await jepret(c, [SI_U])
            p2 = await coba(SR.post_invoice(req(uid, "TESG8-K2"), uuid.UUID(SI_U), **kw()))
            e3 = await jepret(c, [SI_U])
            h["P"] = {"r": [p1, p2], "efek_1": beda(e1, e2), "efek_ulang": beda(e2, e3), "jurnal": e2["jurnal"], "keadaan1": e2, "idem_1": int(e2["idem"][0]) - int(e1["idem"][0])}
            x1 = await coba(SR.void_invoice(req(uid, "TESG8-K2"), uuid.UUID(SI_U), VR(reason="TES E2E void"), **kw()))
            e4 = await jepret(c, [SI_U])
            x2 = await coba(SR.void_invoice(req(uid, "TESG8-K2"), uuid.UUID(SI_U), VR(reason="TES E2E void"), **kw()))
            x3 = await coba(SR.void_invoice(req(uid, "TESG8-K2"), uuid.UUID(SI_U), VR(reason="alasan lain"), **kw()))
            e5 = await jepret(c, [SI_U])
            h["X"] = {"r": [x1, x2, x3], "status": await c.fetchval("SELECT status FROM sales_invoices WHERE id = $1", uuid.UUID(SI_U)),
                      "efek_1": beda(e3, e4), "efek_ulang": beda(e4, e5), "jurnal": e4["jurnal"], "keadaan1": e4,
                      "idem_1": int(e4["idem"][0]) - int(e3["idem"][0])}
        finally:
            await sp.rollback()
        sp = c.transaction(); await sp.start()
        try:
            e0 = await jepret(c, [SI_D])
            d1 = await coba(SR.delete_invoice(req(uid, "TESG8-K3"), uuid.UUID(SI_D), **kw()))
            e1 = await jepret(c, [SI_D])
            d2 = await coba(SR.delete_invoice(req(uid, "TESG8-K3"), uuid.UUID(SI_D), **kw()))
            e2 = await jepret(c, [SI_D])
            h["D"] = {"r": [d1, d2], "ada": await c.fetchval("SELECT count(*) FROM sales_invoices WHERE id = $1", uuid.UUID(SI_D)),
                      "efek_1": beda(e0, e1), "efek_ulang": beda(e1, e2), "audit_1": int(e1["audit"][0]) - int(e0["audit"][0]), "keadaan1": e1,
                      "idem_1": int(e1["idem"][0]) - int(e0["idem"][0])}
        finally:
            await sp.rollback()
        sp = c.transaction(); await sp.start()
        try:
            g1 = await coba(SR.delete_invoice(req(uid, "TESG8-K4"), uuid.UUID(SI_G), **kw()))
            g2 = await coba(SR.delete_invoice(req(uid, "TESG8-K4"), uuid.UUID(SI_G), **kw()))
            h["G"] = [g1, g2]
        finally:
            await sp.rollback()
        print(f"REKAM {NAMA} {json.dumps(h)}", flush=True)
    finally:
        await tx.rollback(); await c.close()
    json.dump(h, open(f"/out/{NAMA}.json", "w"))


async def konk():
    from app.routers import sales_invoices as SR
    from app.schemas.sales_invoices import CreateInvoiceRequest as CR
    TUNDA, BATAS = 2.0, 20.0
    out, sisa_manual = {}, []
    adm = await asyncpg.connect(os.environ["DATABASE_URL"])
    uid = await adm.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    out["sebelum"] = await jepret_uang(adm)

    def pool_untuk(conn):
        class CM:
            async def __aenter__(s): return conn
            async def __aexit__(s, *a): return False
        class Pool:
            def acquire(s): return CM()
        async def gp(): return Pool()
        return gp

    from fastapi import HTTPException

    async def jalan(conn, aw_fn):
        SR.get_pool = pool_untuk(conn)
        try:
            r = await aw_fn()
            return "OK", (r.get("message") if isinstance(r, dict) else getattr(r, "message", None))
        except HTTPException as e:
            return str(e.status_code), str(e.detail)

    sid = None
    nomor = f"TES-E2E-G8-{uuid.uuid4().hex[:6]}"
    try:
        SR.get_pool = pool_untuk(adm)
        r = await SR.create_invoice(req(uid), CR(customer_id=PELANGGAN[0], customer_name=PELANGGAN[1], invoice_date=date.today(),
                                                 invoice_number=nomor, notes="TES E2E AS-G8 KONK",
                                                 items=[{"description": "TES E2E jasa", "quantity": 1, "unit_price": 10000}]))
        sid = await adm.fetchval("SELECT id FROM sales_invoices WHERE tenant_id = $1 AND invoice_number = $2", T, nomor)

        async def ronde(menang, kalah):
            ca, cb = await asyncpg.connect(os.environ["DATABASE_URL"]), await asyncpg.connect(os.environ["DATABASE_URL"])
            hs = {}
            try:
                async def pemenang():
                    tx = ca.transaction(); await tx.start()
                    hs["menang"] = await jalan(ca, lambda: menang[0](req(uid), sid))
                    await asyncio.sleep(TUNDA)
                    await (tx.commit() if menang[1] else tx.rollback())
                async def penantang():
                    await asyncio.sleep(0.4)
                    tx = cb.transaction(); await tx.start(); t0 = time.monotonic()
                    hs["kalah"] = await jalan(cb, lambda: kalah[0](req(uid), sid))
                    hs["tunggu"] = round(time.monotonic() - t0, 2)
                    await (tx.commit() if kalah[1] else tx.rollback())
                await asyncio.wait_for(asyncio.gather(pemenang(), penantang()), BATAS)
            except asyncio.TimeoutError:
                hs["macet"] = True
            finally:
                await ca.close(); await cb.close()
            return hs

        hapus = lambda r, i: SR.delete_invoice(r, i)
        post = lambda r, i: SR.post_invoice(r, i)
        out["R1"] = await ronde((hapus, False), (post, False))   # hapus menang (ROLLBACK) -> posting menunggu lalu berhasil (ROLLBACK)
        out["status_R1"] = await adm.fetchval("SELECT status FROM sales_invoices WHERE id = $1", sid)
        out["R2"] = await ronde((post, True), (hapus, False))     # posting menang (COMMIT) -> hapus menunggu -> 400
        out["status_R2"] = await adm.fetchval("SELECT status FROM sales_invoices WHERE id = $1", sid)
    finally:
        try:
            if sid:
                st = await adm.fetchval("SELECT status FROM sales_invoices WHERE id = $1", sid)
                SR.get_pool = pool_untuk(adm)
                from app.schemas.sales_invoices import VoidInvoiceRequest as VR
                if st == "draft":
                    r = await jalan(adm, lambda: SR.delete_invoice(req(uid), sid))
                elif st != "void" and st is not None:
                    r = await jalan(adm, lambda: SR.void_invoice(req(uid), sid, VR(reason="TES E2E AS-G8 pembersihan")))
                else:
                    r = ("OK", f"status {st}")
                out["bersih"] = [r[0], r[1], st]
                if r[0] != "OK":
                    sisa_manual.append(f"{nomor} {sid} status {st}: {r}")
        except Exception as e:  # noqa: BLE001
            sisa_manual.append(f"{nomor} {sid}: pembersihan galat {type(e).__name__}: {e}")
        out["sisa_manual"] = sisa_manual
        out["status_akhir"] = await adm.fetchval("SELECT status FROM sales_invoices WHERE id = $1", sid) if sid else None
        out["sesudah"] = await jepret_uang(adm)
        await adm.close()
    print(f"KONK {json.dumps(out, default=str)}", flush=True)
    json.dump(out, open("/out/konk.json", "w"), default=str)


def setara(lama, baru):
    """LAMA == BARU pada SEMUA medan keadaan (nomor, status, catatan, total, jurnal, qty SO, audit) kecuali jumlah baris idem (diuji terpisah)."""
    buang = lambda d: {kk: v for kk, v in d.items() if kk != "idem"}
    return all(buang(lama[kk]["keadaan1"]) == buang(baru[kk]["keadaan1"]) for kk in "PXD")


async def main():
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump(await jepret(c), open(f"/out/{NAMA}.json", "w")); await c.close(); print(f"JEPRET {NAMA} ok"); return
    if MODE == "TULIS":
        return await tulis()
    if MODE == "KONK":
        return await konk()
    lama, baru = json.load(open("/out/lama.json")), json.load(open("/out/baru.json"))
    print(f"LAMA {json.dumps(lama)}\nBARU {json.dumps(baru)}")
    U, P, X, D = baru["U"], baru["P"], baru["X"], baru["D"]
    cek(U["r"] == ["OK", "OK", "409"] and U["notes"] == "TES E2E ubah A", "(U) ubah: kunci sama = replay; badan beda = 409, isi tetap A",
        f"lama {lama['U']} / baru {U}")
    cek(P["r"] == ["OK", "OK"] and lama["P"]["r"][1] == "400" and P["efek_ulang"] == [] and "jurnal" in P["efek_1"],
        "(P) posting: kunci sama = replay, jurnal SEKALI, ulangan nol efek (LAMA: 400)", f"lama {lama['P']['r']} / baru {P['r']} efek1={P['efek_1']} ulang={P['efek_ulang']}")
    harap = "409" if MERAH else "OK"
    cek(X["r"][0] == harap and X["status"] == "void" and "jurnal" in X["efek_1"], "(X) kunci SAMA untuk aksi LAIN (void sesudah posting) = JALAN", str(X))
    cek(X["r"][1:] == ["OK", "409"] and X["efek_ulang"] == [], "(X) void ulang = replay nol efek; alasan beda = 409", str(X))
    cek(D["r"] == ["OK", "OK"] and lama["D"]["r"][1] == "404" and D["ada"] == 0 and D["efek_ulang"] == [] and "so_qty" in D["efek_1"]
        and D["audit_1"] >= 1, "(D) hapus draf dari SO: replay (LAMA 404); qty SO dilepas SEKALI; log hapus SEKALI", f"lama {lama['D']['r']} / {D}")
    cek(baru["G"] == ["400", "400"] and lama["G"] == baru["G"], "(G) hapus faktur POSTED berkunci: 400 2x (galat tak dicatat)", f"{lama['G']} / {baru['G']}")
    cek(setara(lama, baru), "(=) keadaan sesudah panggilan PERTAMA: LAMA == BARU (posting, void, hapus; U dikecualikan: lama menerima badan beda, baru 409 -- diuji di (U))",
        "" if setara(lama, baru) else str({kk: (lama[kk].get("keadaan1"), baru[kk].get("keadaan1")) for kk in "PXD"}))
    cek([lama[kk]["idem_1"] for kk in "PXD"] == [0, 0, 0] and [baru[kk]["idem_1"] for kk in "PXD"] == [1, 1, 1],
        "(=) baris idem panggilan PERTAMA: LAMA 0, BARU +1 (posting, void, hapus)", f"lama {[lama[kk]['idem_1'] for kk in 'PXD']} / baru {[baru[kk]['idem_1'] for kk in 'PXD']}")
    s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
    for kk in SQL_FIX:
        cek(s[kk] == e[kk], f"NET 0 rollback (sesudah proses): {kk}", "" if s[kk] == e[kk] else f"{s[kk]} / {e[kk]}")
    if not os.path.exists("/out/konk.json"):
        if MERAH or os.environ.get("LEWATI_KONK") == "1":
            print("INFO KONK dilewati")
        else:
            cek(False, "(K) KONK tak jalan")
        print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)
    k = json.load(open("/out/konk.json"))
    r1, r2 = k.get("R1", {}), k.get("R2", {})
    cek(not r1.get("macet") and r1.get("menang", [None])[0] == "OK" and r1.get("kalah", [None])[0] == "OK" and r1.get("tunggu", 0) >= 1.5
        and k.get("status_R1") == "draft", "(K1) hapus menang (rollback) || posting MENUNGGU lalu berhasil; tanpa macet", str(r1))
    cek(not r2.get("macet") and r2.get("menang", [None])[0] == "OK" and r2.get("kalah", [None])[0] == "400"
        and "sudah diterbitkan" in str(r2.get("kalah")) and r2.get("tunggu", 0) >= 1.5 and k.get("status_R2") in ("posted", "unpaid", "sent"),
        "(K2) posting menang (COMMIT) || hapus MENUNGGU lalu ditolak 'sudah diterbitkan' (dulu: race bisa menghapus)", str(r2))
    ks, ke = k["sebelum"], k["sesudah"]
    cek(k.get("bersih", [None])[0] == "OK" and k.get("status_akhir") == "void" and not k["sisa_manual"], "(K) pembersihan void lewat aplikasi; sisa_manual kosong", str(k.get("bersih")))
    cek(ks["gl"] == ke["gl"] and ks["ar"] == ke["ar"], "(K) NET uang 0: GL per akun + AR (compute_ar_rincian) sebelum == sesudah",
        "" if ks["gl"] == ke["gl"] else f"gl berubah | ar {ks['ar']} / {ke['ar']}")
    dj = int(ke["jurnal_semua"][0]) - int(ks["jurnal_semua"][0]); ds = int(ke["si_semua"][0]) - int(ks["si_semua"][0])
    print(f"DELTA KONK: faktur +{ds} (void) · jurnal +{dj} (total_debit {ks['jurnal_semua'][1]} -> {ke['jurnal_semua'][1]}; Σ per akun 0)")
    cek(ds == 1 and dj >= 2 and dj % 2 == 0, "(K) delta: +1 faktur void, jurnal berpasangan (posting + pembalik)", f"faktur +{ds}, jurnal +{dj}")
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
