"""UJI NYATA NK-RENCANA-A (23 Okt 2026): pratinjau apply/unapply/refund nota kredit = penentu READ ONLY; penulis lama == baru. kaos SAJA;
pelanggan + faktur INV-2610-0001 ('TES E2E CW-SO Pelanggan', terbit, belum ber-NK), rekening BCA Operasional kaos. SEMUA tulisan di SATU
transaksi luar yang SELALU di-ROLLBACK (get_pool tertambal; transaksi handler = savepoint; transaksi READ ONLY pratinjau dijadikan savepoint di
koneksi yang sama supaya melihat NK sintetis -- penentu tetap tanpa tulis/kunci: pencacah + hitungan + kunci advisory sesi sebelum == sesudah).
Langkah (tiap langkah: PRATINJAU lewat rute nyata -> TULIS lewat rute nyata -> bandingkan):
  R  refund sebagian NK bebas lewat rekening bank (jurnal CREDIT_NOTE_REFUND + cermin bank withdrawal)
  K  refund NK bebas lewat account_id = CoA rekening (cermin lewat reverse-lookup CoA)
  A  terapkan NK bebas ke faktur (tanpa jurnal; sisa faktur sebelum -> sesudah)
  U  lepas penerapan (tanpa jurnal; sisa faktur kembali; audit_logs)
  B1 blok apply nilai tak penuh · B2 refund melebihi sisa · B3 unapply tanpa alasan · B4 refund NK bertaut (409 dict) · B5 apply 2 faktur:
     blok PERTAMA pratinjau == galat tulis (status + pesan)
Pratinjau pohon BARU dibandingkan dengan keadaan NYATA sesudah tulis (after, sisa faktur, saldo akun, cermin bank, jurnal); pratinjau pohon
LAMA (tulis-lalu-rollback) juga direkam -> BANDING: pratinjau lama == baru (kecuali journal_number: null di baru; memo baris tambahan).
Rekaman tulis per langkah (tanpa uuid/cap waktu/nomor) -> /out/<NAMA>.json; BANDING lama == baru + NET 0 (sebelum == sesudah, proses terpisah).
MERAH=1 : after.remaining pratinjau R dimutasi -> WAJIB gagal.  MERAH_BOCOR=1 : 1 baris idempotency_keys lewat koneksi terpisah yang COMMIT
-> net 0 WAJIB gagal (.sh membersihkan + sisa 0).
MODE=BACA TENANT=<t> : pratinjau SEMUA NK tenant lewat koneksi default_transaction_read_only=on (tulis apa pun = galat) -- grapgrap BACA-SAJA."""
import asyncio, json, os, sys
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = os.environ.get("TENANT", "kaos-biru-konveksi")
FAKTUR = "INV-2610-0001"
REKENING = "56842900-0ae4-4c25-8ad5-d0726a36e21b"  # BCA Operasional kaos
TGL = date(2026, 10, 23)
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
MERAH, BOCOR = os.environ.get("MERAH") == "1", os.environ.get("MERAH_BOCOR") == "1"
KUNCI_BOCOR = "TES-E2E-NKRA-BOCOR"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def D(v): return Decimal(str(v if v is not None else 0))


def js(v):
    if isinstance(v, Decimal): return str(v.normalize())
    if isinstance(v, date): return v.isoformat()
    return str(v) if v is not None and not isinstance(v, (int, float, str, bool)) else v


def req(uid, method="POST"):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": method, "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


SQL_JEPRET = {
    "cn": "SELECT count(*), COALESCE(sum(amount_applied),0), COALESCE(sum(amount_refunded),0) FROM credit_notes WHERE tenant_id = $1",
    "cna": "SELECT count(*), count(*) FILTER (WHERE status = 'active') FROM credit_note_applications WHERE tenant_id = $1",
    "cnr": "SELECT count(*), COALESCE(sum(amount),0) FROM credit_note_refunds WHERE tenant_id = $1",
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0) FROM journal_entries WHERE tenant_id = $1",
    "bank": "SELECT count(*), COALESCE(sum(amount),0) FROM bank_transactions WHERE tenant_id = $1",
    "krd": "SELECT count(*), COALESCE(sum(amount),0) FROM customer_deposits WHERE tenant_id = $1",
    "audit": "SELECT count(*) FROM audit_logs WHERE tenant_id = $1",
    "faktur": "SELECT json_agg(json_build_object('p', amount_paid, 's', status, 'u', updated_at)) FROM sales_invoices WHERE tenant_id = $1 AND invoice_number = '" + FAKTUR + "'",
    "piutang": "SELECT COALESCE(sum(outstanding),0) FROM compute_ar_outstanding($1)",
    "seq_jurnal": "SELECT json_agg(json_build_object('p', prefix, 'y', year, 'm', month, 'n', last_number) ORDER BY prefix, year, month) FROM journal_number_sequences WHERE tenant_id = $1",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
}
SQL_KUNCI = "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"


async def jepret(c, kunci=None):
    return {k: [js(v) for v in (await c.fetchrow(q, T)).values()] for k, q in SQL_JEPRET.items() if not kunci or k in kunci}


async def rekam_nk(c, cid) -> dict:
    """Jejak tulisan untuk NK ini (tanpa uuid/nomor/cap waktu): jurnal + baris, cermin bank, aplikasi, refund, kolom NK, audit."""
    jur = []
    for j in await c.fetch("SELECT id, source_type, description, journal_date, total_debit, status FROM journal_entries WHERE tenant_id = $1 AND source_id = $2", T, cid):
        baris = [(r["account_code"], str(D(r["debit"]).normalize()), str(D(r["credit"]).normalize()), r["memo"], r["line_number"]) for r in await c.fetch(
            """SELECT coa.account_code, jl.debit, jl.credit, jl.memo, jl.line_number FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id
               WHERE jl.journal_id = $1 ORDER BY jl.line_number""", j["id"])]
        bt = [(r["bank_account_id"], r["transaction_type"], str(D(r["amount"]).normalize()), r["reference_type"], r["reference_number"], r["description"],
               r["payee_payer"], js(r["transaction_date"])) for r in await c.fetch(
            "SELECT * FROM bank_transactions WHERE tenant_id = $1 AND journal_id = $2 ORDER BY amount", T, j["id"])]
        jur.append({"source_type": j["source_type"], "description": j["description"], "tanggal": js(j["journal_date"]), "status": j["status"],
                    "total": str(D(j["total_debit"]).normalize()), "lines": baris, "bank": [[js(x) for x in b] for b in bt]})
    cn = await c.fetchrow("SELECT status, amount_applied, amount_refunded, original_invoice_id, original_invoice_number FROM credit_notes WHERE id = $1", cid)
    apps = [[js(v) for k, v in r.items()] for r in await c.fetch(
        """SELECT invoice_id, invoice_number, amount_applied, application_date, status, reversal_reason, reversed_at IS NOT NULL AS dibalik
           FROM credit_note_applications WHERE credit_note_id = $1 ORDER BY created_at""", cid)]
    refs = [[js(v) for k, v in r.items()] for r in await c.fetch(
        """SELECT amount, refund_date, payment_method, account_id, bank_account_id, reference, notes, journal_id IS NOT NULL AS berjurnal
           FROM credit_note_refunds WHERE credit_note_id = $1 ORDER BY created_at""", cid)]
    aud = [[r["eventType"], r["entity_type"], r["source"], {k: v for k, v in json.loads(r["metadata"]).items() if k not in ("application_id",)}]
           for r in await c.fetch("""SELECT "eventType", entity_type, source, metadata::text AS metadata FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2
                                     ORDER BY "createdAt" """, T, cid)]
    return {"jurnal": sorted(jur, key=lambda x: json.dumps(x, sort_keys=True)), "nk": [js(v) for v in cn.values()] if cn else None,
            "aplikasi": apps, "refund": refs, "audit": aud}


def pv_inti(d: dict) -> dict:
    """Bagian pratinjau yang dibandingkan lama vs baru: tanpa journal_number + memo baris (baru menambah memo; lama tanpa)."""
    x = json.loads(json.dumps(d, default=str))
    x.pop("credit_note_id", None)  # NK sintetis berbeda per proses (uuid baru), bukan isi pratinjau
    # pratinjau LAMA membaca "jurnal transaksi ini" (created_at = now()): di harness transaksi luar juga memuat jurnal POSTING NK sintetis
    # (CREDIT_NOTE) -- artefak harness, bukan isi aksi -> dibuang sebelum dibandingkan
    x["journals"] = [j for j in (x.get("journals") or []) if j.get("source_type") != "CREDIT_NOTE"]
    for j in x["journals"]:
        j.pop("journal_number", None)
        for l in j.get("lines", []):
            l.pop("memo", None)
    return x


async def tulis():
    from fastapi import HTTPException
    from app.routers import credit_notes as CR
    from app.schemas.credit_notes import (CreateCreditNoteRequest, ApplyCreditNoteRequest, ApplyCreditNoteItem, RefundCreditNoteRequest,
                                          UnapplyCreditNoteRequest)
    baru = hasattr(CR, "NKR")
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    fk = await c.fetchrow("SELECT id, customer_id, customer_name FROM sales_invoices WHERE tenant_id = $1 AND invoice_number = $2", T, FAKTUR)
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    coa_bank = await c.fetchval("SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2", __import__("uuid").UUID(REKENING), T)

    class Kon:  # koneksi tertambal: transaction(readonly=...) pratinjau / savepoint inti = savepoint biasa di transaksi luar
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

        async def buat(total):
            r = await CR.create_credit_note(req(uid), CreateCreditNoteRequest(
                customer_id=str(fk["customer_id"]), customer_name=fk["customer_name"], credit_note_date=TGL, reason="other",
                ref_no="TES E2E NKRA", notes="TES E2E NKRA", items=[{"description": "TES E2E NKRA", "quantity": 1, "unit_price": total}]))
            cid = (r["data"] if isinstance(r, dict) else r.data)["id"]
            await CR.post_credit_note(req(uid), __import__("uuid").UUID(str(cid)))
            return __import__("uuid").UUID(str(cid))

        async def pratinjau(fn, *a):
            sb = await jepret(c, ("cn", "cna", "cnr", "jurnal", "bank", "krd", "audit", "faktur", "seq_jurnal"))
            kb = await c.fetchval(SQL_KUNCI)
            d = (await fn(req(uid), *a))["data"]
            ss = await jepret(c, ("cn", "cna", "cnr", "jurnal", "bank", "krd", "audit", "faktur", "seq_jurnal"))
            ks = await c.fetchval(SQL_KUNCI)
            if baru:
                cek(sb == ss, f"pratinjau {d['action']} tak menulis apa pun (hitungan + pencacah + cache faktur sama)", "" if sb == ss else f"{sb} / {ss}")
                cek(ks == kb, f"pratinjau {d['action']} tanpa kunci advisory baru", f"{kb} -> {ks}")
                cek(all(j["journal_number"] is None for j in d["journals"]), f"pratinjau {d['action']} tanpa nomor jurnal")
            return d

        async def langkah(nama, cid, pv_fn, pv_args, tulis_fn, tulis_args):
            pv = await pratinjau(pv_fn, cid, *pv_args)
            hasil["pratinjau"][nama] = pv_inti(pv)
            j0 = {r["id"] for r in await c.fetch("SELECT id FROM journal_entries WHERE tenant_id = $1 AND source_id = $2", T, cid)}
            s_akun = None
            if pv.get("bank"):
                s_akun = await CR._saldo_akun_nk(c, T, __import__("uuid").UUID(pv["bank"]["account_id"]))
            await tulis_fn(req(uid), cid, *tulis_args)
            rk = await rekam_nk(c, cid)
            hasil["tulis"][nama] = rk
            if not baru:
                return pv
            # pratinjau BARU == keadaan NYATA sesudah tulis
            nyata = await CR._keadaan_nk(c, T, cid)
            fid = nyata.pop("original_invoice_id", None)
            after = dict(pv["after"])
            if MERAH and nama == "R":
                after["remaining"] = after["remaining"] + 1
            cek(pv["can_proceed"] and after == json.loads(json.dumps(nyata, default=str)), f"({nama}) after pratinjau == keadaan NK sesudah tulis",
                "" if after == nyata else f"{after} / {nyata}")
            if pv["invoice"]:
                f1 = await CR._sisa_faktur_nk(c, T, __import__("uuid").UUID(pv["invoice"]["invoice_id"]))
                cek(D(pv["invoice"]["remaining_after"]) == D(f1), f"({nama}) sisa faktur pratinjau == compute_ar_outstanding sesudah tulis",
                    f"{pv['invoice']} / {f1}")
            jb = [j for j in await c.fetch("SELECT id, source_type, description, journal_date FROM journal_entries WHERE tenant_id = $1 AND source_id = $2",
                                           T, cid) if j["id"] not in j0]
            rencana = sorted([(j["source_type"], j["description"], j["journal_date"],
                               [(l["account_code"], str(D(l["debit"]).normalize()), str(D(l["credit"]).normalize()), l["memo"]) for l in j["lines"]])
                              for j in pv["journals"]], key=str)
            tertulis = []
            for j in jb:
                bl = await c.fetch("""SELECT coa.account_code, jl.debit, jl.credit, jl.memo FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id
                                      WHERE jl.journal_id = $1 ORDER BY jl.line_number""", j["id"])
                tertulis.append((j["source_type"], j["description"], j["journal_date"].isoformat(),
                                 [(l["account_code"], str(D(l["debit"]).normalize()), str(D(l["credit"]).normalize()), l["memo"]) for l in bl]))
            cek(rencana == sorted(tertulis, key=str), f"({nama}) jurnal pratinjau == jurnal tertulis (akun, D/K, memo, tanggal)",
                "" if rencana == sorted(tertulis, key=str) else f"{rencana} / {tertulis}")
            if pv.get("bank"):
                s1 = await CR._saldo_akun_nk(c, T, __import__("uuid").UUID(pv["bank"]["account_id"]))
                cek(D(pv["bank"]["balance_before"]) == D(s_akun) and D(pv["bank"]["balance_after"]) == D(s1),
                    f"({nama}) saldo akun pratinjau sebelum/sesudah == jurnal", f"{pv['bank']['balance_before']}->{pv['bank']['balance_after']} / {s_akun}->{s1}")
                btn = [(str(r["bank_account_id"]), r["account_name"], r["transaction_type"], float(r["amount"])) for r in await c.fetch(
                    """SELECT bt.bank_account_id, ba.account_name, bt.transaction_type, bt.amount FROM bank_transactions bt
                       JOIN bank_accounts ba ON ba.id = bt.bank_account_id WHERE bt.tenant_id = $1 AND bt.journal_id = ANY($2::uuid[])""",
                    T, [j["id"] for j in jb])]
                pvb = [(b["bank_account_id"], b["bank_account_name"], b["transaction_type"], b["amount"]) for b in pv["bank"]["bank_transactions"]]
                cek(sorted(pvb) == sorted(btn) and len(btn) == 1, f"({nama}) cermin bank pratinjau == bank_transactions tertulis", f"{pvb} / {btn}")
            return pv

        async def blok(nama, pv_fn, tulis_fn, cid, pv_args, tulis_args):
            d = (await pv_fn(req(uid), cid, *pv_args))["data"]
            sp = c.transaction(); await sp.start()
            try:
                await tulis_fn(req(uid), cid, *tulis_args)
                hb = "LOLOS"
            except HTTPException as e:
                hb = (e.status_code, e.detail if isinstance(e.detail, str) else e.detail.get("message"))
            finally:
                await sp.rollback()
            hasil["blok"][nama] = {"tulis": list(hb) if isinstance(hb, tuple) else hb, "pratinjau": d["blocks"]}
            cek(not d["can_proceed"] and isinstance(hb, tuple) and d["blocks"][0]["message"] == hb[1],
                f"({nama}) blok PERTAMA pratinjau == galat tulis", f"{d['blocks']} / {hb}")

        rb = RefundCreditNoteRequest(amount=Decimal("1000"), refund_date=TGL, payment_method="transfer", bank_account_id=REKENING, reference="TES E2E NKRA")
        # R refund sebagian lewat rekening
        nr = await buat(3000)
        await langkah("R", nr, CR.preview_refund_credit_note, (rb,), CR.refund_credit_note, (rb,))
        # K refund lewat account_id = CoA rekening (cermin reverse-lookup)
        nk = await buat(2000)
        kb_ = RefundCreditNoteRequest(amount=Decimal("2000"), refund_date=TGL, payment_method="transfer", account_id=str(coa_bank), reference="TES E2E NKRA")
        await langkah("K", nk, CR.preview_refund_credit_note, (kb_,), CR.refund_credit_note, (kb_,))
        # A terapkan, U lepas
        na = await buat(1000)
        ab = ApplyCreditNoteRequest(applications=[ApplyCreditNoteItem(invoice_id=str(fk["id"]), amount=Decimal("1000"))], application_date=TGL)
        await langkah("A", na, CR.preview_apply_credit_note, (ab,), CR.apply_credit_note, (ab,))
        await langkah("U", na, CR.preview_unapply_credit_note, (CR.PratinjauUnapplyNk(reason="TES E2E NKRA"),), CR.unapply_credit_note,
                      (UnapplyCreditNoteRequest(reason="TES E2E NKRA"),))
        # blok
        nb = await buat(1500)
        await blok("B1", CR.preview_apply_credit_note, CR.apply_credit_note, nb,
                   (ApplyCreditNoteRequest(applications=[ApplyCreditNoteItem(invoice_id=str(fk["id"]), amount=Decimal("1000"))]),),
                   (ApplyCreditNoteRequest(applications=[ApplyCreditNoteItem(invoice_id=str(fk["id"]), amount=Decimal("1000"))]),))
        lebih = RefundCreditNoteRequest(amount=Decimal("99999"), refund_date=TGL, payment_method="transfer", bank_account_id=REKENING)
        await blok("B2", CR.preview_refund_credit_note, CR.refund_credit_note, nb, (lebih,), (lebih,))
        await blok("B3", CR.preview_unapply_credit_note, CR.unapply_credit_note, nb, (CR.PratinjauUnapplyNk(reason="  "),),
                   (UnapplyCreditNoteRequest(reason="  "),))
        ab2 = ApplyCreditNoteRequest(applications=[ApplyCreditNoteItem(invoice_id=str(fk["id"]), amount=Decimal("1500"))], application_date=TGL)
        await CR.apply_credit_note(req(uid), nb, ab2)
        await blok("B4", CR.preview_refund_credit_note, CR.refund_credit_note, nb, (rb,), (rb,))
        dua = ApplyCreditNoteRequest(applications=[ApplyCreditNoteItem(invoice_id=str(fk["id"]), amount=Decimal("1")),
                                                   ApplyCreditNoteItem(invoice_id=str(fk["id"]), amount=Decimal("1"))])
        await blok("B5", CR.preview_apply_credit_note, CR.apply_credit_note, nr, (dua,), (dua,))
    finally:
        await tx.rollback(); await c.close()
    json.dump(hasil, open(f"/out/{NAMA}.json", "w"), indent=1, default=str)
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)


async def baca():
    """Pratinjau SEMUA NK tenant di koneksi READ ONLY (tulis apa pun = galat 25006). Tanpa membandingkan -- bukti nol 500 / nol tulis."""
    import uuid
    from fastapi import HTTPException
    from app.services import nota_kredit_rencana as NKR
    from app.schemas.credit_notes import ApplyCreditNoteRequest, ApplyCreditNoteItem, RefundCreditNoteRequest
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    ctx = {"tenant_id": T, "user_id": uuid.uuid4()}
    rek = await c.fetchval("SELECT id FROM bank_accounts WHERE tenant_id = $1 AND is_active = true ORDER BY created_at LIMIT 1", T)
    nks = await c.fetch("SELECT id, customer_id, status FROM credit_notes WHERE tenant_id = $1", T)
    jumlah, ringkas = 0, {}
    for n in nks:
        fk = await c.fetchval("""SELECT id FROM sales_invoices WHERE tenant_id = $1 AND customer_id = $2 AND status NOT IN ('draft','void')
                                 ORDER BY created_at DESC LIMIT 1""", T, n["customer_id"])
        for aksi, body in (("apply", ApplyCreditNoteRequest(applications=[ApplyCreditNoteItem(invoice_id=str(fk or uuid.uuid4()), amount=Decimal("1"))])),
                           ("unapply", "uji baca"),
                           ("refund", RefundCreditNoteRequest(amount=Decimal("1"), refund_date=TGL, payment_method="transfer",
                                                              bank_account_id=str(rek) if rek else None))):
            try:
                async with c.transaction(readonly=True):
                    d = await NKR.pratinjau_aksi_nk(c, ctx, n["id"], aksi, body)
                jumlah += 1
                kunci = f"{aksi}:{'bisa' if d['can_proceed'] else d['blocks'][0]['code']}"
                ringkas[kunci] = ringkas.get(kunci, 0) + 1
            except HTTPException as e:
                cek(False, f"BACA {aksi} {n['id']} HTTPException {e.status_code}", str(e.detail)[:120])
            except Exception as e:  # noqa: BLE001 -- termasuk galat tulis di transaksi READ ONLY
                cek(False, f"BACA {aksi} {n['id']} {type(e).__name__}", str(e)[:160])
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
        for k in ("R", "K", "A", "U"):
            cek(lama["tulis"].get(k) == baru["tulis"].get(k), f"penulis LAMA == BARU: langkah {k}",
                "" if lama["tulis"].get(k) == baru["tulis"].get(k) else f"{lama['tulis'].get(k)} / {baru['tulis'].get(k)}")
            cek(lama["pratinjau"].get(k) == baru["pratinjau"].get(k), f"pratinjau LAMA (tulis+rollback) == BARU (penentu): langkah {k}",
                "" if lama["pratinjau"].get(k) == baru["pratinjau"].get(k) else f"{lama['pratinjau'].get(k)} / {baru['pratinjau'].get(k)}")
        for k in ("B1", "B2", "B3", "B4", "B5"):
            cek(lama["blok"][k]["tulis"] == baru["blok"][k]["tulis"], f"galat tulis LAMA == BARU: {k}", f"{lama['blok'][k]['tulis']} / {baru['blok'][k]['tulis']}")
        s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
        for k in SQL_JEPRET:
            cek(s[k] == e[k], f"NET 0 (sesudah proses selesai): {k}", "" if s[k] == e[k] else f"{s[k]} / {e[k]}")
        print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
