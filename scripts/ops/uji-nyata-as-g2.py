"""UJI NYATA AS-G2 (24 Okt 2026): idempotensi aksi Pesanan Penjualan (konfirmasi / batal / tutup / hapus). kaos SAJA, SATU transaksi luar
SELALU di-ROLLBACK (get_pool tertambal), tiap skenario savepoint sendiri. Pohon LAMA & BARU direkam; BARU dinilai.
  C konfirmasi draf SO-2609-0312 kunci K 2x: BARU replay (LAMA 400); kode order TAK terbit ulang (order_code + order_code_events/counters tetap)
  X kunci SAMA dipakai TUTUP sesudah konfirmasi (aksi lain) = JALAN; tutup ulang = replay; alasan beda = 409
  B0 batal SO-2609-0003 DIBLOK (UM draf) kunci K4 2x: 400 dua-duanya (galat tak dicatat -> bukan replay/409)
  B batal SO-2609-0024 (confirmed, ber-UM + proforma terbit) kunci K2 2x: BARU replay (LAMA 400); proforma batal SEKALI; tautan UM tak berubah;
    riwayat (audit_logs) +1 SO +n proforma SEKALI; alasan beda = 409
  D hapus draf SO-2609-0001 kunci K3 2x: BARU replay (LAMA 404); log hapus (audit_logs trg_log_deletion) SEKALI
Efek samping per aksi diukur SESUDAH panggilan pertama dan SESUDAH ulangan: ulangan = nol efek baru. LAMA == BARU untuk keadaan sesudah panggilan
pertama. NET 0 (proses terpisah, hanya baris fixture). MERAH=1: harapan X dibalik -> WAJIB gagal."""
import asyncio, inspect, json, os, sys, uuid
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
SO_C, SO_B, SO_D = "c14cf875-f55e-40ff-a0f6-c7f988a018e2", "ecacc69b-7e52-4526-ba69-22a199065474", "3a1d8ebe-32c9-40bc-800d-580f85266957"
SO_BLOK = "368efdeb-a344-455d-a54f-bbdf4058ddcd"  # SO-2609-0003: batal DIBLOK SO_DEPOSIT_DRAFT (UM draf) -> galat berkunci TAK dicatat
FIX = [SO_C, SO_B, SO_D, SO_BLOK]
MODE, NAMA, MERAH = os.environ["MODE"], os.environ.get("NAMA", ""), os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


ENT = """SELECT x::text FROM unnest($2::uuid[]) x UNION SELECT p.id::text FROM proformas p WHERE p.sales_order_id = ANY($2::uuid[])
         UNION SELECT d.id::text FROM customer_deposits d WHERE d.sales_order_id = ANY($2::uuid[])"""
SQL_JEPRET = {
    "so": "SELECT json_agg(json_build_object('n', order_number, 's', status, 'k', order_code) ORDER BY order_number)::text FROM sales_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
    "pf": "SELECT json_agg(json_build_object('n', proforma_number, 's', status) ORDER BY proforma_number)::text FROM proformas WHERE tenant_id = $1 AND sales_order_id = ANY($2::uuid[])",
    "um": "SELECT json_agg(json_build_object('n', deposit_number, 's', status, 'so', sales_order_id, 'a', amount) ORDER BY deposit_number)::text FROM customer_deposits WHERE tenant_id = $1 AND sales_order_id = ANY($2::uuid[])",
    # penghitung tanpa updated_at (= NOW() transaksi; beda tiap jalan, bukan isi -- diukur 24 Okt)
    "kode": "SELECT (SELECT count(*) FROM order_code_events WHERE tenant_id = $1), (SELECT json_agg(json_build_object('p', c.period_key, 'n', c.last_seq) ORDER BY c.period_key)::text FROM order_code_counters c WHERE c.tenant_id = $1) WHERE $2::uuid[] IS NOT NULL",
    "audit": f"SELECT count(*) FROM audit_logs WHERE entity_id::text IN ({ENT}) AND $1::text IS NOT NULL",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1 AND $2::uuid[] IS NOT NULL",
}


async def jepret(c):
    ids = [uuid.UUID(x) for x in FIX]
    return {k: [str(v) for v in (await c.fetchrow(q, T, ids)).values()] for k, q in SQL_JEPRET.items()}


def req(uid, kunci=None):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


async def tulis():
    from fastapi import HTTPException
    from starlette.responses import Response
    from app.routers import sales_orders as SR
    from app.schemas.sales_orders import CancelSalesOrderRequest as BR, CloseSalesOrderRequest as TR
    baru = "response" in inspect.signature(SR.confirm_sales_order).parameters
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    for so, st in ((SO_C, "draft"), (SO_B, "confirmed"), (SO_D, "draft"), (SO_BLOK, "confirmed")):
        assert await c.fetchval("SELECT status FROM sales_orders WHERE id = $1 AND tenant_id = $2", uuid.UUID(so), T) == st, f"prasyarat {so} {st}"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)

    class Kon:
        def __getattr__(s, a): return getattr(c, a)
        def transaction(s, **kw): return c.transaction()
    k = Kon()
    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False
        def __await__(s): return asyncio.sleep(0, k).__await__()
    class Pool:
        def acquire(s): return CM()
    async def gp(): return Pool()
    SR.get_pool = gp
    kw = lambda: {"response": Response()} if baru else {}

    async def coba(aw):
        sp = c.transaction(); await sp.start()
        try:
            r = await aw; await sp.commit()
            return "OK", (r.get("data") if isinstance(r, dict) else getattr(r, "data", None))
        except HTTPException as e:
            await sp.rollback(); return str(e.status_code), None
        except Exception as e:  # noqa: BLE001
            await sp.rollback(); return f"GALAT {type(e).__name__}: {str(e)[:100]}", None

    async def efek():
        j = await jepret(c)
        return {kk: j[kk] for kk in ("so", "pf", "um", "kode", "audit")}

    h = {"baru": baru}
    tx = c.transaction(); await tx.start()
    try:
        sp = c.transaction(); await sp.start()
        try:
            e0 = await efek()
            r1, d1 = await coba(SR.confirm_sales_order(req(uid, "TESG2-K"), SO_C, **kw()))
            e1 = await efek()
            r2, d2 = await coba(SR.confirm_sales_order(req(uid, "TESG2-K"), SO_C, **kw()))
            e2 = await efek()
            h["C"] = {"r1": r1, "r2": r2, "kode1": (d1 or {}).get("order_code"), "kode2": (d2 or {}).get("order_code"),
                      "efek_berubah_1": [kk for kk in e0 if e0[kk] != e1[kk]], "efek_berubah_ulang": [kk for kk in e1 if e1[kk] != e2[kk]],
                      "status": await c.fetchval("SELECT status FROM sales_orders WHERE id = $1", uuid.UUID(SO_C)), "keadaan1": e1,
                      # medan yang DIKECUALIKAN dari perbandingan, direkam mentah untuk bukti tak-deterministik (2 jalan pohon LAMA)
                      "kode_updated_at": str(await c.fetchval("SELECT max(updated_at) FROM order_code_counters WHERE tenant_id = $1", T))}
            x1, _ = await coba(SR.close_sales_order(req(uid, "TESG2-K"), SO_C, TR(reason="TES E2E tutup"), **kw()))
            e3 = await efek()
            x2, _ = await coba(SR.close_sales_order(req(uid, "TESG2-K"), SO_C, TR(reason="TES E2E tutup"), **kw()))
            x3, _ = await coba(SR.close_sales_order(req(uid, "TESG2-K"), SO_C, TR(reason="alasan lain"), **kw()))
            e4 = await efek()
            h["X"] = {"r1": x1, "r2": x2, "r3": x3, "status": await c.fetchval("SELECT status FROM sales_orders WHERE id = $1", uuid.UUID(SO_C)),
                      "efek_berubah_1": [kk for kk in e2 if e2[kk] != e3[kk]], "efek_berubah_ulang": [kk for kk in e3 if e3[kk] != e4[kk]]}
        finally:
            await sp.rollback()
        sp = c.transaction(); await sp.start()
        try:
            g1, _ = await coba(SR.cancel_sales_order(req(uid, "TESG2-K4"), SO_BLOK, BR(reason="TES E2E batal"), **kw()))
            g2, _ = await coba(SR.cancel_sales_order(req(uid, "TESG2-K4"), SO_BLOK, BR(reason="TES E2E batal"), **kw()))
            h["B0"] = {"r1": g1, "r2": g2}
        finally:
            await sp.rollback()
        sp = c.transaction(); await sp.start()
        try:
            e0 = await efek()
            b1, db1 = await coba(SR.cancel_sales_order(req(uid, "TESG2-K2"), SO_B, BR(reason="TES E2E batal"), **kw()))
            e1 = await efek()
            b2, _ = await coba(SR.cancel_sales_order(req(uid, "TESG2-K2"), SO_B, BR(reason="TES E2E batal"), **kw()))
            b3, _ = await coba(SR.cancel_sales_order(req(uid, "TESG2-K2"), SO_B, BR(reason="alasan lain"), **kw()))
            e2 = await efek()
            h["B"] = {"r1": b1, "r2": b2, "r3": b3, "pf_batal": len((db1 or {}).get("proformas_cancelled") or []),
                      "um_sisa": len((db1 or {}).get("deposits_remaining") or []),
                      "audit_tambah_1": int(e1["audit"][0]) - int(e0["audit"][0]), "audit_tambah_ulang": int(e2["audit"][0]) - int(e1["audit"][0]),
                      "um_sama": e0["um"] == e1["um"] == e2["um"], "pf_ulang_sama": e1["pf"] == e2["pf"],
                      "efek_berubah_ulang": [kk for kk in e1 if e1[kk] != e2[kk]], "keadaan1": e1}
        finally:
            await sp.rollback()
        sp = c.transaction(); await sp.start()
        try:
            e0 = await efek()
            d1r, _ = await coba(SR.delete_sales_order(req(uid, "TESG2-K3"), SO_D, **kw()))
            e1 = await efek()
            d2r, _ = await coba(SR.delete_sales_order(req(uid, "TESG2-K3"), SO_D, **kw()))
            e2 = await efek()
            h["D"] = {"r1": d1r, "r2": d2r, "ada": await c.fetchval("SELECT count(*) FROM sales_orders WHERE id = $1", uuid.UUID(SO_D)),
                      "audit_tambah_1": int(e1["audit"][0]) - int(e0["audit"][0]), "audit_tambah_ulang": int(e2["audit"][0]) - int(e1["audit"][0])}
        finally:
            await sp.rollback()
        print(f"REKAM {NAMA} {json.dumps(h)}", flush=True)
    finally:
        await tx.rollback(); await c.close()
    json.dump(h, open(f"/out/{NAMA}.json", "w"))


async def main():
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump(await jepret(c), open(f"/out/{NAMA}.json", "w")); await c.close(); print(f"JEPRET {NAMA} ok"); return
    if MODE == "TULIS":
        return await tulis()
    lama, baru = json.load(open("/out/lama.json")), json.load(open("/out/baru.json"))
    lama2 = json.load(open("/out/lama2.json"))
    # Bukti pengecualian order_code_counters.updated_at: DUA jalan pohon LAMA (kode sama) -> updated_at BEDA, semua medan bisnis SAMA
    cek(lama["C"]["kode_updated_at"] != lama2["C"]["kode_updated_at"]
        and all(lama[s]["keadaan1"] == lama2[s]["keadaan1"] for s in ("C", "B")) and lama["C"]["kode1"] == lama2["C"]["kode1"],
        "(=0) order_code_counters.updated_at TAK DETERMINISTIK: beda antar 2 jalan LAMA, medan bisnis (kode/last_seq/events/so/pf/um/audit) sama",
        f"{lama['C']['kode_updated_at']} / {lama2['C']['kode_updated_at']} kode {lama['C']['kode1']}/{lama2['C']['kode1']}")
    print(f"LAMA {json.dumps(lama)}\nBARU {json.dumps(baru)}")
    C, X, B, D = baru["C"], baru["X"], baru["B"], baru["D"]
    cek(C["r1"] == "OK" and C["r2"] == "OK" and lama["C"]["r2"] == "400" and C["status"] == "confirmed",
        "(C) konfirmasi: kunci sama = replay (LAMA: 400)", f"lama r2={lama['C']['r2']} / baru {C['r1']},{C['r2']}")
    cek(C["kode1"] and C["kode2"] == C["kode1"] and C["efek_berubah_ulang"] == [] and "kode" in C["efek_berubah_1"],
        "(C) kode order terbit SEKALI; ulangan = nol efek baru (so/pf/um/kode/audit tak berubah)", f"kode {C['kode1']}/{C['kode2']} efek1={C['efek_berubah_1']} ulang={C['efek_berubah_ulang']}")
    harap_x = "409" if MERAH else "OK"
    cek(X["r1"] == harap_x and X["status"] == "completed", "(X) kunci SAMA untuk aksi LAIN (tutup sesudah konfirmasi) = JALAN", str(X))
    cek(X["r2"] == "OK" and X["r3"] == "409" and X["efek_berubah_ulang"] == [], "(X) tutup ulang = replay nol efek; alasan beda = 409", str(X))
    cek(B["r1"] == "OK" and B["r2"] == "OK" and B["r3"] == "409" and lama["B"]["r2"] == "400", "(B) batal: kunci sama = replay; alasan beda = 409 (LAMA: 400)",
        f"lama r2={lama['B']['r2']} / baru {B['r1']},{B['r2']},{B['r3']}")
    cek(B["pf_batal"] >= 1 and B["audit_tambah_1"] == 1 + B["pf_batal"] and B["audit_tambah_ulang"] == 0,
        "(B) proforma batal + riwayat (1 SO + n proforma) SEKALI; ulangan nol riwayat", f"pf={B['pf_batal']} audit +{B['audit_tambah_1']} ulang +{B['audit_tambah_ulang']}")
    cek(B["um_sama"] and B["um_sisa"] >= 1 and B["pf_ulang_sama"] and B["efek_berubah_ulang"] == [],
        "(B) tautan uang muka TAK berubah (tetap saldo pelanggan); proforma tak disentuh ulang", f"um_sisa={B['um_sisa']} ulang={B['efek_berubah_ulang']}")
    cek(baru["B0"] == {"r1": "400", "r2": "400"} and lama["B0"] == baru["B0"], "(B0) batal DIBLOK berkunci: galat TAK dicatat (ulang = 400 lagi, bukan replay/409)",
        f"{lama['B0']} / {baru['B0']}")
    cek(D["r1"] == "OK" and D["r2"] == "OK" and D["ada"] == 0 and lama["D"]["r2"] == "404" and D["audit_tambah_1"] >= 1 and D["audit_tambah_ulang"] == 0,
        "(D) hapus draf: kunci sama = replay (LAMA: 404); log hapus SEKALI", str(D) + f" / lama r2={lama['D']['r2']}")
    cek(lama["C"]["keadaan1"] == C["keadaan1"] and lama["B"]["keadaan1"] == B["keadaan1"] and lama["D"]["audit_tambah_1"] == D["audit_tambah_1"],
        "(=) keadaan sesudah panggilan PERTAMA: LAMA == BARU (konfirmasi, batal, hapus)")
    s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
    for kk in SQL_JEPRET:
        cek(s[kk] == e[kk], f"NET 0 (sesudah proses selesai): {kk}", "" if s[kk] == e[kk] else f"{s[kk]} / {e[kk]}")
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
