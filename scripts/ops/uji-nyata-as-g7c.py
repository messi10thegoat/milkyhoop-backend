"""UJI NYATA AS-G7c (24 Okt 2026): pratinjau aksi uang muka pelanggan + pindah proforma = penentu bersama BACA SAJA. kaos SAJA,
NOL COMMIT (penulis dijalankan di transaksi luar yang SELALU di-ROLLBACK).
PRATINJAU (pohon LAMA = inti penulis + ROLLBACK, BARU = READ ONLY): void / refund / apply / reverse / pindah untuk SEMUA uang muka
  kaos yang memenuhi (<=25 per aksi) + kasus blok (alasan kosong, faktur pelanggan lain, penerapan sudah dilepas, proforma tak sah).
  BANDING: LAMA == BARU di SEMUA medan; satu-satunya pengecualian (desain, disetujui MASTER) = journals[].journal_number
  (BARU null) -- dinamai & diuji terpisah: LAMA berisi nomor, BARU null.
TULIS (dua pohon, ROLLBACK): penulis void(posted/draf) / refund / apply (1 baris + 2 baris) / reverse -> efek (jurnal+baris, bank,
  uang muka, aplikasi/refund, faktur, riwayat) LAMA == BARU: penulis yang kini menulis DARI rencana tak berubah.
KUNCI: K1 penulis refund uang muka X terbuka di A -> pratinjau refund uang muka Y di B: BARU < 1 dtk, LAMA menunggu >= 1,5 dtk
  (kunci rantai jurnal tenant; kontrol alat ukur). K2 penulis||penulis X sama, K3 X vs Y: penantang MENUNGGU (LAMA == BARU).
  K4 kunci pindah (PROFORMA_SO + FOR UPDATE) dipegang A -> pratinjau pindah B: BARU < 1 dtk, LAMA menunggu.
JEPRET sebelum/sesudah (READ ONLY): net 0."""
import asyncio, json, os, sys, time, uuid
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg

T = "kaos-biru-konveksi"
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
gagal = []
BATAS = 25


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


SQL_JEPRET = {
    "uang_muka": "SELECT md5(COALESCE(string_agg(id::text || status || amount_applied::text || amount_refunded::text || COALESCE(proforma_id::text,'-'), ',' ORDER BY id), '')) FROM customer_deposits WHERE tenant_id = $1",
    "jurnal": "SELECT count(*)::text || '/' || COALESCE(max(journal_number), '') FROM journal_entries WHERE tenant_id = $1",
    "bank": "SELECT count(*) FROM bank_transactions WHERE tenant_id = $1",
    "aplikasi": "SELECT count(*)::text || '/' || count(*) FILTER (WHERE status = 'reversed') FROM customer_deposit_applications WHERE tenant_id = $1",
    "refund": "SELECT count(*) FROM customer_deposit_refunds WHERE tenant_id = $1",
    "faktur": "SELECT md5(COALESCE(string_agg(id::text || status || COALESCE(amount_paid::text,'-'), ',' ORDER BY id), '')) FROM sales_invoices WHERE tenant_id = $1",
    "audit": "SELECT count(*) FROM audit_logs WHERE tenant_id = $1",
}


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
    """Kasus dari data kaos (sama untuk LAMA dan BARU: dibaca sebelum apa pun ditulis)."""
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    from app.routers import customer_deposits as CD
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    hari = await c.fetchval("SELECT tanggal_bisnis($1)", T)
    deps = await c.fetch("""SELECT id, status, customer_id, sales_order_id, proforma_id FROM customer_deposits
                            WHERE tenant_id = $1 AND status <> 'void' ORDER BY created_at DESC, id LIMIT 60""", T)
    sisa = await CD.compute_deposit_remaining_many(c, T, [d["id"] for d in deps])
    kas = await c.fetchval("SELECT coa_id FROM bank_accounts WHERE tenant_id = $1 AND is_active AND coa_id IS NOT NULL ORDER BY created_at LIMIT 1", T)
    k = {"void": [], "refund": [], "apply": [], "reverse": [], "move": []}
    for d in deps[:BATAS]:
        k["void"].append({"dep": str(d["id"]), "body": {"reason": "TES E2E AS-G7c"}})
    if deps:
        k["void"].append({"dep": str(deps[0]["id"]), "body": {"reason": ""}})
    aktif = [d for d in deps if d["status"] in ("posted", "partial")]
    for d in aktif[:BATAS]:
        s = Decimal(str(sisa.get(str(d["id"]), 0)))
        k["refund"].append({"dep": str(d["id"]), "body": {"amount": str(min(s, Decimal("1000")) if s > 0 else Decimal("1000")),
                            "refund_date": str(hari), "account_id": str(kas), "payment_method": "transfer"}})
    for d in aktif:
        s = Decimal(str(sisa.get(str(d["id"]), 0)))
        if s <= 0 or len(k["apply"]) >= BATAS:
            continue
        fs = await c.fetch("""SELECT o.invoice_id, o.outstanding FROM compute_ar_outstanding($1) o
                              JOIN sales_invoices si ON si.id = o.invoice_id
                              WHERE o.outstanding > 0 AND lower(si.customer_id::text) = lower($2) ORDER BY o.invoice_id LIMIT 2""",
                           T, str(d["customer_id"]))
        for f in fs:
            j = min(s, Decimal(str(f["outstanding"])), Decimal("1000"))
            k["apply"].append({"dep": str(d["id"]), "body": {"applications": [{"invoice_id": str(f["invoice_id"]), "amount": str(j)}]}})
        if fs:  # dua baris faktur sama -> galat simulasi baris sebelumnya
            f = fs[0]
            k["apply"].append({"dep": str(d["id"]), "body": {"applications": [{"invoice_id": str(f["invoice_id"]), "amount": "1"},
                                                                              {"invoice_id": str(f["invoice_id"]), "amount": "1"}]}})
        lain = await c.fetchval("""SELECT o.invoice_id FROM compute_ar_outstanding($1) o JOIN sales_invoices si ON si.id = o.invoice_id
                                   WHERE o.outstanding > 0 AND lower(si.customer_id::text) <> lower($2) LIMIT 1""", T, str(d["customer_id"]))
        if lain and not any(x.get("lain") for x in k["apply"]):
            k["apply"].append({"lain": True, "dep": str(d["id"]), "body": {"applications": [{"invoice_id": str(lain), "amount": "1"}]}})
    apps = await c.fetch("""SELECT id, deposit_id, COALESCE(status, 'active') st FROM customer_deposit_applications WHERE tenant_id = $1
                            ORDER BY (COALESCE(status, 'active') = 'reversed'), created_at DESC LIMIT $2""", T, BATAS)
    for a in apps:
        k["reverse"].append({"dep": str(a["deposit_id"]), "body": {"application_id": str(a["id"])}})
    for d in deps:
        if len(k["move"]) >= BATAS or d["status"] not in ("draft", "posted", "partial"):
            continue
        so = await CD.resolve_order_id_for_deposit(c, T, d["sales_order_id"], d["proforma_id"])
        if not so:
            continue
        for p in await c.fetch("SELECT id FROM proformas WHERE tenant_id = $1 AND sales_order_id = $2 ORDER BY id LIMIT 3", T, so):
            k["move"].append({"dep": str(d["id"]), "body": {"target_proforma_id": str(p["id"])}})
    if deps:
        k["move"].append({"dep": str(deps[0]["id"]), "body": {"target_proforma_id": str(uuid.uuid4())}})
    return uid, k


def badan(CD, aksi, b):
    if aksi == "void":
        return CD.PratinjauVoidDp(**b)
    if aksi == "refund":
        return CD.RefundCustomerDepositRequest(**b)
    if aksi == "apply":
        return CD.ApplyCustomerDepositRequest(**b)
    if aksi == "reverse":
        return CD.PratinjauReverseDp(**b)
    return CD.PindahProformaRequest(**b)


async def pratinjau():
    from fastapi import HTTPException
    from app.routers import customer_deposits as CD
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    if NAMA == "lama" or not os.path.exists("/out/kasus.json"):
        uid, k = await kasus(c)
        json.dump({"uid": str(uid), "k": k}, open("/out/kasus.json", "w"))
    d = json.load(open("/out/kasus.json"))
    uid, k = d["uid"], d["k"]
    CD.get_pool = pool_untuk(c)
    ctx = {"tenant_id": T, "user_id": uid, "role": "OWNER"}
    out = {}
    for aksi, daftar in k.items():
        out[aksi] = []
        for x in daftar:
            try:
                b = badan(CD, aksi, x["body"])
                if aksi == "move":
                    r = await CD._jalankan_pindah(ctx, uuid.UUID(x["dep"]), b, True)
                else:
                    r = await CD._pratinjau_dp(ctx, uuid.UUID(x["dep"]), aksi, b)
                out[aksi].append({"ok": rapi(r)})
            except HTTPException as e:
                out[aksi].append({"galat": [e.status_code, rapi(e.detail)]})
    ring = {a: f"{len(v)} (lolos {sum(1 for y in v if y.get('ok', {}).get('can_proceed'))})" for a, v in out.items()}
    print(f"REKAM {NAMA} {ring}", flush=True)
    await c.close()
    json.dump(out, open(f"/out/p-{NAMA}.json", "w"))


async def efek(c, t0_jurnal, t0_bank, dep_ids, inv_ids):
    j = await c.fetch("""SELECT je.id, je.journal_number, je.source_type, je.description, je.journal_date::text d, je.total_debit::text t,
                                je.status, (je.reversal_of_id IS NOT NULL) pembalik, je.source_id::text sid FROM journal_entries je
                         WHERE je.tenant_id = $1 AND je.created_at = now() ORDER BY je.journal_number""", T)
    out = {"jurnal": []}
    for r in j:
        bs = await c.fetch("""SELECT jl.line_number n, coa.account_code a, jl.debit::text dr, jl.credit::text kr, jl.memo m FROM journal_lines jl
                              JOIN chart_of_accounts coa ON coa.id = jl.account_id WHERE jl.journal_id = $1 ORDER BY jl.line_number""", r["id"])
        out["jurnal"].append({**{k: r[k] for k in ("journal_number", "source_type", "description", "d", "t", "status", "pembalik", "sid")},
                              "baris": [dict(b) for b in bs]})
    out["bank"] = [dict(r) for r in await c.fetch(
        """SELECT ba.account_name, bt.amount::text, bt.transaction_type, bt.reference_type, bt.description FROM bank_transactions bt
           JOIN bank_accounts ba ON ba.id = bt.bank_account_id WHERE bt.tenant_id = $1 AND bt.created_at = now() ORDER BY bt.amount""", T)]
    out["uang_muka"] = [dict(r) for r in await c.fetch(
        """SELECT deposit_number, status, amount_applied::text, amount_refunded::text, voided_reason, proforma_id::text
           FROM customer_deposits WHERE id = ANY($1::uuid[]) ORDER BY id""", dep_ids)]
    out["aplikasi"] = [dict(r) for r in await c.fetch(
        """SELECT invoice_number, amount_applied::text, application_date::text, COALESCE(status, 'active') st FROM customer_deposit_applications
           WHERE deposit_id = ANY($1::uuid[]) ORDER BY invoice_number, amount_applied, st""", dep_ids)]
    out["refund"] = [dict(r) for r in await c.fetch(
        "SELECT amount::text, refund_date::text, payment_method FROM customer_deposit_refunds WHERE deposit_id = ANY($1::uuid[]) ORDER BY amount", dep_ids)]
    out["faktur"] = [dict(r) for r in await c.fetch(
        "SELECT invoice_number, status, amount_paid::text FROM sales_invoices WHERE id = ANY($1::uuid[]) ORDER BY invoice_number", inv_ids)]
    return rapi(out)


async def tulis():
    from fastapi import HTTPException
    from app.routers import customer_deposits as CD
    d = json.load(open("/out/kasus.json"))
    uid, k = d["uid"], d["k"]
    ctx = {"tenant_id": T, "user_id": uid}
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    luar = c.transaction(); await luar.start()
    hasil = {}
    try:
        await c.execute(f"SET LOCAL app.tenant_id = '{T}'")

        async def satu(nama, fn, dep, inv=()):
            sp = c.transaction(); await sp.start()
            try:
                try:
                    r = await fn()
                    r = {"ok": rapi(r)}
                except HTTPException as e:
                    r = {"galat": [e.status_code, rapi(e.detail)]}
                r["efek"] = await efek(c, None, None, [uuid.UUID(dep)], [uuid.UUID(i) for i in inv])
                for kk in ("ok",):  # id baru acak per jalan: dibuang dari balasan, efek dibandingkan lewat isi
                    if kk in r and isinstance(r[kk].get("data"), dict):
                        r[kk]["data"] = {x: y for x, y in r[kk]["data"].items() if x not in ("refund_id", "journal_id", "reversal_journal_id")}
                        for a in r[kk]["data"].get("applications", []):
                            a.pop("application_id", None)
                hasil[nama] = r
            finally:
                await sp.rollback()
            # keadaan AKHIR sesudah rollback permintaan (yang dilihat siapa pun sesudah galat; penulis sukses: sama dengan sebelum)
            hasil[nama]["akhir"] = await efek(c, None, None, [uuid.UUID(dep)], [uuid.UUID(i) for i in inv])

        lolos = lambda a: [x for x in k[a] if not x.get("lain")]
        for i, x in enumerate([x for x in k["void"] if x["body"]["reason"]][:6]):
            await satu(f"void{i}", lambda x=x: CD.void_deposit_core(c, ctx, uuid.UUID(x["dep"]), CD.PratinjauVoidDp(**x["body"])), x["dep"])
        for i, x in enumerate(k["refund"][:4]):
            await satu(f"refund{i}", lambda x=x: CD.refund_deposit_core(c, ctx, uuid.UUID(x["dep"]), CD.RefundCustomerDepositRequest(**x["body"])), x["dep"])
        for i, x in enumerate(lolos("apply")[:6]):
            await satu(f"apply{i}", lambda x=x: CD.apply_deposit_core(c, ctx, uuid.UUID(x["dep"]), CD.ApplyCustomerDepositRequest(**x["body"])),
                       x["dep"], [a["invoice_id"] for a in x["body"]["applications"]])
        for i, x in enumerate(k["reverse"][:4]):
            await satu(f"reverse{i}", lambda x=x: CD.reverse_deposit_application_core(c, ctx, uuid.UUID(x["dep"]), uuid.UUID(x["body"]["application_id"])), x["dep"])
        print(f"TULIS {NAMA} {len(hasil)} kasus: " + ", ".join(f"{n}={'ok' if 'ok' in r else r['galat'][0]}" for n, r in hasil.items()), flush=True)
    finally:
        await luar.rollback(); await c.close()
    json.dump(hasil, open(f"/out/t-{NAMA}.json", "w"))


async def kunci():
    from app.routers import customer_deposits as CD
    d = json.load(open("/out/kasus.json"))
    uid, k = d["uid"], d["k"]
    ctx = {"tenant_id": T, "user_id": uid}
    rf = [x for x in k["refund"]]
    out = {}
    if len(rf) < 2:
        out["lewat"] = "uang muka aktif < 2"
        json.dump(out, open(f"/out/k-{NAMA}.json", "w")); print(f"KUNCI {NAMA} {out}"); return
    X, Y = rf[0], rf[1]

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

    def penulis(x):
        async def f(cx):
            return (await CD.refund_deposit_core(cx, ctx, uuid.UUID(x["dep"]), CD.RefundCustomerDepositRequest(**x["body"])))["message"]
        return f

    async def pratinjau_y(cb):
        CD.get_pool = pool_untuk(cb)
        r = await CD._pratinjau_dp(ctx, uuid.UUID(Y["dep"]), "refund", CD.RefundCustomerDepositRequest(**Y["body"]))
        return r["can_proceed"]

    out["K1"] = await ronde(penulis(X), pratinjau_y, tx_b=False)
    out["K2"] = await ronde(penulis(X), penulis(X))
    out["K3"] = await ronde(penulis(X), penulis(Y))
    mv = [x for x in k["move"] if x is not k["move"][-1]]
    if mv:
        M = mv[0]

        async def kunci_pindah(ca):
            so = await CD.resolve_order_id_for_deposit(ca, T, *(await ca.fetchrow(
                "SELECT sales_order_id, proforma_id FROM customer_deposits WHERE id = $1", uuid.UUID(M["dep"]))))
            await ca.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"PROFORMA_SO:{T}:{so}")
            await ca.execute("SELECT id FROM customer_deposits WHERE id = $1 FOR UPDATE", uuid.UUID(M["dep"]))

        async def pratinjau_pindah(cb):
            CD.get_pool = pool_untuk(cb)
            return (await CD._jalankan_pindah(ctx, uuid.UUID(M["dep"]), CD.PindahProformaRequest(**M["body"]), True))["can_proceed"]
        out["K4"] = await ronde(kunci_pindah, pratinjau_pindah, tx_b=False)
    print(f"KUNCI {NAMA} {json.dumps(out)}", flush=True)
    json.dump(out, open(f"/out/k-{NAMA}.json", "w"))


async def jepret():
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    d = {k: str(await c.fetchval(q, T)) for k, q in SQL_JEPRET.items()}
    await c.close()
    json.dump(d, open(f"/out/j-{NAMA}.json", "w")); print(f"JEPRET {NAMA} ok", flush=True)


def tanpa_nomor(d):
    """Pengecualian bernama: journals[].journal_number (BARU null by design)."""
    if isinstance(d, dict):
        return {k: (None if k == "journal_number" else tanpa_nomor(v)) for k, v in d.items()}
    if isinstance(d, list):
        return [tanpa_nomor(x) for x in d]
    return d


def banding():
    lama, baru = json.load(open("/out/p-lama.json")), json.load(open("/out/p-baru.json"))
    pemb = os.environ.get("PEMBANDING", "baru")
    if pemb != "baru":
        baru = json.load(open(f"/out/p-{pemb}.json"))
    for aksi in ("void", "refund", "apply", "reverse", "move"):
        L, B = lama.get(aksi, []), baru.get(aksi, [])
        beda = [i for i in range(max(len(L), len(B))) if i >= len(L) or i >= len(B) or tanpa_nomor(L[i]) != tanpa_nomor(B[i])]
        lolos = sum(1 for y in L if y.get("ok", {}).get("can_proceed"))
        blok = sorted({b["code"] for y in L for b in y.get("ok", {}).get("blocks", [])})
        cek(len(L) > 0 and not beda, f"(P-{aksi}) pratinjau LAMA == BARU semua medan kecuali nomor jurnal ({len(L)} kasus, lolos {lolos}, blok {blok})",
            "" if not beda else json.dumps([L[beda[0]] if beda[0] < len(L) else None, B[beda[0]] if beda[0] < len(B) else None])[:900])
    nl = [j.get("journal_number") for a in lama.values() for y in a for j in y.get("ok", {}).get("journals", [])]
    nb = [j.get("journal_number") for a in baru.values() for y in a for j in y.get("ok", {}).get("journals", [])]
    cek(bool(nl) and all(nl) and all(n is None for n in nb) and len(nl) == len(nb),
        f"(N) nomor jurnal: LAMA bernomor ({len(nl)}), BARU null ({len(nb)}) -- satu-satunya medan beda")
    if pemb != "baru":
        return
    tl, tb = json.load(open("/out/t-lama.json")), json.load(open("/out/t-baru.json"))
    periksa_t(tl, tb)
    kl, kb = json.load(open("/out/k-lama.json")), json.load(open("/out/k-baru.json"))
    if "lewat" in kb:
        cek(False, f"(K) kasus kunci tak bisa dijalankan: {kb['lewat']}")
    else:
        cek(kl["K1"]["menunggu"] and kl["K1"]["detik"] >= 1.5, "(K1 kontrol) LAMA: pratinjau refund Y MENUNGGU penulis X >= 1,5 dtk", str(kl["K1"]))
        cek(not kb["K1"]["menunggu"] and kb["K1"]["detik"] < 1.0, "(K1) BARU: pratinjau refund Y < 1 dtk walau penulis X memegang kunci", str(kb["K1"]))
        cek(kb["K2"]["menunggu"] and kl["K2"]["menunggu"], "(K2) penulis||penulis uang muka sama: penantang menunggu (LAMA == BARU)", f"{kl['K2']} / {kb['K2']}")
        cek(kb["K3"]["menunggu"] and kl["K3"]["menunggu"], "(K3) penulis||penulis uang muka lain: penantang menunggu kunci jurnal tenant (LAMA == BARU)", f"{kl['K3']} / {kb['K3']}")
        if "K4" in kb:
            cek(kl["K4"]["menunggu"] and kl["K4"]["detik"] >= 1.5, "(K4 kontrol) LAMA: pratinjau pindah MENUNGGU kunci pindah", str(kl["K4"]))
            cek(not kb["K4"]["menunggu"] and kb["K4"]["detik"] < 1.0, "(K4) BARU: pratinjau pindah < 1 dtk walau kunci pindah dipegang", str(kb["K4"]))
    s, e = json.load(open("/out/j-sebelum.json")), json.load(open("/out/j-sesudah.json"))
    for k in SQL_JEPRET:
        cek(s[k] == e[k], f"NET 0 sesudah proses: {k}", "" if s[k] == e[k] else f"{s[k]} / {e[k]}")


def periksa_t(tl, tb, label=""):
    sukses = [n for n in tl if "ok" in tl[n]]
    galat = [n for n in tl if "galat" in tl[n]]
    beda_ok = [n for n in sukses if tl[n] != tb.get(n)]
    cek(bool(sukses) and not beda_ok and set(tl) == set(tb),
        f"(T{label}) penulis SUKSES LAMA == BARU: balasan + efek jurnal/baris/bank/uang muka/aplikasi/refund/faktur ({len(sukses)} kasus)",
        "" if not beda_ok else f"{beda_ok[0]}: " + "; ".join(jalur_beda(tl[beda_ok[0]], tb.get(beda_ok[0]))[:6]))
    beda_g = [n for n in galat if (tl[n]["galat"], tl[n]["akhir"]) != (tb[n].get("galat"), tb[n].get("akhir"))]
    cek(not beda_g, f"(T-galat{label}) penulis DITOLAK: kode + teks galat + keadaan AKHIR sesudah rollback LAMA == BARU ({len(galat)} kasus: {galat})",
        "" if not beda_g else f"{beda_g[0]}: " + "; ".join(jalur_beda(tl[beda_g[0]], tb.get(beda_g[0]))[:6]))
    dalam = [n for n in galat if tl[n]["efek"] != tb[n]["efek"]]
    print(f"BEDA-NYATA (dilaporkan, bukan disembunyikan): {len(dalam)} kasus galat berbeda DI DALAM transaksi sebelum rollback {dalam} -- "
          "LAMA menulis baris sebelumnya lalu 400, BARU menolak sebelum menulis apa pun", flush=True)


def jalur_beda(a, b, jalur=""):
    """Daftar jalur medan yang berbeda (untuk laporan, bukan untuk menyembunyikan)."""
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
    return [] if a == b else [f"{jalur}: {json.dumps(a, default=str)[:80]} != {json.dumps(b, default=str)[:80]}"]


def laporan_tulis():
    """HANYA_TULIS: LAMA vs LAMA2 (determinisme pohon LAMA) dan LAMA vs BARU, per kasus jalur medan yang beda."""
    tl, t2, tb = (json.load(open(f"/out/t-{n}.json")) for n in ("lama", "lama2", "baru"))
    for judul, x, y in (("LAMA vs LAMA2", tl, t2), ("LAMA vs BARU", tl, tb)):
        for n in sorted(x):
            bd = jalur_beda(x[n], y.get(n))
            print(f"BEDA {judul} {n}: {len(bd)}" + ("".join(f"\n    {b}" for b in bd[:8]) if bd else ""), flush=True)
    print("=== (T) BARU", flush=True)
    periksa_t(tl, tb)
    print("=== (T) kontrol: SALINAN tersabotase (memo refund) -> (T) WAJIB merah", flush=True)
    n0 = len(gagal)
    periksa_t(tl, json.load(open("/out/t-merah.json")), " MERAH")
    print(f"KONTROL_T {'MERAH-OK' if len(gagal) > n0 else 'TIDAK-MERAH'}", flush=True)
    del gagal[n0:]
    sb, ss = json.load(open("/out/j-sebelum.json")), json.load(open("/out/j-sesudah.json"))
    for k in SQL_JEPRET:
        cek(sb[k] == ss[k], f"NET 0 sesudah proses: {k}", "" if sb[k] == ss[k] else f"{sb[k]} / {ss[k]}")
    print(f"RINGKAS gagal={len(gagal)} {gagal}", flush=True)


async def main():
    if MODE == "JEPRET":
        return await jepret()
    if MODE == "PRATINJAU":
        return await pratinjau()
    if MODE == "TULIS":
        return await tulis()
    if MODE == "KUNCI":
        return await kunci()
    if MODE == "LAPOR":
        return laporan_tulis()
    banding()
    print(f"RINGKAS gagal={len(gagal)} {gagal}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
