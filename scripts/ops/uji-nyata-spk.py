"""UJI NYATA No. SPK internal (12 Okt 2026) di KAOS, SEMUA ROLLBACK; kaos disetel 'manual_only' DI DALAM transaksi (meniru
grapgrap V417). Handler/inti ASLI: terbitkan() saat konfirmasi (no-op), POST /sales-orders/{id}/kode-order (+preview),
Terima DP dengan terbitkan_kode_order (paritas jurnal DP), data cetak proforma tanpa kode. Kontrol merah: kode master."""
import asyncio
import json
import os
import sys
import uuid
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request

T = "kaos-biru-konveksi"
USER = uuid.UUID("0bccdb25-fdf0-4e99-9024-b9a20846f76c")
SO_A = uuid.UUID("dbc66cbe-13d8-4f8c-8128-aa934bf27abe")   # SO-2609-0331 confirmed tanpa kode
SO_B = uuid.UUID("505b2f20-80b1-4173-8687-0748aebfbf41")   # SO-2609-0330 confirmed tanpa kode (Terima DP)
SO_DRAF = uuid.UUID("c14cf875-f55e-40ff-a0f6-c7f988a018e2")
PF_BERKODE = uuid.UUID("7540d66f-ae5c-4671-885a-f73f216ece57")   # PRO-2610-0004, SO ber-kode 002-10-26
BANK = "56842900-0ae4-4c25-8ad5-d0726a36e21b"
COA_BANK = "15105bf6-183e-4f93-abcc-5689f22fa394"
HASIL = []


def cek(n, ok, info=""):
    HASIL.append((n, bool(ok))); print(("PASS " if ok else "FAIL ") + n + (f" | {info}" if info else ""), flush=True)


class _Acq:
    def __init__(self, c): self.c = c
    async def __aenter__(self): return self.c
    async def __aexit__(self, *a): return False


class PoolSatu:
    def __init__(self, c): self.c = c
    def acquire(self): return _Acq(self.c)


class _Batal(Exception):
    pass


async def sp(conn, fn):
    out = {}
    try:
        async with conn.transaction():
            out["v"] = await fn()
            raise _Batal()
    except _Batal:
        pass
    return out["v"]


async def galat(coro):
    try:
        return {"ok": await coro}
    except HTTPException as e:
        return {"HTTP": e.status_code, "detail": e.detail}


def req():
    r = Request({"type": "http", "method": "POST", "headers": [], "path": "/", "query_string": b"", "state": {}})
    r.state.user = {"tenant_id": T, "user_id": str(USER), "role": "OWNER"}
    return r


async def jumlah(c):
    q = {"je": "SELECT count(*) FROM journal_entries WHERE tenant_id=$1",
         "audit": "SELECT count(*) FROM audit_logs WHERE tenant_id=$1",
         "kode": "SELECT md5(string_agg(id::text||coalesce(order_code,''), ',' ORDER BY id)) FROM sales_orders WHERE tenant_id=$1",
         "pengh": "SELECT md5(string_agg(period_key||last_seq::text, ',' ORDER BY period_key)) FROM order_code_counters WHERE tenant_id=$1",
         "setelan": "SELECT trigger FROM order_code_settings WHERE tenant_id=$1",
         "dep": "SELECT count(*) FROM customer_deposits WHERE tenant_id=$1"}
    return {k: await c.fetchval(v, T) for k, v in q.items()}


async def main():
    from app.services import kode_order as KO
    import app.routers.kode_order as KR
    import app.routers.customer_deposits as CD
    import app.routers.proformas as PF
    from app.schemas.customer_deposits import CreateCustomerDepositRequest
    from app.services import policy_engine_client as PEC
    pe = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    PEC.init_policy_engine(pe)
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tr = c.transaction(); await tr.start()
    pool = PoolSatu(c)

    async def gp(): return pool
    KR.get_db_pool = gp; CD.get_pool = gp; PF.get_pool = gp
    sebelum = await jumlah(c)
    try:
        await c.execute("UPDATE order_code_settings SET trigger='manual_only' WHERE tenant_id=$1", T)
        so_a = await c.fetchrow("SELECT order_number, customer_id, customer_name FROM sales_orders WHERE id=$1", SO_A)

        # 1) konfirmasi pada tenant manual_only: terbitkan() otomatis = no-op
        oto = await KO.terbitkan(c, T, SO_A, date(2026, 10, 12), "so_confirmed", USER, so_a["order_number"])
        cek("1 manual_only: terbit otomatis saat konfirmasi = no-op (kode tetap kosong)",
            oto is None and await c.fetchval("SELECT order_code FROM sales_orders WHERE id=$1", SO_A) is None)

        # 10) data cetak proforma: tanpa kode order
        pdf = await PF.muat_pdf_proforma_id(c, {"tenant_id": T, "user_id": USER}, PF_BERKODE)
        dd = pdf["proforma_data"]
        cek("10 data cetak proforma (SO ber-kode 002-10-26) TANPA kode order; nomor SO tetap",
            "order_code_cetak" not in dd and "order_code_pendek" not in dd and "002-10-26" not in json.dumps(dd, default=str)
            and dd.get("sales_order_number"), str({k: dd.get(k) for k in ("sales_order_number", "order_code_cetak")}))
        try:
            # 2) pratinjau Terbitkan SPK: nomor calon, nol tulis
            pv = (await KR.pratinjau_terbitkan_spk(req(), str(SO_A)))["data"]
            cek("2 pratinjau: can_proceed, nomor calon, issued, TAK tersimpan",
                pv["can_proceed"] and pv["order_code"] and pv["preview"] is True
                and await c.fetchval("SELECT order_code FROM sales_orders WHERE id=$1", SO_A) is None, str(pv))
            a0 = await c.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id=$1", T)
            ev0 = await c.fetchval("SELECT count(*) FROM order_code_events WHERE tenant_id=$1", T)

            # 3) Terbitkan SPK
            r1 = (await KR.terbitkan_spk(req(), str(SO_A)))["data"]
            kode_a = await c.fetchval("SELECT order_code FROM sales_orders WHERE id=$1", SO_A)
            cek("3 terbit: issued true, kode = pratinjau, tersimpan, event + riwayat (+1/+1)",
                r1["issued"] is True and r1["order_code"] == pv["order_code"] == kode_a
                and await c.fetchval("SELECT count(*) FROM order_code_events WHERE tenant_id=$1", T) == ev0 + 1
                and await c.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id=$1", T) == a0 + 1, str(r1))
            # 4) idempoten
            r2 = (await KR.terbitkan_spk(req(), str(SO_A)))["data"]
            cek("4 ulang: idempoten issued false, kode sama, nol event baru",
                r2["issued"] is False and r2["order_code"] == kode_a
                and await c.fetchval("SELECT count(*) FROM order_code_events WHERE tenant_id=$1", T) == ev0 + 1)
            # 5) draf -> 409
            r3 = await galat(KR.terbitkan_spk(req(), str(SO_DRAF)))
            cek("5 SO draf: 409 SPK_STATUS_TIDAK_SAH, tanpa kode", r3.get("HTTP") == 409 and r3["detail"]["code"] == "SPK_STATUS_TIDAK_SAH"
                and await c.fetchval("SELECT order_code FROM sales_orders WHERE id=$1", SO_DRAF) is None, str(r3)[:160])
            # 6) kode manual tak ditimpa jalur otomatis
            await c.execute("UPDATE sales_orders SET order_code='MANUAL-UJI', order_code_source='manual' WHERE id=$1", SO_DRAF)
            await c.execute("UPDATE order_code_settings SET trigger='so_confirmed' WHERE tenant_id=$1", T)
            oto2 = await KO.terbitkan(c, T, SO_DRAF, date(2026, 10, 12), "so_confirmed", USER, "x")
            cek("6 kode manual TIDAK ditimpa terbit otomatis", oto2 is None
                and await c.fetchval("SELECT order_code FROM sales_orders WHERE id=$1", SO_DRAF) == "MANUAL-UJI")
            await c.execute("UPDATE order_code_settings SET trigger='manual_only' WHERE tenant_id=$1", T)

            # 7) Terima DP + terbitkan_kode_order: satu transaksi; paritas jurnal DP dengan tanpa-opsi
            so_b = await c.fetchrow("SELECT customer_id, customer_name FROM sales_orders WHERE id=$1", SO_B)

            async def terima(opsi):
                b = CreateCustomerDepositRequest(customer_id=str(so_b["customer_id"]), customer_name=so_b["customer_name"], amount=50000,
                                                 deposit_date=date(2026, 10, 12), payment_method="transfer", account_id=COA_BANK,
                                                 bank_account_id=BANK, auto_post=True, sales_order_id=str(SO_B),
                                                 notes="TES E2E dp spk", idempotency_key=f"tes-e2e-spk-{uuid.uuid4()}",
                                                 **({"terbitkan_kode_order": True} if opsi else {}))
                r = await CD.create_customer_deposit(req(), b)
                d = r if isinstance(r, dict) else r.model_dump()
                jl = [(x["account_code"], x["debit"], x["credit"]) for x in await c.fetch(
                    """SELECT coa.account_code, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id=jl.account_id
                       WHERE jl.journal_id=$1 ORDER BY jl.line_number""", uuid.UUID(str(d["data"]["journal_id"])))]
                return d["data"], jl, await c.fetchval("SELECT order_code FROM sales_orders WHERE id=$1", SO_B)
            dt, jt, kt = await sp(c, lambda: terima(False))
            do, jo, ko = await sp(c, lambda: terima(True))
            cek("7 Terima DP + opsi: DP posted, SPK terbit (order_code_issued true), kode tersimpan",
                do.get("status") == "posted" and do.get("order_code_issued") is True and ko and do.get("order_code") == ko, str(do)[:220])
            cek("8 tanpa opsi: SPK TIDAK terbit (manual_only)", kt is None and "order_code_issued" not in dt)
            cek("9 paritas jurnal DP: baris identik dengan/tanpa opsi", jt == jo, f"{jt} | {jo}")
        except AttributeError as e:  # kontrol merah: kode master tanpa rute/opsi SPK
            cek("2-9 Terbitkan SPK / opsi Terima DP tersedia", False, repr(e)[:160])
    finally:
        await tr.rollback(); await pe.close()
    cek("AKHIR nol jejak (je, audit, kode SO, penghitung, setelan, uang muka)", await jumlah(c) == sebelum)
    await c.close()
    g = [n for n, ok in HASIL if not ok]
    print(f"RINGKAS {len(HASIL)-len(g)}/{len(HASIL)}", "GAGAL: " + "; ".join(g) if g else "SEMUA LULUS")

asyncio.run(main())
