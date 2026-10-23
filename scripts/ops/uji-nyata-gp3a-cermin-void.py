"""UJI NYATA GP3a (23 Okt 2026): void pembayaran keluar membalik cermin bank KEDUA jenis pembayaran. kaos SAJA, SATU transaksi luar yang
SELALU di-ROLLBACK (get_pool tertambal). Lewat rute nyata POST /bill-payments/{id}/void:
  BP  BP-2609-0001 (record_payment lama: cermin reference_type 'bill' + id TAGIHAN)
  PAY PAY-202609-0001 (/bill-payments: cermin reference_type 'bill_payment' + id pembayaran)
Per pembayaran: cermin bank (journal_id asli + jurnal void) = 2 baris, Σ amount = 0; saldo GL akun bank dari kedua jurnal = 0; status voided.
Pohon LAMA direkam (BP: cermin TERTINGGAL = 1 baris, Σ != 0 -- cacat yang diperbaiki); pohon BARU dinilai. NET 0 (proses terpisah).
MERAH=1: harapan Σ cermin digeser 1 -> WAJIB gagal."""
import asyncio, json, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
BAYAR = {"BP": "767026a7-fe16-4395-a814-0fa1073729e6", "PAY": "97ab274d-ddfd-4480-a9f3-bf9fac897c80"}
MODE, NAMA, MERAH = os.environ["MODE"], os.environ.get("NAMA", ""), os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


SQL_JEPRET = {
    "bayar": "SELECT json_agg(json_build_object('n', payment_number, 's', status) ORDER BY payment_number)::text FROM bill_payments_v2 WHERE tenant_id = $1",
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0)::text FROM journal_entries WHERE tenant_id = $1",
    "bank": "SELECT count(*), COALESCE(sum(amount),0)::text FROM bank_transactions WHERE tenant_id = $1",
    "tagihan": "SELECT json_agg(json_build_object('n', invoice_number, 'p', amount_paid, 's', status) ORDER BY invoice_number)::text FROM bills WHERE tenant_id = $1",
    "pph": "SELECT count(*) FILTER (WHERE status = 'void') FROM withholding_tax_records WHERE tenant_id = $1",
    "seq": "SELECT json_agg(json_build_object('p', prefix, 'n', last_number) ORDER BY prefix, year, month)::text FROM journal_number_sequences WHERE tenant_id = $1",
}


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


async def tulis():
    import uuid
    from app.routers import bill_payments as BPR
    from app.schemas.bill_payments import VoidBillPaymentRequest
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
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
    BPR.get_pool = gp
    hasil = {}
    tx = c.transaction(); await tx.start()
    try:
        for jenis, pid in BAYAR.items():
            await BPR.void_bill_payment(req(uid), pid, VoidBillPaymentRequest(void_reason="TES E2E GP3a"))
            p = await c.fetchrow("SELECT status, journal_id, void_journal_id, bank_account_id FROM bill_payments_v2 WHERE id = $1", uuid.UUID(pid))
            bt = await c.fetch("SELECT amount, transaction_type, reference_type FROM bank_transactions WHERE tenant_id = $1 AND journal_id = ANY($2::uuid[])",
                               T, [p["journal_id"], p["void_journal_id"]])
            coa = await c.fetchval("SELECT coa_id FROM bank_accounts WHERE id = $1", p["bank_account_id"])
            gl = await c.fetchval("""SELECT COALESCE(sum(debit - credit), 0) FROM journal_lines WHERE account_id = $1 AND journal_id = ANY($2::uuid[])""",
                                  coa, [p["journal_id"], p["void_journal_id"]])
            hasil[jenis] = {"status": p["status"], "n_cermin": len(bt), "sigma_cermin": str(sum((b["amount"] for b in bt), 0)),
                            "gl_bank": str(gl), "rt": sorted(b["reference_type"] for b in bt)}
        print(f"REKAM {NAMA} {json.dumps(hasil)}", flush=True)
    finally:
        await tx.rollback(); await c.close()
    json.dump(hasil, open(f"/out/{NAMA}.json", "w"))


async def main():
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump({kk: [str(v) for v in (await c.fetchrow(q, T)).values()] for kk, q in SQL_JEPRET.items()}, open(f"/out/{NAMA}.json", "w"))
        await c.close(); print(f"JEPRET {NAMA} ok"); return
    if MODE == "TULIS":
        return await tulis()
    lama, baru = json.load(open("/out/lama.json")), json.load(open("/out/baru.json"))
    print(f"LAMA {lama}\nBARU {baru}")
    harap = "1" if MERAH else "0"
    for jenis in ("BP", "PAY"):
        b = baru[jenis]
        cek(b["status"] == "voided" and b["n_cermin"] == 2 and str(float(b["sigma_cermin"])) == str(float(harap)) and float(b["gl_bank"]) == 0,
            f"({jenis}) void: cermin bank 2 baris Σ 0 + GL bank 0 (baru)", str(b))
    cek(lama["BP"]["n_cermin"] == 1, "(BP) pohon LAMA: cermin TERTINGGAL (1 baris) -- cacat yang diperbaiki terbukti nyata", str(lama["BP"]))
    cek(lama["PAY"] == baru["PAY"], "(PAY) lama == baru (jalur yang sudah benar tak berubah)", f"{lama['PAY']} / {baru['PAY']}")
    s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
    for kk in SQL_JEPRET:
        cek(s[kk] == e[kk], f"NET 0 (sesudah proses selesai): {kk}", "" if s[kk] == e[kk] else f"{s[kk]} / {e[kk]}")
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
