"""UJI NYATA V444 sesudah diterapkan (23 Okt 2026): (1) BACA: celah peran semai = 0 di SEMUA tenant "Tenant" (tenant wajib ada);
(2) kaos TULIS di satu transaksi luar yang SELALU di-ROLLBACK: penyesuaian saldo BCA Operasional +1.000 lalu -1.000 lewat rute nyata
/bank-accounts/{id}/adjust -> jurnal mengkredit 4-90000 / mendebit 5-20900 (dulu 400 "Akun penyesuaian saldo belum diatur");
(3) NET 0: jepret sebelum == sesudah di proses terpisah. MERAH=1: kode akun harapan dirusak -> WAJIB gagal."""
import asyncio, json, os, sys, uuid
from datetime import date
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T = "kaos-biru-konveksi"
REKENING = uuid.UUID("56842900-0ae4-4c25-8ad5-d0726a36e21b")  # BCA Operasional kaos
MODE, NAMA, MERAH = os.environ["MODE"], os.environ.get("NAMA", ""), os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


SQL_JEPRET = {"jurnal": "SELECT count(*), COALESCE(sum(total_debit),0)::text FROM journal_entries WHERE tenant_id = $1",
              "bank": "SELECT count(*), COALESCE(sum(amount),0)::text FROM bank_transactions WHERE tenant_id = $1",
              "peran": "SELECT count(*) FROM account_roles WHERE tenant_id = $1",
              "seq": "SELECT json_agg(json_build_object('p', prefix, 'n', last_number) ORDER BY prefix, year, month)::text FROM journal_number_sequences WHERE tenant_id = $1"}


async def main():
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        json.dump({k: [str(v) for v in (await c.fetchrow(q, T)).values()] for k, q in SQL_JEPRET.items()}, open(f"/out/{NAMA}.json", "w"))
        await c.close(); print(f"JEPRET {NAMA} ok"); return
    if MODE == "BANDING":
        s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
        for k in SQL_JEPRET:
            cek(s[k] == e[k], f"NET 0 (sesudah proses selesai): {k}", "" if s[k] == e[k] else f"{s[k]} / {e[k]}")
        print(f"RINGKAS gagal={len(gagal)}"); sys.exit(1 if gagal else 0)
    from app.services.role_resolver import PERAN_SEMAI
    from app.routers import bank_accounts as BA
    from app.schemas.bank_accounts import AdjustBalanceRequest
    from fastapi import HTTPException
    baca = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    tenant = [r["id"] for r in await baca.fetch('SELECT id FROM "Tenant" ORDER BY id')]
    cek(T in tenant and "grapgrap-manado" in tenant, "tenant kaos + grapgrap-manado ADA di \"Tenant\"", str(tenant))
    celah = await baca.fetch("""SELECT t.id, array_agg(p.r ORDER BY p.r) AS peran FROM "Tenant" t CROSS JOIN unnest($1::text[]) p(r)
                                WHERE NOT EXISTS (SELECT 1 FROM account_roles a WHERE a.tenant_id = t.id AND a.role_key = p.r) GROUP BY t.id""",
                             sorted(PERAN_SEMAI))
    cek(not celah, f"celah peran semai = 0 di {len(tenant)} tenant", str([(r['id'], list(r['peran'])) for r in celah]))
    await baca.close()
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
    BA.get_pool = gp
    harap = {1000: "4-90000", -1000: "5-20900"}
    if MERAH:
        harap = {1000: "4-90001", -1000: "5-20900"}
    tx = c.transaction(); await tx.start()
    try:
        for jumlah, kode in harap.items():
            sb = {r["id"] for r in await c.fetch("SELECT id FROM journal_entries WHERE tenant_id = $1", T)}
            try:
                await BA.adjust_bank_balance(req(uid), REKENING, AdjustBalanceRequest(adjustment_date=date(2026, 10, 23), adjustment_amount=jumlah,
                                                                                     reason="TES E2E V444"))
            except HTTPException as e:
                cek(False, f"penyesuaian {jumlah} lolos (dulu 400 peran tak terpetakan)", f"{e.status_code} {e.detail}"); continue
            baru = [r for r in await c.fetch("SELECT id FROM journal_entries WHERE tenant_id = $1", T) if r["id"] not in sb]
            baris = await c.fetch("""SELECT coa.account_code, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id
                                     WHERE jl.journal_id = ANY($1::uuid[])""", [r["id"] for r in baru])
            sisi = [b for b in baris if b["account_code"] == kode and ((jumlah > 0 and b["credit"] > 0) or (jumlah < 0 and b["debit"] > 0))]
            cek(len(baru) == 1 and len(sisi) == 1, f"penyesuaian {jumlah:+} -> jurnal ber-akun lawan {kode}",
                str([(b["account_code"], str(b["debit"]), str(b["credit"])) for b in baris]))
    finally:
        await tx.rollback(); await c.close()
    print(f"RINGKAS gagal={len(gagal)} {gagal[:4]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
