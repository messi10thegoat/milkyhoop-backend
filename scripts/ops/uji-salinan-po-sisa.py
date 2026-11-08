"""ROW (s) -- kartu summary PO 'Perlu diterima' di SALINAN (klon milkydb): handler ASLI summary + daftar.
T1 paritas: summary {k}_count == daftar ?aksi=k total (k = PD.NILAI_AKSI).
T2 nilai: perlu_diterima_value DAN telat_terima_value ((t) 8 Nov) == Σ hitung MANDIRI Python atas baris PO (barang: (dipesan - diterima) x total/qty, 2dp HALF_UP; jasa 0).
T3 kunci lain tak berubah: {k}_value == Σ total_amount daftar ?aksi=k.
Angka lama -> baru dicetak [info] (data uji bergerak; ukur 8 Nov: nyawit 18.840.000 -> 2.844.000)."""
import asyncio
import os
import sys
from decimal import Decimal as D, ROUND_HALF_UP

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import purchase_orders as RO  # noqa: E402
from app.services import po_daftar as PD  # noqa: E402
from app.services.grn_rencana import JENIS_JASA  # noqa: E402

HASIL = []
TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
SQL_BARIS = """SELECT i.quantity, i.quantity_received, i.total, p.item_type FROM purchase_order_items i
               JOIN purchase_orders po ON po.id = i.po_id AND po.tenant_id = $1
               LEFT JOIN products p ON p.id = i.item_id AND p.tenant_id = $1
               WHERE po.tenant_id = $1 AND po.id = ANY($2::uuid[])"""


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/api/purchase-orders", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-po-sisa"}
    return r


async def daftar(tid, aksi):
    hasil, off = [], 0
    while True:
        r = await RO.list_purchase_orders(req(tid), status="all", vendor_id=None, search=None, date_from=None, date_to=None, skip=0,
                                          limit=100, page=1, per_page=None, offset=off, sort_by="created_at", sort_order="desc",
                                          aksi=aksi, with_totals=False)
        d = r.get("data", r)
        hasil += d["items"]
        off += 100
        if not d["has_more"]:
            return d["total"], hasil


async def periksa(tid):
    ring = (await RO.get_purchase_orders_summary(req(tid), search=None))["data"]
    pool = await RO.get_pool()
    for k in PD.NILAI_AKSI:  # kartu barang-belum-datang: perlu_diterima, telat_terima ((t) 8 Nov)
        n, rows = await daftar(tid, k)
        cek(f"{tid} T1 summary {k}_count == daftar", ring[f"{k}_count"] == n == len(rows), f"{ring[f'{k}_count']} vs {n}")
        async with pool.acquire() as conn:
            db = await conn.fetchval("SELECT current_database()")
            if not db.startswith("scratch_po_sisa_"):
                sys.exit(f"BUKAN salinan: {db}")
            baris = await conn.fetch(SQL_BARIS, tid, [r["id"] for r in rows])
        mandiri = sum(((D(b["quantity"]) - D(b["quantity_received"] or 0)).max(D(0)) * D(b["total"]) / D(b["quantity"])).quantize(D("0.01"), ROUND_HALF_UP)
                      for b in baris if b["item_type"] != JENIS_JASA and D(b["quantity"]) > 0)
        lama = sum(D(str(r["total_amount"])) for r in rows)
        cek(f"{tid} T2 {k}_value == Σ mandiri baris", D(str(ring[f"{k}_value"])) == D(mandiri),
            f"summary {ring[f'{k}_value']} mandiri {mandiri} (lama Σ total PO {lama})")
        for r in rows:
            print(f"[info] {tid} {k} {r['po_number']} total {r['total_amount']} diterima {r['amount_received']}", flush=True)
        print(f"[info] {tid} {k}: lama Σ total PO {lama} -> baru {ring[f'{k}_value']} (ukur 8 Nov ROW s: nyawit 18.840.000 -> 2.844.000)", flush=True)
    for k in PD.AKSI:
        if k in PD.NILAI_AKSI:
            continue
        nk, rk = await daftar(tid, k)
        s = sum(D(str(r["total_amount"])) for r in rk)
        cek(f"{tid} T3 {k}_value tetap Σ total PO", D(str(ring[f"{k}_value"])) == s, f"{ring[f'{k}_value']} vs {s}")


async def utama():
    for tid in TENANT:
        await periksa(tid)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return all(HASIL)

sys.exit(0 if asyncio.run(utama()) else 1)
