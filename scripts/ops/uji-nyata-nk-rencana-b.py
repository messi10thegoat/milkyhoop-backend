"""UJI NYATA NK-RENCANA-B (23 Okt 2026): pratinjau terbit/batal nota kredit = penentu READ ONLY; penulis lama == baru. kaos SAJA ditulis,
SEMUA di SATU transaksi luar yang SELALU di-ROLLBACK (get_pool tertambal; transaksi handler = savepoint; transaksi READ ONLY pratinjau
dijadikan savepoint di koneksi yang sama -- penentu tetap tanpa tulis/kunci: pencacah + hitungan + kunci advisory sesi sebelum == sesudah).
Langkah (PRATINJAU lewat rute nyata -> TULIS lewat rute nyata -> bandingkan):
  P1 terbit NK bebas 'other' (Dr Retur / Cr Piutang)
  P2 terbit NK 'other' 150.000 atas INV-2609-0122 (sisa 120.000) -> Cr Piutang 120.000 + Cr Uang Muka 30.000 (KRD baru) [+ porsi Dimuka]
  P3 terbit RETUR 1 Jersey Dryfit atas INV-2609-0140 (lunas, terkirim 2) -> Cr Uang Muka 100.000 (KRD) + restock HPP asal + jurnal HPP
  P4 terbit RETUR 1 'UJI FG Subkontrak' tanpa faktur -> WAC + gudang pertama + jurnal HPP
  V1..V4 batal P3 (pembalik + HPP + KRD void), P2, P4, P1; V5 batal draf (= dihapus)
  B1 terbit NK yang sudah terbit · B2 batal NK yang sudah diterapkan ke faktur: blok PERTAMA pratinjau == galat tulis
Pratinjau BARU vs keadaan NYATA sesudah tulis (after tanpa nomor KRD, sisa faktur, jurnal akun/D/K/memo/tanggal); pratinjau LAMA (tulis+
rollback) direkam -> BANDING lama == baru (tanpa nomor jurnal/KRD, memo baris, id NK). Rekaman TULIS per langkah (tanpa uuid/cap waktu/
nomor) -> BANDING lama == baru (jurnal, kartu stok, KRD, DTL, kolom NK, alokasi faktur + baris porsi) + NET 0 (proses terpisah).
MERAH=1 : after.remaining pratinjau P1 dimutasi -> WAJIB gagal.  MERAH_BOCOR=1 : 1 baris idempotency_keys COMMIT -> net 0 WAJIB gagal.
MODE=BACA TENANT=<t> : pratinjau terbit/batal SEMUA NK tenant di koneksi default_transaction_read_only=on (tenant wajib ada)."""
import asyncio, json, os, sys, uuid
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = os.environ.get("TENANT", "kaos-biru-konveksi")
F_KRD, F_RETUR, F_APPLY = "INV-2609-0122", "INV-2609-0140", "INV-2610-0001"
BARANG_RETUR, SII_RETUR = "4eaa19a0-5659-485a-a664-92de2b6151be", "d91a4926-915f-47dc-bb2b-e53d5c50d167"  # Jersey Dryfit di INV-2609-0140
BARANG_WAC = "34424f65-2061-4690-ba5f-de9b13942f46"  # UJI FG Subkontrak 3B09D3 (dilacak)
TGL = date(2026, 10, 23)
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
MERAH, BOCOR = os.environ.get("MERAH") == "1", os.environ.get("MERAH_BOCOR") == "1"
KUNCI_BOCOR = "TES-E2E-NKRB-BOCOR"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def D(v): return Decimal(str(v if v is not None else 0))


def js(v):
    if isinstance(v, Decimal): return str(v.normalize())
    if isinstance(v, date): return v.isoformat()
    return str(v) if v is not None and not isinstance(v, (int, float, str, bool)) else v


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


SQL_JEPRET = {
    "cn": "SELECT count(*), COALESCE(sum(total_amount),0)::text FROM credit_notes WHERE tenant_id = $1",
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0)::text FROM journal_entries WHERE tenant_id = $1",
    "stok": "SELECT count(*), COALESCE(sum(quantity_in - quantity_out),0)::text FROM inventory_ledger WHERE tenant_id = $1",
    "krd": "SELECT count(*), COALESCE(sum(amount),0)::text FROM customer_deposits WHERE tenant_id = $1",
    "dtl": "SELECT count(*) FROM document_tax_lines WHERE tenant_id = $1",
    "porsi": "SELECT count(*) FROM credit_note_deferral_lines WHERE tenant_id = $1",
    "alokasi": "SELECT COALESCE(sum(sii.allocated_amount),0)::text FROM sales_invoice_items sii JOIN sales_invoices si ON si.id = sii.invoice_id WHERE si.tenant_id = $1",
    "faktur": "SELECT json_agg(json_build_object('n', invoice_number, 'p', amount_paid, 's', status, 'u', updated_at) ORDER BY invoice_number)::text FROM sales_invoices WHERE tenant_id = $1 AND invoice_number IN ('INV-2609-0122','INV-2609-0140','INV-2610-0001')",
    "piutang": "SELECT COALESCE(sum(outstanding),0)::text FROM compute_ar_outstanding($1)",
    "seq": "SELECT json_agg(json_build_object('p', prefix, 'y', year, 'm', month, 'n', last_number) ORDER BY prefix, year, month)::text FROM journal_number_sequences WHERE tenant_id = $1",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
}
SQL_KUNCI = "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"


async def jepret(c, kunci=None):
    return {k: [js(v) for v in (await c.fetchrow(q, T)).values()] for k, q in SQL_JEPRET.items() if not kunci or k in kunci}


async def rekam_nk(c, cid) -> dict:
    jur = []
    for j in await c.fetch("SELECT id, source_type, description, journal_date, total_debit, status, reversal_of_id IS NOT NULL AS balik, "
                           "reversed_by_id IS NOT NULL AS dibalik FROM journal_entries WHERE tenant_id = $1 AND source_id = $2", T, cid):
        baris = [(r["account_code"], js(D(r["debit"])), js(D(r["credit"])), r["memo"], r["line_number"]) for r in await c.fetch(
            """SELECT coa.account_code, jl.debit, jl.credit, jl.memo, jl.line_number FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id
               WHERE jl.journal_id = $1 ORDER BY jl.line_number""", j["id"])]
        jur.append({"t": j["source_type"], "d": j["description"], "tgl": js(j["journal_date"]), "s": j["status"], "tot": js(D(j["total_debit"])),
                    "balik": j["balik"], "dibalik": j["dibalik"], "lines": sorted(baris, key=str)})
    stok = sorted([[js(v) for v in r.values()] for r in await c.fetch(
        """SELECT product_id, warehouse_id, quantity_in, quantity_out, unit_cost, total_cost, movement_type, source_type, journal_id IS NOT NULL
           FROM inventory_ledger WHERE tenant_id = $1 AND source_id = $2""", T, cid)], key=str)
    cn = await c.fetchrow("SELECT status, amount_applied, amount_refunded, original_invoice_id, created_deposit_id IS NOT NULL AS krd, voided_reason "
                          "FROM credit_notes WHERE id = $1", cid)
    krd = [[js(v) for v in r.values()] for r in await c.fetch(
        """SELECT d.amount, d.status, d.payment_method, d.reference, d.notes, d.voided_reason, d.deposit_date FROM customer_deposits d
           JOIN credit_notes n ON n.created_deposit_id = d.id WHERE n.id = $1""", cid)]
    dtl = [[js(v) for v in r.values()] for r in await c.fetch(
        "SELECT direction, base_amount, tax_amount FROM document_tax_lines WHERE tenant_id = $1 AND document_id = $2", T, cid)]
    porsi = [[js(v) for v in r.values()] for r in await c.fetch(
        "SELECT invoice_item_id, amount, recognized_at_cn, reversed_at IS NOT NULL FROM credit_note_deferral_lines WHERE credit_note_id = $1 ORDER BY 1", cid)]
    return {"jurnal": sorted(jur, key=lambda x: json.dumps(x, sort_keys=True, default=str)), "stok": stok,
            "nk": [js(v) for v in cn.values()] if cn else None, "krd": krd, "dtl": dtl, "porsi": porsi}


def pv_inti(d: dict) -> dict:
    x = json.loads(json.dumps(d, default=str))
    x.pop("credit_note_id", None)
    for k in ("before", "after"):
        if isinstance(x.get(k), dict) and isinstance(x[k].get("customer_credit"), dict):
            x[k]["customer_credit"].pop("deposit_number", None)
    for j in x.get("journals") or []:
        j.pop("journal_number", None)
        for ln in j.get("lines", []):
            ln.pop("memo", None)
    # pratinjau LAMA membaca "jurnal transaksi ini" (created_at = now()): di harness transaksi luar juga memuat jurnal POSTING NK sintetis
    # yang dibuat langkah sebelumnya -> untuk batal hanya jurnal pembalik ("Void ...") yang milik aksi ini (artefak harness)
    js_ = x.get("journals") or []
    if x.get("action") == "void":
        js_ = [j for j in js_ if str(j.get("description", "")).startswith("Void")]
    x["journals"] = sorted(js_, key=lambda j: j.get("source_type", ""))
    return x


async def tulis():
    from fastapi import HTTPException
    from app.routers import credit_notes as CR
    from app.schemas.credit_notes import CreateCreditNoteRequest, ApplyCreditNoteRequest, ApplyCreditNoteItem, VoidCreditNoteRequest
    baru = hasattr(CR.NKR, "pratinjau_post_void_nk")
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    fk = {r["invoice_number"]: r for r in await c.fetch(
        "SELECT id, invoice_number, customer_id, customer_name FROM sales_invoices WHERE tenant_id = $1 AND invoice_number = ANY($2::text[])",
        T, [F_KRD, F_RETUR, F_APPLY])}
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()
    k = Kon()
    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False
    class Tambal:
        def acquire(s): return CM()
    async def gp(): return Tambal()
    CR.get_pool = gp
    hasil = {"tulis": {}, "pratinjau": {}, "blok": {}}
    tx = c.transaction(); await tx.start()
    try:
        if BOCOR:
            b = await asyncpg.connect(os.environ["DATABASE_URL"])
            await b.execute("INSERT INTO idempotency_keys (key, tenant_id, expires_at) VALUES ($1, $2, NOW() + interval '1 hour')", KUNCI_BOCOR, T)
            await b.close()

        async def buat(total, alasan="other", faktur=None, items=None):
            f = fk[faktur] if faktur else fk[F_APPLY]
            body = CreateCreditNoteRequest(
                customer_id=str(f["customer_id"]), customer_name=f["customer_name"], credit_note_date=TGL, reason=alasan,
                original_invoice_id=(str(f["id"]) if faktur else None), ref_no="TES E2E NKRB", notes="TES E2E NKRB",
                items=items or [{"description": "TES E2E NKRB", "quantity": 1, "unit_price": total}])
            r = await CR.create_credit_note(req(uid), body)
            return uuid.UUID(str((r["data"] if isinstance(r, dict) else r.data)["id"]))

        async def pratinjau(fn, *a):
            sb = await jepret(c, ("cn", "jurnal", "stok", "krd", "dtl", "porsi", "alokasi", "faktur", "seq"))
            kb = await c.fetchval(SQL_KUNCI)
            d = (await fn(req(uid), *a))["data"]
            ss = await jepret(c, ("cn", "jurnal", "stok", "krd", "dtl", "porsi", "alokasi", "faktur", "seq"))
            ks = await c.fetchval(SQL_KUNCI)
            if baru:
                cek(sb == ss, f"pratinjau {d['action']} tak menulis apa pun", "" if sb == ss else f"{sb} / {ss}")
                cek(ks == kb, f"pratinjau {d['action']} tanpa kunci advisory baru", f"{kb} -> {ks}")
                cek(all(j["journal_number"] is None for j in d["journals"]), f"pratinjau {d['action']} tanpa nomor jurnal")
            return d

        async def langkah(nama, cid, aksi, alasan=None):
            if aksi == "post":
                pv = await pratinjau(CR.preview_post_credit_note, cid)
            else:
                pv = await pratinjau(CR.preview_void_credit_note, cid, CR.PratinjauVoidNk(reason=alasan))
            hasil["pratinjau"][nama] = pv_inti(pv)
            j0 = {r["id"] for r in await c.fetch("SELECT id FROM journal_entries WHERE tenant_id = $1 AND source_id = $2", T, cid)}
            if aksi == "post":
                await CR.post_credit_note(req(uid), cid)
            else:
                await CR.void_credit_note(req(uid), cid, VoidCreditNoteRequest(reason=alasan))
            hasil["tulis"][nama] = await rekam_nk(c, cid)
            if not baru:
                return pv
            nyata = await CR._keadaan_nk(c, T, cid)
            nyata.pop("original_invoice_id", None)
            if isinstance(nyata.get("customer_credit"), dict):
                nyata["customer_credit"] = {**nyata["customer_credit"], "deposit_number": None}
            after = json.loads(json.dumps(pv["after"], default=str))
            if isinstance(after.get("customer_credit"), dict):  # nomor KRD: pratinjau terbit = null (belum terbit), batal = nomor nyata
                after["customer_credit"]["deposit_number"] = None
            if MERAH and nama == "P1":
                after["remaining"] = after.get("remaining", 0) + 1
            cek(pv["can_proceed"] and after == json.loads(json.dumps(nyata, default=str)), f"({nama}) after pratinjau == keadaan NK sesudah tulis",
                "" if after == nyata else f"{after} / {nyata}")
            if pv["invoice"]:
                f1 = await CR._sisa_faktur_nk(c, T, uuid.UUID(pv["invoice"]["invoice_id"]))
                cek(D(pv["invoice"]["remaining_after"]) == D(f1), f"({nama}) sisa faktur pratinjau == compute_ar_outstanding sesudah tulis",
                    f"{pv['invoice']} / {f1}")
            jb = [j for j in await c.fetch("SELECT id, source_type, description, journal_date FROM journal_entries WHERE tenant_id = $1 AND source_id = $2",
                                           T, cid) if j["id"] not in j0]
            rencana = sorted([(j["source_type"], j["description"], j["journal_date"],
                               sorted([(ln["account_code"], js(D(ln["debit"])), js(D(ln["credit"])), ln["memo"]) for ln in j["lines"]], key=str))
                              for j in pv["journals"]], key=str)
            tertulis = []
            for j in jb:
                bl = await c.fetch("""SELECT coa.account_code, jl.debit, jl.credit, jl.memo FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id
                                      WHERE jl.journal_id = $1""", j["id"])
                tertulis.append((j["source_type"], j["description"], j["journal_date"].isoformat(),
                                 sorted([(ln["account_code"], js(D(ln["debit"])), js(D(ln["credit"])), ln["memo"]) for ln in bl], key=str)))
            tertulis = sorted(tertulis, key=str)
            cek(rencana == tertulis, f"({nama}) jurnal pratinjau == jurnal tertulis (akun, D/K, memo, tanggal, jenis)",
                "" if rencana == tertulis else f"{rencana} / {tertulis}")
            return pv

        async def blok(nama, cid, aksi, alasan, tulis_fn):
            if aksi == "post":
                d = (await CR.preview_post_credit_note(req(uid), cid))["data"]
            else:
                d = (await CR.preview_void_credit_note(req(uid), cid, CR.PratinjauVoidNk(reason=alasan)))["data"]
            sp = c.transaction(); await sp.start()
            try:
                await tulis_fn()
                hb = "LOLOS"
            except HTTPException as e:
                hb = (e.status_code, e.detail if isinstance(e.detail, str) else e.detail.get("message"))
            finally:
                await sp.rollback()
            hasil["blok"][nama] = {"tulis": list(hb) if isinstance(hb, tuple) else hb}
            cek(not d["can_proceed"] and isinstance(hb, tuple) and d["blocks"][0]["message"] == hb[1], f"({nama}) blok PERTAMA pratinjau == galat tulis",
                f"{d['blocks']} / {hb}")

        n1 = await buat(5000)
        await langkah("P1", n1, "post")
        n2 = await buat(150000, faktur=F_KRD)
        await langkah("P2", n2, "post")
        n3 = await buat(100000, alasan="return", faktur=F_RETUR,
                        items=[{"item_id": BARANG_RETUR, "description": "TES E2E retur", "quantity": 1, "unit_price": 100000,
                                "original_invoice_item_id": SII_RETUR}])
        await langkah("P3", n3, "post")
        n4 = await buat(1000, alasan="return", items=[{"item_id": BARANG_WAC, "description": "TES E2E retur WAC", "quantity": 1, "unit_price": 1000}])
        await langkah("P4", n4, "post")
        # blok
        await blok("B1", n1, "post", None, lambda: CR.post_credit_note(req(uid), n1))
        n5 = await buat(1000)
        await CR.post_credit_note(req(uid), n5)
        await CR.apply_credit_note(req(uid), n5, ApplyCreditNoteRequest(applications=[ApplyCreditNoteItem(invoice_id=str(fk[F_APPLY]["id"]),
                                                                                                         amount=Decimal("1000"))]))
        await blok("B2", n5, "void", "TES E2E", lambda: CR.void_credit_note(req(uid), n5, VoidCreditNoteRequest(reason="TES E2E")))
        # batal
        await langkah("V1", n3, "void", "TES E2E NKRB")
        await langkah("V2", n2, "void", "TES E2E NKRB")
        await langkah("V3", n4, "void", "TES E2E NKRB")
        await langkah("V4", n1, "void", "TES E2E NKRB")
        n6 = await buat(700)
        pd = await pratinjau(CR.preview_void_credit_note, n6, CR.PratinjauVoidNk(reason="TES E2E"))
        await CR.void_credit_note(req(uid), n6, VoidCreditNoteRequest(reason="TES E2E"))
        ada = await c.fetchval("SELECT count(*) FROM credit_notes WHERE id = $1", n6)
        hasil["pratinjau"]["V5"] = pv_inti(pd)
        cek(pd["after"] == {"exists": False} and ada == 0, "(V5) batal draf: pratinjau after.exists false == tulis menghapus")
    finally:
        await tx.rollback(); await c.close()
    json.dump(hasil, open(f"/out/{NAMA}.json", "w"), indent=1, default=str)
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)


async def baca():
    from fastapi import HTTPException
    from app.services import nota_kredit_rencana as NKR
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        cek(False, f"tenant {T} ADA di \"Tenant\"", "tenant tak ada -> pengukuran kosong palsu"); await c.close()
        print(f"RINGKAS gagal={len(gagal)}"); sys.exit(1)
    ctx = {"tenant_id": T, "user_id": uuid.uuid4()}
    nks = await c.fetch("SELECT id FROM credit_notes WHERE tenant_id = $1", T)
    jumlah, ringkas = 0, {}
    for n in nks:
        for aksi in ("post", "void"):
            try:
                async with c.transaction(readonly=True):
                    d = await NKR.pratinjau_post_void_nk(c, ctx, n["id"], aksi, "uji baca")
                jumlah += 1
                kunci = f"{aksi}:{'bisa' if d['can_proceed'] else d['blocks'][0]['code']}"
                ringkas[kunci] = ringkas.get(kunci, 0) + 1
            except HTTPException as e:
                cek(False, f"BACA {aksi} {n['id']} HTTPException {e.status_code}", str(e.detail)[:120])
            except Exception as e:  # noqa: BLE001 -- termasuk galat tulis di transaksi READ ONLY
                cek(False, f"BACA {aksi} {n['id']} {type(e).__name__}", str(e)[:200])
    await c.close()
    print(f"BACA tenant={T} nk={len(nks)} pratinjau={jumlah} ringkas={json.dumps(ringkas, sort_keys=True)}")
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)


async def main():
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump(await jepret(c), open(f"/out/{NAMA}.json", "w"), default=str); await c.close(); print(f"JEPRET {NAMA} ok"); return
    if MODE == "TULIS":
        return await tulis()
    if MODE == "BACA":
        return await baca()
    if MODE == "BANDING":
        lama, baru = json.load(open("/out/lama.json")), json.load(open("/out/baru.json"))
        for k in ("P1", "P2", "P3", "P4", "V1", "V2", "V3", "V4"):
            cek(lama["tulis"].get(k) == baru["tulis"].get(k), f"penulis LAMA == BARU: {k}",
                "" if lama["tulis"].get(k) == baru["tulis"].get(k) else f"{lama['tulis'].get(k)} / {baru['tulis'].get(k)}")
        for k in ("P1", "P2", "P3", "P4", "V1", "V2", "V3", "V4", "V5"):
            cek(lama["pratinjau"].get(k) == baru["pratinjau"].get(k), f"pratinjau LAMA (tulis+rollback) == BARU (penentu): {k}",
                "" if lama["pratinjau"].get(k) == baru["pratinjau"].get(k) else f"{lama['pratinjau'].get(k)} / {baru['pratinjau'].get(k)}")
        for k in ("B1", "B2"):
            cek(lama["blok"][k]["tulis"] == baru["blok"][k]["tulis"], f"galat tulis LAMA == BARU: {k}", f"{lama['blok'][k]} / {baru['blok'][k]}")
        s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
        for k in SQL_JEPRET:
            cek(s[k] == e[k], f"NET 0 (sesudah proses selesai): {k}", "" if s[k] == e[k] else f"{s[k]} / {e[k]}")
        print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
