"""Harness NYATA B1b-2 (kaos SAJA, penanda 'TES E2E'): hapus DRAF massal Faktur Pembelian / Pembayaran Keluar / Uang Muka / Nota Debit lewat
handler ASLI terhadap DB produksi. SATU transaksi luar pada satu koneksi SELALU di-ROLLBACK (pola AS-GP2/3/4: get_pool tertambal, transaksi
dalam = savepoint; catatan: readonly pratinjau tak ditegakkan DB di dalam savepoint -- dikunci tes unit). Sesudah ROLLBACK diukur di koneksi
LAIN: nol sisa dokumen TES E2E.

Per modul: buat 2 draf (handler create ASLI) -> pratinjau massal [d1, dokumen terposting/void yang ada, id hantu] (nol tulis) -> hapus massal
[d1, terposting] kunci K (d1 hilang, terposting tetap, kode = kode pratinjau) -> ulang kunci K (replay) -> d2 lewat DELETE TUNGGAL (pembanding)
-> tagihan: DOCUMENT_DELETED ber-userId (V230). Nol jurnal / transaksi bank baru. Semua kanal keluar (HTTP/SMTP) di-stub: WAJIB 0 panggilan.
Argumen: <keluaran.json>."""
import asyncio
import json
import os
import sys
import uuid
from datetime import timedelta
from decimal import Decimal

import asyncpg

sys.path.insert(0, os.getcwd())
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

from app.routers import bill_payments as RBP  # noqa: E402
from app.routers import bills as RB  # noqa: E402
from app.routers import bulk_hapus_beli as BH  # noqa: E402
from app.routers import vendor_credits as RVC  # noqa: E402
from app.routers import vendor_deposits as RVD  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

T = "kaos-biru-konveksi"
VENDOR_TAGIHAN, BARANG = "73a2b4bd-72d3-46cb-8adb-79f14f9e28e7", "862be8e3-b35a-48e3-b8c3-c200f75feba9"  # == AS-GP2
TAGIHAN_SISA = "BILL-2609-0005"   # tagihan terposting bersisa (alokasi draf pembayaran 1.000)
TAGIHAN_ND = "BILL-2609-0003"     # vendor ND (== uji-nyata-nota-debit-idem)
TERPOSTING = {"bills": ("bills", "status_v2 = 'posted'"), "bill-payments": ("bill_payments_v2", "status = 'posted'"),
              "vendor-deposits": ("vendor_deposits", "status <> 'draft'"), "vendor-credits": ("vendor_credits", "status <> 'draft'")}
PENANDA = "TES E2E B1b-2"


def req(uid, kunci=None):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}
    return r


def ep(modul, path):
    return next(r.endpoint for r in BH.ROUTERS[modul].routes if r.path == path)


def dt(x):
    return x.model_dump(mode="json") if hasattr(x, "model_dump") else x


async def main(keluar):
    from app.schemas.bill_payments import CreateBillPaymentRequest as CBP
    from app.schemas.bills import CreateBillRequestV2 as CB
    from app.schemas.vendor_credits import CreateVendorCreditRequest as CVC
    from app.schemas.vendor_deposits import VendorDepositCreate as CVD

    c = await asyncpg.connect(os.environ["URL"])
    assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T), "tenant tak ada -- harness menolak"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    hari = await tanggal_dokumen(c, T)
    bank = await c.fetchval("SELECT id FROM bank_accounts WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1", T)
    vend_um = await c.fetchval("SELECT id FROM vendors WHERE tenant_id = $1 ORDER BY name LIMIT 1", T)
    tsisa = await c.fetchrow("SELECT id, vendor_id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, TAGIHAN_SISA)
    tnd = await c.fetchrow("SELECT vendor_id, vendor_name FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, TAGIHAN_ND)

    class Kon:
        def __getattr__(s, a): return getattr(c, a)
        def transaction(s, **kw): return c.transaction()  # dalam transaksi luar = savepoint (readonly diabaikan, lihat docstring)

    class CM:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class Pool:
        def acquire(s): return CM()

    async def gp(): return Pool()
    for mod in (RB, RBP, RVD, RVC, BH):
        mod.get_pool = gp
    cek, catat = [], {}

    def harus(nama, kondisi, info=None):
        cek.append({"cek": nama, "ok": bool(kondisi), "info": info})
        print(("OK   " if kondisi else "GAGAL ") + nama + ("" if kondisi else f" -> {info}"), flush=True)

    async def ada(tabel, i):
        return bool(await c.fetchval(f"SELECT 1 FROM {tabel} WHERE id = $1 AND tenant_id = $2", uuid.UUID(str(i)), T))  # nosec B608

    async def hitung():
        return (await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", T),
                await c.fetchval("SELECT count(*) FROM bank_transactions WHERE tenant_id = $1", T))

    n = {"x": 0}

    async def draf(modul):
        n["x"] += 1
        tag = f"{PENANDA} {modul} {n['x']}"
        if modul == "bills":
            nomor = f"TES-E2E-B1B2-{n['x']}-{uuid.uuid4().hex[:6]}"
            await RB.create_bill_v2(req(uid), CB(vendor_id=VENDOR_TAGIHAN, invoice_number=nomor, due_date=hari + timedelta(days=30), notes=tag,
                                                 items=[{"product_id": BARANG, "product_name": "TES E2E barang", "qty": 1, "price": 10000}]))
            return await c.fetchval("SELECT id FROM bills WHERE tenant_id = $1 AND invoice_number = $2", T, nomor)
        if modul == "bill-payments":
            await RBP.create_bill_payment(req(uid), CBP(vendor_id=str(tsisa["vendor_id"]), payment_date=hari, bank_account_id=str(bank),
                                                       total_amount=1000, allocations=[{"bill_id": str(tsisa["id"]), "amount_applied": 1000}],
                                                       save_as_draft=True, notes=tag))
            return await c.fetchval("SELECT id FROM bill_payments_v2 WHERE tenant_id = $1 AND notes = $2", T, tag)
        if modul == "vendor-deposits":
            r = dt(await RVD.create_vendor_deposit(req(uid), CVD(deposit_date=hari, vendor_id=vend_um, amount=Decimal("1000"), bank_account_id=bank,
                                                                reference=PENANDA, notes=tag)))
            return uuid.UUID(str(r.get("id") or r["data"]["id"]))
        r = dt(await RVC.create_vendor_credit(req(uid), CVC(vendor_id=str(tnd["vendor_id"]), vendor_name=tnd["vendor_name"], vendor_credit_date=hari,
                                                            reason="other", ref_no=PENANDA, notes=tag,
                                                            items=[{"description": "TES E2E", "quantity": 1, "unit_price": 1000}])))
        return uuid.UUID(str(r["data"]["id"]))

    async def tunggal(modul, i):
        if modul == "bills":
            return await RB.delete_bill(req(uid), uuid.UUID(str(i)))
        if modul == "bill-payments":
            return await RBP.delete_bill_payment(req(uid), str(i))
        if modul == "vendor-deposits":
            return await RVD.delete_vendor_deposit(req(uid), uuid.UUID(str(i)))
        return await RVC.delete_vendor_credit(req(uid), uuid.UUID(str(i)))

    dibuat = {}
    luar = c.transaction()
    await luar.start()
    try:
        awal = await hitung()
        for modul, (tabel, syarat) in TERPOSTING.items():
            d1, d2 = await draf(modul), await draf(modul)
            dibuat[modul] = [str(d1), str(d2)]
            pos = await c.fetchval(f"SELECT id FROM {tabel} WHERE tenant_id = $1 AND {syarat} LIMIT 1", T)  # nosec B608 (peta tetap)
            hantu = uuid.uuid4()
            p = (await ep(modul, "/bulk/delete/preview")(req(uid), BH.BulkHapusRequest(ids=[str(d1), str(pos), str(hantu)])))["data"]
            st = [(x["ok"], [b["code"] for b in x["blocks"]][:1]) for x in p["items"]]
            harus(f"{modul}: pratinjau [draf, terposting, hantu]", st[0] == (True, []) and not st[1][0] and st[1][1] and not st[2][0] and st[2][1], st)
            harus(f"{modul}: pratinjau nol tulis", await ada(tabel, d1) and await ada(tabel, pos))
            kunci = f"b1b2-{modul}-{uuid.uuid4().hex[:8]}"
            w = (await ep(modul, "/bulk/delete")(req(uid, kunci), BH.BulkHapusRequest(ids=[str(d1), str(pos)])))["data"]
            hw = [(x["status"], x["code"]) for x in w["items"]]
            harus(f"{modul}: hapus massal [draf, terposting]", hw == [("ok", None), ("rejected", st[1][1][0])], (hw, st))
            harus(f"{modul}: draf hilang, terposting tetap", not await ada(tabel, d1) and await ada(tabel, pos))
            w2 = (await ep(modul, "/bulk/delete")(req(uid, kunci), BH.BulkHapusRequest(ids=[str(d1), str(pos)])))["data"]
            harus(f"{modul}: ulang kunci sama = replay", w2["items"][0]["replay"] is True and w2["items"][0]["result"] == w["items"][0]["result"],
                  w2["items"][0])
            sp = c.transaction()
            await sp.start()
            try:
                await tunggal(modul, d2)
                await sp.commit()
            except Exception as e:  # noqa: BLE001
                await sp.rollback()
                harus(f"{modul}: DELETE tunggal d2", False, repr(e)[:200])
            harus(f"{modul}: DELETE tunggal d2 menghapus (pembanding)", not await ada(tabel, d2))
            if modul == "bills":
                dl = await c.fetch("""SELECT entity_id, "userId" FROM audit_logs WHERE tenant_id = $1 AND "eventType" = 'DOCUMENT_DELETED'
                                      AND entity_type = 'bills' AND entity_id = ANY($2::uuid[])""", T, [d1, d2])
                harus("bills: DOCUMENT_DELETED d1 (massal) + d2 (tunggal) ber-userId (V230)",
                      sorted(str(r["entity_id"]) for r in dl) == sorted([str(d1), str(d2)]) and all(r["userId"] == str(uid) for r in dl),
                      [dict(r) for r in dl])
        au = await c.fetchval("""SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND "eventType" = 'BULK_DELETE' AND "userId" = $2
                                 AND metadata->>'modul' = ANY($3::text[]) AND "createdAt" >= now() - interval '1 hour'
                                 AND (metadata->'ids')::text LIKE ANY($4::text[])""", T, str(uid), list(TERPOSTING),
                              [f"%{v[0]}%" for v in dibuat.values()])
        harus("audit BULK_DELETE 2 per modul (tulis + replay)", au == 8, au)
        harus("nol jurnal + nol transaksi bank baru", await hitung() == awal, (awal, await hitung()))
    finally:
        await luar.rollback()
    sisa = {}
    for modul, (tabel, _) in TERPOSTING.items():
        sisa[modul] = await c.fetchval(f"SELECT count(*) FROM {tabel} WHERE tenant_id = $1 AND id = ANY($2::uuid[])", T,  # nosec B608
                                       [uuid.UUID(x) for x in dibuat.get(modul, [])])
    harus("ROLLBACK: nol sisa dokumen TES E2E di kaos", all(v == 0 for v in sisa.values()), sisa)
    harus("nol panggilan kanal keluar (surel/WA/HTTP)", KELUAR == [], KELUAR)
    await c.close()
    catat["dibuat"] = dibuat
    gagal = [x["cek"] for x in cek if not x["ok"]]
    json.dump({"cek": cek, "catat": catat, "gagal": gagal}, open(keluar, "w"), default=str, indent=1)
    print("HASIL", "LULUS" if not gagal else f"GAGAL {gagal}", f"({len(cek)} cek)")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
