"""BUG-046 uji DB di SALINAN (bukan prod): kode cabang fix/bug046-bayar-tunai atas klon milkydb, handler create_invoice ASLI.
T1 nyata: lunas (outstanding 0, RCV posted, cermin bank 1, R9 delta 0, jurnal bayar == pratinjau) · T2 galat disuntik di
buat_penerimaan -> 0 faktur/jurnal/RCV (+ kontrol merah) · T3 tanpa kunci -> 400 nol tulis; replay = respons sama nol tulis ·
T4 customer_id huruf besar lolos."""
import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, Response
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import sales_invoices as SI  # noqa: E402
from app.routers import receive_payments as RP  # noqa: E402
from app.schemas.sales_invoices import CreateInvoiceRequest  # noqa: E402

TID = "pt-kita-nyawit-bersama"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(ok)
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(uid, kunci):
    r = Request({"type": "http", "method": "POST", "path": "/api/sales-invoices",
                 "headers": [(b"x-idempotency-key", kunci.encode())] if kunci else []})
    r.state.user = {"tenant_id": TID, "user_id": str(uid), "username": "uji-salinan"}
    return r


TABEL = ["sales_invoices", "journal_entries", "receive_payments", "bank_transactions", "inventory_ledger"]


async def hitung(c):
    return {t: await c.fetchval(f"SELECT count(*) FROM {t} WHERE tenant_id = $1", TID) for t in TABEL}


def selisih(a, b):
    return {k: b[k] - a[k] for k in a if b[k] != a[k]}


async def saldo(c, coa, ba):
    led = await c.fetchval("""SELECT COALESCE(SUM(jl.debit - jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id
                              WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND jl.account_id = $2""", TID, coa)
    bt = await c.fetchval("SELECT COALESCE(SUM(amount), 0) FROM bank_transactions WHERE tenant_id = $1 AND bank_account_id = $2", TID, ba)
    return Decimal(str(led)), Decimal(str(bt))


async def main():
    pool = await SI.get_pool()
    async with pool.acquire() as c:
        uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', TID)
        cust = await c.fetchrow("SELECT id, nama FROM customers WHERE tenant_id = $1 ORDER BY (nama ILIKE '%umum%') DESC LIMIT 1", TID)
        st = await c.fetchrow("""SELECT ws.item_id, ws.warehouse_id, p.nama_produk, p.sales_price FROM warehouse_stock ws
                                 JOIN products p ON p.id = ws.item_id WHERE ws.tenant_id = $1 AND ws.quantity >= 5
                                 AND p.item_type = 'goods' ORDER BY ws.quantity DESC LIMIT 1""", TID)
        ba = await c.fetchrow("SELECT id, coa_id, account_name FROM bank_accounts WHERE tenant_id = $1 AND is_active AND account_type = 'bank' LIMIT 1", TID)
    print(f"data: pelanggan={cust['nama']} barang={st['nama_produk']} rekening={ba['account_name']}", flush=True)

    def badan(cid=None, bt=True):
        return {"customer_id": cid or str(cust["id"]), "customer_name": cust["nama"], "invoice_date": str(date.today()),
                "warehouse_id": str(st["warehouse_id"]),
                "items": [{"item_id": str(st["item_id"]), "description": st["nama_produk"], "quantity": 2,
                           "unit_price": float(st["sales_price"] or 25000)}],
                "auto_post": True, **({"bayar_tunai": {"bank_account_id": str(ba["id"]), "reference": "UJI-046"}} if bt else {})}

    async def buat(b, kunci):
        return await SI.create_invoice(req(uid, kunci), CreateInvoiceRequest(**b), Response())

    # ---- T1 nyata
    print("== T1 NYATA", flush=True)
    async with pool.acquire() as c:
        prev = await SI.preview_journal(req(uid, None), badan())
        a, s0 = await hitung(c), await saldo(c, ba["coa_id"], ba["id"])
    r = await buat(badan(), f"u046-{uuid.uuid4().hex}")
    d = r["data"]
    async with pool.acquire() as c:
        iid = uuid.UUID(d["id"])
        out = await c.fetchval("SELECT outstanding FROM compute_ar_outstanding($1) WHERE invoice_id = $2", TID, iid)
        rp = await c.fetchrow("SELECT status, journal_id, total_amount FROM receive_payments WHERE id = $1", uuid.UUID(d["pembayaran"]["id"]))
        n_bt = await c.fetchval("SELECT count(*) FROM bank_transactions WHERE journal_id = $1", rp["journal_id"])
        jl = await c.fetch("""SELECT coa.name, jl.debit, jl.credit FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id
                              WHERE jl.journal_id = $1 ORDER BY jl.debit DESC""", rp["journal_id"])
        s1, dlt = await saldo(c, ba["coa_id"], ba["id"]), selisih(a, await hitung(c))
    cek("T1 status faktur paid + pesan dibayar", d["status"] == "paid" and "dibayar" in r["message"], f"{d['status']} {r['message']}")
    cek("T1 outstanding 0 (compute_ar_outstanding)", out is None or Decimal(str(out)) == 0, str(out))
    cek("T1 RCV posted, jumlah == total faktur", rp["status"] == "posted" and Decimal(str(rp["total_amount"])) == Decimal(str(d["total_amount"])),
        f"{rp['status']} {rp['total_amount']} vs {d['total_amount']}")
    cek("T1 cermin bank_transactions tepat 1", n_bt == 1, str(n_bt))
    cek("T1 R9: delta ledger == delta bank_txn", (s1[0] - s0[0]) == (s1[1] - s0[1]) == Decimal(str(d["total_amount"])),
        f"ledger {s1[0]-s0[0]} bank_txn {s1[1]-s0[1]}")
    pv = [(x["account_name"], Decimal(str(x["debit"])), Decimal(str(x["credit"]))) for x in prev["journal_lines"] if x["event"] == "PAYMENT"]
    db = [(x["name"], Decimal(str(x["debit"])), Decimal(str(x["credit"]))) for x in jl]
    cek("T1 jurnal bayar DB == baris PAYMENT pratinjau", sorted(pv) == sorted(db) and not prev["warnings"], f"pv={pv} db={db} w={prev['warnings']}")
    cek("T1 delta: 1 faktur, 1 RCV, 1 bank_txn", dlt.get("sales_invoices") == 1 and dlt.get("receive_payments") == 1
        and dlt.get("bank_transactions") == 1, str(dlt))

    # ---- T2 galat disuntik di buat_penerimaan (SESUDAH ia menulis) -> seluruh transaksi batal
    print("== T2 ATOMIK", flush=True)
    asli = RP.buat_penerimaan

    async def gagal(conn, ctx, body):
        await asli(conn, ctx, body)
        raise HTTPException(status_code=409, detail="galat disuntik (uji)")
    RP.buat_penerimaan = gagal
    try:
        async with pool.acquire() as c:
            a = await hitung(c)
        try:
            await buat(badan(), f"u046-{uuid.uuid4().hex}")
            cek("T2 galat diteruskan", False, "lolos")
        except HTTPException as e:
            cek("T2 galat diteruskan 409", e.status_code == 409, str(e.status_code))
        async with pool.acquire() as c:
            cek("T2 0 faktur / 0 jurnal / 0 RCV / 0 bank_txn / 0 kartu stok", selisih(a, await hitung(c)) == {}, str(selisih(a, await hitung(c))))
            # KONTROL MERAH alat ukur: faktur auto_post TANPA bayar (tak memanggil buat_penerimaan) -> alat ukur WAJIB melihat 1 faktur
            a = await hitung(c)
        await buat(badan(bt=False), f"u046-{uuid.uuid4().hex}")
        async with pool.acquire() as c:
            dd = selisih(a, await hitung(c))
        cek("T2 KONTROL MERAH: faktur tanpa bayar tertulis -> alat ukur melihat +1 faktur, 0 RCV", dd.get("sales_invoices") == 1
            and "receive_payments" not in dd, str(dd))
    finally:
        RP.buat_penerimaan = asli

    # ---- T3 idempotensi
    print("== T3 IDEMPOTENSI", flush=True)
    async with pool.acquire() as c:
        a = await hitung(c)
    try:
        await buat(badan(), None)
        cek("T3 tanpa kunci ditolak", False, "lolos")
    except HTTPException as e:
        cek("T3 tanpa kunci -> 400 kalimat final", e.status_code == 400 and e.detail == "Penjualan tunai butuh kunci idempotensi.", str(e.detail))
    async with pool.acquire() as c:
        cek("T3 tanpa kunci: nol tulis", selisih(a, await hitung(c)) == {}, "")
    k = f"u046-{uuid.uuid4().hex}"
    r1 = await buat(badan(), k)
    async with pool.acquire() as c:
        a = await hitung(c)
    r2 = await buat(badan(), k)
    j2 = r2 if isinstance(r2, dict) else __import__("json").loads(r2.body)
    async with pool.acquire() as c:
        cek("T3 replay kunci sama: id sama, nol tulis", j2["data"]["id"] == r1["data"]["id"] and selisih(a, await hitung(c)) == {},
            f"{j2['data']['id']} {selisih(a, await hitung(c))}")

    # ---- T4 UUID huruf besar
    print("== T4 PELANGGAN KANONIK", flush=True)
    r = await buat(badan(cid=str(cust["id"]).upper()), f"u046-{uuid.uuid4().hex}")
    cek("T4 customer_id HURUF BESAR -> lunas", r["data"]["status"] == "paid", r["data"]["status"])

    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
