"""BUG-131 / ROW (r) -- uji di SALINAN (klon milkydb): handler ASLI GRN daftar/summary/detail + PO to-bill (benih faktur DRAF di salinan).
T1 paritas per kunci GD.AKSI: summary {k}_count == daftar ?aksi=k total; id daftar ?aksi=k == id baris ber-aksi k (SQL == Python medan).
T2 benih: GRN belum_ditagih -> PO to-bill (draf, sisa penuh) -> GRN itu aksi draf_tagihan + draft_bills memuat tagihan baru,
   status_text tetap; summary belum_ditagih_count MENYUSUT, draf_tagihan_count BERTAMBAH; paritas T1 ulang.
T3 to-bill KEDUA untuk PO yang sama ditolak (pagar quantity_billed termasuk draf) -> tak ada tagihan ganda.
T4 draf SEBAGIAN (1 unit dari GRN lain ber-sisa > 1) -> GRN tetap belum_ditagih."""
import asyncio
import os
import sys
from decimal import Decimal as D

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import goods_receipts as RG, purchase_orders as RO  # noqa: E402
from app.schemas.purchase_orders import ConvertToBillRequest, ToBillItemRequest  # noqa: E402
from app.services import grn_daftar as GD  # noqa: E402

HASIL = []
TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/api/goods-receipts", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-bug131"}
    return r


async def daftar(tid, aksi=None):
    hasil, off = [], 0
    while True:
        d = (await RG.list_goods_receipts(req(tid), purchase_order_id=None, status=None, aksi=aksi, limit=200, offset=off, search=None,
                                         date_from=None, date_to=None, sort_by="receipt_date", sort_order="desc"))["data"]
        hasil += d["items"]
        off += 200
        if not d["has_more"]:
            return d["total"], hasil


async def paritas(tid, tahap):
    ring = (await RG.goods_receipts_summary(req(tid), purchase_order_id=None, search=None))["data"]
    _, semua = await daftar(tid)
    for k in GD.AKSI:
        n, bk = await daftar(tid, k)
        cek(f"{tid} [{tahap}] {k}: summary == daftar ?aksi", ring[f"{k}_count"] == n == len(bk), f"{ring[f'{k}_count']} vs {n}")
        cek(f"{tid} [{tahap}] {k}: id SQL == aksi Python", {b["id"] for b in bk} == {b["id"] for b in semua if b["aksi"] == k})
    return ring, {b["id"]: b for b in semua}


async def periksa(tid):
    ring0, b0 = await paritas(tid, "awal")
    print(f"[info] {tid}: GRN={len(b0)} belum_ditagih={ring0['belum_ditagih_count']} draf_tagihan={ring0['draf_tagihan_count']}", flush=True)
    calon = [b for b in b0.values() if b["aksi"] == "belum_ditagih"]
    if not calon:
        print(f"[info] {tid}: tak ada GRN belum_ditagih -- T2-T4 dilewati", flush=True)
        return
    g = calon[-1]  # terlama
    try:
        t = await RO.convert_to_bill(req(tid), RO.UUID(g["purchase_order_id"]), ConvertToBillRequest(bill_date=g["receipt_date"]))
        nomor = (t.get("data") or {}).get("bill_number") or (t.get("data") or {}).get("invoice_number")
    except HTTPException as e:
        cek(f"{tid} benih to-bill {g['po_number']}", False, str(e.detail)[:200])
        return
    print(f"[benih] {tid}: {g['receipt_number']} / {g['po_number']} -> draf {nomor}", flush=True)
    ring1, b1 = await paritas(tid, "sesudah draf")
    g1 = b1[g["id"]]
    cek(f"{tid} {g['receipt_number']} aksi draf_tagihan", g1["aksi"] == "draf_tagihan", g1["aksi"])
    cek(f"{tid} {g['receipt_number']} status_text tetap", g1["status_text"] == g["status_text"], g1["status_text"])
    cek(f"{tid} {g['receipt_number']} draft_bills memuat draf baru", any(x["bill_number"] == nomor for x in g1["draft_bills"]), g1["draft_bills"])
    d = (await RG.get_goods_receipt(req(tid), RO.UUID(g["id"])))["data"]
    cek(f"{tid} detail == baris (aksi, draft_bills)", (d["aksi"], d["draft_bills"]) == (g1["aksi"], g1["draft_bills"]))
    pindah = sum(1 for i, b in b1.items() if b0[i]["aksi"] == "belum_ditagih" and b["aksi"] == "draf_tagihan")
    cek(f"{tid} belum_ditagih_count menyusut", ring1["belum_ditagih_count"] == ring0["belum_ditagih_count"] - pindah and pindah >= 1,
        f"{ring0['belum_ditagih_count']} -> {ring1['belum_ditagih_count']} (pindah {pindah})")
    cek(f"{tid} draf_tagihan_count bertambah", ring1["draf_tagihan_count"] == ring0["draf_tagihan_count"] + pindah)
    try:
        await RO.convert_to_bill(req(tid), RO.UUID(g["purchase_order_id"]), ConvertToBillRequest(bill_date=g["receipt_date"]))
        cek(f"{tid} to-bill kedua ditolak (tanpa tagihan ganda)", False, "LOLOS -- tagihan kedua terbuat")
    except HTTPException as e:
        cek(f"{tid} to-bill kedua ditolak (tanpa tagihan ganda)", True, str(e.detail)[:160])
    pool = await RO.get_pool()
    async with pool.acquire() as conn:
        lain = await conn.fetchrow(
            """SELECT g.id, g.receipt_number, i.purchase_order_item_id, g.purchase_order_id FROM goods_receipts g
               JOIN goods_receipt_items i ON i.goods_receipt_id = g.id AND i.ber_stok
               JOIN purchase_order_items p ON p.id = i.purchase_order_item_id
               WHERE g.tenant_id = $1 AND g.status = 'posted' AND g.purchase_order_id <> $2::uuid
                 AND p.quantity_received - COALESCE(p.quantity_billed, 0) > 1
               ORDER BY g.receipt_date LIMIT 1""", tid, g["purchase_order_id"])
    if not lain:
        print(f"[info] {tid}: tak ada GRN lain ber-sisa > 1 -- T4 dilewati", flush=True)
        return
    try:
        await RO.convert_to_bill(req(tid), lain["purchase_order_id"], ConvertToBillRequest(
            bill_date=g["receipt_date"], items=[ToBillItemRequest(po_item_id=str(lain["purchase_order_item_id"]), quantity_to_bill=D("1"))]))
    except HTTPException as e:
        cek(f"{tid} benih draf sebagian", False, str(e.detail)[:200])
        return
    _, b2 = await paritas(tid, "sesudah draf sebagian")
    cek(f"{tid} {lain['receipt_number']} draf sebagian -> tetap belum_ditagih", b2[str(lain["id"])]["aksi"] == "belum_ditagih",
        b2[str(lain["id"])]["aksi"])


async def utama():
    pool = await RO.get_pool()
    async with pool.acquire() as conn:  # penjaga: harness MENULIS (benih draf) -> wajib di salinan, bukan milkydb
        db = await conn.fetchval("SELECT current_database()")
    if not db.startswith("scratch_b131_"):
        print(f"TOLAK: current_database={db} bukan salinan", flush=True)
        return 4
    for t in TENANT:
        await periksa(t)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
