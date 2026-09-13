"""GERBANG unit 1 — bill_payments: pihak VENDOR sama + TENANT di SELECT bills.

Dijalankan terhadap satu modul (argumen: path bill_payments.py dan label 'lama'|'baru').
ROLLBACK, hasil di memori, cacah di-assert per label, nol menetap di KEDUA tenant.

Sisi (lama = celah, baru = tertutup):
  KONTROL       vendor SAMA -> buat draf + POST -> 200; AP outstanding tagihan -1000; amount_paid +1000
  VENDOR-BEDA   buat draf lintas vendor + POST -> lama: 200 dan UANG PINDAH (outstanding -1000,
                amount_paid +1000 di tagihan vendor Y) | baru: 400 saat buat, nol tulis
  MULTI         [SAMA, BEDA] -> lama: 200 | baru: 400 dan nol tulis
  KEBERADAAN    id tagihan tenant lain vs id karangan -> lama: jawaban BEDA | baru: jawaban SAMA
  SABOTASE (baru saja) helper no-op -> vendor beda lolos lagi
"""
import asyncio
import importlib.util
import os
import sys
import uuid
from datetime import date

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

PATH, LABEL = sys.argv[1], sys.argv[2]
A, B = "kaos-biru-konveksi", "grapgrap-manado"
hasil = []


def catat(sisi, uji, ok, ket=""):
    hasil.append((sisi, uji, bool(ok), str(ket)[:300]))


def req(uid):
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"",
                    "state": {"user": {"tenant_id": A, "user_id": str(uid)}}})


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
    if os.path.exists("/tmp/pihak_helpers.py"):
        muat("app.services.pihak_helpers", "/tmp/pihak_helpers.py")
    bp = muat("app.routers.bill_payments_uji", PATH)
    from app.schemas.bill_payments import CreateBillPaymentRequest as P, BillAllocationInput as Al

    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", A)
    ba = await conn.fetchval("SELECT id FROM bank_accounts WHERE tenant_id=$1 AND is_active ORDER BY account_name LIMIT 1", A)

    async def cacah():
        return (await conn.fetchval("SELECT count(*) FROM bill_payment_allocations"),
                await conn.fetchval("SELECT count(*) FROM bill_payments_v2"),
                await conn.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", A),
                await conn.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id=$1", B))
    awal = await cacah()

    luar = conn.transaction(); await luar.start()
    try:
        tag = await conn.fetch(
            """SELECT a.bill_id, b.vendor_id FROM compute_ap_outstanding($1) a JOIN bills b ON b.id=a.bill_id
               WHERE a.bill_id IS NOT NULL AND a.outstanding >= 1000 ORDER BY a.bill_id""", A)
        t1 = tag[0]
        t2 = next(t for t in tag if t["vendor_id"] != t1["vendor_id"])  # tagihan vendor LAIN
        tag_b = await conn.fetchval("SELECT id FROM bills WHERE tenant_id=$1 ORDER BY id LIMIT 1", B)

        async def bayar(vendor_id, allocs, post=True):
            sp = conn.transaction(); await sp.start()
            ids = [b for b, _ in allocs]
            o0 = {b: await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ap_outstanding($1) WHERE bill_id=$2", A, b) for b in ids}
            p0 = {b: await conn.fetchval("SELECT amount_paid FROM bills WHERE id=$1", b) for b in ids}
            c0 = await cacah()
            kode, det, tahap = 200, "OK", "buat"
            try:
                body = P(vendor_id=str(vendor_id), payment_date=date.today(), bank_account_id=str(ba),
                         total_amount=sum(n for _, n in allocs),
                         allocations=[Al(bill_id=str(b), amount_applied=n) for b, n in allocs], save_as_draft=True)
                r = await bp.create_bill_payment(req(uid), body)
                # handler dipanggil langsung -> dict, bukan model (run pertama salah baca, sama spt V244)
                data = r.data if hasattr(r, "data") else r.get("data")
                pid = (data or {}).get("id")
                if post and not pid:
                    raise RuntimeError(f"ALAT: id pembayaran tak terbaca, keys={list((data or {}).keys())}")
                if post:
                    tahap = "post"
                    await bp.post_bill_payment(req(uid), str(pid))
            except Exception as e:  # noqa: BLE001
                kode, det = getattr(e, "status_code", type(e).__name__), getattr(e, "detail", str(e))
            o1 = {b: await conn.fetchval("SELECT COALESCE(SUM(outstanding),0) FROM compute_ap_outstanding($1) WHERE bill_id=$2", A, b) for b in ids}
            p1 = {b: await conn.fetchval("SELECT amount_paid FROM bills WHERE id=$1", b) for b in ids}
            c1 = await cacah()
            await sp.rollback()
            return dict(kode=kode, det=det, tahap=tahap,
                        do={str(b)[:8]: o1[b] - o0[b] for b in ids},
                        dp={str(b)[:8]: (p1[b] or 0) - (p0[b] or 0) for b in ids},
                        dc=[y - x for x, y in zip(c0, c1)])

        r = await bayar(t1["vendor_id"], [(t1["bill_id"], 1000)])
        catat("KONTROL", "vendor SAMA buat+POST -> 200; outstanding -1000; amount_paid +1000",
              r["kode"] == 200 and list(r["do"].values())[0] == -1000 and list(r["dp"].values())[0] == 1000, r)

        r = await bayar(t1["vendor_id"], [(t2["bill_id"], 1000)])
        if LABEL == "lama":
            catat("MERAH lama", "lintas VENDOR buat+POST -> 200 dan UANG PINDAH ke tagihan vendor lain",
                  r["kode"] == 200 and list(r["do"].values())[0] == -1000 and list(r["dp"].values())[0] == 1000, r)
        else:
            catat("TOLAK baru", "lintas VENDOR -> 400 saat BUAT, nol tulis, tagihan diam",
                  r["kode"] == 400 and r["tahap"] == "buat" and r["dc"] == [0, 0, 0, 0]
                  and all(v == 0 for v in r["do"].values()), r)

        r = await bayar(t1["vendor_id"], [(t1["bill_id"], 1000), (t2["bill_id"], 1000)])
        if LABEL == "lama":
            catat("MERAH lama", "multi [SAMA, BEDA] -> 200", r["kode"] == 200, r)
        else:
            catat("TOLAK baru", "multi [SAMA, BEDA] -> 400 dan NOL tulis (tagihan SAMA pun diam)",
                  r["kode"] == 400 and r["dc"] == [0, 0, 0, 0] and all(v == 0 for v in r["do"].values())
                  and all(v == 0 for v in r["dp"].values()), r)

        rb = await bayar(t1["vendor_id"], [(tag_b, 1000)], post=False)
        rk = await bayar(t1["vendor_id"], [(uuid.uuid4(), 1000)], post=False)
        # bentuk jawaban dibandingkan dgn id dinormalkan: pesan memuat id yang diminta, jadi string
        # utuh selalu beda (run pertama sisi baru memerah palsu karena itu).
        import re as _re
        def _bentuk(d):
            return _re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<id>", str(d))
        sama_jawab = (rb["kode"], _bentuk(rb["det"])) == (rk["kode"], _bentuk(rk["det"]))
        if LABEL == "lama":
            catat("MERAH lama", "KEBERADAAN: tagihan tenant lain vs id karangan -> jawaban BEDA (bocor)",
                  not sama_jawab, f"tenant_lain={rb['kode']} {rb['det']} | karangan={rk['kode']} {rk['det']}")
        else:
            catat("TOLAK baru", "KEBERADAAN: tagihan tenant lain vs id karangan -> jawaban SAMA",
                  sama_jawab and rb["kode"] == 400 and rb["dc"] == [0, 0, 0, 0],
                  f"tenant_lain={rb['kode']} {rb['det']} | karangan={rk['kode']} {rk['det']}")

        if LABEL == "baru":
            asli = bp.pastikan_pihak_sama
            bp.pastikan_pihak_sama = lambda *a, **k: None
            r = await bayar(t1["vendor_id"], [(t2["bill_id"], 1000)], post=False)
            bp.pastikan_pihak_sama = asli
            catat("SABOTASE", "helper no-op -> lintas vendor lolos lagi (gerbang bisa merah)", r["kode"] == 200, r)
    finally:
        await luar.rollback()

    akhir = await cacah()
    catat("KONTROL", "nol menetap di KEDUA tenant", awal == akhir, f"{awal} -> {akhir}")
    await conn.close()
    harap = 5 if LABEL == "lama" else 6
    for s, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s:10} {u}  | {k}")
    g = sum(1 for h in hasil if not h[2])
    print(f"\n[{LABEL}] gagal={g} total={len(hasil)} harap={harap} -> {'LENGKAP' if len(hasil) == harap else 'TAK SAH'}")
    sys.exit(0 if g == 0 and len(hasil) == harap else 1)


asyncio.run(main())
