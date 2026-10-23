"""UJI NYATA Nota Debit U3d (23 Okt 2026): pratinjau aksi == tulis, penulis lama == baru. kaos SAJA; vendor 'UJI Maklun 3B09D3', tagihan
BILL-2609-0003, barang uji dilacak 'ZZTest T179 Hoodie' (WAC 180.000) untuk jalur HPP retur. SEMUA tulisan di SATU transaksi luar yang SELALU
di-ROLLBACK (get_pool tertambal; transaksi handler = savepoint; transaksi READ ONLY pratinjau dijadikan savepoint di koneksi yang sama supaya
melihat ND sintetis -- penentu tetap tanpa tulis, dibuktikan pencacah/hitungan sebelum == sesudah tiap pratinjau).
Langkah (tiap langkah: PRATINJAU lewat rute nyata -> TULIS lewat rute nyata -> bandingkan jurnal baru):
  P  posting ND retur barang dilacak (jurnal VENDOR_CREDIT + jurnal HPP VENDOR_CREDIT_COGS + stok keluar)
  R  terima pengembalian dana sebagian ND bebas (jurnal VENDOR_CREDIT_REFUND + cermin bank)
  A  terapkan ND bebas ke tagihan (tanpa jurnal; sisa tagihan sebelum -> sesudah)
  U  lepas penerapan (tanpa jurnal; sisa tagihan kembali)
  V  batal ND posting dari P (cermin jurnal posting + cermin jurnal HPP)
  D  batal ND draf (= dihapus; pratinjau after.exists false)
  B  blok: posting ND sudah terbit -> pratinjau blok BUKAN_DRAF dan tulis 400 dengan pesan YANG SAMA
Rekaman jurnal baru per langkah (tanpa uuid/cap waktu/nomor) -> /out/<NAMA>.json; BANDING: lama.json == baru.json (penulis lama == baru)
+ sebelum.json == sesudah.json (NET 0 sesudah proses selesai). Pohon LAMA tak punya rute pratinjau -> langkah pratinjau dilewati di sana.
MERAH=1 : satu baris rencana pratinjau P dimutasi -> P WAJIB gagal.  MERAH_BOCOR=1 : 1 baris idempotency_keys lewat koneksi terpisah yang
COMMIT -> net 0 WAJIB gagal (.sh membersihkan + sisa 0)."""
import asyncio, json, os, sys
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
TAGIHAN = "BILL-2609-0003"
BARANG = "a053cd5c-2b27-48e4-8d0a-7ea37121b03a"  # ZZTest T179 Hoodie (dilacak, WAC 180.000)
REKENING = "56842900-0ae4-4c25-8ad5-d0726a36e21b"  # BCA Operasional kaos
TGL = date(2026, 10, 23)
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
MERAH, BOCOR = os.environ.get("MERAH") == "1", os.environ.get("MERAH_BOCOR") == "1"
KUNCI_BOCOR = "TES-E2E-NDU3D-BOCOR"
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
    "vc": "SELECT count(*) FROM vendor_credits WHERE tenant_id = $1",
    "vca": "SELECT count(*) FROM vendor_credit_applications WHERE tenant_id = $1",
    "vcr": "SELECT count(*) FROM vendor_credit_refunds WHERE tenant_id = $1",
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0) FROM journal_entries WHERE tenant_id = $1",
    "bank": "SELECT count(*), COALESCE(sum(amount),0) FROM bank_transactions WHERE tenant_id = $1",
    "stok": "SELECT count(*), COALESCE(sum(quantity_in - quantity_out),0) FROM inventory_ledger WHERE tenant_id = $1",
    "tagihan": "SELECT json_agg(json_build_object('p', amount_paid, 's', status, 'u', updated_at)) FROM bills WHERE tenant_id = $1 AND invoice_number = '" + TAGIHAN + "'",
    "hutang": "SELECT COALESCE(sum(outstanding),0) FROM compute_ap_outstanding($1)",
    "seq_jurnal": "SELECT json_agg(json_build_object('p', prefix, 'y', year, 'm', month, 'n', last_number) ORDER BY prefix, year, month) FROM journal_number_sequences WHERE tenant_id = $1",
    "idem": "SELECT count(*) FROM idempotency_keys WHERE tenant_id = $1",
}


async def jepret(c, kunci=None):
    return {k: [js(v) for v in (await c.fetchrow(q, T)).values()] for k, q in SQL_JEPRET.items() if not kunci or k in kunci}


async def jurnal_vc(c, vid) -> dict:
    """{journal_id: rekaman} jurnal bersumber ND ini (tanpa uuid/nomor/cap waktu)."""
    out = {}
    for j in await c.fetch("SELECT id, source_type, description, total_debit, reversal_of_id FROM journal_entries WHERE tenant_id = $1 AND source_id = $2",
                           T, vid):
        baris = [(r["account_code"], str(D(r["debit"]).normalize()), str(D(r["credit"]).normalize()), r["memo"]) for r in await c.fetch(
            """SELECT coa.account_code, jl.debit, jl.credit, jl.memo FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id
               WHERE jl.journal_id = $1 ORDER BY jl.line_number""", j["id"])]
        out[str(j["id"])] = {"source_type": j["source_type"], "description": j["description"], "total": str(D(j["total_debit"]).normalize()),
                             "pembalik": j["reversal_of_id"] is not None, "lines": baris}
    return out


def urut(js_):
    return sorted(js_, key=lambda x: (x["source_type"], x["description"], json.dumps(x["lines"])))


def dari_rencana(jurnal) -> list:
    return [{"source_type": j["source_type"], "description": j["description"],
             "lines": [(l["account_code"], str(D(l["debit"]).normalize()), str(D(l["credit"]).normalize()), l["memo"]) for l in j["lines"]]}
            for j in jurnal]


async def tulis():
    from fastapi import HTTPException
    from app.routers import vendor_credits as VR
    from app.schemas.vendor_credits import (CreateVendorCreditRequest, ApplyVendorCreditRequest, ReceiveRefundRequest,
                                            VoidVendorCreditRequest, UnapplyVendorCreditRequest)
    baru = hasattr(VR, "preview_post_vendor_credit")
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    bill = await c.fetchrow("SELECT id, vendor_id, vendor_name FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, TAGIHAN)
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)

    class Kon:  # koneksi tertambal: transaction(readonly=...) pratinjau = savepoint biasa di transaksi luar
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()
    k = Kon()
    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False
    class Tambal:
        def acquire(s): return CM()
    async def gp(): return Tambal()
    VR.get_pool = gp
    hasil = {}
    tx = c.transaction(); await tx.start()
    try:
        if BOCOR:
            b = await asyncpg.connect(os.environ["DATABASE_URL"])
            await b.execute("INSERT INTO idempotency_keys (key, tenant_id, expires_at) VALUES ($1, $2, NOW() + interval '1 hour')", KUNCI_BOCOR, T)
            await b.close()

        async def buat(total, alasan="other", barang=False):
            items = [{"description": "TES E2E U3d", "quantity": 1, "unit_price": total, **({"item_id": BARANG} if barang else {})}]
            r = await VR.create_vendor_credit(req(uid), CreateVendorCreditRequest(
                vendor_id=str(bill["vendor_id"]), vendor_name=bill["vendor_name"], vendor_credit_date=TGL, reason=alasan, ref_no="TES E2E U3d",
                notes="TES E2E U3d", items=items))
            return r["data"]["id"] if isinstance(r, dict) else r.data["id"]

        async def pratinjau(fn, *a):
            if not baru:
                return None
            sb = await jepret(c, ("vc", "vca", "vcr", "jurnal", "bank", "stok", "seq_jurnal"))
            d = (await fn(req(uid), *a))["data"]
            ss = await jepret(c, ("vc", "vca", "vcr", "jurnal", "bank", "stok", "seq_jurnal"))
            cek(sb == ss, f"pratinjau {d['action']} tak menulis apa pun (hitungan + pencacah sama)", "" if sb == ss else f"{sb} / {ss}")
            cek(all(j["journal_number"] is None for j in d["journals"]), f"pratinjau {d['action']} tanpa nomor jurnal")
            return d

        async def langkah(nama, vid, pv_fn, pv_args, tulis_fn, tulis_args):
            pv = await pratinjau(pv_fn, *pv_args) if pv_fn else None
            sebelum = await jurnal_vc(c, vid)
            await tulis_fn(req(uid), *tulis_args)
            sesudah = await jurnal_vc(c, vid)
            baru_j = urut([{kk: v for kk, v in j.items() if kk in ("source_type", "description", "lines")}
                           for jid, j in sesudah.items() if jid not in sebelum])
            hasil[nama] = baru_j
            if pv is not None:
                rencana = urut(dari_rencana(pv["journals"]))
                if MERAH and nama == "P" and rencana:
                    rencana[0]["lines"][0] = (rencana[0]["lines"][0][0], "1", *rencana[0]["lines"][0][2:])
                cek(pv["can_proceed"] and rencana == baru_j, f"({nama}) jurnal pratinjau == jurnal tertulis", "" if rencana == baru_j else f"{rencana} / {baru_j}")
            return pv

        # P posting retur barang dilacak
        vp = await buat(200000, "return", barang=True)
        pv = await langkah("P", vp, getattr(VR, "preview_post_vendor_credit", None), (vp,), VR.post_vendor_credit, (vp,))
        if pv is not None:
            st = await c.fetch("SELECT quantity_out, unit_cost, total_cost FROM inventory_ledger WHERE tenant_id = $1 AND source_id = $2", T, vp)
            cek(len(pv["stock"]) == 1 and len(st) == 1 and D(pv["stock"][0]["unit_cost"]) == D(st[0]["unit_cost"])
                and D(pv["stock"][0]["total_cost"]) == D(st[0]["total_cost"]), "(P) stok/HPP pratinjau == kartu stok tertulis",
                f"{pv['stock']} / {[dict(x) for x in st]}")
            cek(any(j["source_type"] == "VENDOR_CREDIT_COGS" for j in pv["journals"]), "(P) jurnal HPP ikut direncanakan")
        # B blok: posting ulang ND yang sudah terbit
        if baru:
            pb = await pratinjau(VR.preview_post_vendor_credit, vp)
            sp = c.transaction(); await sp.start()
            try:
                await VR.post_vendor_credit(req(uid), vp)
                hb = "LOLOS"
            except HTTPException as e:
                hb = (e.status_code, e.detail)
            finally:
                await sp.rollback()
            cek(not pb["can_proceed"] and isinstance(hb, tuple) and pb["blocks"][0]["message"] == hb[1] and hb[0] == 400,
                "(B) blok pratinjau == galat tulis (posting ulang)", f"{pb['blocks']} / {hb}")
        # R refund sebagian ND bebas
        vr = await buat(3000); await VR.post_vendor_credit(req(uid), vr)
        rb = ReceiveRefundRequest(amount=1000, refund_date=TGL, payment_method="transfer", bank_account_id=REKENING, reference="TES E2E U3d")
        pv = await langkah("R", vr, getattr(VR, "preview_receive_refund_vendor_credit", None), (vr, rb), VR.receive_refund, (vr, rb))
        if pv is not None:
            bt = await c.fetch("SELECT amount, transaction_type FROM bank_transactions WHERE tenant_id = $1 AND reference_type = 'vendor_credit_refund' "
                               "AND reference_number = (SELECT credit_number FROM vendor_credits WHERE id = $2)", T, vr)
            cek(len(bt) == 1 and D(pv["bank_transactions"][0]["amount"]) == D(bt[0]["amount"]) and pv["bank_transactions"][0]["transaction_type"] ==
                bt[0]["transaction_type"], "(R) cermin bank pratinjau == tertulis", f"{pv['bank_transactions']} / {[dict(x) for x in bt]}")
        # A terapkan, U lepas
        va = await buat(1000); await VR.post_vendor_credit(req(uid), va)
        ab = ApplyVendorCreditRequest(applications=[{"bill_id": str(bill["id"]), "amount": 1000}], application_date=TGL)
        sisa0 = D(await c.fetchval("SELECT outstanding FROM compute_ap_rincian($1) WHERE bill_id = $2", T, bill["id"]))
        pv = await langkah("A", va, getattr(VR, "preview_apply_vendor_credit", None), (va, ab), VR.apply_vendor_credit, (va, ab))
        sisa1 = D(await c.fetchval("SELECT outstanding FROM compute_ap_rincian($1) WHERE bill_id = $2", T, bill["id"]))
        if pv is not None:
            cek(D(pv["bills"][0]["remaining_before"]) == sisa0 and D(pv["bills"][0]["remaining_after"]) == sisa1, "(A) sisa tagihan pratinjau == nyata",
                f"{pv['bills']} / {sisa0}->{sisa1}")
        ub = UnapplyVendorCreditRequest(reason="TES E2E U3d")
        pv = await langkah("U", va, getattr(VR, "preview_unapply_vendor_credit", None), (va, VR._PratinjauAlasan(reason="TES E2E U3d")) if baru else (),
                           VR.unapply_vendor_credit, (va, ub))
        sisa2 = D(await c.fetchval("SELECT outstanding FROM compute_ap_rincian($1) WHERE bill_id = $2", T, bill["id"]))
        if pv is not None:
            cek(D(pv["bills"][0]["remaining_before"]) == sisa1 and D(pv["bills"][0]["remaining_after"]) == sisa2 == sisa0,
                "(U) sisa tagihan pratinjau == nyata (kembali)", f"{pv['bills']} / {sisa1}->{sisa2}")
        # V batal ND dari P (cermin posting + cermin HPP)
        vb = VoidVendorCreditRequest(reason="TES E2E U3d")
        await langkah("V", vp, getattr(VR, "preview_void_vendor_credit", None), (vp, VR._PratinjauAlasan(reason="TES E2E U3d")) if baru else (),
                      VR.void_vendor_credit, (vp, vb))
        cek(len(hasil["V"]) == 2, "(V) dua jurnal pembalik (posting + HPP)", str(len(hasil["V"])))
        # D batal draf = hapus
        vd = await buat(500)
        if baru:
            pd = await pratinjau(VR.preview_void_vendor_credit, vd, VR._PratinjauAlasan(reason="TES E2E U3d"))
            cek(pd["after"] == {"exists": False} and pd["can_proceed"], "(D) pratinjau batal draf = dihapus (after.exists false)")
        await VR.void_vendor_credit(req(uid), vd, vb)
        cek(await c.fetchval("SELECT count(*) FROM vendor_credits WHERE id = $1", vd) == 0, "(D) draf terhapus oleh tulis")
    finally:
        await tx.rollback(); await c.close()
    json.dump(hasil, open(f"/out/{NAMA}.json", "w"), indent=1, default=str)
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)


async def main():
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump(await jepret(c), open(f"/out/{NAMA}.json", "w"), default=str); await c.close(); print(f"JEPRET {NAMA} ok"); return
    if MODE == "TULIS":
        return await tulis()
    if MODE == "BANDING":
        lama, baru = json.load(open("/out/lama.json")), json.load(open("/out/baru.json"))
        for k in ("P", "R", "A", "U", "V"):
            cek(lama.get(k) == baru.get(k), f"penulis LAMA == BARU: langkah {k}", "" if lama.get(k) == baru.get(k) else f"{lama.get(k)} / {baru.get(k)}")
        s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
        for k in SQL_JEPRET:
            cek(s[k] == e[k], f"NET 0 (sesudah proses selesai): {k}", "" if s[k] == e[k] else f"{s[k]} / {e[k]}")
        print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
