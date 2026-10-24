"""UJI NYATA AS-GP3 (24 Okt 2026): Pembayaran Keluar void / posting draf / hapus draf -- penentu bersama + pratinjau READ ONLY + idempotensi
kunci klien. kaos SAJA untuk tulis: SATU transaksi luar SELALU di-ROLLBACK (get_pool tertambal), tiap skenario di savepoint sendiri.
  V  void BP-2609-0001 (record_payment lama, cermin 'bill') + PAY-202609-0001 (/bill-payments) dgn kunci K: jurnal pembalik, cermin bank,
     sisa tagihan; ulang kunci K (BARU replay, 1 jurnal void); kunci K + alasan beda (BARU 409)
  P  draf baru (BILL-2609-0017) -> posting dgn kunci: jurnal, cermin, sisa; ulang kunci (BARU replay; LAMA 400)
  D  draf baru -> hapus dgn kunci; ulang (BARU replay; LAMA 404); pratinjau hapus atas pembayaran posted = blok STATUS_BUKAN_DRAF
Pohon BARU: pratinjau == tulis (baris jurnal kode/D/K, cermin bank, sisa sesudah). LAMA == BARU untuk efek tulis (V, P).
grapgrap-manado: HANYA pratinjau void atas semua pembayaran posted di koneksi default_transaction_read_only (nol tulis mungkin).
NET 0 diukur proses terpisah. MERAH=1: harapan nominal cermin pratinjau digeser 1 -> WAJIB gagal."""
import asyncio, inspect, json, os, sys, uuid
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T, G = "kaos-biru-konveksi", "grapgrap-manado"
BAYAR = {"BP": "767026a7-fe16-4395-a814-0fa1073729e6", "PAY": "97ab274d-ddfd-4480-a9f3-bf9fac897c80"}
TAGIHAN, VENDOR, REK = "199ccc30-dd37-4b63-ba38-7395cb25fd66", "ab81e287-bdb3-40b0-add7-bb5982572670", "56842900-0ae4-4c25-8ad5-d0726a36e21b"
MODE, NAMA, MERAH = os.environ["MODE"], os.environ.get("NAMA", ""), os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


SQL_JEPRET = {
    "bayar": "SELECT count(*), json_agg(json_build_object('n', payment_number, 's', status) ORDER BY payment_number)::text FROM bill_payments_v2 WHERE tenant_id = $1",
    "alokasi": "SELECT count(*) FROM bill_payment_allocations a JOIN bill_payments_v2 p ON p.id = a.payment_id WHERE p.tenant_id = $1",
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0)::text FROM journal_entries WHERE tenant_id = $1",
    "bank": "SELECT count(*), COALESCE(sum(amount),0)::text FROM bank_transactions WHERE tenant_id = $1",
    "tagihan": "SELECT json_agg(json_build_object('n', invoice_number, 'p', amount_paid, 's', status) ORDER BY invoice_number)::text FROM bills WHERE tenant_id = $1",
    "pph": "SELECT count(*) FILTER (WHERE status = 'void') FROM withholding_tax_records WHERE tenant_id = $1",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
    "seq": "SELECT json_agg(json_build_object('p', prefix, 'n', last_number) ORDER BY prefix, year, month)::text FROM journal_number_sequences WHERE tenant_id = $1",
}


def req(uid, kunci=None):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


def n(v):
    return str(Decimal(str(v)).quantize(Decimal("0.01")))


def pesan(r):
    return r.get("message") if isinstance(r, dict) else getattr(r, "message", None)


async def tulis():
    from fastapi import HTTPException
    from starlette.responses import Response
    from app.routers import bill_payments as BPR
    from app.services import bayar_keluar_rencana as BKR
    from app.schemas.bill_payments import VoidBillPaymentRequest as VR, CreateBillPaymentRequest as CR
    baru = hasattr(BKR, "pratinjau_batal_bayar")
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
    BPR.get_pool = gp

    def resp():
        return Response() if "response" in inspect.signature(BPR.void_bill_payment).parameters else None

    async def coba(aw):
        sp = c.transaction(); await sp.start()
        try:
            r = await aw; out = ("OK", pesan(r))
            await sp.commit()
        except HTTPException as e:
            await sp.rollback(); out = (str(e.status_code), str(e.detail)[:80])
        except Exception as e:  # noqa: BLE001
            await sp.rollback(); out = ("GALAT", f"{type(e).__name__}: {str(e)[:100]}")
        return out

    async def jurnal(jid):
        rows = await c.fetch("""SELECT coa.account_code, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id
                                WHERE jl.journal_id = $1 ORDER BY jl.line_number""", jid)
        return sorted([r["account_code"], n(r["debit"]), n(r["credit"])] for r in rows)

    async def cermin(jid):
        return sorted([n(r["amount"]), r["transaction_type"]] for r in await c.fetch("SELECT amount, transaction_type FROM bank_transactions WHERE journal_id = $1 AND tenant_id = $2", jid, T))

    async def pv(fn, *a):  # pratinjau lewat RUTE (transaksi readonly di rute = savepoint di sini, di-rollback rute)
        return (await fn(req(uid), *a))["data"]

    def pv_jurnal(d):
        return sorted(sorted([l["account_code"], n(l["debit"]), n(l["credit"])] for l in j["lines"]) for j in d["journals"])[0] if d["journals"] else []

    async def draf():
        p = CR(vendor_id=VENDOR, payment_date=date.today(), bank_account_id=REK, total_amount=100000,
               allocations=[{"bill_id": TAGIHAN, "amount_applied": 100000}], save_as_draft=True, notes="TES E2E AS-GP3")
        await BPR.create_bill_payment(req(uid), p)
        return await c.fetchval("SELECT id FROM bill_payments_v2 WHERE tenant_id = $1 AND status = 'draft' ORDER BY created_at DESC LIMIT 1", T)

    h = {"baru": baru}
    tx = c.transaction(); await tx.start()
    try:
        for jenis, pid in BAYAR.items():
            sp = c.transaction(); await sp.start()
            try:
                if baru:
                    d = await pv(BPR.preview_void_bill_payment, pid, VR(void_reason="TES E2E GP3 void"))
                    h[f"pv_{jenis}"] = {"ok": d["can_proceed"], "blok": [b["code"] for b in d["blocks"]], "jurnal": pv_jurnal(d),
                                        "cermin": sorted([n(b["amount"]), b["transaction_type"]] for b in d["bank_transactions"]),
                                        "sisa": {b["bill_id"]: n(b["remaining_after"]) for b in d["bills"]},
                                        "tagihan_aktif": [b.get("bill_active") for b in d["bills"]]}
                al = [r["bill_id"] for r in await c.fetch("SELECT bill_id FROM bill_payment_allocations WHERE payment_id = $1", uuid.UUID(pid))]
                r1 = await coba(BPR.void_bill_payment(req(uid, "TESGP3-V"), pid, VR(void_reason="TES E2E GP3 void"), **({"response": resp()} if resp() else {})))
                vj = await c.fetchval("SELECT void_journal_id FROM bill_payments_v2 WHERE id = $1", uuid.UUID(pid))
                efek = {"r1": r1[0], "jurnal": await jurnal(vj) if vj else [], "cermin": await cermin(vj) if vj else [],
                        "sisa": {str(b): n(await BKR.sisa_tagihan(c, T, b)) for b in al}}
                r2 = await coba(BPR.void_bill_payment(req(uid, "TESGP3-V"), pid, VR(void_reason="TES E2E GP3 void"), **({"response": resp()} if resp() else {})))
                r3 = await coba(BPR.void_bill_payment(req(uid, "TESGP3-V"), pid, VR(void_reason="alasan lain"), **({"response": resp()} if resp() else {})))
                efek.update({"r2": list(r2), "r3": r3[0], "n_void": await c.fetchval(
                    "SELECT count(*) FROM journal_entries WHERE reversal_of_id = (SELECT journal_id FROM bill_payments_v2 WHERE id = $1)", uuid.UUID(pid))})
                h[f"V_{jenis}"] = efek
            finally:
                await sp.rollback()
        # P
        sp = c.transaction(); await sp.start()
        try:
            dp = await draf()
            if baru:
                d = await pv(BPR.preview_post_bill_payment, str(dp))
                h["pv_P"] = {"ok": d["can_proceed"], "blok": [b["code"] for b in d["blocks"]], "jurnal": pv_jurnal(d),
                             "cermin": sorted([n(b["amount"]), b["transaction_type"]] for b in d["bank_transactions"]),
                             "sisa": {b["bill_id"]: n(b["remaining_after"]) for b in d["bills"]}}
            kw = {"response": resp()} if resp() else {}
            r1 = await coba(BPR.post_bill_payment(req(uid, "TESGP3-P"), str(dp), **kw))
            jid = await c.fetchval("SELECT journal_id FROM bill_payments_v2 WHERE id = $1", dp)
            jnum = await c.fetchval("SELECT journal_number FROM bill_payments_v2 WHERE id = $1", dp)
            efek = {"r1": r1[0], "jurnal": await jurnal(jid) if jid else [], "cermin": await cermin(jid) if jid else [],
                    "sisa": {TAGIHAN: n(await BKR.sisa_tagihan(c, T, uuid.UUID(TAGIHAN)))}}
            r2 = await coba(BPR.post_bill_payment(req(uid, "TESGP3-P"), str(dp), **kw))
            efek.update({"r2": r2[0], "n_jurnal": await c.fetchval("SELECT count(*) FROM journal_entries WHERE source_id = $1 AND tenant_id = $2", dp, T),
                         "nomor_sama": (await c.fetchval("SELECT journal_number FROM bill_payments_v2 WHERE id = $1", dp)) == jnum})
            h["P"] = efek
        finally:
            await sp.rollback()
        # D
        sp = c.transaction(); await sp.start()
        try:
            dd = await draf()
            if baru:
                d = await pv(BPR.preview_delete_bill_payment, str(dd))
                h["pv_D"] = {"ok": d["can_proceed"], "after": d["after"], "n_bills": len(d["bills"])}
                d2 = await pv(BPR.preview_delete_bill_payment, BAYAR["PAY"])
                h["pv_D_posted"] = [b["code"] for b in d2["blocks"]]
            kw = {"response": resp()} if resp() else {}
            r1 = await coba(BPR.delete_bill_payment(req(uid, "TESGP3-D"), str(dd), **kw))
            ada = await c.fetchval("SELECT count(*) FROM bill_payments_v2 WHERE id = $1", dd)
            r2 = await coba(BPR.delete_bill_payment(req(uid, "TESGP3-D"), str(dd), **kw))
            h["D"] = {"r1": r1[0], "sisa_baris": ada, "r2": r2[0]}
        finally:
            await sp.rollback()
        print(f"REKAM {NAMA} {json.dumps(h)}", flush=True)
    finally:
        await tx.rollback(); await c.close()
    json.dump(h, open(f"/out/{NAMA}.json", "w"))


async def grapgrap():
    from app.services import bayar_keluar_rencana as BKR
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', G), "tenant grapgrap tak ada -- harness menolak"
    ids = [r["id"] for r in await c.fetch("SELECT id FROM bill_payments_v2 WHERE tenant_id = $1 AND status = 'posted' ORDER BY payment_number", G)]
    out = {"n": len(ids), "ok": 0, "blok": {}, "timpang": 0, "tanpa_jurnal": 0, "galat": []}
    for pid in ids:
        try:
            async with c.transaction(readonly=True):
                d = await BKR.pratinjau_batal_bayar(c, G, pid, "pratinjau saja")
        except Exception as e:  # noqa: BLE001
            out["galat"].append(f"{type(e).__name__}: {str(e)[:80]}"); continue
        out["ok"] += d["can_proceed"]
        for b in d["blocks"]:
            out["blok"][b["code"]] = out["blok"].get(b["code"], 0) + 1
        if not d["journals"]:
            out["tanpa_jurnal"] += 1
        for j in d["journals"]:
            if abs(sum(l["debit"] for l in j["lines"]) - sum(l["credit"] for l in j["lines"])) > 0.001:
                out["timpang"] += 1
    await c.close()
    print(f"GRAPGRAP {json.dumps(out)}", flush=True)
    json.dump(out, open("/out/grapgrap.json", "w"))


PIN_BLOK = "892e0de7cc824eca"  # sha256[:16] blok pd_bayar..c_payment compute_ap_rincian (V441) == tests/unit/test_as_gp3_aksi_bayar.py


async def paritas():
    """SQL_BAGIAN_BAYAR = SALINAN rumus atribusi bayar compute_ap_rincian -> sumber kedua yang bisa melantur. READ ONLY, kaos + grapgrap:
    Σ bagian (SEMUA pembayaran) per tagihan == payment_amount compute_ap_rincian (tagihan aktif); blok fungsi HIDUP == PIN. Kontrol merah:
    rumus disabotase (+0.01 per baris & sisa pembulatan dibuang) -> selisih WAJIB muncul; sabotase pembulatan saja (ROUND 0) dilaporkan."""
    import hashlib
    from app.services import bayar_keluar_rencana as BKR
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    d = await c.fetchval("SELECT pg_get_functiondef('compute_ap_rincian(text)'::regprocedure)")
    b = d[d.index("-- BADAN-MULAI"):d.index("-- BADAN-SELESAI")]
    blok = b[b.index("pd_bayar AS ("):b.index("c_vendor_credit AS (")]
    out = {"pin_hidup": hashlib.sha256(" ".join(blok.split()).encode()).hexdigest()[:16]}
    sabot = BKR.SQL_BAGIAN_BAYAR.replace("ELSE 0 END AS bagian", "ELSE 0 END + 0.01 AS bagian").replace(
        "THEN x.ap_debit - x.jumlah ELSE 0", "THEN 0 ELSE 0")
    bulat = BKR.SQL_BAGIAN_BAYAR.replace("SUM(a.amount_applied) OVER (), 2)", "SUM(a.amount_applied) OVER (), 0)")
    assert sabot != BKR.SQL_BAGIAN_BAYAR and bulat != BKR.SQL_BAGIAN_BAYAR, "sabotase tak mengenai teks -- alat ukur rusak"
    for tid in (T, G):
        assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', tid), f"tenant {tid} tak ada -- harness menolak"
        rin = {r["bill_id"]: r["payment_amount"] for r in await c.fetch("SELECT bill_id, payment_amount FROM compute_ap_rincian($1)", tid)}
        pids = [r["id"] for r in await c.fetch("SELECT id FROM bill_payments_v2 WHERE tenant_id = $1", tid)]
        hasil = {}
        for nama, sql in (("asli", BKR.SQL_BAGIAN_BAYAR), ("sabotase", sabot), ("bulat0", bulat)):
            jum = {}
            for pid in pids:
                for r in await c.fetch(sql, pid, tid):
                    jum[r["bill_id"]] = jum.get(r["bill_id"], Decimal("0")) + Decimal(str(r["bagian"]))
            hasil[nama] = sum(1 for bid, pa in rin.items() if Decimal(str(pa)) != jum.get(bid, Decimal("0")))
        out[tid] = {"n_bayar": len(pids), "n_tagihan_aktif": len(rin), "n_dibayar": sum(1 for v in rin.values() if v), **hasil}
    await c.close()
    print(f"PARITAS {json.dumps(out)}", flush=True)
    json.dump(out, open("/out/paritas.json", "w"))


async def main():
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump({kk: [str(v) for v in (await c.fetchrow(q, T)).values()] for kk, q in SQL_JEPRET.items()}, open(f"/out/{NAMA}.json", "w"))
        await c.close(); print(f"JEPRET {NAMA} ok"); return
    if MODE == "TULIS":
        return await tulis()
    if MODE == "GRAPGRAP":
        return await grapgrap()
    if MODE == "PARITAS":
        return await paritas()
    lama, baru = json.load(open("/out/lama.json")), json.load(open("/out/baru.json"))
    print(f"LAMA {json.dumps(lama)}\nBARU {json.dumps(baru)}")
    geser = (lambda cm: [[n(Decimal(a) + 1), t] for a, t in cm]) if MERAH else (lambda cm: cm)
    for jenis in ("BP", "PAY"):
        b, l, p = baru[f"V_{jenis}"], lama[f"V_{jenis}"], baru[f"pv_{jenis}"]
        cek(b["r1"] == "OK" and len(b["jurnal"]) >= 2 and len(b["cermin"]) == 1, f"(V {jenis}) void baru: jurnal pembalik + 1 cermin", str(b))
        cek(p["ok"] and p["blok"] == [] and p["jurnal"] == b["jurnal"] and geser(p["cermin"]) == b["cermin"] and p["sisa"] == b["sisa"],
            f"(V {jenis}) pratinjau == tulis (jurnal kode/D/K, cermin, sisa sesudah)", f"pv={p} / tulis={b}")
        cek({kk: l[kk] for kk in ("jurnal", "cermin", "sisa")} == {kk: b[kk] for kk in ("jurnal", "cermin", "sisa")},
            f"(V {jenis}) efek tulis LAMA == BARU", f"{l} / {b}")
        cek(b["r2"][0] == "OK" and b["n_void"] == 1 and b["r3"] == "409", f"(V {jenis}) kunci sama = replay (1 jurnal void); alasan beda = 409",
            f"r2={b['r2']} n_void={b['n_void']} r3={b['r3']} (lama r3={l['r3']})")
    b, l, p = baru["P"], lama["P"], baru["pv_P"]
    cek(b["r1"] == "OK" and len(b["jurnal"]) >= 2 and len(b["cermin"]) == 1, "(P) posting draf baru: jurnal + 1 cermin", str(b))
    cek(p["ok"] and p["jurnal"] == b["jurnal"] and geser(p["cermin"]) == b["cermin"] and p["sisa"] == b["sisa"], "(P) pratinjau == tulis", f"pv={p} / tulis={b}")
    cek({kk: l[kk] for kk in ("jurnal", "cermin", "sisa")} == {kk: b[kk] for kk in ("jurnal", "cermin", "sisa")}, "(P) efek tulis LAMA == BARU", f"{l} / {b}")
    cek(b["r2"] == "OK" and b["n_jurnal"] == 1 and b["nomor_sama"] and l["r2"] == "400", "(P) kunci sama = replay, 1 jurnal (LAMA: 400)",
        f"baru r2={b['r2']} n={b['n_jurnal']} / lama r2={l['r2']}")
    b, l = baru["D"], lama["D"]
    cek(b == {"r1": "OK", "sisa_baris": 0, "r2": "OK"} and l["r2"] == "404", "(D) hapus draf; kunci sama = replay (LAMA: 404)", f"{l} / {b}")
    cek(baru["pv_BP"]["tagihan_aktif"] == [False] and baru["pv_PAY"]["tagihan_aktif"] == [True],
        "(V) ANOMALI data kaos tercatat: BP-2609-0001 menunjuk tagihan VOID (BILL-2609-0002, sisa 0 tetap 0); PAY = tagihan aktif (data bersih)",
        f"{baru['pv_BP']['tagihan_aktif']} / {baru['pv_PAY']['tagihan_aktif']}")
    cek(baru["pv_D"]["ok"] and baru["pv_D"]["after"] == {"exists": False} and baru["pv_D"]["n_bills"] == 1
        and baru["pv_D_posted"] == ["STATUS_BUKAN_DRAF"], "(D) pratinjau hapus: draf boleh, posted = blok", f"{baru['pv_D']} / {baru['pv_D_posted']}")
    g = json.load(open("/out/grapgrap.json"))
    cek(g["n"] > 0 and not g["galat"] and g["timpang"] == 0 and g["ok"] + sum(g["blok"].values()) >= g["n"],
        "(G) grapgrap: pratinjau void semua posted read-only tanpa galat, jurnal seimbang", str(g))
    pa = json.load(open("/out/paritas.json"))
    cek(pa["pin_hidup"] == PIN_BLOK, "(R) blok atribusi compute_ap_rincian HIDUP == PIN (V441)", pa["pin_hidup"])
    for tid in (T, G):
        r = pa[tid]
        cek(r["n_dibayar"] > 0 and r["asli"] == 0, f"(R {tid}) paritas SQL_BAGIAN_BAYAR == compute_ap_rincian: 0 selisih", str(r))
        cek(r["sabotase"] > 0, f"(R {tid}) kontrol merah: rumus disabotase -> selisih muncul", str(r))
    s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
    for kk in SQL_JEPRET:
        cek(s[kk] == e[kk], f"NET 0 (sesudah proses selesai): {kk}", "" if s[kk] == e[kk] else f"{s[kk]} / {e[kk]}")
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
