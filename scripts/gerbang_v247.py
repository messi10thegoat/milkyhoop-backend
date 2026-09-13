"""GERBANG V247 — perilaku LINTAS TIPE di SATU transaksi ROLLBACK.

Fase A (varchar, hari ini)  -> pasang badan V247 (uuid + FK komposit) -> Fase B (uuid).
Kode yg diuji: DEPLOY 1 (dwi-kompatibel, dari /tmp). Kode LAMA (hidup) juga dijalankan di Fase B sbg bukti perlunya deploy 1.

L1 RUTE: setiap GET di tabel rute hidup milik router credit_notes & customer_deposits + GET customers/{id}/journal-entries
   + GET sales-invoices/{id}/applicable-deposits. Handler nyata, hasil DIVALIDASI thd response_model rute (memanggil
   handler langsung melewati validasi respons FastAPI — di sanalah UUID ditolak). Status A == status B (kode baru).
L2 ISI tab jurnal pelanggan ber-CN == hitungan independen di A dan B.
L3 TULIS: buat CN/DP sah -> 200 kanonik (A,B); nama -> 400 (A,B); merge pelanggan ber-DP -> sukses (A,B);
   terapkan CN -> 400 berpesan penutupan (A,B).
L4 PAGAR DB (B): INSERT langsung customer_id = UUID pelanggan TENANT LAIN -> galat FK komposit;
   INSERT customer_id 'Toko Melati' -> galat tipe.
L5 hc_verdict 8 sama A vs B; waktu UNIQUE + ALTER dicatat.
L6 SABOTASE: kode baru dgn satu pembaca DP dikembalikan mentah -> daftar DP gagal validasi di B.
Hasil di memori Python; cacah harapan dihitung dari STRUKTUR.
"""
import asyncio
import importlib.util
import inspect
import json
import os
import sys
import time
import uuid
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402
from fastapi.params import Param, Body as BodyParam, Depends as DepParam  # noqa: E402
from pydantic import TypeAdapter  # noqa: E402

T, TB = "kaos-biru-konveksi", "grapgrap-manado"
V247 = "/tmp/V247_badan.sql"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:240]))


def muat(nama, path):
    spec = importlib.util.spec_from_file_location(nama, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[nama] = m
    spec.loader.exec_module(m)
    return m


def req(uid, body=None):
    isi = json.dumps(body).encode() if body is not None else b""

    async def terima():
        return {"type": "http.request", "body": isi, "more_body": False}
    return Request({"type": "http", "method": "POST" if body is not None else "GET", "path": "/", "query_string": b"",
                    "headers": [(b"content-type", b"application/json")],
                    "state": {"user": {"tenant_id": T, "user_id": str(uid)}}}, terima)


def baca_id(r):
    for k in (r, getattr(r, "data", None), (r.get("data") if isinstance(r, dict) else None)):
        if isinstance(k, dict) and k.get("id"):
            return k["id"]
        if k is not None and getattr(k, "id", None):
            return k.id
    raise RuntimeError(f"ALAT: id tak terbaca dari {type(r).__name__}")


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    import app.services.db_pool as dbp

    class FakePool:
        def acquire(self, *a, **k):
            class C:
                async def __aenter__(s):
                    return conn
                async def __aexit__(s, *e):
                    return False
            return C()
        async def release(self, c):
            return None

    async def fake(*a, **k):
        return FakePool()
    dbp.get_db_pool = fake

    from app.main import app
    baru = {
        "credit_notes": muat("app.routers.credit_notes_baru", "/tmp/v247/credit_notes.py"),
        "customer_deposits": muat("app.routers.customer_deposits_baru", "/tmp/v247/customer_deposits.py"),
        "customers": muat("app.routers.customers_baru", "/tmp/v247/customers.py"),
        "sales_invoices": muat("app.routers.sales_invoices_baru", "/tmp/v247/sales_invoices.py"),
    }
    lama = {k: sys.modules.get(f"app.routers.{k}") or importlib.import_module(f"app.routers.{k}") for k in baru}

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    cn_id = await conn.fetchval("SELECT id FROM credit_notes WHERE tenant_id=$1 AND customer_id IS NOT NULL ORDER BY status='posted' DESC, id LIMIT 1", T)
    dep_id = await conn.fetchval("SELECT id FROM customer_deposits WHERE tenant_id=$1 AND customer_id IS NOT NULL AND status IN ('posted','partial') ORDER BY id LIMIT 1", T)
    cust_cn = await conn.fetchval("SELECT customer_id FROM credit_notes WHERE id=$1", cn_id)
    inv_dp = await conn.fetchval("""SELECT s.id FROM sales_invoices s WHERE s.tenant_id=$1 AND s.journal_id IS NOT NULL
        AND s.customer_id::text IN (SELECT customer_id::text FROM customer_deposits WHERE tenant_id=$1 AND status IN ('posted','partial')) LIMIT 1""", T)
    cust_lain = await conn.fetchval("SELECT id FROM customers WHERE tenant_id=$1 ORDER BY id LIMIT 1", TB)
    akun = await conn.fetchval("SELECT coa_id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", T)
    NILAI = {"credit_note_id": cn_id, "deposit_id": dep_id, "customer_id": cust_cn, "invoice_id": inv_dp}

    rute = []
    for r in app.routes:
        ep = getattr(r, "endpoint", None)
        if not ep or "GET" not in getattr(r, "methods", ()):
            continue
        modn = ep.__module__.split(".")[-1]
        if modn in ("credit_notes", "customer_deposits") or (modn == "customers" and ep.__name__ == "get_customer_journal_entries") \
                or (modn == "sales_invoices" and ep.__name__ == "get_applicable_deposits"):
            rute.append((modn, ep.__name__, r.path, getattr(r, "response_model", None)))
    catat("PRASYARAT", f"subjek & rute ({len(rute)} rute GET)", all(NILAI.values()) and cust_lain and rute, NILAI)

    async def panggil(mod, nama, rm):
        fn = getattr(mod, nama)
        kw = {}
        for n, p in inspect.signature(fn).parameters.items():
            if n == "request":
                kw[n] = req(uid)
            elif n in NILAI:
                ann = p.annotation
                kw[n] = uuid.UUID(str(NILAI[n])) if ann is uuid.UUID else str(NILAI[n])
            elif isinstance(p.default, Param):
                kw[n] = p.default.default if not callable(getattr(p.default, "default_factory", None)) else None
            elif p.default is not inspect._empty:
                kw[n] = p.default
            else:
                return "DILEWATI", f"parameter tak dikenal: {n}"
        sp = conn.transaction(); await sp.start()
        try:
            out = await fn(**kw)
            if rm is not None:
                TypeAdapter(rm).validate_python(out if not hasattr(out, "body") else json.loads(out.body))
            kode, det = 200, ""
        except Exception as e:  # noqa: BLE001
            kode, det = getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))[:120]
        await sp.rollback()
        return kode, det

    async def fase(label):
        st = {}
        for modn, nama, path, rm in rute:
            st[(modn, nama)] = await panggil(baru[modn], nama, rm)
        v = {}
        for t in (T, TB):
            for c in ("ap_invariant", "inventory_value", "status_desync", "bank_sync"):
                v[(t, c)] = await conn.fetchval("SELECT verdict FROM hc_verdict($1,$2)", c, t)
        return st, v

    async def isi_jurnal(mod):
        ind = await conn.fetchval("""
            WITH p AS (SELECT $2::uuid cid), c AS (
              SELECT si.journal_id j FROM sales_invoices si, p WHERE si.customer_id=p.cid AND si.tenant_id=$1
              UNION SELECT si.cogs_journal_id FROM sales_invoices si, p WHERE si.customer_id=p.cid AND si.tenant_id=$1
              UNION SELECT rp.journal_id FROM receive_payments rp, p WHERE rp.customer_id=p.cid AND rp.tenant_id=$1
              UNION SELECT cd.journal_id FROM customer_deposits cd, p WHERE cd.customer_id::text=p.cid::text AND cd.tenant_id=$1
              UNION SELECT cn.journal_id FROM credit_notes cn, p WHERE cn.customer_id::text=p.cid::text AND cn.tenant_id=$1
              UNION SELECT sip.journal_id FROM sales_invoice_payments sip JOIN sales_invoices si ON si.id=sip.invoice_id, p WHERE si.customer_id=p.cid AND si.tenant_id=$1)
            SELECT count(*) FROM c JOIN journal_entries je ON je.id=c.j WHERE je.tenant_id=$1 AND je.status='POSTED'""", T, str(cust_cn))
        sp = conn.transaction(); await sp.start()
        try:
            r = await mod.get_customer_journal_entries(req(uid), str(cust_cn), None, None, None, 1, 100)
            n = len(r["data"]["entries"]); tot = r["data"]["total"]
        except Exception as e:  # noqa: BLE001
            n, tot = f"GAGAL {getattr(e,'detail',e)}", None
        await sp.rollback()
        return n, tot, ind

    async def tulis(label):
        import app.schemas.credit_notes as scn, app.schemas.customer_deposits as sdp
        Item = scn.CreateCreditNoteRequest.model_fields["items"].annotation.__args__[0]
        out = {}
        for tag, nilai in (("sah", str(cust_cn)), ("nama", "Toko Melati")):
            sp = conn.transaction(); await sp.start()
            try:
                r = await baru["credit_notes"].create_credit_note(req(uid), scn.CreateCreditNoteRequest(
                    customer_id=nilai, customer_name="Uji V247", credit_note_date=date.today(), reason="other",
                    items=[Item(description="uji", quantity=1, unit_price=1000)]))
                disimpan = await conn.fetchval("SELECT customer_id::text FROM credit_notes WHERE id=$1", uuid.UUID(str(baca_id(r))))
                out[f"CN {tag}"] = (200, disimpan)
            except Exception as e:  # noqa: BLE001
                out[f"CN {tag}"] = (getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))[:60])
            await sp.rollback()
            sp = conn.transaction(); await sp.start()
            try:
                r = await baru["customer_deposits"].create_customer_deposit(req(uid), sdp.CreateCustomerDepositRequest(
                    customer_id=nilai, customer_name="Uji V247", amount=1000, deposit_date=date.today(),
                    payment_method="transfer", account_id=str(akun)))
                disimpan = await conn.fetchval("SELECT customer_id::text FROM customer_deposits WHERE id=$1", uuid.UUID(str(baca_id(r))))
                out[f"DP {tag}"] = (200, disimpan)
            except Exception as e:  # noqa: BLE001
                out[f"DP {tag}"] = (getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))[:60])
            await sp.rollback()
        # merge: pelanggan ber-DP (sumber) -> pelanggan lain tenant sama (sasaran)
        src = await conn.fetchval("SELECT customer_id::text FROM customer_deposits WHERE tenant_id=$1 AND customer_id IS NOT NULL LIMIT 1", T)
        tgt = await conn.fetchval("SELECT id::text FROM customers WHERE tenant_id=$1 AND id::text <> $2 AND deleted_at IS NULL LIMIT 1", T, src)
        sp = conn.transaction(); await sp.start()
        try:
            await baru["customers"].merge_customers(req(uid, {"source_ids": [src], "target_id": tgt}))
            pindah = await conn.fetchval("SELECT count(*) FROM customer_deposits WHERE customer_id::text=$1", tgt)
            out["merge"] = (200, f"DP di sasaran={pindah}")
        except Exception as e:  # noqa: BLE001
            out["merge"] = (getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))[:90])
        await sp.rollback()
        import app.schemas.credit_notes as scn2
        sp = conn.transaction(); await sp.start()
        try:
            await baru["credit_notes"].apply_credit_note(req(uid), cn_id, scn2.ApplyCreditNoteRequest(
                applications=[scn2.ApplyCreditNoteItem(invoice_id=str(inv_dp), amount=1)]))
            out["apply CN"] = (200, "")
        except Exception as e:  # noqa: BLE001
            out["apply CN"] = (getattr(e, "status_code", type(e).__name__), str(getattr(e, "detail", e))[:60])
        await sp.rollback()
        return out

    je0 = await conn.fetchval("SELECT count(*) FROM journal_entries")
    luar = conn.transaction(); await luar.start()
    try:
        # ---------- FASE A ----------
        stA, vA = await fase("A")
        jA = await isi_jurnal(baru["customers"])
        wA = await tulis("A")

        # merge LAMA hari ini (bukti apakah merge sudah rusak sebelum V247)
        src = await conn.fetchval("SELECT customer_id::text FROM customer_deposits WHERE tenant_id=$1 AND customer_id IS NOT NULL LIMIT 1", T)
        tgt = await conn.fetchval("SELECT id::text FROM customers WHERE tenant_id=$1 AND id::text <> $2 AND deleted_at IS NULL LIMIT 1", T, src)
        sp = conn.transaction(); await sp.start()
        try:
            await lama["customers"].merge_customers(req(uid, {"source_ids": [src], "target_id": tgt}))
            mlama = "200"
        except Exception as e:  # noqa: BLE001
            mlama = f"{getattr(e,'status_code',type(e).__name__)} {str(getattr(e,'detail',e))[:80]}"
        await sp.rollback()
        catat("A TEMUAN", "merge pelanggan kode LAMA hari ini (varchar)", True, mlama)

        # ---------- V247 ----------
        t0 = time.time()
        await conn.execute(open(V247, encoding="utf-8").read())
        catat("V247", "badan V247 terpasang (waktu UNIQUE+ALTER+FK)", True, f"{time.time()-t0:.3f}s")
        # Meniru koneksi BARU sesudah restart gateway: kosongkan cache statement & skema asyncpg.
        # Run pertama TANPA ini memberi 3 merah 'uuid = text' / InvalidCachedStatement yang ternyata perlu
        # dibedakan: cacat kode, atau rencana ter-cache dari fase varchar (= alasan restart wajib di deploy 2).
        await conn.reload_schema_state()
        if hasattr(conn, "_drop_local_statement_cache"):
            conn._drop_local_statement_cache()
        tipe = await conn.fetch("SELECT table_name, data_type FROM information_schema.columns WHERE column_name='customer_id' AND table_name IN ('credit_notes','customer_deposits')")
        catat("V247", "kedua kolom kini uuid", all(x["data_type"] == "uuid" for x in tipe), [dict(x) for x in tipe])

        # ---------- FASE B ----------
        stB, vB = await fase("B")
        for key in stA:
            a, b = stA[key], stB[key]
            catat("L1 RUTE", f"{key[0]}.{key[1]}: status A == B (kode deploy 1)", a[0] == b[0], f"A={a} B={b}")
        jB = await isi_jurnal(baru["customers"])
        catat("L2 ISI", "tab jurnal pelanggan ber-CN: entries == independen di A", jA[0] == jA[2] and jA[1] == jA[2], jA)
        catat("L2 ISI", "tab jurnal pelanggan ber-CN: entries == independen di B", jB[0] == jB[2] and jB[1] == jB[2], jB)
        wB = await tulis("B")
        for fase_label, w in (("A", wA), ("B", wB)):
            catat("L3 TULIS", f"{fase_label}: CN sah 200 kanonik", w["CN sah"][0] == 200 and w["CN sah"][1] == str(cust_cn).lower(), w["CN sah"])
            catat("L3 TULIS", f"{fase_label}: DP sah 200 kanonik", w["DP sah"][0] == 200 and w["DP sah"][1] == str(cust_cn).lower(), w["DP sah"])
            catat("L3 TULIS", f"{fase_label}: CN & DP nama -> 400", w["CN nama"][0] == 400 and w["DP nama"][0] == 400, (w["CN nama"], w["DP nama"]))
            catat("L3 TULIS", f"{fase_label}: merge pelanggan ber-DP sukses", w["merge"][0] == 200, w["merge"])
            catat("L3 TULIS", f"{fase_label}: terapkan CN -> 400 penutupan", w["apply CN"][0] == 400 and "belum tersedia" in w["apply CN"][1], w["apply CN"])

        # L4 pagar DB
        for tabel in ("credit_notes", "customer_deposits"):
            contoh = await conn.fetchrow(f"SELECT * FROM {tabel} WHERE tenant_id=$1 LIMIT 1", T)
            kol = [k for k in contoh.keys() if k not in ("id",)]
            for tag, nilai, harap in (("tenant lain", cust_lain, "foreign_key_violation"), ("nama", "Toko Melati", "invalid_text_representation")):
                sp = conn.transaction(); await sp.start()
                try:
                    vals = [dict(contoh)[k] for k in kol]
                    idx = kol.index("customer_id")
                    vals[idx] = nilai
                    for u in ("credit_note_number", "deposit_number", "idempotency_key"):
                        if u in kol:
                            vals[kol.index(u)] = f"UJI-V247-{uuid.uuid4().hex[:8]}" if u != "idempotency_key" else None
                    cols = ", ".join(["id"] + kol)
                    ph = ", ".join(["gen_random_uuid()"] + [f"${i+1}" for i in range(len(kol))])
                    await conn.execute(f"INSERT INTO {tabel} ({cols}) VALUES ({ph})", *vals)
                    got = "LOLOS"
                except asyncpg.PostgresError as e:
                    got = e.__class__.__name__ + ":" + (e.sqlstate or "")
                except Exception as e:  # noqa: BLE001
                    got = "ALAT:" + type(e).__name__ + ":" + str(e)[:80]
                await sp.rollback()
                ok = ("ForeignKeyViolation" in got) if harap == "foreign_key_violation" else ("InvalidTextRepresentation" in got or "DataError" in got or "expected str" in got or "invalid input" in got.lower())
                catat("L4 PAGAR DB", f"{tabel}: INSERT langsung pelanggan {tag} -> {harap}", ok, got)

        catat("L5", "hc_verdict 8 sama A vs B", vA == vB, {k: (vA[k], vB[k]) for k in vA if vA[k] != vB[k]})

        # bukti perlunya deploy 1: kode LAMA di fase B
        for modn, nama in (("customer_deposits", "list_customer_deposits"), ("customers", "get_customer_journal_entries")):
            cocok = [x for x in rute if x[0] == modn and x[1] == nama]
            if cocok:
                kode, det = await panggil(lama[modn], nama, cocok[0][3])
                catat("BUKTI PERLU", f"kode LAMA {modn}.{nama} sesudah ALTER -> gagal", kode != 200, f"{kode} {det}")

        # L6 sabotase
        src_txt = open("/tmp/v247/customer_deposits.py", encoding="utf-8").read()
        sab = src_txt.replace('"customer_id": str(row["customer_id"]) if row["customer_id"] is not None else None,', '"customer_id": row["customer_id"],')
        open("/tmp/v247/customer_deposits_sab.py", "w", encoding="utf-8").write(sab)
        msab = muat("app.routers.customer_deposits_sab", "/tmp/v247/customer_deposits_sab.py")
        daftar = [x for x in rute if x[0] == "customer_deposits" and x[1] == "list_customer_deposits"]
        if daftar:
            kode, det = await panggil(msab, "list_customer_deposits", daftar[0][3])
            catat("L6 SABOTASE", "pembaca DP dikembalikan mentah -> daftar DP gagal validasi sesudah ALTER", kode != 200, f"{kode} {det}")
        else:
            catat("L6 SABOTASE", "rute list_customer_deposits ditemukan", False, [x[1] for x in rute if x[0] == "customer_deposits"])
    finally:
        await luar.rollback()
    je1 = await conn.fetchval("SELECT count(*) FROM journal_entries")
    tipe = await conn.fetchval("SELECT string_agg(data_type, ',') FROM information_schema.columns WHERE column_name='customer_id' AND table_name IN ('credit_notes','customer_deposits')")
    catat("KONTROL", "nol menetap: jurnal sama, kolom kembali varchar", je0 == je1 and "uuid" not in tipe, f"je {je0}->{je1} tipe={tipe}")
    await conn.close()

    n_rute = len(rute)
    harap = 1 + 1 + 2 + n_rute + 2 + 10 + 4 + 1 + 2 + 1 + 1
    for s, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s:12} {u}  | {k}")
    g = sum(1 for h in hasil if not h[2])
    print(f"\ngagal={g} total={len(hasil)} harap={harap} (dari struktur, {n_rute} rute) -> {'LENGKAP' if len(hasil) == harap else 'TAK SAH'}")
    sys.exit(0 if g == 0 and len(hasil) == harap else 1)


asyncio.run(main())
