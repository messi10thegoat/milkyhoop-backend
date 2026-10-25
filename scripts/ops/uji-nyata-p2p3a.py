"""UJI NYATA P2P-3a (25 Okt 2026): Nota Debit + Nota Kredit -- PATCH / DELETE idem + balapan hapus||posting ditutup. kaos SAJA.
TULIS (pohon LAMA & BARU, transaksi luar SELALU ROLLBACK): draf TES dibuat di dalam tx, lalu
  U  PATCH kunci K 2x (BARU replay) + badan beda kunci sama (BARU 409; LAMA menimpa)
  D  DELETE kunci K2 2x (BARU replay; LAMA 404)
  keadaan sesudah panggilan PERTAMA LAMA == BARU (catatan, keberadaan); baris idem LAMA 0 / BARU +1 diuji terpisah.
KONK (pohon yang diberikan, COMMIT 'TES E2E' -- izin MASTER, kaos saja): satu draf ND + satu draf NK, dua koneksi:
  R1 hapus menang (pegang kunci 2 dtk, ROLLBACK) || posting MENUNGGU lalu berhasil (ROLLBACK)
  R2 posting menang (COMMIT) || hapus MENUNGGU lalu DITOLAK 400 'sudah terbit' (ROLLBACK)
  pembersihan: void lewat APLIKASI (finally); daftar id dicetak untuk cek tangan; GL per akun + AP (compute_ap_rincian) +
  AR (compute_ar_rincian) sebelum == sesudah; delta tepat dilaporkan.
MERAH (salinan pohon tersabotase, oleh .sh): (ii) idem sesudah cek status -> replay hapus 404 = (D) merah; (i-b) hapus tanpa kunci/
  FOR UPDATE/syarat -> R2 hapus lolos = (K2) merah; (i-a) hanya tanpa syarat status -> tetap hijau (kunci saja cukup: pertahanan berlapis)."""
import asyncio, inspect, json, os, sys, time, uuid
from datetime import date
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request
from starlette.responses import Response

T = "kaos-biru-konveksi"
VENDOR = ("2a18aff0-f65a-472c-b835-762410f4c771", "PT Grosir Kaos")
PELANGGAN = ("d2d6d24b-1de1-4be0-8ef8-4cd057ca5617", "Toko Merdeka")
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
gagal = []
SQL_UANG = {
    "gl": """SELECT json_object_agg(k, s ORDER BY k)::text FROM (SELECT coa.account_code k, sum(jl.debit - jl.credit)::text s FROM journal_lines jl
             JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts coa ON coa.id = jl.account_id
             WHERE je.tenant_id = $1 AND je.status = 'POSTED' GROUP BY 1) x""",
    "ap": "SELECT COALESCE(sum(outstanding),0)::text, count(*) FROM compute_ap_rincian($1)",
    "ar": "SELECT COALESCE(sum(outstanding),0)::text, count(*) FROM compute_ar_rincian($1)",
    "nd": "SELECT count(*)::text || '/' || count(*) FILTER (WHERE status = 'void') FROM vendor_credits WHERE tenant_id = $1",
    "nk": "SELECT count(*)::text || '/' || count(*) FILTER (WHERE status = 'void') FROM credit_notes WHERE tenant_id = $1",
    "jurnal": "SELECT count(*) FROM journal_entries WHERE tenant_id = $1",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
}


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def req(uid, kunci=None):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid), "id": str(uid), "role": "OWNER"}; return r


def pool_untuk(conn, tanpa_kw=False):
    class K:
        def __getattr__(s, a): return getattr(conn, a)
        def transaction(s, **kw): return conn.transaction()
    k = K() if tanpa_kw else conn
    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False
    class Pool:
        def acquire(s): return CM()
    async def gp(): return Pool()
    return gp


async def uang(c):
    return {k: [str(v) for v in (await c.fetchrow(q, T)).values()] for k, q in SQL_UANG.items()}


def badan_nd(catatan):
    from app.schemas.vendor_credits import CreateVendorCreditRequest
    return CreateVendorCreditRequest(vendor_id=VENDOR[0], vendor_name=VENDOR[1], vendor_credit_date=date.today(), reason="other",
                                     notes=catatan, items=[{"description": "TES E2E P2P-3a", "quantity": 1, "unit_price": 1000}])


def badan_nk(catatan):
    from app.schemas.credit_notes import CreateCreditNoteRequest
    return CreateCreditNoteRequest(customer_id=PELANGGAN[0], customer_name=PELANGGAN[1], credit_note_date=date.today(), reason="other",
                                   notes=catatan, items=[{"description": "TES E2E P2P-3a", "quantity": 1, "unit_price": 1000}])


def jenis():
    from app.routers import vendor_credits as VC, credit_notes as CN
    from app.schemas.vendor_credits import UpdateVendorCreditRequest as UV, VoidVendorCreditRequest as VV
    from app.schemas.credit_notes import UpdateCreditNoteRequest as UC, VoidCreditNoteRequest as VN
    return {
        "nd": dict(mod=VC, buat=VC.create_vendor_credit, badan=badan_nd, ubah=VC.update_vendor_credit, UB=UV, hapus=VC.delete_vendor_credit,
                   post=VC.post_vendor_credit, void=VC.void_vendor_credit, VB=VV, tabel="vendor_credits"),
        "nk": dict(mod=CN, buat=CN.create_credit_note, badan=badan_nk, ubah=CN.update_credit_note, UB=UC, hapus=CN.delete_credit_note,
                   post=CN.post_credit_note, void=CN.void_credit_note, VB=VN, tabel="credit_notes"),
    }


def kw(fn):
    return {"response": Response()} if "response" in inspect.signature(fn).parameters else {}


def id_dari(r):
    d = r if isinstance(r, dict) else r.model_dump()
    return uuid.UUID(str((d.get("data") or {}).get("id")))


async def tulis():
    from fastapi import HTTPException
    J = jenis()
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    out = {}
    luar = c.transaction(); await luar.start()
    try:
        for nama, j in J.items():
            j["mod"].get_pool = pool_untuk(c, tanpa_kw=True)

            async def coba(aw):
                sp = c.transaction(); await sp.start()
                try:
                    await aw; await sp.commit(); return "OK"
                except HTTPException as e:
                    await sp.rollback(); return str(e.status_code)
            did = id_dari(await j["buat"](req(uid), j["badan"]("TES E2E awal"), **kw(j["buat"])))
            idem0 = await c.fetchval("SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1", T)
            u1 = await coba(j["ubah"](req(uid, f"P3A-{nama}-K"), did, j["UB"](notes="TES E2E ubah A"), **kw(j["ubah"])))
            catatan1 = await c.fetchval(f"SELECT notes FROM {j['tabel']} WHERE id = $1", did)
            idem1 = await c.fetchval("SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1", T)
            u2 = await coba(j["ubah"](req(uid, f"P3A-{nama}-K"), did, j["UB"](notes="TES E2E ubah A"), **kw(j["ubah"])))
            u3 = await coba(j["ubah"](req(uid, f"P3A-{nama}-K"), did, j["UB"](notes="TES E2E ubah B"), **kw(j["ubah"])))
            catatan = await c.fetchval(f"SELECT notes FROM {j['tabel']} WHERE id = $1", did)
            d1 = await coba(j["hapus"](req(uid, f"P3A-{nama}-K2"), did, **kw(j["hapus"])))
            ada1 = await c.fetchval(f"SELECT count(*) FROM {j['tabel']} WHERE id = $1", did)
            d2 = await coba(j["hapus"](req(uid, f"P3A-{nama}-K2"), did, **kw(j["hapus"])))
            out[nama] = {"U": [u1, u2, u3], "catatan1": catatan1, "catatan": catatan, "D": [d1, d2], "ada1": ada1,
                         "idem_1": idem1 - idem0}
        print(f"REKAM {NAMA} {json.dumps(out)}", flush=True)
    finally:
        await luar.rollback(); await c.close()
    json.dump(out, open(f"/out/t-{NAMA}.json", "w"))


async def konk():
    from fastapi import HTTPException
    J = jenis()
    TUNDA, BATAS = 2.0, 25.0
    adm = await asyncpg.connect(os.environ["DATABASE_URL"])
    uid = await adm.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    out = {"sebelum": await uang(adm), "id": {}, "sisa_manual": []}

    async def jalan(conn, j, fn_aw):
        j["mod"].get_pool = pool_untuk(conn, tanpa_kw=True)
        try:
            r = await fn_aw()
            return "OK", (r.get("message") if isinstance(r, dict) else getattr(r, "message", None))
        except HTTPException as e:
            return str(e.status_code), str(e.detail)[:120]

    try:
        for nama, j in J.items():
            j["mod"].get_pool = pool_untuk(adm, tanpa_kw=True)
            did = id_dari(await j["buat"](req(uid), j["badan"](f"TES E2E P2P-3a KONK {nama}"), **kw(j["buat"])))
            out["id"][nama] = str(did)

            async def ronde(menang, kalah):
                ca, cb = await asyncpg.connect(os.environ["DATABASE_URL"]), await asyncpg.connect(os.environ["DATABASE_URL"])
                hs = {}
                try:
                    async def pemenang():
                        tx = ca.transaction(); await tx.start()
                        hs["menang"] = await jalan(ca, j, lambda: menang[0](req(uid), did))
                        await asyncio.sleep(TUNDA)
                        await (tx.commit() if menang[1] else tx.rollback())

                    async def penantang():
                        await asyncio.sleep(0.4)
                        tx = cb.transaction(); await tx.start(); t0 = time.monotonic()
                        hs["kalah"] = await jalan(cb, j, lambda: kalah[0](req(uid), did))
                        hs["tunggu"] = round(time.monotonic() - t0, 2)
                        await (tx.commit() if kalah[1] else tx.rollback())
                    await asyncio.wait_for(asyncio.gather(pemenang(), penantang()), BATAS)
                except asyncio.TimeoutError:
                    hs["macet"] = True
                finally:
                    await ca.close(); await cb.close()
                return hs
            hapus = lambda r, i, j=j: j["hapus"](r, i, **kw(j["hapus"]))
            post = lambda r, i, j=j: j["post"](r, i, **kw(j["post"]))
            out[f"{nama}_R1"] = await ronde((hapus, False), (post, False))
            out[f"{nama}_st1"] = await adm.fetchval(f"SELECT status FROM {j['tabel']} WHERE id = $1", did)
            out[f"{nama}_R2"] = await ronde((post, True), (hapus, False))
            out[f"{nama}_st2"] = await adm.fetchval(f"SELECT status FROM {j['tabel']} WHERE id = $1", did)
    finally:
        for nama, j in J.items():
            if nama not in out["id"]:
                continue
            did = uuid.UUID(out["id"][nama])
            try:
                st = await adm.fetchval(f"SELECT status FROM {j['tabel']} WHERE id = $1", did)
                if st == "draft":
                    r = await jalan(adm, j, lambda: j["hapus"](req(uid), did, **kw(j["hapus"])))
                elif st not in ("void", None):
                    r = await jalan(adm, j, lambda: j["void"](req(uid), did, j["VB"](reason="TES E2E P2P-3a pembersihan"), **kw(j["void"])))
                else:
                    r = ("OK", f"status {st}")
                out[f"{nama}_bersih"] = [r[0], r[1], st]
                if r[0] != "OK":
                    out["sisa_manual"].append(f"{nama} {did} status {st}: {r}")
            except Exception as e:  # noqa: BLE001
                out["sisa_manual"].append(f"{nama} {did}: pembersihan galat {type(e).__name__}: {e}")
            out[f"{nama}_akhir"] = await adm.fetchval(f"SELECT status FROM {j['tabel']} WHERE id = $1", did)
        out["sesudah"] = await uang(adm)
        await adm.close()
    print(f"KONK {NAMA} {json.dumps({k: v for k, v in out.items() if k not in ('sebelum', 'sesudah')})}", flush=True)
    print(f"CEK-TANGAN id TES E2E: {out['id']} -> akhir nd={out.get('nd_akhir')} nk={out.get('nk_akhir')}", flush=True)
    json.dump(out, open(f"/out/k-{NAMA}.json", "w"), default=str)


def banding():
    lama, baru = json.load(open("/out/t-lama.json")), json.load(open("/out/t-baru.json"))
    for n in ("nd", "nk"):
        L, B = lama[n], baru[n]
        cek(B["U"] == ["OK", "OK", "409"] and B["catatan"] == "TES E2E ubah A" and L["U"] == ["OK", "OK", "OK"] and L["catatan"] == "TES E2E ubah B",
            f"({n}-U) ubah: kunci sama = replay; badan beda = 409, isi tetap A (LAMA menimpa B)", f"lama {L['U']} {L['catatan']} / baru {B['U']} {B['catatan']}")
        cek(B["D"] == ["OK", "OK"] and L["D"] == ["OK", "404"] and B["ada1"] == L["ada1"] == 0,
            f"({n}-D) hapus: kunci sama = replay (LAMA 404); terhapus sesudah panggilan pertama", f"lama {L['D']} / baru {B['D']}")
        cek(L["catatan1"] == B["catatan1"] == "TES E2E ubah A", f"({n}=) keadaan sesudah ubah PERTAMA LAMA == BARU")
        cek(L["idem_1"] == 0 and B["idem_1"] == 1, f"({n}=) baris idem ubah pertama: LAMA 0, BARU +1", f"{L['idem_1']} / {B['idem_1']}")
    pemb = os.environ.get("PEMBANDING_K", "baru")
    if not os.path.exists(f"/out/k-{pemb}.json"):
        return
    k = json.load(open(f"/out/k-{pemb}.json"))
    for n in ("nd", "nk"):
        r1, r2 = k.get(f"{n}_R1", {}), k.get(f"{n}_R2", {})
        cek(not r1.get("macet") and r1.get("menang", [""])[0] == "OK" and r1.get("kalah", [""])[0] == "OK" and r1.get("tunggu", 0) >= 1.0,
            f"({n}-K1) hapus menang (ROLLBACK) || posting MENUNGGU lalu berhasil", str(r1))
        cek(not r2.get("macet") and r2.get("menang", [""])[0] == "OK" and r2.get("kalah", [""])[0] == "400" and r2.get("tunggu", 0) >= 1.0,
            f"({n}-K2) posting menang (COMMIT) || hapus MENUNGGU lalu DITOLAK 400 (dulu: bisa menghapus dokumen terbit)", str(r2))
        cek(k.get(f"{n}_akhir") == "void", f"({n}-K) pembersihan void lewat aplikasi", str(k.get(f"{n}_bersih")))
    cek(not k["sisa_manual"], "(K) sisa_manual kosong", str(k["sisa_manual"]))
    s, e = k["sebelum"], k["sesudah"]
    cek(s["gl"] == e["gl"] and s["ap"] == e["ap"] and s["ar"] == e["ar"], "(K) NET uang 0: GL per akun + AP + AR sebelum == sesudah",
        "" if s["gl"] == e["gl"] else "GL beda")
    print(f"DELTA KONK: ND {s['nd']} -> {e['nd']} (total/void) · NK {s['nk']} -> {e['nk']} · jurnal {s['jurnal'][0]} -> {e['jurnal'][0]}", flush=True)


async def main():
    if MODE == "TULIS":
        return await tulis()
    if MODE == "KONK":
        return await konk()
    banding()
    print(f"RINGKAS gagal={len(gagal)} {gagal}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
