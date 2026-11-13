"""BUG-213/214 uji DB di SALINAN (bukan prod): handler ASLI complete_order + report_output + penguras tutup bulan atas WO klon, tenant kaos + nyawit + busana (klon). Mutasi HANYA di klon.
A WO Dirilis (status diset di klon) lapor hasil -> 400 WO_BELUM_DIMULAI 'Mulai proses dulu', nol completion baru
B WO berjalan Selesai -> status completed + variance_amount, NOL journal_entries baru ber-source_id WO (tak ada JV-VAR); tombol Selesai tak menyentuh WIP
C penguras tutup bulan (PW.sisa_wip_selesai) sesudahnya: sisa WIP WO selesai == saldo akun WIP jurnal efektif WO itu (hitungan SQL independen)"""
import asyncio
import sys
from datetime import date

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import production as PR  # noqa: E402
from app.services import produksi_wip_sisa as PW  # noqa: E402
from app.services.galat_berkode import GalatBerkode  # noqa: E402

TENANTS = ("kaos-biru-konveksi", "pt-kita-nyawit-bersama", "pt-busana-indah-semesta")
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


class Body:
    good_quantity, scrap_quantity = 1, 0


async def main():
    pool = await PR.get_pool()
    async with pool.acquire() as c:
        for tid in TENANTS:
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            wo = await c.fetchrow("/* tenant_id */ SELECT id, order_number FROM production_orders WHERE tenant_id = $1 AND status = 'in_progress' ORDER BY created_at LIMIT 1", tid)
            if uid is None or wo is None:
                print(f"CATATAN {tid}: tak ada pemilik atau WO berjalan di klon; dilewati", flush=True)
                continue
            rq, oid = req(tid, uid), wo["id"]
            n_comp = lambda: c.fetchval("/* tenant_id */ SELECT count(*) FROM production_completions WHERE production_order_id = $1", oid)  # noqa: E731
            n_je = lambda: c.fetchval("/* tenant_id */ SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_id = $2", tid, oid)  # noqa: E731
            # A
            await c.execute("UPDATE production_orders SET status = 'released' WHERE tenant_id = $1 AND id = $2", tid, oid)
            sebelum = await n_comp()
            try:
                await PR.report_output(rq, oid, Body())
                cek(f"A {tid} {wo['order_number']} Dirilis: lapor hasil DITOLAK", False, "lolos tanpa galat")
            except GalatBerkode as e:
                cek(f"A {tid} {wo['order_number']} Dirilis: lapor hasil -> 400 WO_BELUM_DIMULAI 'Mulai proses dulu', nol completion baru",
                    (e.status_code, e.code) == (400, "WO_BELUM_DIMULAI") and "Mulai proses dulu" in e.detail and await n_comp() == sebelum, f"{e.code} {e.detail}")
            except Exception as e:  # noqa: BLE001
                cek(f"A {tid} {wo['order_number']} lapor hasil Dirilis", False, f"{type(e).__name__}: {getattr(e, 'detail', e)}")
            # B
            await c.execute("UPDATE production_orders SET status = 'in_progress' WHERE tenant_id = $1 AND id = $2", tid, oid)
            je0 = await n_je()
            try:
                h = await PR.complete_order(rq, oid)
                st = await c.fetchval("/* tenant_id */ SELECT status FROM production_orders WHERE tenant_id = $1 AND id = $2", tid, oid)
                je1 = await n_je()
                cek(f"B {tid} {wo['order_number']} Selesai: status completed, variance_amount ada, NOL jurnal baru ber-source_id WO ({je0} -> {je1})",
                    st == "completed" and "variance_amount" in h["data"] and je1 == je0, f"{h}")
            except Exception as e:  # noqa: BLE001
                cek(f"B {tid} {wo['order_number']} Selesai tanpa galat", False, f"{type(e).__name__}: {getattr(e, 'detail', e)}")
                continue
            # C
            wip = await PR.resolve_account_id_by_role(c, tid, PR.AccountRole.WIP_GENERIC)
            akhir = date.today()
            sisa = {x["order_id"]: x["sisa"] for x in await PW.sisa_wip_selesai(c, tid, wip, akhir)}
            saldo = await c.fetchval("/* tenant_id */ SELECT COALESCE(SUM(jl.debit - jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id WHERE je.tenant_id = $1 AND jl.account_id = $2 AND je.source_id = $3 AND is_effective_journal(je.id) AND je.journal_date <= $4", tid, wip, oid, akhir)
            ada = sisa.get(str(oid))
            cek(f"C {tid} {wo['order_number']} penguras tutup bulan: sisa {ada} == saldo WIP jurnal efektif WO ({saldo})",
                (ada is None and abs(saldo) < 0.005) or (ada is not None and abs(float(ada) - float(saldo)) < 0.005), f"sisa={ada} saldo={saldo}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
