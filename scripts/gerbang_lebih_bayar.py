"""GERBANG lebih bayar AR -> DP otomatis. Satu alat, label lama|baru, data sama, ROLLBACK, cacah di-assert.

lama:  KONTROL tanpa sisa 200 efek sama · MERAH lebih bayar 500 NOL tulis
baru:  KONTROL tanpa sisa 200 · lebih bayar 200 dgn efek akuntansi:
         jurnal Dr bank = total · Cr RECEIVABLE = teralokasi · Cr kewajiban DP = sisa
         customer_deposits: 1 baris, amount = sisa, customer_id = teks UUID huruf kecil pembayar
         outstanding faktur turun = teralokasi · receive_payments.unapplied_amount = sisa
       DP otomatis -> diterapkan ke faktur pelanggan SAMA -> 200 (helper pihak menerima DP buatan sendiri)
       SABOTASE normalisasi dicabut (kembali ke UUID mentah) -> lebih bayar 500 lagi
"""
import asyncio
import importlib.util
import os
import sys
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

PATH, LABEL = sys.argv[1], sys.argv[2]
T = "kaos-biru-konveksi"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:300]))


def baca_id(r):
    for k in (r, getattr(r, "data", None), (r.get("data") if isinstance(r, dict) else None)):
        if isinstance(k, dict) and k.get("id"):
            return k["id"]
        if k is not None and getattr(k, "id", None):
            return k.id
    raise RuntimeError(f"ALAT: id tak terbaca dari {type(r).__name__}")


def req(uid):
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                    "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})


def muat(nama, path):
    spec = importlib.util.spec_from_file_location(nama, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[nama] = m
    spec.loader.exec_module(m)
    return m


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
    rp = muat("app.routers.receive_payments_uji", PATH)
    from app.routers import customer_deposits as cd
    from app.schemas.receive_payments import CreateReceivePaymentRequest as RC
    from app.schemas.customer_deposits import ApplyCustomerDepositRequest as AR_, ApplyDepositItem as AI
    RA = RC.model_fields["allocations"].annotation.__args__[0]

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)
    ba = await conn.fetchrow("SELECT id, coa_id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", T)

    async def cacah():
        return (await conn.fetchval("SELECT count(*) FROM journal_entries"),
                await conn.fetchval("SELECT count(*) FROM receive_payments"),
                await conn.fetchval("SELECT count(*) FROM customer_deposits"),
                await conn.fetchval("SELECT count(*) FROM receive_payment_allocations"))
    awal = await cacah()

    luar = conn.transaction(); await luar.start()
    try:
        inv = await conn.fetchrow(
            """SELECT a.invoice_id, s.customer_id FROM compute_ar_outstanding($1) a JOIN sales_invoices s ON s.id=a.invoice_id
               WHERE a.outstanding >= 5000 ORDER BY a.invoice_id LIMIT 1""", T)
        inv2 = await conn.fetchval(
            """SELECT a.invoice_id FROM compute_ar_outstanding($1) a JOIN sales_invoices s ON s.id=a.invoice_id
               WHERE s.customer_id=$2 AND a.invoice_id <> $3 AND a.outstanding >= 1000 ORDER BY a.invoice_id LIMIT 1""",
            T, inv["customer_id"], inv["invoice_id"])

        async def terima(total, alokasi, terapkan_dp=False):
            sp = conn.transaction(); await sp.start()
            c0 = await cacah()
            o0 = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, inv["invoice_id"])
            out = dict(kode=200, det="OK")
            try:
                body = RC(customer_id=str(inv["customer_id"]), payment_date=date.today(), payment_method="bank_transfer",
                          bank_account_id=str(ba["id"]), total_amount=total,
                          allocations=[RA(invoice_id=str(inv["invoice_id"]), amount_applied=alokasi)], save_as_draft=False)
                pid = baca_id(await rp.create_receive_payment(req(uid), body))
                r = await conn.fetchrow("SELECT journal_id, unapplied_amount, created_deposit_id FROM receive_payments WHERE id=$1::uuid", pid)
                out["unapplied"] = r["unapplied_amount"]
                jl = await conn.fetch(
                    """SELECT jl.account_id, coa.account_type, coa.account_code, jl.debit, jl.credit FROM journal_lines jl
                       JOIN chart_of_accounts coa ON coa.id=jl.account_id WHERE jl.journal_id=$1""", r["journal_id"])
                out["dr_bank"] = sum(x["debit"] for x in jl if x["account_id"] == ba["coa_id"])
                out["cr_ar"] = sum(x["credit"] for x in jl if x["account_type"] == "RECEIVABLE")
                out["cr_lain"] = [(x["account_code"], x["credit"]) for x in jl if x["credit"] > 0 and x["account_type"] != "RECEIVABLE"]
                if r["created_deposit_id"]:
                    d = await conn.fetchrow("SELECT amount, customer_id, status, journal_id FROM customer_deposits WHERE id=$1", r["created_deposit_id"])
                    out["dp"] = dict(d)
                    if terapkan_dp and inv2:
                        try:
                            await cd.apply_customer_deposit(req(uid), r["created_deposit_id"],
                                                            AR_(applications=[AI(invoice_id=str(inv2), amount=1000)]))
                            out["terapkan_dp"] = "200"
                        except Exception as e:  # noqa: BLE001
                            out["terapkan_dp"] = f"{getattr(e,'status_code',type(e).__name__)} {getattr(e,'detail',e)}"
                o1 = await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ar_outstanding($1) WHERE invoice_id=$2", T, inv["invoice_id"])
                out["d_outstanding"] = o1 - o0
            except Exception as e:  # noqa: BLE001
                out.update(kode=getattr(e, "status_code", type(e).__name__), det=getattr(e, "detail", str(e)))
            out["dc"] = [y - x for x, y in zip(c0, await cacah())]
            await sp.rollback()
            return out

        r = await terima(1000, 1000)
        catat("KONTROL", "tanpa sisa -> 200; Dr bank 1000; Cr AR 1000; outstanding -1000; tanpa DP",
              r["kode"] == 200 and r.get("dr_bank") == 1000 and r.get("cr_ar") == 1000 and r.get("d_outstanding") == -1000
              and "dp" not in r, r)

        r = await terima(3000, 1000, terapkan_dp=(LABEL == "baru"))
        if LABEL == "lama":
            catat("MERAH lama", "lebih bayar -> 500", r["kode"] == 500, r)
            catat("MERAH lama", "kegagalan NOL tulis (jurnal, penerimaan, DP, alokasi)", r["dc"] == [0, 0, 0, 0], r["dc"])
        else:
            catat("HIJAU baru", "lebih bayar -> 200", r["kode"] == 200, r)
            catat("HIJAU baru", "jurnal: Dr bank 3000 · Cr AR 1000 · Cr kewajiban DP 2000",
                  r.get("dr_bank") == 3000 and r.get("cr_ar") == 1000 and sum(c for _, c in r.get("cr_lain", [])) == 2000, r)
            dp = r.get("dp") or {}
            catat("HIJAU baru", "customer_deposits: amount 2000, pelanggan = pembayar (teks UUID huruf kecil), posted",
                  dp.get("amount") == 2000 and dp.get("customer_id") == str(inv["customer_id"]).lower() and dp.get("status") == "posted", dp)
            catat("HIJAU baru", "outstanding faktur -1000 (= teralokasi) · unapplied_amount = 2000",
                  r.get("d_outstanding") == -1000 and r.get("unapplied") == 2000, r)
            catat("HIJAU baru", "DP otomatis diterapkan ke faktur pelanggan SAMA -> 200", r.get("terapkan_dp") == "200", r.get("terapkan_dp"))
            src = open(PATH, encoding="utf-8").read()
            sab = src.replace('str(normalisasi_pihak(payment["customer_id"], "Pelanggan pembayaran"))', 'payment["customer_id"]')
            if sab == src:
                catat("SABOTASE", "jangkar normalisasi ditemukan", False, "tak ditemukan")
            else:
                open("/tmp/rp_sabotase.py", "w", encoding="utf-8").write(sab)
                rp_asli = rp
                rp = muat("app.routers.receive_payments_sabotase", "/tmp/rp_sabotase.py")
                r = await terima(3000, 1000)
                rp = rp_asli
                catat("SABOTASE", "normalisasi dicabut -> lebih bayar 500 lagi", r["kode"] == 500, r)
    finally:
        await luar.rollback()
    catat("KONTROL", "nol menetap (jurnal, penerimaan, DP, alokasi)", awal == await cacah(), awal)
    await conn.close()
    harap = 4 if LABEL == "lama" else 8
    for s, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s:10} {u}  | {k}")
    g = sum(1 for h in hasil if not h[2])
    print(f"\n[{LABEL}] gagal={g} total={len(hasil)} harap={harap} -> {'LENGKAP' if len(hasil) == harap else 'TAK SAH'}")
    sys.exit(0 if g == 0 and len(hasil) == harap else 1)


asyncio.run(main())
