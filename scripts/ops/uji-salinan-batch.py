"""BE-A / BUG-164 -- uji di SALINAN (satu klon): penyesuaian batch = dokumen Penyesuaian Stok bernomor + jurnal + kartu stok bertanda batch;
pratinjau nol tulis; tak cukup ditolak tanpa tulis; idempotensi; batal SA membalik batch; daftar/detail memuat aksi_tersedia + teks.
Handler ASLI pohon yang dipasang. Tulis HANYA di salinan (nama DB dicek)."""
import asyncio
import json
import os
import sys
import uuid
from decimal import Decimal as D

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import item_batches as IB, stock_adjustments as SA  # noqa: E402
from app.schemas.item_batches import AdjustBatchQuantityRequest, SetBatchStatusRequest  # noqa: E402
from app.services import batch_aksi as BA  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from app.schemas.stock_adjustments import VoidStockAdjustmentRequest  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
CEK = []


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:600]])
    print(("[ok] " if ok else "[GAGAL] ") + nama + ("" if ok else " :: " + str(info)[:300]), flush=True)


def req(tid, method="GET", hdr=None):
    r = Request({"type": "http", "method": method, "path": "/", "headers": hdr or []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-batch"}
    return r


def j(v):
    return json.loads(json.dumps(v, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


async def coba(f):
    try:
        return {"ok": True, "hasil": j(await f)}
    except HTTPException as e:
        return {"ok": False, "status": e.status_code, "code": getattr(e, "code", None), "detail": j(e.detail)}


async def sidik(conn, tid):
    return {t: await conn.fetchval(f"SELECT md5(COALESCE(string_agg(x::text, '|' ORDER BY x.id), '')) FROM {t} x WHERE x.tenant_id = $1", tid)
            for t in ("stock_adjustments", "journal_entries", "inventory_ledger", "batch_warehouse_stock", "item_batches")}


async def satu(conn, tid):
    # barang goods berstok >= 6 di satu gudang (ledger, Law 16) + gudang itu
    r = await conn.fetchrow(
        """SELECT p.id AS pid, il.warehouse_id AS wid FROM products p JOIN inventory_ledger il ON il.product_id = p.id AND il.tenant_id = p.tenant_id
           WHERE p.tenant_id = $1 AND p.item_type = 'goods' AND COALESCE(p.track_inventory, true) AND il.warehouse_id IS NOT NULL
           GROUP BY p.id, il.warehouse_id HAVING SUM(il.quantity_in) - SUM(il.quantity_out) >= 6 ORDER BY p.id LIMIT 1""", tid)
    if not r:
        print(f"[info] {tid}: tak ada barang goods berstok >= 6 -> dilewati", flush=True)
        return
    pid, wid = r["pid"], r["wid"]
    bid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO item_batches (id, tenant_id, item_id, batch_number, initial_quantity, current_quantity, unit_cost, total_value, status, received_date) "
        "VALUES ($1, $2, $3, $4, 5, 0, 0, 0, 'active', CURRENT_DATE)", bid, tid, pid, f"UJI-{uuid.uuid4().hex[:6]}")
    await conn.execute("INSERT INTO batch_warehouse_stock (tenant_id, batch_id, warehouse_id, quantity) VALUES ($1, $2, $3, 5)", tid, bid, wid)

    async def stok():
        return D(str(await conn.fetchval("SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 AND warehouse_id = $3",
                                         tid, pid, wid)))

    async def bws():
        return D(str(await conn.fetchval("SELECT quantity FROM batch_warehouse_stock WHERE tenant_id = $1 AND batch_id = $2 AND warehouse_id = $3", tid, bid, wid)))
    s0, b0 = await stok(), await bws()
    cek(f"{tid} awal: batch 5", b0 == 5, b0)

    # daftar + detail memuat teks + aksi
    d = await coba(IB.get_item_batch(req(tid), bid))
    dd = d["hasil"]["data"] if d["ok"] else {}
    cek(f"{tid} detail: aksi_tersedia + position_text", d["ok"] and set(dd.get("aksi_tersedia", {})) == {"sesuaikan", "ubah", "karantina", "aktifkan"} and dd.get("position_text"), d)
    ls = await coba(IB.list_item_batches(req(tid), skip=0, limit=100, item_id=pid, status=None, search=None, aksi=None))
    cek(f"{tid} daftar: baris batch uji punya status_text", ls["ok"] and any(x["id"] == str(bid) and x["status_text"] == "Aktif" for x in ls["hasil"]["data"]), ls)

    # pratinjau nol tulis
    sd = await sidik(conn, tid)
    pv = await coba(IB.preview_adjust_batch(req(tid, "POST"), bid, AdjustBatchQuantityRequest(warehouse_id=wid, quantity_change=D("-3"), reason="UJI")))
    cek(f"{tid} pratinjau -3: bisa lanjut, nol tulis", pv["ok"] and pv["hasil"]["can_proceed"] and D(pv["hasil"]["batch_after"]) == 2 and sd == await sidik(conn, tid), pv)

    # tulis -3 + idempotensi
    hdr = [(b"x-idempotency-key", f"uji-batch-{bid}".encode())]
    body = AdjustBatchQuantityRequest(warehouse_id=wid, quantity_change=D("-3"), reason="UJI kedaluwarsa")
    h1 = await coba(IB.adjust_batch_quantity(req(tid, "POST", hdr), bid, body))
    cek(f"{tid} adjust -3 ok + nomor SA", h1["ok"] and h1["hasil"].get("adjustment_number", "").startswith("SA"), h1)
    if not h1["ok"]:
        return
    sa_id = uuid.UUID(h1["hasil"]["adjustment_id"])
    cek(f"{tid} batch 2, stok barang -3", await bws() == 2 and await stok() == s0 - 3, (await bws(), await stok(), s0))
    sa = await conn.fetchrow("SELECT status, adjustment_type FROM stock_adjustments WHERE id = $1 AND tenant_id = $2", sa_id, tid)
    cek(f"{tid} dokumen SA posted/decrease", sa and sa["status"] == "posted" and sa["adjustment_type"] == "decrease", sa)
    led = await conn.fetch("SELECT batch_id, quantity_out, warehouse_id FROM inventory_ledger WHERE tenant_id = $1 AND source_type = 'STOCK_ADJUSTMENT' AND source_id = $2", tid, sa_id)
    cek(f"{tid} kartu stok bertanda batch", len(led) == 1 and led[0]["batch_id"] == bid and D(str(led[0]["quantity_out"])) == 3 and led[0]["warehouse_id"] == wid, [dict(x) for x in led])
    h2 = await coba(IB.adjust_batch_quantity(req(tid, "POST", hdr), bid, body))
    cek(f"{tid} idempotensi: replay sama, batch tetap 2", h2["ok"] and h2["hasil"].get("adjustment_id") == str(sa_id) and await bws() == 2, h2)

    # tak cukup -> 422 tanpa tulis
    sd = await sidik(conn, tid)
    hx = await coba(IB.adjust_batch_quantity(req(tid, "POST"), bid, AdjustBatchQuantityRequest(warehouse_id=wid, quantity_change=D("-9"), reason="UJI minus")))
    cek(f"{tid} -9 ditolak BATCH_STOK_TIDAK_CUKUP tanpa tulis", not hx["ok"] and hx["status"] == 422 and hx["code"] == "BATCH_STOK_TIDAK_CUKUP" and sd == await sidik(conn, tid), hx)

    # batal SA -> batch kembali 5
    hv = await coba(SA.void_stock_adjustment(req(tid, "POST"), sa_id, VoidStockAdjustmentRequest(reason="UJI batal")))
    cek(f"{tid} batal SA ok", hv["ok"], hv)
    cek(f"{tid} batal SA: batch kembali 5, stok kembali", await bws() == 5 and await stok() == s0, (await bws(), await stok(), s0))

    # karantina lalu karantina lagi ditolak 409
    from app.schemas.item_batches import UpdateItemBatchRequest
    k1 = await coba(IB.update_item_batch(req(tid, "PATCH"), bid, UpdateItemBatchRequest(status="quarantine")))
    k2 = await coba(IB.update_item_batch(req(tid, "PATCH"), bid, UpdateItemBatchRequest(status="quarantine")))
    cek(f"{tid} karantina: pertama ok, kedua 409", k1["ok"] and k1["hasil"]["data"]["status_text"] == "Karantina" and not k2["ok"] and k2["status"] == 409, (k1, k2))

    # ---- BE-A tambahan: ringkasan == saringan ?aksi= == penentu Python; teks gudang; pratinjau + penulis status
    h0 = date.today()
    for no, st, hari in (("EXP", "active", -3), ("SEGERA", "active", 10), ("JAUH", "active", 90), ("KAR", "quarantine", -5), ("HABIS", "depleted", None), ("BASI", "expired", -9)):
        await conn.execute(
            "INSERT INTO item_batches (id, tenant_id, item_id, batch_number, initial_quantity, current_quantity, unit_cost, total_value, status, received_date, expiry_date) "
            "VALUES ($1, $2, $3, $4, 5, 0, 0, 0, $5, CURRENT_DATE, $6)", uuid.uuid4(), tid, pid, f"UJI-{no}", st, (h0 + timedelta(days=hari)) if hari is not None else None)
    sm = (await coba(IB.get_batches_summary(req(tid), pid)))
    cek(f"{tid} summary ok", sm["ok"], sm)
    ringk = sm["hasil"]["data"] if sm["ok"] else {}
    semua = (await coba(IB.list_item_batches(req(tid), skip=0, limit=100, item_id=pid, status=None, search=None, aksi=None)))["hasil"]["data"]
    py = {k: 0 for k in BA.KUNCI}
    for x in semua:
        py[BA.kunci_dari_baris(x["status"], date.fromisoformat(x["expiry_date"]) if x["expiry_date"] else None, h0)] += 1
    py["semua"] = len(semua)
    cek(f"{tid} ringkasan == penentu Python atas semua baris", all(ringk.get(k) == py[k] for k in BA.KUNCI), (ringk, py))
    for k in BA.KUNCI:
        ls = await coba(IB.list_item_batches(req(tid), skip=0, limit=100, item_id=pid, status=None, search=None, aksi=k))
        cek(f"{tid} ?aksi={k}: jumlah == ringkasan == Python", ls["ok"] and len(ls["hasil"]["data"]) == ringk.get(k) == py[k], (k, ls.get("hasil", ls) if not ls["ok"] else len(ls["hasil"]["data"]), ringk.get(k), py[k]))
    ex = await coba(IB.list_item_batches(req(tid), skip=0, limit=100, item_id=pid, status="expired", search=None, aksi=None))
    cek(f"{tid} ?status=expired ikut menangkap 'active' yang lewat tanggal", ex["ok"] and {"UJI-EXP", "UJI-BASI"} <= {x["batch_number"] for x in ex["hasil"]["data"]}, ex)
    bad = await coba(IB.list_item_batches(req(tid), skip=0, limit=100, item_id=pid, status=None, search=None, aksi="ngawur"))
    cek(f"{tid} ?aksi tak sah -> 422 AKSI_TAK_SAH", not bad["ok"] and bad["status"] == 422 and bad["code"] == "AKSI_TAK_SAH", bad)

    # teks stok per gudang di detail
    d2 = await coba(IB.get_item_batch(req(tid), bid))
    wsl = d2["hasil"]["data"]["warehouse_stock"] if d2["ok"] else []
    cek(f"{tid} detail warehouse_stock[] memuat stock_text", bool(wsl) and all(w.get("stock_text") and w.get("available_text") for w in wsl), wsl)

    # pratinjau status + penulis (bid sedang karantina dari langkah sebelumnya)
    sd = await sidik(conn, tid)
    pa = await coba(IB.preview_status_batch(req(tid, "POST"), bid, SetBatchStatusRequest(status="active")))
    cek(f"{tid} pratinjau aktifkan: ok, nol tulis", pa["ok"] and pa["hasil"]["data"]["ok"] and sd == await sidik(conn, tid), pa)
    pk = await coba(IB.preview_status_batch(req(tid, "POST"), bid, SetBatchStatusRequest(status="quarantine")))
    cek(f"{tid} pratinjau karantina saat sudah karantina: blok BATCH_AKSI_TIDAK_TERSEDIA + field", pk["ok"] and pk["hasil"]["data"]["blocks"][0]["code"] == "BATCH_AKSI_TIDAK_TERSEDIA"
        and pk["hasil"]["data"]["blocks"][0]["field"] == "status", pk)
    ps = await coba(IB.set_batch_status(req(tid, "POST"), bid, SetBatchStatusRequest(status="active")))
    cek(f"{tid} POST status active ok -> Aktif", ps["ok"] and ps["hasil"]["data"]["status_text"] == "Aktif", ps)
    ps2 = await coba(IB.set_batch_status(req(tid, "POST"), bid, SetBatchStatusRequest(status="active")))
    cek(f"{tid} POST status active lagi -> 409", not ps2["ok"] and ps2["status"] == 409 and ps2["code"] == "BATCH_AKSI_TIDAK_TERSEDIA", ps2)


async def utama():
    pool = await SA.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_batch_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            await satu(conn, tid)
    json.dump({"CEK": CEK}, open(os.environ["KELUAR"], "w"), indent=1)
    gagal = [c for c in CEK if not c[1]]
    print(f"[info] {len(CEK)} cek, {len(gagal)} gagal", flush=True)
    sys.exit(1 if gagal or not CEK else 0)

asyncio.run(utama())
