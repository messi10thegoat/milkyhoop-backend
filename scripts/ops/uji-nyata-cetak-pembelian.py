"""P2P cetak (25 Okt 2026): uji nyata BACA-SAJA cetak bill / payment_out / debit_note di kaos + grapgrap-manado.

Dijalankan DUA kali oleh .sh: MODE=LAMA (pohon utama) menulis get_bill_v2 per faktur ke /out; MODE=BARU (worktree)
(H0) get_bill_v2_conn == get_bill_v2 LAMA per faktur (JSON identik); (H1) _render tiap jenis lewat routers/dokumen
memberi HTML berisi nomor + total, PDF terbentuk; (H2) kertas ND menjumlah ke total tersimpan; (H3) ukur ND dengan diskon
baris. Koneksi: server_settings default_transaction_read_only=on (tulis apa pun = galat). Tenant tak ada = GAGAL."""
import asyncio
import json
import os
import sys
from decimal import Decimal

import asyncpg

sys.path.insert(0, "/app/backend/api_gateway")
TENANT = ("kaos-biru-konveksi", "grapgrap-manado")
MODE, OUT, MERAH = os.environ["MODE"], "/out", os.environ.get("MERAH", "")
N = 4
gagal = []


def cek(ok, pesan):
    print(("OK   " if ok else "GAGAL ") + pesan)
    if not ok:
        gagal.append(pesan)


def _json(x):
    return json.dumps(x, default=str, sort_keys=True)


async def main():
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3,
                                     server_settings={"default_transaction_read_only": "on"})
    async with pool.acquire() as conn:
        ada = {r["id"] for r in await conn.fetch('SELECT id FROM "Tenant" WHERE id = ANY($1::text[])', list(TENANT))}
        cek(ada == set(TENANT), f"tenant ada: {sorted(ada)}")
        cek(await conn.fetchval("SHOW transaction_read_only") == "on", "koneksi read-only")
        ids = {}
        for t in TENANT:
            ids[t] = {
                "bill": [r["id"] for r in await conn.fetch(
                    "SELECT id FROM bills WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT $2", t, N)],
                "payment_out": [r["id"] for r in await conn.fetch(
                    "SELECT id FROM bill_payments_v2 WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT $2", t, N)],
                "debit_note": [r["id"] for r in await conn.fetch(
                    "SELECT id FROM vendor_credits WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT $2", t, N)],
            }
    from app.services.bills_service import BillsService
    bs = BillsService(pool)
    if MODE == "LAMA":
        hasil = {}
        for t in TENANT:
            for bid in ids[t]["bill"]:
                hasil[str(bid)] = _json(await bs.get_bill_v2(t, bid))
        json.dump(hasil, open(f"{OUT}/lama.json", "w"))
        print(f"LAMA: {len(hasil)} faktur ditulis")
        await pool.close()
        return 0

    lama = json.load(open(f"{OUT}/lama.json"))
    from app.routers import dokumen as DK
    from app.services.cetak_debit_note import muat_pdf_debit_note
    from app.services.pdf_service import get_pdf_service
    ps = get_pdf_service()
    async with pool.acquire() as conn:
        for t in TENANT:
            ctx = {"tenant_id": t, "user_id": None}
            nb = 0
            for bid in ids[t]["bill"]:
                baru = _json(await bs.get_bill_v2_conn(conn, t, bid))
                if MERAH == "h0":
                    baru = baru.replace('"status":', '"status_x":', 1)
                nb += lama.get(str(bid)) == baru
            cek(nb == len(ids[t]["bill"]), f"H0 {t}: get_bill_v2_conn == get_bill_v2 LAMA {nb}/{len(ids[t]['bill'])}")
            for jenis, daftar in ids[t].items():
                for did in daftar:
                    try:
                        r, nomor = await DK._render(conn, ctx, None, jenis, str(did))
                        pdf = ps.tulis_pdf(r)
                        hal = len(ps.dokumen(r).pages)
                        ok = pdf[:5] == b"%PDF-" and str(nomor) in r.html and "{{" not in r.html and hal >= 1
                        cek(ok, f"H1 {t} {jenis} {nomor}: PDF {len(pdf)} B, {hal} hal")
                    except Exception as e:  # noqa: BLE001
                        cek(False, f"H1 {t} {jenis} {did}: {type(e).__name__}: {e}")
            for did in ids[t]["debit_note"]:
                d = (await muat_pdf_debit_note(conn, ctx, did))["debit_note_data"]
                neto = sum((i["net"] for i in d["items"]), Decimal("0"))
                ok = (neto == d["subtotal"] - d["item_discount_total"]
                      and d["subtotal"] - d["item_discount_total"] - d["discount_amount"] + d["tax_amount"] == d["total_amount"])
                cek(ok or not d["items"], f"H2 {t} {d['credit_number']}: kertas menjumlah ke total {d['total_amount']}")
            h3 = await conn.fetchrow(
                """SELECT count(DISTINCT vc.id) AS nd,
                          count(DISTINCT vc.id) FILTER (WHERE vci.discount_amount > 0) AS nd_diskon_baris
                   FROM vendor_credits vc LEFT JOIN vendor_credit_items vci ON vci.vendor_credit_id = vc.id
                   WHERE vc.tenant_id = $1""", t)
            print(f"H3 {t}: ND={h3['nd']} ND dengan diskon baris={h3['nd_diskon_baris']}")
    await pool.close()
    print(f"SELESAI {MODE}: {len(gagal)} gagal")
    return 1 if gagal else 0


sys.exit(asyncio.run(main()))
