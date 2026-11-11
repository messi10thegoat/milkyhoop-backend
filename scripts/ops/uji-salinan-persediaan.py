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
from app.routers import items as IT, stock_adjustments as SA, warehouses as WH  # noqa: E402
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


def req_json(tid, body, headers=None):
    """POST ber-body JSON (quick adjust items.py membaca request.json()); headers opsional (mis. X-Idempotency-Key)."""
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"content-type", b"application/json")] + (headers or [])}, receive)
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


async def baris_jurnal(conn, tid, jid):
    if not jid:
        return None
    return j([dict(r) for r in await conn.fetch(
        "SELECT jl.account_id, jl.debit, jl.credit FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id "
        "WHERE je.tenant_id = $1 AND je.id = $2 ORDER BY jl.line_number", tid, jid)])


async def lawan(conn, tid, o):
    """penyesuaian-2 (akun lawan pilihan): D_quick_bawaan = quick adjust TANPA pilihan (lama == baru: akun 5-50100 keras, angka sama);
    sisanya BARU saja: dropdown tanpa akun terlarang, pilihan sah dipakai di quick adjust + dokumen (pratinjau == jurnal == pembalik),
    pilihan terlarang (kas/persediaan) -> 422 berkode TANPA tulisan."""
    pr = await conn.fetch(
        """SELECT p.id FROM products p JOIN inventory_ledger il ON il.product_id = p.id AND il.tenant_id = p.tenant_id
           WHERE p.tenant_id = $1 AND p.item_type = 'goods' GROUP BY p.id HAVING SUM(il.quantity_in) - SUM(il.quantity_out) > 1
           ORDER BY p.id LIMIT 1""", tid)
    if not pr:
        o["D_lewat"] = "barang berstok < 2"
        return
    p = pr[0]["id"]
    h = await coba(IT.create_stock_adjustment(req_json(tid, {"adjustment_type": "add", "quantity": 1, "reason": "UJI salinan bawaan"}), str(p)))
    jid = h["hasil"].get("journal_id") if h["ok"] else None
    o["D_quick_bawaan"] = {"ok": h["ok"], "status": h.get("status"), "jurnal": await baris_jurnal(conn, tid, jid),
                           "hasil": {k: v for k, v in h.get("hasil", {}).items() if k != "journal_id"}}
    if not hasattr(SA, "daftar_akun_lawan_penyesuaian"):  # pohon lama (master) belum punya akun lawan pilihan; BARU (gelombang 2) sudah True di master
        return
    from app.schemas.stock_adjustments import PostStockAdjustmentRequest as Pos
    dd = (await SA.daftar_akun_lawan_penyesuaian(req(tid)))["data"]
    ids_dd = {a["id"] for a in dd["akun"]}
    kas = await conn.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND is_cash AND NOT is_header ORDER BY account_code LIMIT 1", tid)
    persed = await conn.fetchval("SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND psak_sub_category = 'INVENTORY' AND NOT is_header "
                                 "ORDER BY account_code LIMIT 1", tid)
    cek(f"{tid} dropdown tanpa kas/persediaan/induk", str(kas) not in ids_dd and str(persed) not in ids_dd and bool(dd["akun"]) and dd["bawaan_id"] in ids_dd)
    sah = next((a for a in dd["akun"] if not a["bawaan"] and a["account_type"] == "EXPENSE"), None) or next((a for a in dd["akun"] if not a["bawaan"]), None)
    if not sah or not kas:
        o["D_lewat_pilihan"] = "tak ada akun sah/kas"
        return
    bawaan, sah_id = dd["bawaan_id"], sah["id"]

    async def sidik2():
        return [await conn.fetchval(f"SELECT md5(COALESCE(string_agg(x::text, '|' ORDER BY x.id), '')) FROM {t} x WHERE x.tenant_id = $1", tid)
                for t in ("journal_entries", "inventory_ledger", "stock_adjustments")]

    # quick adjust: pilihan sah dipakai (lawan = pilihan, bukan bawaan), terlarang -> 422 tanpa tulisan
    hq = await coba(IT.create_stock_adjustment(req_json(tid, {"adjustment_type": "add", "quantity": 1, "reason": "UJI pilihan",
                                                              "counter_account_id": sah_id}), str(p)))
    akun_q = {str(b["account_id"]) for b in (await baris_jurnal(conn, tid, hq["hasil"].get("journal_id") if hq["ok"] else None) or [])}
    cek(f"{tid} quick adjust: lawan = pilihan (bukan bawaan)", hq["ok"] and sah_id in akun_q and bawaan not in akun_q and len(akun_q) == 2, (hq, akun_q))
    s0 = await sidik2()
    for nama, a in (("kas", kas), ("persediaan", persed)):
        if not a:
            continue
        ht = await coba(IT.create_stock_adjustment(req_json(tid, {"adjustment_type": "add", "quantity": 1, "reason": "UJI tolak",
                                                                  "counter_account_id": str(a)}), str(p)))
        cek(f"{tid} quick adjust tolak {nama} 422 berkode, nol tulisan", not ht["ok"] and ht["status"] == 422 and ht["code"] == "AKUN_LAWAN_DITOLAK"
            and s0 == await sidik2(), ht)
    # dokumen: draf -> pratinjau (pilihan sah/terlarang) -> posting terlarang ditolak -> posting pilihan -> batal (pembalik = akun yang sama)
    body = CreateStockAdjustmentRequest(adjustment_date="2026-11-09", adjustment_type="increase", notes="UJI pilihan",
                                        items=[{"product_id": str(p), "quantity_adjustment": 1}])
    sa_id = (await SA.create_stock_adjustment(req(tid, "POST"), body))["data"]["id"]
    s0 = await sidik2()
    pp = (await SA.preview_post_stock_adjustment(req(tid, "POST"), sa_id, Pos(counter_account_id=sah_id)))["data"]
    pk = (await SA.preview_post_stock_adjustment(req(tid, "POST"), sa_id, Pos(counter_account_id=str(kas))))["data"]
    cek(f"{tid} pratinjau pilihan sah memakai akun pilihan", pp["ok"] and sah_id in {str(x["account_id"]) for x in pp["jurnal"]}, pp)
    cek(f"{tid} pratinjau pilihan kas = blok AKUN_LAWAN_DITOLAK", not pk["ok"] and "AKUN_LAWAN_DITOLAK" in [b["code"] for b in pk.get("blocks", [])], pk)
    cek(f"{tid} pratinjau pilihan nol tulis", s0 == await sidik2())
    ht = await coba(SA.post_stock_adjustment(req(tid, "POST"), sa_id, None, Pos(counter_account_id=str(kas))))
    cek(f"{tid} posting pilihan kas 422 berkode, nol tulisan", not ht["ok"] and ht["status"] == 422 and ht["code"] == "AKUN_LAWAN_DITOLAK" and s0 == await sidik2(), ht)
    hp = await coba(SA.post_stock_adjustment(req(tid, "POST"), sa_id, None, Pos(counter_account_id=sah_id)))
    e = await efek(conn, tid, sa_id)
    cek(f"{tid} posting pilihan sah: jurnal == pratinjau", hp["ok"] and pp["ok"] and [(str(x["account_id"]), Decimal(x["debit"]), Decimal(x["credit"]))
                                                                              for x in pp["jurnal"]]
        == [(str(x["account_id"]), Decimal(x["debit"]), Decimal(x["credit"])) for x in e["jurnal"]], (hp, pp, e["jurnal"]))
    hv = await coba(SA.void_stock_adjustment(req(tid, "POST"), sa_id, VoidStockAdjustmentRequest(reason="UJI pilihan")))
    e = await efek(conn, tid, sa_id)
    cek(f"{tid} batal: pembalik memakai akun jurnal asli (pilihan)", hv["ok"] and {str(x["account_id"]) for x in e["jurnal"]}
        == {str(x["account_id"]) for x in e["pembalik"]} and sah_id in {str(x["account_id"]) for x in e["pembalik"]}, (hv, e))
    o["D_pilihan"] = {"sah": sah["account_code"], "kas": True}


async def quick3(conn, tid, o):
    """penyesuaian-3: quick adjust = penulis DOKUMEN (nomor SA-, gudang, daftar, batal), pagar minus/tanpa-stok, per gudang, set, alias akun, idempotensi,
    data lama QSA- tak berubah. Hanya pohon yang punya SA._buat_draf."""
    if not hasattr(SA, "_buat_draf"):
        return
    pr = await conn.fetch(
        """SELECT p.id FROM products p JOIN inventory_ledger il ON il.product_id = p.id AND il.tenant_id = p.tenant_id
           WHERE p.tenant_id = $1 AND p.item_type = 'goods' GROUP BY p.id HAVING SUM(il.quantity_in) - SUM(il.quantity_out) > 1
           ORDER BY p.id LIMIT 1""", tid)
    if not pr:
        o["D3_lewat"] = "barang berstok < 2"
        return
    p = str(pr[0]["id"])
    gud = await conn.fetch("SELECT id FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC, created_at", tid)
    utama_id = str(gud[0]["id"])

    async def sidik3():
        return [await conn.fetchval(f"SELECT md5(COALESCE(string_agg(x::text, '|' ORDER BY x.id), '')) FROM {t} x WHERE x.tenant_id = $1", tid)
                for t in ("journal_entries", "inventory_ledger", "stock_adjustments")]

    async def jml(sql):
        return await conn.fetchval(sql, tid)
    qsa0 = await jml("SELECT COUNT(*) FROM journal_entries WHERE tenant_id = $1 AND journal_number LIKE 'QSA-%'")
    n0 = await jml("SELECT COUNT(*) FROM stock_adjustments WHERE tenant_id = $1")
    # 1. add 2 -> dokumen posted bernomor SA- di gudang utama, jurnal tertaut, muncul di daftar
    h = await coba(IT.create_stock_adjustment(req_json(tid, {"adjustment_type": "add", "quantity": 2, "reason": "UJI q3 tambah"}), p))
    ok1 = h["ok"] and (h["hasil"].get("adjustment_id") is not None)
    doc = await conn.fetchrow("SELECT status, adjustment_number, warehouse_id, journal_id FROM stock_adjustments WHERE id = $1 AND tenant_id = $2",
                              h["hasil"]["adjustment_id"], tid) if ok1 else None
    cek(f"{tid} q3 add: dokumen posted bernomor SA- di gudang utama + jurnal tertaut", ok1 and doc and doc["status"] == "posted"
        and doc["adjustment_number"].startswith("SA-") and str(doc["warehouse_id"]) == utama_id and doc["journal_id"] is not None
        and str(doc["journal_id"]) == h["hasil"]["journal_id"] and h["hasil"]["warehouse_id"] == utama_id, (h, doc))
    cek(f"{tid} q3 add: jumlah dokumen +1", await jml("SELECT COUNT(*) FROM stock_adjustments WHERE tenant_id = $1") == n0 + 1)
    if ok1:
        led = [dict(r) for r in await conn.fetch(
            "SELECT quantity_in, quantity_out, warehouse_id, journal_id FROM inventory_ledger WHERE tenant_id = $1 AND source_id = $2", tid, h["hasil"]["adjustment_id"])]
        cek(f"{tid} q3 add: ledger masuk 2 di gudang utama, bertaut jurnal", len(led) == 1 and Decimal(led[0]["quantity_in"]) == 2 and str(led[0]["warehouse_id"]) == utama_id
            and str(led[0]["journal_id"]) == h["hasil"]["journal_id"], led)
        d = await SA.list_stock_adjustments(req(tid), status="all", adjustment_type=None, search=None, date_from=None, date_to=None, skip=0, limit=100,
                                            sort_by="created_at", sort_order="desc")
        cek(f"{tid} q3 add: muncul di Daftar Penyesuaian", h["hasil"]["adjustment_id"] in {x["id"] for x in d["items"]})
        for k in ("success", "message", "old_stock", "new_stock", "adjustment", "journal_id", "unit_cost", "cost_source"):
            cek(f"{tid} q3 respons lama tetap: {k}", k in h["hasil"])
        # 2. batal dokumen: pembalik + ledger kembali (Inv-1)
        hv = await coba(SA.void_stock_adjustment(req(tid, "POST"), h["hasil"]["adjustment_id"], VoidStockAdjustmentRequest(reason="UJI q3")))
        e = await efek(conn, tid, h["hasil"]["adjustment_id"])
        cek(f"{tid} q3 batal dokumen quick: pembalik ada, Inv-1 barang", hv["ok"] and e["pembalik"] is not None and await inv1(conn, tid, [pr[0]["id"]]) == 0, (hv, e))
    # 3. minus ditolak berkode tanpa tulisan
    s0 = await sidik3()
    hm = await coba(IT.create_stock_adjustment(req_json(tid, {"adjustment_type": "subtract", "quantity": 99999999, "reason": "UJI q3 minus"}), p))
    cek(f"{tid} q3 minus: 422 STOK_TIDAK_CUKUP tanpa tulisan", not hm["ok"] and hm["status"] == 422 and hm["code"] == "STOK_TIDAK_CUKUP" and s0 == await sidik3(), hm)
    # 4. set: stok gudang utama -> +3 ; adjustment 3
    sebelum = Decimal(str(await conn.fetchval("SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2::uuid "
                                              "AND warehouse_id = $3::uuid", tid, p, utama_id)))
    hs = await coba(IT.create_stock_adjustment(req_json(tid, {"adjustment_type": "set", "quantity": float(sebelum + 3), "reason": "UJI q3 set"}), p))
    cek(f"{tid} q3 set: selisih per gudang +3, stok baru == target", hs["ok"] and Decimal(hs["hasil"]["adjustment"]) == 3 and Decimal(hs["hasil"]["new_stock"]) == sebelum + 3, (hs, sebelum))
    # 5. gudang kedua (bila ada)
    if len(gud) >= 2:
        g2 = str(gud[1]["id"])
        hg = await coba(IT.create_stock_adjustment(req_json(tid, {"adjustment_type": "add", "quantity": 1, "reason": "UJI q3 gudang2", "warehouse_id": g2}), p))
        led2 = [dict(r) for r in await conn.fetch("SELECT warehouse_id FROM inventory_ledger WHERE tenant_id = $1 AND source_id = $2", tid, hg["hasil"]["adjustment_id"])] if hg["ok"] else []
        cek(f"{tid} q3 gudang kedua: dokumen + ledger di gudang itu", hg["ok"] and hg["hasil"]["warehouse_id"] == g2 and len(led2) == 1 and str(led2[0]["warehouse_id"]) == g2, (hg, led2))
    # 6. alias adjustment_account_id (nama yang dikirim FE form lama) dipakai sebagai akun lawan
    dd = (await SA.daftar_akun_lawan_penyesuaian(req(tid)))["data"]
    sah = next((a for a in dd["akun"] if not a["bawaan"]), None)
    if sah:
        ha = await coba(IT.create_stock_adjustment(req_json(tid, {"adjustment_type": "add", "quantity": 1, "reason": "UJI q3 alias", "adjustment_account_id": sah["id"]}), p))
        ak = {str(b["account_id"]) for b in (await baris_jurnal(conn, tid, ha["hasil"].get("journal_id") if ha["ok"] else None) or [])}
        cek(f"{tid} q3 alias adjustment_account_id jadi akun lawan", ha["ok"] and sah["id"] in ak and dd["bawaan_id"] not in ak, (ha, ak))
    # 7. idempotensi: kunci sama -> respons sama, satu dokumen
    n1 = await jml("SELECT COUNT(*) FROM stock_adjustments WHERE tenant_id = $1")
    hdr = [(b"x-idempotency-key", f"uji-q3-{tid}".encode())]
    b1 = await coba(IT.create_stock_adjustment(req_json(tid, {"adjustment_type": "add", "quantity": 1, "reason": "UJI q3 idem"}, hdr), p))
    b2 = await coba(IT.create_stock_adjustment(req_json(tid, {"adjustment_type": "add", "quantity": 1, "reason": "UJI q3 idem"}, hdr), p))
    cek(f"{tid} q3 idempotensi: replay sama, satu dokumen", b1["ok"] and b2["ok"] and b1["hasil"].get("adjustment_id") == b2["hasil"].get("adjustment_id")
        and await jml("SELECT COUNT(*) FROM stock_adjustments WHERE tenant_id = $1") == n1 + 1, (b1, b2))
    # 8. barang non-inventory ditolak berkode (bila tenant punya)
    ni = await conn.fetchval("SELECT id FROM products WHERE tenant_id = $1 AND (item_type <> 'goods' OR track_inventory IS FALSE) ORDER BY id LIMIT 1", tid)
    if ni:
        s0 = await sidik3()
        hn = await coba(IT.create_stock_adjustment(req_json(tid, {"adjustment_type": "add", "quantity": 1, "reason": "UJI q3 tanpa stok"}), str(ni)))
        cek(f"{tid} q3 non-inventory: 422 BARANG_TANPA_STOK tanpa tulisan", not hn["ok"] and hn["status"] == 422 and hn["code"] == "BARANG_TANPA_STOK" and s0 == await sidik3(), hn)
    # 9. data lama QSA- tak diubah (tak ada jurnal QSA- baru)
    cek(f"{tid} q3 jurnal QSA- lama tak berubah", await jml("SELECT COUNT(*) FROM journal_entries WHERE tenant_id = $1 AND journal_number LIKE 'QSA-%'") == qsa0)
    o["D3"] = {"produk": p, "gudang": len(gud), "non_inventory_diuji": bool(ni), "akun_alias_diuji": bool(sah)}


async def utama():
    pool = await SA.get_pool()
    out = {"baru": BARU, "pen2": hasattr(SA, "daftar_akun_lawan_penyesuaian"), "pen3": hasattr(SA, "_buat_draf")}  # pen2 = pembeda lama/baru sejak gelombang 2 LIVE (BARU sudah True di master)
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_persediaan_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            o = out[tid] = {}
            await baca(conn, tid, o)
            await tulis(conn, tid, o)
            await lawan(conn, tid, o)
            await quick3(conn, tid, o)
    out["CEK"] = CEK
    print("[info] penyesuaian-3: " + ", ".join(f"{t}: {out[t].get('D3') or out[t].get('D3_lewat') or 'BELUM JALAN'}" for t in TENANT), flush=True)
    print("[info] akun-pilihan: " + ", ".join(f"{t}: {out[t].get('D_pilihan') or out[t].get('D_lewat') or out[t].get('D_lewat_pilihan') or 'BELUM JALAN'}"
                                              for t in TENANT), flush=True)
    json.dump(out, open(os.environ["KELUAR"], "w"), sort_keys=True, indent=1)
    print("[info] " + ", ".join(f"{t}: {len(out[t]['A_sal'])} penyesuaian, {len(out[t]['A_whl'])} gudang, {out[t].get('C_lewat', 'tulis jalan')}"
                                for t in TENANT), flush=True)

asyncio.run(utama())
