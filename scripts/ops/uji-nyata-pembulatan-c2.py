"""Harness NYATA c-2 PEMBULATAN PEMBELIAN (kaos SAJA, penanda 'TES E2E PEMBULATAN-C2'): SATU transaksi luar pada satu koneksi SELALU
di-ROLLBACK (pola FPB-DEBIT-PENUH: pool tertambal, transaksi dalam = savepoint). PRASYARAT: V479 SUDAH diterapkan (jendela migrasi
sendiri; MASTER 5 Nov: tanpa DDL di dalam harness). Sesudah ROLLBACK diukur di koneksi LAIN: nol sisa tagihan/ND/jurnal/stok.

Rantai = contoh angka yang disetujui pemilik LANGSUNG 5 Nov (fixture di dalam ROLLBACK: kaos non-PKP, tanpa uang_bersen):
  tagihan 3 x 16.650 + PPN 11% (dikapitalisasi) = 55.444,50 -> draf mentah -> pratinjau posting == jurnal tertulis ->
  Dr Persediaan 55.444,50 · Dr Pembulatan 0,50 · Cr Hutang 55.445; grand_total 55.445, pembulatan 0,50; kartu stok = 55.444,50;
  ND retur 1 x 16.650 + 11% = 18.481,50 -> Dr Hutang 18.482 · Cr Retur 18.481,50 · Cr Pembulatan 0,50; sisa tagihan 36.963;
  batal ND -> batal tagihan -> GL setiap akun yang tersentuh + qty stok kembali ke sebelum (net 0).
Kanal keluar di-stub: WAJIB 0. Argumen: <keluaran.json>. Di pohon DASAR (kode tanpa c-2) harness WAJIB GAGAL (kontrol merah)."""
import asyncio
import json
import os
import sys
import uuid
from datetime import timedelta
from decimal import Decimal

import asyncpg

sys.path.insert(0, os.getcwd())
sys.path.insert(0, "/app")
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-harness")

KELUAR = []


def _pasang_pagar_keluar():
    import smtplib

    def catat(nama):
        def f(*a, **k):
            KELUAR.append(nama)
            raise RuntimeError(f"harness: kanal keluar {nama} diblokir")
        return f

    async def acatat(*a, **k):
        KELUAR.append("async-http")
        raise RuntimeError("harness: kanal keluar async-http diblokir")
    smtplib.SMTP.__init__ = catat("smtp")
    smtplib.SMTP_SSL.__init__ = catat("smtp_ssl")
    for modul, kelas, attr, f in (("httpx", "Client", "send", catat("httpx")), ("httpx", "AsyncClient", "send", acatat),
                                  ("requests", "Session", "send", catat("requests")), ("aiohttp", "ClientSession", "_request", acatat)):
        try:
            setattr(getattr(__import__(modul), kelas), attr, f)
        except ImportError:
            pass


_pasang_pagar_keluar()

from starlette.requests import Request  # noqa: E402

from app.routers import bills as RB  # noqa: E402
from app.routers import vendor_credits as RVC  # noqa: E402
from app.schemas.bills import CreateBillRequestV2 as CB, VoidBillRequest  # noqa: E402
from app.schemas.vendor_credits import CreateVendorCreditRequest, VoidVendorCreditRequest  # noqa: E402
from app.services import db_pool  # noqa: E402
from app.services import policy_engine_client as PEC  # noqa: E402
from app.services import tagihan_aksi as TA  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

T = "kaos-biru-konveksi"
VENDOR, STOK = "73a2b4bd-72d3-46cb-8adb-79f14f9e28e7", "862be8e3-b35a-48e3-b8c3-c200f75feba9"  # == harness ND-BUAT / FPB-DEBIT-SATU
PENANDA = "TES E2E PEMBULATAN-C2"
D = Decimal


def req(uid, role="OWNER"):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid), "role": role}
    return r


async def main(keluar):
    c = await asyncpg.connect(os.environ["URL"])
    c2 = await asyncpg.connect(os.environ["URL"])
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval("""SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id
                              WHERE utr.tenant_id = $1 AND r.code = 'OWNER' LIMIT 1""", T)

    class Kon:
        def __getattr__(s, a): return getattr(c, a)
        def transaction(s, **kw): return c.transaction()

    class CM:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class Pool:
        def acquire(s): return CM()

    async def gp(): return Pool()
    RB.get_pool = gp
    RVC.get_pool = gp
    db_pool.get_db_pool = gp
    PEC.init_policy_engine(Pool())
    try:
        from backend.api_gateway.app.services import policy_engine_client as PEC_MW
        PEC_MW.init_policy_engine(Pool())
    except Exception:  # noqa: BLE001
        pass
    cek, catat = [], {}

    def harus(nama, kondisi, info=None):
        cek.append({"cek": nama, "ok": bool(kondisi), "info": info})
        print(("OK   " if kondisi else "GAGAL ") + nama + ("" if kondisi else f" -> {info}"), flush=True)

    async def saksi():
        return {"tagihan": await c2.fetchval("SELECT count(*) FROM bills WHERE tenant_id = $1 AND notes = $2", T, PENANDA),
                "nd": await c2.fetchval("SELECT count(*) FROM vendor_credits WHERE tenant_id = $1 AND notes = $2", T, PENANDA),
                "jurnal": await c2.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", T),
                "stok": await c2.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1", T),
                "peran": await c2.fetchval("SELECT count(*) FROM account_roles WHERE role_key = 'PURCHASE_ROUNDING'")}

    async def baris_jurnal(jid):
        rows = await c.fetch("""SELECT ca.account_code, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts ca ON ca.id = jl.account_id
                                WHERE jl.journal_id = $1 ORDER BY jl.line_number""", jid)
        return [(r["account_code"], D(str(r["debit"])), D(str(r["credit"]))) for r in rows]

    async def gl(kode):
        return {k: D(str(await c.fetchval(
            """SELECT COALESCE(sum(jl.debit - jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
               JOIN chart_of_accounts ca ON ca.id = jl.account_id WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND ca.account_code = $2""",
            T, k))) for k in kode}

    async def kode_peran(k):
        return await c.fetchval("""SELECT ca.account_code FROM account_roles ar JOIN chart_of_accounts ca ON ca.id = ar.account_id
                                   WHERE ar.tenant_id = $1 AND ar.role_key = $2""", T, k)

    sebelum = await saksi()
    luar = c.transaction()
    await luar.start()
    try:
        harus("prasyarat: kolom pembulatan + peran PURCHASE_ROUNDING kaos", await c.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'bills' AND column_name = 'pembulatan'") == 1
            and await kode_peran("PURCHASE_ROUNDING") == "5-90100")
        # fixture (di dalam ROLLBACK): non-PKP + presisi 0
        catat["fixture"] = {"is_pkp": await c.fetchval('SELECT is_pkp FROM "Tenant" WHERE id = $1', T),
                            "uang_bersen": await c.fetchval("SELECT count(*) FROM tenant_features WHERE tenant_id = $1 AND feature = 'uang_bersen' AND enabled", T)}
        await c.execute('UPDATE "Tenant" SET is_pkp = false WHERE id = $1', T)
        await c.execute("DELETE FROM tenant_features WHERE tenant_id = $1 AND feature = 'uang_bersen'", T)
        hari = await tanggal_dokumen(c, T)
        AP, INV, BUL, RET = (await kode_peran("AP_TRADE"), await kode_peran("INVENTORY_MERCHANDISE"), "5-90100",
                             await kode_peran("COGS_PURCHASE_RETURN"))
        catat["akun"] = {"AP": AP, "INV": INV, "BUL": BUL, "RET": RET}
        akun = sorted({a for a in (AP, INV, BUL, RET) if a})
        gl0 = await gl(akun)
        qty0 = D(str(await c.fetchval("SELECT COALESCE(sum(quantity), 0) FROM warehouse_stock WHERE tenant_id = $1 AND item_id = $2", T, STOK) or 0))
        tc = await c.fetchval("""SELECT id FROM tax_codes WHERE tenant_id = $1 AND tax_type = 'ppn' AND rate = 11 AND is_active
                                 AND COALESCE(dpp_factor_num, 1) = COALESCE(dpp_factor_den, 1) ORDER BY created_at LIMIT 1""", T)
        harus("prasyarat: kode PPN 11% DPP penuh kaos", tc is not None, tc)

        # 1. tagihan draf mentah -> pratinjau == posting
        nomor = f"TES-E2E-C2-{uuid.uuid4().hex[:6]}"
        await RB.create_bill_v2(req(uid), CB(vendor_id=VENDOR, invoice_number=nomor, due_date=hari + timedelta(days=30), notes=PENANDA,
                                             tax_rate=11, tax_code_id=tc, status="draft",
                                             items=[{"product_id": STOK, "product_name": "TES E2E C2", "qty": 3, "price": 16650, "tax_rate": 11}]))
        b = await c.fetchrow("SELECT id, grand_total, tax_amount, pembulatan FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, nomor)
        harus("draf: total MENTAH 55.444,50 (PPN 5.494,50), pembulatan 0", (b["grand_total"], b["tax_amount"], b["pembulatan"])
              == (D("55444.50"), D("5494.50"), D("0")), dict(b))
        pr = await TA.rencana_posting(c, T, b["id"])
        harus("pratinjau posting: nol blok, total 55.445", not pr["blocks"] and pr["grand_total"] == D("55445"), [pr["blocks"], str(pr["grand_total"])])
        await RB.post_bill(req(uid), b["id"])
        b = await c.fetchrow("SELECT id, grand_total, amount, pembulatan, journal_id FROM bills WHERE id = $1", b["id"])
        harus("posted: grand_total 55.445 + pembulatan 0,50", (b["grand_total"], b["pembulatan"]) == (D("55445"), D("0.50")), dict(b))
        jl = await baris_jurnal(b["journal_id"])
        catat["jurnal_tagihan"] = [[a, str(d), str(k)] for a, d, k in jl]
        harus("jurnal tagihan == contoh pemilik: Dr Persediaan 55.444,50 · Dr Pembulatan 0,50 · Cr Hutang 55.445",
              sorted(jl) == sorted([(INV, D("55444.50"), D("0")), (BUL, D("0.50"), D("0")), (AP, D("0"), D("55445"))]), jl)
        harus("pratinjau posting == jurnal tertulis", sorted((x["account_code"], D(x["debit"]), D(x["credit"])) for x in pr["baris"]) == sorted(jl),
              pr["baris"])
        hdr = await c.fetchrow("SELECT total_debit, total_credit FROM journal_entries WHERE id = $1", b["journal_id"])
        harus("header jurnal == Σ baris (Law 4)", hdr["total_debit"] == hdr["total_credit"] == sum(d for _, d, _ in jl), dict(hdr))
        kartu = D(str(await c.fetchval("""SELECT COALESCE(sum(total_cost), 0) FROM inventory_ledger WHERE tenant_id = $1 AND source_id = $2
                                         AND movement_type = 'PURCHASE'""", T, b["id"]) or 0))  # source_id uuid (skema diukur)
        catat["kartu_tagihan"] = str(kartu)
        harus("kartu stok = nilai baris asli 55.444,50 (pembulatan bukan biaya barang)", kartu == D("55444.50"), str(kartu))
        sisa0 = D(str(await c.fetchval("SELECT outstanding FROM compute_ap_outstanding($1) WHERE bill_id = $2", T, b["id"]) or 0))
        harus("sisa hutang (jurnal) = 55.445", sisa0 == D("55445"), str(sisa0))

        # 2. ND sebagian 1 x 16.650 + 11% = 18.481,50
        bi = await c.fetchval("SELECT id FROM bill_items WHERE bill_id = $1 ORDER BY line_number LIMIT 1", b["id"])
        vr = await RVC.create_vendor_credit(req(uid), CreateVendorCreditRequest(
            vendor_id=VENDOR, vendor_name="TES", vendor_credit_date=hari, original_bill_id=str(b["id"]), reason="return", notes=PENANDA,
            items=[{"item_id": STOK, "description": "TES E2E C2", "quantity": 1, "unit_price": 16650, "tax_rate": 11,
                    "original_bill_item_id": str(bi)}], tax_rate=11))
        vid = await c.fetchval("SELECT id FROM vendor_credits WHERE tenant_id = $1 AND notes = $2 ORDER BY created_at DESC LIMIT 1", T, PENANDA)
        v = await c.fetchrow("SELECT total_amount, tax_amount, pembulatan FROM vendor_credits WHERE id = $1", vid)
        harus("ND draf: total MENTAH 18.481,50", (v["total_amount"], v["pembulatan"]) == (D("18481.50"), D("0")), [dict(v), str(vr)[:200]])
        await RVC.post_vendor_credit(req(uid), vid)
        v = await c.fetchrow("SELECT total_amount, pembulatan, journal_id FROM vendor_credits WHERE id = $1", vid)
        harus("ND posted: total 18.482 + pembulatan 0,50", (v["total_amount"], v["pembulatan"]) == (D("18482"), D("0.50")), dict(v))
        jn = await baris_jurnal(v["journal_id"])
        catat["jurnal_nd"] = [[a, str(d), str(k)] for a, d, k in jn]
        harus("jurnal ND == contoh pemilik: Dr Hutang 18.482 · Cr Retur 18.481,50 · Cr Pembulatan 0,50",
              sorted(jn) == sorted([(AP, D("18482"), D("0")), (RET, D("0"), D("18481.50")), (BUL, D("0"), D("0.50"))]), jn)
        sisa1 = D(str(await c.fetchval("SELECT outstanding FROM compute_ap_outstanding($1) WHERE bill_id = $2", T, b["id"]) or 0))
        catat["sisa_sesudah_nd"] = str(sisa1)

        # 3. batal ND -> batal tagihan -> net 0
        await RVC.void_vendor_credit(req(uid), vid, VoidVendorCreditRequest(reason=PENANDA))
        await RB.void_bill(req(uid), b["id"], VoidBillRequest(reason=PENANDA))
        gl1 = await gl(akun)
        qty1 = D(str(await c.fetchval("SELECT COALESCE(sum(quantity), 0) FROM warehouse_stock WHERE tenant_id = $1 AND item_id = $2", T, STOK) or 0))
        harus("net 0: GL Hutang/Persediaan/Pembulatan/Retur kembali ke sebelum", gl1 == gl0, [str(gl0), str(gl1)])
        harus("net 0: qty stok kembali", qty1 == qty0, [str(qty0), str(qty1)])
        n_tak_seimbang = await c.fetchval("""SELECT count(*) FROM journal_entries je WHERE je.tenant_id = $1 AND je.created_at >= now() - interval '1 hour'
                                            AND je.status = 'POSTED' AND (je.total_debit <> je.total_credit OR je.total_debit <>
                                            (SELECT sum(debit) FROM journal_lines WHERE journal_id = je.id))""", T)
        harus("semua jurnal rantai seimbang header == Σ baris", n_tak_seimbang == 0, n_tak_seimbang)
    finally:
        await luar.rollback()
    sesudah = await saksi()
    harus("ROLLBACK: dari koneksi LAIN nol sisa tagihan/ND TES E2E, jurnal/stok/peran tak berubah", sesudah == sebelum, [sebelum, sesudah])
    harus("nol panggilan kanal keluar", KELUAR == [], KELUAR)
    await c.close()
    await c2.close()
    gagal = [x["cek"] for x in cek if not x["ok"]]
    json.dump({"cek": cek, "catat": catat, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}", f"({len(cek)} cek)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
