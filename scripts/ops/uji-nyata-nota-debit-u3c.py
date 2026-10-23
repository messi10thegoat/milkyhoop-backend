"""UJI NYATA Nota Debit U3c (23 Okt 2026; KONTRAK-nota-debit-u3c-20261023.md). kaos SAJA (grapgrap BACA: 0 baris VC). Data VC = 0 baris di semua
tenant -> 5 nota debit SINTETIS 'TES E2E' dibuat lewat HANDLER NYATA (buat/posting/refund/terapkan/void) di SATU transaksi luar yang SELALU
di-ROLLBACK (get_pool tertambal -> transaksi handler = savepoint; nol commit). Vendor = 'UJI Maklun 3B09D3' (vendor uji), tagihan BILL-2609-0003.
Keadaan: A draf · B terbit bebas utuh · C terbit bebas sebagian dikembalikan · D terbit bertaut tagihan · E batal.
MODE TULIS:
  (a) ?aksi=belum_terpakai SQL == Python aksi baris; summary belum_terpakai_count/_value == baris (nilai = Σ sisa jurnal)
  (b) remaining_amount (rumus LAMA, tak diubah) == sisa jurnal sql_sisa_nd untuk SETIAP keadaan terbit/batal -- selisih DILAPORKAN (WORKSPACE
      minta hasil paritas di catatan rilis); draf: sisa jurnal 0, remaining = total (tampil apa adanya, == NK)
  (c) detail == baris (semua medan U3c); sisa_keadaan konsisten dengan sisa jurnal
  (d) bundel: kunci kelompok ⊆ {po, faktur, nota_debit}, nota_debit TERAKHIR, jenis ⊆ {purchase_order, bill, debit_note}, status ⊆ kamus FE;
      documents[] baris == documents_baris(bundel); jangkar paid/balance null bukan-posted, posted: paid + balance == total
  (e) with_totals: sum_sisa == Σ sisa jurnal, sum_total == Σ total bukan batal; (f) ?search= per kata; (g) riwayat: kejadian per keadaan;
  id asing -> 404. summary total_applied == Σ terpakai baris.
MODE JEPRET/BANDING: net 0 sesudah proses selesai.
MERAH=1 : Python aksi_nd disabotase (selalu None) -> (a) WAJIB gagal.
MERAH_BOCOR=1 : 1 baris idempotency_keys lewat koneksi TERPISAH yang COMMIT -> BANDING net 0 WAJIB gagal (.sh membersihkan + sisa 0)."""
import asyncio, json, os, sys
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
TAGIHAN = "BILL-2609-0003"
REKENING = "56842900-0ae4-4c25-8ad5-d0726a36e21b"  # BCA Operasional kaos
TGL = date(2026, 10, 23)
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
MERAH, BOCOR = os.environ.get("MERAH") == "1", os.environ.get("MERAH_BOCOR") == "1"
KUNCI_BOCOR = "TES-E2E-NDU3C-BOCOR"
KELOMPOK_FE, JENIS_FE = {"po", "faktur", "nota_debit"}, {"purchase_order", "bill", "debit_note"}
STATUS_FE = {"draf", "terbit", "batal", "lunas", "diposting", "dibayar"}
MEDAN = ("aksi", "aksi_semua", "sisa_keadaan", "status_text", "terpakai", "dikembalikan", "original_bill_id", "original_bill_number",
         "purchase_order_id", "purchase_order_number", "documents", "doc_count", "remaining_amount")
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def D(v): return Decimal(str(v if v is not None else 0))


def js(v):
    if isinstance(v, Decimal): return str(v.normalize())
    if isinstance(v, date): return v.isoformat()
    return str(v) if v is not None and not isinstance(v, (int, float, str, bool)) else v


def req(uid, method="GET"):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": method, "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


SQL_JEPRET = {
    "vc": "SELECT count(*) FROM vendor_credits WHERE tenant_id = $1",
    "vca": "SELECT count(*) FROM vendor_credit_applications WHERE tenant_id = $1",
    "vcr": "SELECT count(*) FROM vendor_credit_refunds WHERE tenant_id = $1",
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0) FROM journal_entries WHERE tenant_id = $1",
    "bank": "SELECT count(*), COALESCE(sum(amount),0) FROM bank_transactions WHERE tenant_id = $1",
    "tagihan": "SELECT json_agg(json_build_object('p', amount_paid, 's', status, 'u', updated_at)) FROM bills WHERE tenant_id = $1 AND invoice_number = '" + TAGIHAN + "'",
    "hutang": "SELECT COALESCE(sum(outstanding),0) FROM compute_ap_outstanding($1)",
    "seq_jurnal": "SELECT json_agg(json_build_object('p', prefix, 'y', year, 'm', month, 'n', last_number) ORDER BY prefix, year, month) FROM journal_number_sequences WHERE tenant_id = $1",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
    "audit": "SELECT count(*) FROM audit_logs WHERE \"tenantId\" = $1",
}


async def jepret(c):
    out = {}
    for k, q in SQL_JEPRET.items():
        try:
            out[k] = [js(v) for v in (await c.fetchrow(q, T)).values()]
        except asyncpg.UndefinedColumnError:
            out[k] = None
    return out


async def tulis():
    from fastapi import HTTPException
    from app.routers import vendor_credits as VR
    from app.schemas.vendor_credits import (CreateVendorCreditRequest, ApplyVendorCreditRequest, ReceiveRefundRequest,
                                            VoidVendorCreditRequest)
    from app.services import nota_debit_daftar as NDD
    from app.services.nota_debit import sql_sisa_nd
    from app.services.tagihan_daftar import documents_baris
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    bill = await c.fetchrow("SELECT id, vendor_id, vendor_name FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, TAGIHAN)
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class Tambal:
        def acquire(s): return CM()
    async def gp(): return Tambal()
    VR.get_pool = gp
    if MERAH:
        NDD.aksi_nd = lambda *a, **k: None
    tx = c.transaction(); await tx.start()
    try:
        if BOCOR:
            b = await asyncpg.connect(os.environ["DATABASE_URL"])
            await b.execute("INSERT INTO idempotency_keys (key, tenant_id, expires_at) VALUES ($1, $2, NOW() + interval '1 hour')", KUNCI_BOCOR, T)
            await b.close()

        async def buat(total):
            body = CreateVendorCreditRequest(vendor_id=str(bill["vendor_id"]), vendor_name=bill["vendor_name"], vendor_credit_date=TGL,
                                             reason="other", ref_no="TES E2E U3c", notes="TES E2E U3c",
                                             items=[{"description": "TES E2E U3c", "quantity": 1, "unit_price": total}])
            r = await VR.create_vendor_credit(req(uid, "POST"), body)
            return r["data"]["id"] if isinstance(r, dict) else r.data["id"]

        async def posting(i):
            await VR.post_vendor_credit(req(uid, "POST"), i)
        ids = {}
        ids["A"] = await buat(1000)
        ids["B"] = await buat(2000); await posting(ids["B"])
        ids["C"] = await buat(3000); await posting(ids["C"])
        await VR.receive_refund(req(uid, "POST"), ids["C"], ReceiveRefundRequest(amount=1000, refund_date=TGL, payment_method="transfer",
                                                                                  bank_account_id=REKENING, reference="TES E2E U3c"))
        ids["D"] = await buat(4000); await posting(ids["D"])
        await VR.apply_vendor_credit(req(uid, "POST"), ids["D"], ApplyVendorCreditRequest(applications=[{"bill_id": str(bill["id"]), "amount": 4000}],
                                                                                            application_date=TGL))
        ids["E"] = await buat(5000); await posting(ids["E"])
        await VR.void_vendor_credit(req(uid, "POST"), ids["E"], VoidVendorCreditRequest(reason="TES E2E U3c"))
        kunci = {str(v): k for k, v in ids.items()}
        print("INFO_ND " + " ".join(f"{k}={v}" for k, v in ids.items()), flush=True)

        async def semua(**kw):
            rows, skip = [], 0
            while True:
                r = await VR.list_vendor_credits(req(uid), status="all", vendor_id=None, search=kw.get("search"), date_from=None, date_to=None,
                                                 skip=skip, limit=100, sort_by="created_at", sort_order="desc", aksi=kw.get("aksi"),
                                                 with_totals=kw.get("with_totals", False))
                rows += r["items"]; skip += 100
                if not r["has_more"]: return rows, r
        rows, _ = await semua()
        kita = {x["id"]: x for x in rows if x["id"] in kunci}
        cek(len(kita) == 5, "5 nota debit sintetis tampil di daftar", str(len(kita)))
        sj = {str(r["id"]): D(r["s"]) for r in await c.fetch(
            f"SELECT vendor_credits.id, {sql_sisa_nd()} AS s FROM vendor_credits WHERE vendor_credits.tenant_id = $1", T)}
        harap_ks = {"A": "draf", "B": "utuh", "C": "sebagian_dikembalikan", "D": "terpakai", "E": "batal"}
        for i, x in kita.items():
            k = kunci[i]
            cek(x["sisa_keadaan"] == harap_ks[k], f"(c) keadaan {k}", f"{x['sisa_keadaan']} / {harap_ks[k]}")
            if k != "A":
                cek(D(x["remaining_amount"]) == sj[i], f"(b) remaining_amount == sisa jurnal ({k} {harap_ks[k]})", f"{x['remaining_amount']} / {sj[i]}")
            else:
                cek(sj[i] == 0 and D(x["remaining_amount"]) == 1000, "(b) draf: sisa jurnal 0, remaining = total (tampil apa adanya, == NK)")
        # (a) aksi
        sql_ids = {x["id"] for x in (await semua(aksi="belum_terpakai"))[0]}
        py_ids = {x["id"] for x in rows if x["aksi"] == "belum_terpakai"}
        cek(sql_ids == py_ids and {kunci[i] for i in sql_ids if i in kunci} == {"B", "C"}, "(a) ?aksi=belum_terpakai SQL == Python (B, C)",
            f"sql {sorted(kunci.get(i, i) for i in sql_ids)} py {sorted(kunci.get(i, i) for i in py_ids)}")
        cek(all(x["aksi_semua"] == ([x["aksi"]] if x["aksi"] else []) for x in rows), "(a) aksi_semua == [aksi]")
        sm = (await VR.get_vendor_credits_summary(req(uid), search=None))["data"]
        nilai = sum((sj[x["id"]] for x in rows if x["aksi"] == "belum_terpakai"), Decimal(0))
        cek(sm["belum_terpakai_count"] == len(py_ids) and D(sm["belum_terpakai_value"]) == nilai, "(a) summary belum_terpakai == baris",
            f"{sm['belum_terpakai_count']}/{sm['belum_terpakai_value']} vs {len(py_ids)}/{nilai}")
        cek(D(sm["total_applied"]) == sum((D(x["terpakai"]) for x in rows if x["status"] != "void"), Decimal(0)),
            "summary total_applied == Σ terpakai baris", f"{sm['total_applied']}")
        # (c) detail == baris
        for i, x in kita.items():
            d = (await VR.get_vendor_credit(req(uid), i))["data"]
            d = d if isinstance(d, dict) else d.model_dump()
            beda = [m for m in MEDAN if d.get(m) != x.get(m)]
            cek(not beda, f"(c) detail == baris ({kunci[i]})", str(beda))
        # (d) bundel
        for i, x in kita.items():
            b = await VR.get_vendor_credit_documents(req(uid), i)
            keys = [g["key"] for g in b["groups"]]
            docs = [d for g in b["groups"] for d in g["docs"]]
            ok = (set(keys) <= KELOMPOK_FE and keys[-1] == "nota_debit" and {d["kind"] for d in docs} <= JENIS_FE
                  and {d["status"] for d in docs} <= STATUS_FE and documents_baris(b["groups"]) == x["documents"])
            cek(ok, f"(d) bundel kamus FE + documents[] ({kunci[i]})", f"{keys} {[(d['kind'], d['status']) for d in docs]}")
            a = b["anchor"]
            if kunci[i] in ("A", "E"):
                cek(a["paid"] is None and a["balance"] is None, f"(d) jangkar null bukan-posted ({kunci[i]})", f"{a['paid']}/{a['balance']}")
            else:
                cek(D(a["paid"]) + D(a["balance"]) == D(a["total"]) and D(a["balance"]) == sj[i], f"(d) jangkar paid+balance == total ({kunci[i]})",
                    f"{a['paid']}+{a['balance']} / {a['total']}")
        cek(kita[str(ids["D"])]["original_bill_number"] == TAGIHAN and any(g["key"] == "faktur" for g in
            (await VR.get_vendor_credit_documents(req(uid), ids["D"]))["groups"]), "(d) D bertaut: kelompok faktur = tagihan asal")
        # (e) totals
        _, rt = await semua(with_totals=True)
        cek(D(rt["sum_sisa"]) == sum((sj[x["id"]] for x in rows), Decimal(0))
            and D(rt["sum_total"]) == sum((D(x["total_amount"]) for x in rows if x["status"] != "void"), Decimal(0)), "(e) with_totals",
            f"{rt['sum_total']}/{rt['sum_sisa']}")
        # (f) search
        nomor_b = kita[str(ids["B"])]["vendor_credit_number"]
        hasil = {x["id"] for x in (await semua(search=f"{nomor_b} UJI"))[0]}
        cek(hasil == {str(ids["B"])}, "(f) ?search= per kata (nomor + vendor)", str(len(hasil)))
        sm_c = (await VR.get_vendor_credits_summary(req(uid), search=nomor_b))["data"]
        cek(sm_c["total"] == 1, "(f) summary?search= == daftar", str(sm_c["total"]))
        # (g) riwayat
        from app.routers import vendor_credits as _vr  # noqa: F401
        import app.services.dashboard_izin as DI
        async def ya(*a, **k): return True
        DI.boleh_baca = ya
        harap_ev = {"A": {"NOTA_DEBIT_DIBUAT"}, "C": {"NOTA_DEBIT_DIPOSTING", "NOTA_DEBIT_DIKEMBALIKAN"}, "D": {"NOTA_DEBIT_DITERAPKAN"},
                    "E": {"NOTA_DEBIT_DIBATALKAN"}}
        for k, ev in harap_ev.items():
            h = (await VR.get_vendor_credit_history(req(uid), ids[k], limit=200))["data"]
            jenis = {e.get("jenis") or e.get("type") or e.get("event_type") for e in h["events"]}
            cek(ev <= jenis, f"(g) riwayat {k}", str(sorted(j for j in jenis if j)))
        try:
            await VR.get_vendor_credit_history(req(uid), "00000000-0000-0000-0000-000000000000", limit=200)
            cek(False, "(g) id asing -> 404")
        except HTTPException as e:
            cek(e.status_code == 404, "(g) id asing -> 404")
    finally:
        await tx.rollback(); await c.close()
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)


async def main():
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump(await jepret(c), open(f"/out/{NAMA}.json", "w"), default=str); await c.close(); print(f"JEPRET {NAMA} ok"); return
    if MODE == "TULIS":
        return await tulis()
    if MODE == "BANDING":
        s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
        for k in SQL_JEPRET:
            cek(s[k] == e[k], f"NET 0 (sesudah proses selesai): {k}", "" if s[k] == e[k] else f"{s[k]} / {e[k]}")
        print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
