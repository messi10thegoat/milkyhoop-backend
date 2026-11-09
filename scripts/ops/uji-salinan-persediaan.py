"""PERSEDIAAN standar SO (penyesuaian stok + gudang) -- uji di SALINAN: SATU dump, DUA klon, handler ASLI pohon LAMA (master) dan BARU.
Tulis HANYA di salinan (trap). JSON per pohon -> banding-persediaan.py.
 A baca: daftar/ringkasan/detail penyesuaian + gudang SEBELUM tulis (baru = lama + medan aditif; nomor jurnal lama tetap)
 C tulis per tenant: buat penyesuaian KURANG (barang 1, -1) + TAMBAH (barang 2, +2) -> posting keduanya -> batal yang KURANG;
   efek per langkah: baris draf, jurnal (akun/debit/kredit), inventory_ledger (gudang/qty/biaya), warehouse_stock barang itu
 BARU: pratinjau buat/posting/batal == tulis (baris draf, jurnal, gerak); pratinjau nol tulis (sidik); Inv-1 barang tersentuh;
   jadikan utama gudang ke-2 -> tepat satu gudang utama; nonaktif gudang berstok ditolak (ledger)."""
import asyncio
import json
import os
import sys
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import stock_adjustments as SA, warehouses as WH  # noqa: E402
from app.schemas.stock_adjustments import CreateStockAdjustmentRequest, VoidStockAdjustmentRequest  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
BARU = hasattr(SA, "preview_post_stock_adjustment")
CEK = []


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:500]])


def req(tid, method="GET"):
    r = Request({"type": "http", "method": method, "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-persediaan"}
    return r


def j(v):
    return json.loads(json.dumps(v, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


async def coba(f):
    try:
        return {"ok": True, "hasil": j(await f)}
    except HTTPException as e:
        return {"ok": False, "status": e.status_code, "detail": j(e.detail), "code": getattr(e, "code", None)}


async def sidik(conn, tid):
    return {t: await conn.fetchval(f"SELECT md5(COALESCE(string_agg(x::text, '|' ORDER BY x.id), '')) FROM {t} x WHERE x.tenant_id = $1", tid)
            for t in ("stock_adjustments", "journal_entries", "inventory_ledger", "warehouses")}


async def efek(conn, tid, sa_id):
    sa = await conn.fetchrow("SELECT status, journal_id, total_value, item_count FROM stock_adjustments WHERE id = $1 AND tenant_id = $2", sa_id, tid)
    if not sa:
        return {"dihapus": True}
    je = await conn.fetchrow("SELECT journal_number, reversed_by_id FROM journal_entries WHERE id = $1 AND tenant_id = $2", sa["journal_id"], tid) \
        if sa["journal_id"] else None

    async def baris(jid):
        return [dict(r) for r in await conn.fetch(
            "SELECT jl.account_id, jl.debit, jl.credit FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id "
            "WHERE je.tenant_id = $1 AND je.id = $2 ORDER BY jl.line_number", tid, jid)] if jid else None
    return j({
        "status": sa["status"], "total_value": sa["total_value"], "item_count": sa["item_count"],
        "jurnal_nomor": je["journal_number"] if je else None, "jurnal": await baris(sa["journal_id"]),
        "pembalik": await baris(je["reversed_by_id"]) if je else None,
        "ledger": [dict(r) for r in await conn.fetch(
            "SELECT source_type, product_id, warehouse_id, quantity_in, quantity_out, unit_cost FROM inventory_ledger "
            "WHERE tenant_id = $1 AND source_id = $2 ORDER BY source_type, product_id, quantity_in", tid, sa_id)],
        "item": [dict(r) for r in await conn.fetch(
            "SELECT i.product_id, i.quantity_adjustment, i.unit_cost, i.total_value FROM stock_adjustment_items i "
            "JOIN stock_adjustments s ON s.id = i.stock_adjustment_id AND s.tenant_id = $1 WHERE i.stock_adjustment_id = $2 ORDER BY i.line_number",
            tid, sa_id)],
    })


async def ws(conn, tid, pids):
    return j([dict(r) for r in await conn.fetch(
        "SELECT item_id, warehouse_id, quantity FROM warehouse_stock WHERE tenant_id = $1 AND item_id = ANY($2::uuid[]) ORDER BY 1, 2", tid, pids)])


async def inv1(conn, tid, pids):
    """Inv-1: warehouse_stock == GREATEST(0, Σ ledger) per (barang, gudang) barang tersentuh."""
    return await conn.fetchval(
        """SELECT COUNT(*) FROM (SELECT product_id, warehouse_id, SUM(quantity_in) - SUM(quantity_out) AS q FROM inventory_ledger
           WHERE tenant_id = $1 AND product_id = ANY($2::uuid[]) AND warehouse_id IS NOT NULL GROUP BY 1, 2) l
           LEFT JOIN warehouse_stock w ON w.tenant_id = $1 AND w.item_id = l.product_id AND w.warehouse_id = l.warehouse_id
           WHERE COALESCE(w.quantity, 0) <> GREATEST(0, l.q)""", tid, pids)


async def baca(conn, tid, o):
    d = await SA.list_stock_adjustments(req(tid), status="all", adjustment_type=None, search=None, date_from=None, date_to=None, skip=0, limit=100,
                                        sort_by="created_at", sort_order="desc")
    o["A_sal"] = {x["id"]: j(x) for x in d["items"]}
    for x in d["items"]:
        o[f"A_sad:{x['id']}"] = j((await SA.get_stock_adjustment(req(tid), x["id"]))["data"])
    o["A_sas"] = j((await SA.get_stock_adjustments_summary(req(tid)))["data"])
    w = await WH.list_warehouses(req(tid), skip=0, limit=100, search=None, is_active=None, is_branch=None)
    o["A_whl"] = {str(x.id): j(x) for x in w.data}
    for x in w.data:
        o[f"A_whd:{x.id}"] = j((await WH.get_warehouse(req(tid), x.id)).data)


async def tulis(conn, tid, o):
    pr = await conn.fetch(
        """SELECT p.id FROM products p JOIN inventory_ledger il ON il.product_id = p.id AND il.tenant_id = p.tenant_id
           WHERE p.tenant_id = $1 AND p.item_type = 'goods' GROUP BY p.id HAVING SUM(il.quantity_in) - SUM(il.quantity_out) > 1
           ORDER BY p.id LIMIT 2""", tid)
    if len(pr) < 2:
        o["C_lewat"] = "barang berstok < 2"
        return
    p1, p2 = pr[0]["id"], pr[1]["id"]
    o["C_ws0"] = await ws(conn, tid, [p1, p2])
    kurang = CreateStockAdjustmentRequest(adjustment_date="2026-11-09", adjustment_type="decrease", notes="UJI salinan",
                                          items=[{"product_id": str(p1), "quantity_adjustment": -1}])
    tambah = CreateStockAdjustmentRequest(adjustment_date="2026-11-09", adjustment_type="increase", notes="UJI salinan",
                                          items=[{"product_id": str(p2), "quantity_adjustment": 2}])
    ids = {}
    for kunci, body in (("kurang", kurang), ("tambah", tambah)):
        if BARU:
            s0 = await sidik(conn, tid)
            pb = (await SA.preview_create_stock_adjustment(req(tid, "POST"), body))["data"]
            cek(f"{tid} {kunci} pratinjau buat nol tulis", s0 == await sidik(conn, tid))
        h = await coba(SA.create_stock_adjustment(req(tid, "POST"), body))
        o[f"C_{kunci}_buat"] = {"ok": h["ok"], "status": h.get("status")}
        if not h["ok"]:
            continue
        ids[kunci] = h["hasil"]["data"]["id"]
        if BARU:
            e = await efek(conn, tid, ids[kunci])
            cek(f"{tid} {kunci} pratinjau buat == draf", [(b["product_id"], Decimal(b["quantity_adjustment"]), Decimal(b["total_value"]))
                                                           for b in pb["baris_draf"]]
                == [(x["product_id"], Decimal(x["quantity_adjustment"]), Decimal(x["total_value"])) for x in e["item"]], (pb, e["item"]))
            s0 = await sidik(conn, tid)
            pp = (await SA.preview_post_stock_adjustment(req(tid, "POST"), ids[kunci]))["data"]
            cek(f"{tid} {kunci} pratinjau posting nol tulis", s0 == await sidik(conn, tid))
        hp = await coba(SA.post_stock_adjustment(req(tid, "POST"), ids[kunci]))
        o[f"C_{kunci}_post"] = {"ok": hp["ok"], "status": hp.get("status"), "efek": await efek(conn, tid, ids[kunci])}
        if BARU and hp["ok"]:
            e = o[f"C_{kunci}_post"]["efek"]
            cek(f"{tid} {kunci} pratinjau posting == jurnal", pp["ok"] and [(x["account_id"], Decimal(x["debit"]), Decimal(x["credit"]))
                                                                           for x in pp["jurnal"]]
                == [(x["account_id"], Decimal(x["debit"]), Decimal(x["credit"])) for x in e["jurnal"]], (pp, e["jurnal"]))
            cek(f"{tid} {kunci} pratinjau gerak == ledger", sorted((g["product_id"], Decimal(g["masuk"]), Decimal(g["keluar"])) for g in pp["gerak"])
                == sorted((x["product_id"], Decimal(x["quantity_in"]), Decimal(x["quantity_out"])) for x in e["ledger"]), (pp["gerak"], e["ledger"]))
            cek(f"{tid} {kunci} ledger di gudang pratinjau", all(x["warehouse_id"] == pp["gudang"]["id"] for x in e["ledger"]))
    if "kurang" in ids:
        if BARU:
            s0 = await sidik(conn, tid)
            pv = (await SA.preview_void_stock_adjustment(req(tid, "POST"), ids["kurang"], VoidStockAdjustmentRequest(reason="UJI salinan")))["data"]
            cek(f"{tid} pratinjau batal nol tulis", s0 == await sidik(conn, tid))
        hv = await coba(SA.void_stock_adjustment(req(tid, "POST"), ids["kurang"], VoidStockAdjustmentRequest(reason="UJI salinan")))
        o["C_kurang_void"] = {"ok": hv["ok"], "status": hv.get("status"), "efek": await efek(conn, tid, ids["kurang"])}
        if BARU and hv["ok"]:
            e = o["C_kurang_void"]["efek"]
            cek(f"{tid} pratinjau batal == pembalik", pv["ok"] and [(x["account_id"], Decimal(x["debit"]), Decimal(x["credit"])) for x in pv["jurnal"]]
                == [(x["account_id"], Decimal(x["debit"]), Decimal(x["credit"])) for x in e["pembalik"]], (pv, e["pembalik"]))
    o["C_ws1"] = await ws(conn, tid, [p1, p2])
    cek(f"{tid} Inv-1 barang tersentuh", await inv1(conn, tid, [p1, p2]) == 0)
    # gudang: nonaktif gudang berstok ditolak; jadikan utama gudang lain
    gud = await conn.fetch("SELECT id, is_default FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC, created_at", tid)
    o["C_wh_nonaktif"] = {k: v for k, v in (await coba(WH.delete_warehouse(req(tid, "DELETE"), gud[0]["id"]))).items() if k in ("ok", "status")}
    if len(gud) >= 2:
        o["C_wh_utama"] = (await coba(WH.set_default_warehouse(req(tid, "POST"), gud[1]["id"])))["ok"]
        o["C_wh_jumlah_utama"] = await conn.fetchval("SELECT COUNT(*) FROM warehouses WHERE tenant_id = $1 AND is_default", tid)
        if BARU:
            cek(f"{tid} tepat satu gudang utama", o["C_wh_jumlah_utama"] == 1)


async def utama():
    pool = await SA.get_pool()
    out = {"baru": BARU}
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_persediaan_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            o = out[tid] = {}
            await baca(conn, tid, o)
            await tulis(conn, tid, o)
    out["CEK"] = CEK
    json.dump(out, open(os.environ["KELUAR"], "w"), sort_keys=True, indent=1)
    print("[info] " + ", ".join(f"{t}: {len(out[t]['A_sal'])} penyesuaian, {len(out[t]['A_whl'])} gudang, {out[t].get('C_lewat', 'tulis jalan')}"
                                for t in TENANT), flush=True)

asyncio.run(utama())
