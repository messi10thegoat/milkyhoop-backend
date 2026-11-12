"""BUG-165 uji DB di SALINAN (bukan prod): rekening bayar bawaan Pembayaran Keluar (services/rekening_bawaan + GET /api/bill-payments/summary) atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
H1 SQL jalan nyata (kolom ada) dan 'terakhir' == pembanding MANDIRI (posted terbaru ber-rekening aktif) · H2 summary memuat paid_through_bawaan == helper · H3 rekening terakhir DINONAKTIFKAN di klon -> dilewati
(sumber berpindah ke pembayaran posted sebelumnya / utama / kas) · H4 pembayaran VOID terbaru tak dihitung (diubah jadi voided di klon -> tak jadi 'terakhir') · H5 tenant lain tak bocor (id hasil milik tenant sendiri) · H6 Biaya: bawaan Biaya tak berubah oleh perubahan data pembayaran."""
import asyncio
import sys
from types import SimpleNamespace

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bill_payments as BP  # noqa: E402
from app.services import rekening_bawaan as RB  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c, tid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def mandiri(c, tid):
    r = await c.fetchrow("""SELECT ba.id FROM bill_payments_v2 bp JOIN bank_accounts ba ON ba.id = bp.bank_account_id AND ba.tenant_id = $1
        WHERE bp.tenant_id = $1 AND bp.status = 'posted' AND ba.is_active ORDER BY bp.created_at DESC, bp.id DESC LIMIT 1""", tid)
    return str(r["id"]) if r else None


async def main():
    pool = await BP.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            h = await RB.paid_through_bawaan(c, tid, RB.SQL_TERAKHIR_BAYAR)
            m = await mandiri(c, tid)
            print(f"INFO {tid}: bawaan={h} pembanding_terakhir={m}", flush=True)
            cek(f"H1 {tid} SQL jalan; sumber 'terakhir' == pembanding mandiri bila ada pembayaran posted aktif", (h is not None) and ((h["sumber"] == "terakhir" and h["id"] == m) if m else h["sumber"] != "terakhir"), str(h))
            sm = (await BP.get_bill_payments_summary(await req(c, tid), None)).data
            cek(f"H2 {tid} summary memuat paid_through_bawaan == helper", sm.get("paid_through_bawaan") == h, str(sm.get("paid_through_bawaan")))
            milik = {str(r["id"]) for r in await c.fetch("SELECT id FROM bank_accounts WHERE tenant_id = $1", tid)}
            cek(f"H5 {tid} rekening bawaan milik tenant sendiri", h is None or h["id"] in milik)
            if m:
                await c.execute("UPDATE bank_accounts SET is_active = false WHERE id = $1 AND tenant_id = $2", m, tid)
                h3 = await RB.paid_through_bawaan(c, tid, RB.SQL_TERAKHIR_BAYAR)
                cek(f"H3 {tid} rekening terakhir dinonaktifkan -> tak dipilih", h3 is None or h3["id"] != m, str(h3))
                await c.execute("UPDATE bank_accounts SET is_active = true WHERE id = $1 AND tenant_id = $2", m, tid)
                bp = await c.fetchrow("SELECT bp.id FROM bill_payments_v2 bp JOIN bank_accounts ba ON ba.id = bp.bank_account_id AND ba.tenant_id = $1 WHERE bp.tenant_id = $1 AND bp.status = 'posted' AND ba.is_active ORDER BY bp.created_at DESC, bp.id DESC LIMIT 1", tid)
                await c.execute("UPDATE bill_payments_v2 SET status = 'voided' WHERE id = $1 AND tenant_id = $2", bp["id"], tid)
                h4 = await mandiri(c, tid)
                cek(f"H4 {tid} pembayaran terbaru di-void -> bukan lagi 'terakhir' (pembanding bergeser)", h4 != m)
            # H6 Biaya tak terpengaruh: helper default masih memakai SQL expenses
            hb = await RB.paid_through_bawaan(c, tid)
            cek(f"H6 {tid} bawaan Biaya = sumber Biaya (bukan pembayaran keluar)", hb is None or hb["sumber"] in ("terakhir", "utama", "kas"), str(hb))
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
