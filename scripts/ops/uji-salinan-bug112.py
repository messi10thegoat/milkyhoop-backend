"""BUG-112 (c)(d) -- uji di SALINAN (klon milkydb), BACA saja: handler ASLI daftar/summary/detail/bundel PO + riwayat_po, per tenant (kaos, nyawit).
T1 paritas per kunci PD.AKSI: summary {k}_count == daftar ?aksi=k total; himpunan id daftar ?aksi=k == id baris daftar ber-aksi k (SQL == Python).
T2 'sent' murni tak pernah perlu_diterima (putusan 22 Okt + MASTER (A)).
T3 PO-2611-0010 (nyawit): teks dua dimensi di baris == detail == anchor; aksi perlu_diterima|telat_terima; terima tak "selesai";
   riwayat memuat TAGIHAN_DIPOSTING + PEMBAYARAN_DIBAYAR BP-2611-0009.
T4 riwayat semua PO tanpa galat; [info] dampak teks/aksi baru (hitungan)."""
import asyncio
import os
import sys
import uuid

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import purchase_orders as RO  # noqa: E402
from app.services import po_daftar as PD, po_riwayat as PRW  # noqa: E402

HASIL = []
TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
DUA = ("Diterima sebagian · ", "Belum diterima · ")


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/api/purchase-orders", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-bug112"}
    return r


async def semua(tid, **kw):
    hasil, off = [], 0
    while True:
        a = dict(status="all", vendor_id=None, search=None, date_from=None, date_to=None, skip=0, limit=100, page=1, per_page=None,
                 offset=off, sort_by="created_at", sort_order="desc", aksi=None, with_totals=False)
        a.update(kw)
        r = await RO.list_purchase_orders(req(tid), **a)
        hasil += r["items"]
        off += 100
        if not r["has_more"]:
            return r["total"], hasil


async def periksa(tid):
    ring = (await RO.get_purchase_orders_summary(req(tid), search=None))["data"]
    total, baris = await semua(tid)
    print(f"[info] {tid}: PO={total} posisi={sorted({b['position_text'] for b in baris})}", flush=True)
    for k in PD.AKSI:
        n, bk = await semua(tid, aksi=k)
        cek(f"{tid} {k}: summary count == daftar total", ring[f"{k}_count"] == n == len(bk), f"{ring[f'{k}_count']} vs {n}")
        cek(f"{tid} {k}: id SQL == id aksi Python", {b["id"] for b in bk} == {b["id"] for b in baris if b["aksi"] == k},
            f"sql={len(bk)} py={len([b for b in baris if b['aksi'] == k])}")
    cek(f"{tid} sent murni bukan perlu_diterima", not [b for b in baris if b["status"] == "sent" and b["aksi"] == "perlu_diterima"])
    dua = [b for b in baris if b["position_text"].startswith(DUA)]
    print(f"[dampak] {tid}: teks dua dimensi={len(dua)} perlu_diterima={len([b for b in baris if b['aksi'] == 'perlu_diterima'])} "
          f"telat_terima ber-status tagih={len([b for b in baris if b['aksi'] == 'telat_terima' and b['status'] in ('partial_billed', 'billed')])}",
          flush=True)
    pool = await RO.get_pool()
    galat = 0
    async def boleh(m):
        return True
    async with pool.acquire() as conn:
        for b in baris:
            try:
                await PRW.riwayat_po(conn, tid, b["id"], boleh)
            except Exception as e:  # noqa: BLE001
                galat += 1
                print(f"[galat] riwayat {b['po_number']}: {e!r}", flush=True)
    cek(f"{tid} riwayat semua PO tanpa galat", galat == 0, f"{len(baris)} PO")
    if tid == "pt-kita-nyawit-bersama":
        x = [b for b in baris if b["po_number"] == "PO-2611-0010"]
        cek("PO-2611-0010 ada di salinan", len(x) == 1)
        if x:
            x = x[0]
            d = (await RO.get_purchase_order(req(tid), uuid.UUID(x["id"])))["data"]
            a = (await RO.get_purchase_order_documents(req(tid), uuid.UUID(x["id"])))["anchor"]
            cek("PO-2611-0010 teks dua dimensi baris == detail == anchor",
                x["position_text"] == d["position_text"] == a["position_text"] == "Diterima sebagian · ditagih sebagian",
                f"{x['position_text']!r} / {d['position_text']!r} / {a['position_text']!r}")
            cek("PO-2611-0010 aksi perlu_diterima|telat_terima", x["aksi"] in ("perlu_diterima", "telat_terima"), x["aksi"])
            cek("PO-2611-0010 terima tak 'selesai'", d["terima_keadaan"] != "selesai", d["terima_keadaan"])
            async with pool.acquire() as conn:
                r = await PRW.riwayat_po(conn, tid, x["id"], boleh)
            ev = [(e["jenis"], (e.get("dokumen") or {}).get("nomor"), e["ringkas"]) for e in r["events"]]
            for e in ev:
                print(f"[riwayat] {e}", flush=True)
            cek("PO-2611-0010 riwayat memuat TAGIHAN_DIPOSTING", any(j == "TAGIHAN_DIPOSTING" for j, _, _ in ev))
            cek("PO-2611-0010 riwayat memuat PEMBAYARAN_DIBAYAR BP-2611-0009", any(j == "PEMBAYARAN_DIBAYAR" and n == "BP-2611-0009" for j, n, _ in ev))


async def utama():
    for t in TENANT:
        await periksa(t)
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
