"""K2 kas-negatif uji di SALINAN (klon milkydb, tenant kaos): handler ASLI kasbon (grant + pratinjau), posting pembayaran gaji (kaki tunai),
aktivasi aktiva tetap, jurnal MANUAL buat-posting. Benih (pembayaran gaji draf, aktiva draf) hanya di salinan.
MODE=cabang: nilai > saldo kas -> 400 KAS_MINUS dan NOL jurnal baru; nilai kecil -> lolos (jurnal tertulis).
MODE=dasar (kode master lama = kontrol merah): nilai > saldo kas LOLOS (kas jadi minus) -> membuktikan uji ini bisa membedakan."""
import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import employee_advances as EA  # noqa: E402
from app.routers import fixed_assets as FA  # noqa: E402
from app.routers import journals as J  # noqa: E402
from app.routers import payroll_payments as PP  # noqa: E402
from app.schemas.fixed_assets import ActivateAssetRequest  # noqa: E402
from app.schemas.journals import CreateJournalRequest  # noqa: E402

MODE = sys.argv[1]
TID = "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(ok)
    print(("PASS " if ok else "FAIL ") + f"[{MODE}] {nama}" + (f" -- {info}" if info else ""), flush=True)


def req(uid):
    r = Request({"type": "http", "method": "POST", "path": "/uji", "headers": []})
    r.state.user = {"tenant_id": TID, "user_id": str(uid), "username": "uji-salinan-k2", "role": "OWNER"}
    return r


async def coba(aw):
    """-> ('ok', hasil) | ('galat', kode, status)"""
    try:
        return ("ok", await aw)
    except HTTPException as e:
        return ("galat", getattr(e, "code", None) or (e.detail.get("code") if isinstance(e.detail, dict) else None), e.status_code, str(e.detail)[:120])


async def main():
    pool = await EA.get_pool()
    async with pool.acquire() as c:
        uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', TID)
        kas = await c.fetchrow("""SELECT ba.id, ba.coa_id, ba.account_name,
                   COALESCE((SELECT SUM(jl.debit) - SUM(jl.credit) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                             WHERE jl.account_id = ba.coa_id AND je.status = 'POSTED' AND je.tenant_id = ba.tenant_id), 0) AS saldo
                FROM bank_accounts ba WHERE ba.tenant_id = $1 AND ba.is_active AND ba.account_type IN ('cash', 'petty_cash') AND ba.coa_id IS NOT NULL
                ORDER BY 4 DESC LIMIT 1""", TID)
        emp = await c.fetchval("SELECT id FROM employees WHERE tenant_id = $1 AND is_active ORDER BY created_at LIMIT 1", TID)
        run = await c.fetchval("SELECT id FROM payroll_runs WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT 1", TID)
        beban = await c.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_type = 'EXPENSE' AND NOT is_header ORDER BY account_code LIMIT 1", TID)
        hari = await c.fetchval("SELECT tanggal_bisnis($1)", TID)
        saldo = Decimal(str(kas["saldo"]))
        besar, kecil = saldo + Decimal(1000), Decimal(1)
        print(f"[{MODE}] kas={kas['account_name']} saldo={saldo} emp={bool(emp)} run={bool(run)} beban={bool(beban)}", flush=True)
        cek("prasyarat: kas bersaldo > 1, karyawan, run gaji, akun beban", saldo > 1 and emp and run and beban)

    async def jurnal():
        async with pool.acquire() as c:
            return await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", TID)

    async def kasus(nama, buat, harap_tolak):
        n0 = await jurnal()
        h = await coba(buat())
        n1 = await jurnal()
        if harap_tolak:
            cek(f"{nama}: > saldo kas DITOLAK KAS_MINUS, nol jurnal", h[0] == "galat" and h[1] == "KAS_MINUS" and h[2] == 400 and n1 == n0, f"{h[:3]} jurnal +{n1 - n0}")
        else:
            cek(f"{nama}: lolos, jurnal tertulis", h[0] == "ok" and n1 > n0, f"{h[0]} {h[1:] if h[0] != 'ok' else ''} jurnal +{n1 - n0}")

    tolak = MODE == "cabang"
    G = EA.GrantAdvanceRequest
    # (1) kasbon
    if tolak:
        pv = await EA.preview_grant_advance(req(uid), G(employee_id=emp, principal=besar, granted_date=hari, source_account_id=kas["coa_id"]))
        kode = [b["code"] for b in (pv.get("data", pv).get("blocks") or [])]
        cek("kasbon pratinjau: blok KAS_MINUS (nol tulis)", "KAS_MINUS" in kode, str(kode))
    await kasus("kasbon", lambda: EA.grant_advance(req(uid), G(employee_id=emp, principal=besar, granted_date=hari, source_account_id=kas["coa_id"])), tolak)
    if tolak:
        await kasus("kasbon kecil", lambda: EA.grant_advance(req(uid), G(employee_id=emp, principal=kecil, granted_date=hari, source_account_id=kas["coa_id"])), False)

    # (2) pembayaran gaji tunai (benih draf)
    async def bayar(nilai):
        async with pool.acquire() as c:
            pid = await c.fetchval("""INSERT INTO payroll_payments (tenant_id, payroll_id, payment_type, payment_date, amount, status,
                                          transfer_amount, cash_amount, cash_account_id) VALUES ($1,$2,'salary',$3,$4,'draft',0,$4,$5) RETURNING id""",
                                   TID, run, hari, nilai, kas["id"])
        return await PP.post_payment(req(uid), pid)
    await kasus("gaji tunai", lambda: bayar(besar), tolak)
    if tolak:
        await kasus("gaji tunai kecil", lambda: bayar(kecil), False)

    # (3) aktiva tetap (benih draf)
    async def aktiva(nilai):
        async with pool.acquire() as c:
            aid = await c.fetchval("""INSERT INTO fixed_assets (tenant_id, asset_number, name, purchase_date, purchase_price, useful_life_months,
                                          depreciation_start_date, current_value) VALUES ($1,$2,'UJI K2 aktiva',$3,$4,12,$3,$5) RETURNING id""",
                                   TID, f"UJI-K2-{uuid.uuid4().hex[:6]}", hari, nilai, int(nilai))
        return await FA.activate_fixed_asset(req(uid), aid, ActivateAssetRequest(payment_account_id=kas["coa_id"]))
    await kasus("aktiva tetap", lambda: aktiva(besar), tolak)
    if tolak:
        await kasus("aktiva tetap kecil", lambda: aktiva(kecil), False)

    # (6) jurnal MANUAL buat-posting
    def manual(nilai):
        return J.create_journal(req(uid), CreateJournalRequest(entry_date=hari, description="UJI K2 manual", lines=[
            {"account_id": str(beban), "debit": nilai}, {"account_id": str(kas["coa_id"]), "credit": nilai}]))
    await kasus("jurnal manual", lambda: manual(besar), tolak)
    if tolak:
        await kasus("jurnal manual kecil", lambda: manual(kecil), False)

    print(f"HASIL[{MODE}]: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
