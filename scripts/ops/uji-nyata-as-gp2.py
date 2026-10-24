"""UJI NYATA AS-GP2 (24 Okt 2026): idempotensi aksi Faktur Pembelian (post / void / hapus / ubah v2) + kunci baris ubah v2. kaos SAJA.
TULIS (pohon LAMA & BARU): SATU transaksi luar SELALU di-ROLLBACK (get_pool tertambal), tiap skenario savepoint sendiri:
  U ubah v2 kunci K 2x badan sama (BARU replay) + badan beda (BARU 409)   P posting kunci K 2x (BARU replay, 1 jurnal; LAMA 400)
  X kunci SAMA dipakai VOID sesudah posting = aksi lain -> JALAN; void ulang = replay; alasan beda = 409 (FE CW: SATU kunci per buka halaman)
  D hapus draf kunci K 2x (BARU replay; LAMA 404). LAMA == BARU untuk baris jurnal posting & pembalik.
KONK (pohon BARU saja, COMMIT 'TES E2E', izin MASTER 24 Okt): SATU tagihan draf invoice_number TES-E2E-GP2-KONK, dua koneksi:
  R1 ubah menang (pegang FOR UPDATE 2 dtk, COMMIT) || posting menunggu lalu melihat draf TERUBAH -> posting di-ROLLBACK (draf tetap)
  R2 posting menang (2 dtk, COMMIT) || ubah menunggu lalu blok BUKAN_DRAF. Batas 20 dtk per langkah (macet = FAIL). Pembersihan: void lewat
  aplikasi (finally: draf -> hapus, posted -> void; sisa = sisa_manual). Bukti net: saldo AP (compute_ap_rincian) + GL per akun sebelum == sesudah;
  delta tepat dilaporkan (+1 tagihan void, +2 jurnal Σ 0, +2 nomor jurnal).
MERAH=1: harapan X (aksi lain kunci sama) dibalik -> WAJIB gagal."""
import asyncio, inspect, json, os, sys, time, uuid
from datetime import date, timedelta
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
VENDOR, BARANG = "73a2b4bd-72d3-46cb-8adb-79f14f9e28e7", "862be8e3-b35a-48e3-b8c3-c200f75feba9"
MODE, NAMA, MERAH = os.environ["MODE"], os.environ.get("NAMA", ""), os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


SQL_JEPRET = {
    "tagihan": "SELECT count(*), json_agg(json_build_object('n', invoice_number, 's', status_v2) ORDER BY invoice_number)::text FROM bills WHERE tenant_id = $1",
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0)::text FROM journal_entries WHERE tenant_id = $1",
    "gl": """SELECT json_object_agg(k, s ORDER BY k)::text FROM (SELECT coa.account_code k, sum(jl.debit - jl.credit)::text s FROM journal_lines jl
             JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts coa ON coa.id = jl.account_id
             WHERE je.tenant_id = $1 AND je.status = 'POSTED' GROUP BY 1) x""",
    "ap": "SELECT COALESCE(sum(outstanding),0)::text, count(*) FROM compute_ap_rincian($1)",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
    "seq": "SELECT json_agg(json_build_object('p', prefix, 'n', last_number) ORDER BY prefix, year, month)::text FROM journal_number_sequences WHERE tenant_id = $1",
    "po": "SELECT COALESCE(sum(quantity_billed),0)::text FROM purchase_order_items poi JOIN purchase_orders po ON po.id = poi.po_id WHERE po.tenant_id = $1",
}


async def jepret(c):
    out = {}
    for kk, q in SQL_JEPRET.items():
        try:
            out[kk] = [str(v) for v in (await c.fetchrow(q, T)).values()]
        except asyncpg.PostgresError as e:  # kolom PO bisa beda nama -> catat galatnya, jangan diam
            out[kk] = [f"GALAT {type(e).__name__}"]
    return out


def req(uid, kunci=None):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


def n(v):
    return str(Decimal(str(v)).quantize(Decimal("0.01")))


def badan_draf(nomor):
    from app.schemas.bills import CreateBillRequestV2 as CR
    return CR(vendor_id=VENDOR, invoice_number=nomor, due_date=date.today() + timedelta(days=30), notes="TES E2E AS-GP2",
              items=[{"product_id": BARANG, "product_name": "TES E2E barang", "qty": 1, "price": 10000}])


async def tulis():
    from fastapi import HTTPException
    from starlette.responses import Response
    from app.routers import bills as BR
    from app.schemas.bills import UpdateBillRequestV2 as UR, VoidBillRequest as VR
    baru = "response" in inspect.signature(BR.post_bill).parameters
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)

    class Kon:
        def __getattr__(s, a): return getattr(c, a)
        def transaction(s, **kw): return c.transaction()
    k = Kon()
    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False
    class Pool:
        def acquire(s): return CM()
    async def gp(): return Pool()
    BR.get_pool = gp
    kw = lambda: {"response": Response()} if baru else {}

    async def coba(aw):
        sp = c.transaction(); await sp.start()
        try:
            await aw; await sp.commit(); return "OK"
        except HTTPException as e:
            await sp.rollback(); return str(e.status_code)
        except Exception as e:  # noqa: BLE001
            await sp.rollback(); return f"GALAT {type(e).__name__}: {str(e)[:100]}"

    async def draf(tag):
        nomor = f"TES-E2E-GP2-{tag}"
        await BR.create_bill_v2(req(uid), badan_draf(nomor))
        return await c.fetchval("SELECT id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, nomor)

    async def baris(jid):
        if not jid:
            return []
        return sorted([r["account_code"], n(r["debit"]), n(r["credit"])] for r in await c.fetch(
            """SELECT coa.account_code, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id
               WHERE jl.journal_id = $1""", jid))

    async def jurnal_bill(bid, st):
        return [r["id"] for r in await c.fetch("SELECT id FROM journal_entries WHERE tenant_id = $1 AND source_id = $2 AND source_type = $3 AND status = 'POSTED'",
                                                T, bid, st)]

    h = {"baru": baru}
    tx = c.transaction(); await tx.start()
    try:
        sp = c.transaction(); await sp.start()
        try:
            b = await draf("U")
            r1 = await coba(BR.update_bill_v2(req(uid, "TESGP2-K"), b, UR(notes="TES E2E ubah A"), **kw()))
            r2 = await coba(BR.update_bill_v2(req(uid, "TESGP2-K"), b, UR(notes="TES E2E ubah A"), **kw()))
            r3 = await coba(BR.update_bill_v2(req(uid, "TESGP2-K"), b, UR(notes="TES E2E ubah B"), **kw()))
            h["U"] = {"r1": r1, "r2": r2, "r3": r3, "notes": await c.fetchval("SELECT notes FROM bills WHERE id = $1", b)}
            # P lalu X di tagihan yang SAMA dgn kunci yang SAMA (pola FE CW: satu kunci per buka halaman)
            p1 = await coba(BR.post_bill(req(uid, "TESGP2-K2"), b, **kw()))
            p2 = await coba(BR.post_bill(req(uid, "TESGP2-K2"), b, **kw()))
            jp = await jurnal_bill(b, "BILL")
            x1 = await coba(BR.void_bill(req(uid, "TESGP2-K2"), b, VR(reason="TES E2E void"), **kw()))
            x2 = await coba(BR.void_bill(req(uid, "TESGP2-K2"), b, VR(reason="TES E2E void"), **kw()))
            x3 = await coba(BR.void_bill(req(uid, "TESGP2-K2"), b, VR(reason="alasan lain"), **kw()))
            jv = await jurnal_bill(b, "BILL_VOID")
            h["P"] = {"r1": p1, "r2": p2, "n_jurnal": len(jp), "baris": await baris(jp[0] if jp else None)}
            h["X"] = {"r1": x1, "r2": x2, "r3": x3, "n_balik": len(jv), "baris": await baris(jv[0] if jv else None),
                      "status": await c.fetchval("SELECT status_v2 FROM bills WHERE id = $1", b)}
        finally:
            await sp.rollback()
        sp = c.transaction(); await sp.start()
        try:
            b = await draf("D")
            d1 = await coba(BR.delete_bill(req(uid, "TESGP2-K3"), b, **kw()))
            d2 = await coba(BR.delete_bill(req(uid, "TESGP2-K3"), b, **kw()))
            h["D"] = {"r1": d1, "r2": d2, "ada": await c.fetchval("SELECT count(*) FROM bills WHERE id = $1", b)}
        finally:
            await sp.rollback()
        print(f"REKAM {NAMA} {json.dumps(h)}", flush=True)
    finally:
        await tx.rollback(); await c.close()
    json.dump(h, open(f"/out/{NAMA}.json", "w"))


async def konk():
    """Dua koneksi nyata, COMMIT 'TES E2E'. Penahan = pemenang memegang kunci baris dalam transaksi luar lalu tidur 2 dtk sebelum COMMIT."""
    from app.services.bills_service import BillsService
    from app.routers import bills as BR
    TUNDA, BATAS = 2.0, 20.0
    sisa_manual, out = [], {}
    adm = await asyncpg.connect(os.environ["DATABASE_URL"])
    uid = await adm.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    out["sebelum"] = await jepret(adm)

    def svc(conn):
        class CM:
            async def __aenter__(s): return conn
            async def __aexit__(s, *a): return False
        class Pool:
            def acquire(s): return CM()
        return BillsService(Pool())

    nomor = f"TES-E2E-GP2-KONK-{uuid.uuid4().hex[:6]}"
    bid = None
    try:
        class CMa:
            async def __aenter__(s): return adm
            async def __aexit__(s, *a): return False
        class PoolA:
            def acquire(s): return CMa()
        async def gp(): return PoolA()
        BR.get_pool = gp
        await BR.create_bill_v2(req(uid), badan_draf(nomor))  # COMMIT (koneksi tanpa transaksi luar)
        bid = await adm.fetchval("SELECT id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, nomor)

        async def ronde(menang, kalah, commit_kalah):
            ca, cb = await asyncpg.connect(os.environ["DATABASE_URL"]), await asyncpg.connect(os.environ["DATABASE_URL"])
            hasil = {}
            try:
                async def pemenang():
                    tx = ca.transaction(); await tx.start()
                    r = await menang(svc(ca)); await asyncio.sleep(TUNDA); await tx.commit()
                    hasil["menang"] = r.get("success"), r.get("message")
                async def penantang():
                    await asyncio.sleep(0.4)
                    tx = cb.transaction(); await tx.start(); t0 = time.monotonic()
                    r = await kalah(svc(cb)); hasil["tunggu"] = round(time.monotonic() - t0, 2)
                    hasil["kalah"] = r.get("success"), r.get("message")
                    hasil["kalah_notes"] = await cb.fetchval("SELECT notes FROM bills WHERE id = $1", bid)
                    await (tx.commit() if commit_kalah else tx.rollback())
                await asyncio.wait_for(asyncio.gather(pemenang(), penantang()), BATAS)
            except asyncio.TimeoutError:
                hasil["macet"] = True
            finally:
                await ca.close(); await cb.close()
            return hasil

        ubah = lambda s: s.update_bill_v2(T, bid, {"notes": "TES E2E ubah KONK"}, uid)
        post = lambda s: s.post_bill(T, bid, uid)
        out["R1"] = await ronde(ubah, post, commit_kalah=False)   # ubah menang -> posting menunggu, lihat draf terubah, di-ROLLBACK
        out["status_R1"] = await adm.fetchval("SELECT status_v2 FROM bills WHERE id = $1", bid)
        out["R2"] = await ronde(post, ubah, commit_kalah=False)   # posting menang (COMMIT) -> ubah menunggu -> BUKAN_DRAF
        out["status_R2"] = await adm.fetchval("SELECT status_v2 FROM bills WHERE id = $1", bid)
    finally:
        try:
            if bid:
                st = await adm.fetchval("SELECT status_v2 FROM bills WHERE id = $1", bid)
                s = svc(adm)
                if st == "posted":
                    r = await s.void_bill(T, bid, {"reason": "TES E2E AS-GP2 pembersihan"}, uid)
                elif st == "draft":
                    r = await s.delete_bill(T, bid, uid)
                else:
                    r = {"success": True, "message": f"status {st}"}
                out["bersih"] = r.get("success"), r.get("message"), st
                if not r.get("success"):
                    sisa_manual.append(f"{nomor} {bid} status {st}: {r.get('message')}")
        except Exception as e:  # noqa: BLE001
            sisa_manual.append(f"{nomor} {bid}: pembersihan galat {type(e).__name__}: {e}")
        out["sisa_manual"] = sisa_manual
        out["sesudah"] = await jepret(adm)
        out["status_akhir"] = await adm.fetchval("SELECT status_v2 FROM bills WHERE id = $1", bid) if bid else None
        await adm.close()
    print(f"KONK {json.dumps(out, default=str)}", flush=True)
    json.dump(out, open("/out/konk.json", "w"), default=str)


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
    cek(baru["U"]["r1"] == "OK" and baru["U"]["r2"] == "OK" and baru["U"]["r3"] == "409" and baru["U"]["notes"] == "TES E2E ubah A",
        "(U) ubah v2: kunci sama = replay; badan beda = 409, isi tak berubah (LAMA: badan B ditulis)", f"{lama['U']} / {baru['U']}")
    cek(baru["P"] == {**baru["P"], "r1": "OK", "r2": "OK", "n_jurnal": 1} and lama["P"]["r2"] == "400",
        "(P) posting: kunci sama = replay, 1 jurnal (LAMA: 400)", f"{lama['P']['r2']} / {baru['P']['r2']} n={baru['P']['n_jurnal']}")
    cek(lama["P"]["baris"] == baru["P"]["baris"] and baru["P"]["baris"], "(P) baris jurnal posting LAMA == BARU", str(baru["P"]["baris"]))
    harap_x1 = "409" if MERAH else "OK"
    cek(baru["X"]["r1"] == harap_x1 and baru["X"]["status"] == "void" and baru["X"]["n_balik"] == 1,
        "(X) kunci SAMA untuk aksi LAIN (void sesudah posting) = JALAN, tak 409/replay posting", str(baru["X"]))
    cek(baru["X"]["r2"] == "OK" and baru["X"]["r3"] == "409", "(X) void ulang kunci sama = replay; alasan beda = 409", str(baru["X"]))
    cek(lama["X"]["baris"] == baru["X"]["baris"] and baru["X"]["baris"], "(X) baris jurnal pembalik LAMA == BARU", str(baru["X"]["baris"]))
    cek(baru["D"] == {"r1": "OK", "r2": "OK", "ada": 0} and lama["D"]["r2"] == "404", "(D) hapus draf: kunci sama = replay (LAMA: 404)",
        f"{lama['D']} / {baru['D']}")
    s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
    for kk in SQL_JEPRET:
        cek(s[kk] == e[kk] and "GALAT" not in s[kk][0], f"NET 0 rollback (sesudah proses): {kk}", "" if s[kk] == e[kk] else f"{s[kk]} / {e[kk]}")
    if not os.path.exists("/out/konk.json"):
        cek(False, "(K) KONK tak dijalankan (MERAH=1 sengaja melewatinya: tanpa COMMIT kedua)")
        print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1)
    k = json.load(open("/out/konk.json"))
    r1, r2 = k.get("R1", {}), k.get("R2", {})
    cek(not r1.get("macet") and r1.get("menang", [None])[0] is True and r1.get("kalah", [None])[0] is True and r1.get("tunggu", 0) >= 1.5
        and r1.get("kalah_notes") == "TES E2E ubah KONK" and k.get("status_R1") == "draft",
        "(K1) ubah menang || posting MENUNGGU (>= tunda) lalu melihat draf TERUBAH; tanpa macet", str(r1))
    cek(not r2.get("macet") and r2.get("menang", [None])[0] is True and r2.get("kalah", [None])[0] is False
        and "Only draft bills can be edited" in str(r2.get("kalah")) and r2.get("tunggu", 0) >= 1.5 and k.get("status_R2") == "posted",
        "(K2) posting menang (COMMIT) || ubah MENUNGGU lalu blok BUKAN_DRAF; tanpa macet", str(r2))
    ks, ke = k["sebelum"], k["sesudah"]
    cek(k.get("bersih", [False])[0] is True and k.get("status_akhir") == "void" and not k["sisa_manual"], "(K) pembersihan: tagihan TES E2E di-VOID lewat aplikasi, sisa_manual kosong", str(k.get("bersih")))
    cek(ks["gl"] == ke["gl"] and ks["ap"] == ke["ap"], "(K) NET uang 0: GL per akun & saldo AP (compute_ap_rincian) sebelum == sesudah",
        "" if ks["gl"] == ke["gl"] else f"gl berubah | ap {ks['ap']} / {ke['ap']}")
    dj = int(ke["jurnal"][0]) - int(ks["jurnal"][0]); dsum = Decimal(ke["jurnal"][1]) - Decimal(ks["jurnal"][1])
    dt = int(ke["tagihan"][0]) - int(ks["tagihan"][0])
    print(f"DELTA KONK: tagihan +{dt} (void) · jurnal +{dj} (total_debit +{dsum}, Σ debit-kredit per akun 0) · seq {ks['seq']} -> {ke['seq']}")
    cek(dt == 1 and dj == 2, "(K) delta tepat: +1 tagihan void, +2 jurnal (posting + pembalik)", f"tagihan +{dt}, jurnal +{dj}")
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
