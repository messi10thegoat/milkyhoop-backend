"""terbit-pratinjau-1 uji di SALINAN, handler ASLI. SIAPKAN=1 (pohon LAMA, sekali, HANYA klon): draf faktur ber-stok tenant uji diurut id;
indeks genap -> warehouse_stock barangnya di gudang posting di-NOL-kan (kekurangan buatan), ganjil -> dicukupkan (qty dasar faktur + 1).
Cek (pohon BARU):
 E  setiap draf: rencana_terbit_ro blok STOK_KURANG  <=>  penulis (simulasi posting savepoint+rollback) menolak dengan 409 "Stok ..."; kalimat IDENTIK.
 J  preview-journal {invoice_id}: ditolak -> ok:false + blok, NOL baris jurnal, tanpa "Pelanggan belum dipilih"; lolos -> ok:true + baris jurnal.
 K  cakupan: ada >=1 draf kurang DAN >=1 draf cukup.
 L  (lama vs baru, dibanding di luar) draf lolos: journal_lines lama == baru."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

from fastapi import HTTPException

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import sales_invoices as SI  # noqa: E402
from app.services import faktur_terbit_rencana as FTR  # noqa: E402
from app.services import gudang_bawaan as GB  # noqa: E402
from app.services import satuan_dasar as SD  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
BARU = hasattr(FTR, "PENANDA_TERBIT_PRATINJAU")
CEK, OUT = [], {"jurnal": {}, "draf": {}}


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:600]])


def req(tid, uid):
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": tid, "user_id": uid}))


async def pemilik(conn, tid):
    return str(await conn.fetchval("""SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id
                                      WHERE utr.tenant_id = $1 AND r.code = 'OWNER' AND lower(utr.status) = 'active'
                                      ORDER BY utr.user_id LIMIT 1""", tid))


async def draf_berstok(conn, tid):
    return [r["id"] for r in await conn.fetch(
        """SELECT DISTINCT si.id FROM sales_invoices si JOIN sales_invoice_items it ON it.invoice_id = si.id
           JOIN products p ON p.id = it.item_id AND p.tenant_id = si.tenant_id
           WHERE si.tenant_id = $1 AND si.status = 'draft' AND COALESCE(p.track_inventory, false) ORDER BY si.id""", tid)]


async def siapkan(conn):
    n = 0
    for tid in TENANT:
        for k, iid in enumerate(await draf_berstok(conn, tid)):
            inv = await conn.fetchrow("SELECT warehouse_id FROM sales_invoices WHERE id = $1 AND tenant_id = $2", iid, tid)
            g = inv["warehouse_id"] or (await GB.bawaan(conn, tid) or {}).get("id")
            if g is None:
                continue
            rows = await conn.fetch("""SELECT it.item_id, it.unit, it.quantity FROM sales_invoice_items it JOIN sales_invoices si ON si.id = it.invoice_id
                                      WHERE it.invoice_id = $1 AND si.tenant_id = $2 AND it.item_id IS NOT NULL""", iid, tid)
            ktx = await SD.muat(conn, tid, [r["item_id"] for r in rows])
            for r in rows:
                h = SD.hitung(ktx, r["item_id"], r["unit"], r["quantity"], "konversi")
                q = 0 if k % 2 == 0 else (h["qty_dasar"] if not h["blok"] else r["quantity"]) + 1
                await conn.execute("""INSERT INTO warehouse_stock (tenant_id, warehouse_id, item_id, quantity) VALUES ($1, $2, $3, $4)
                                      ON CONFLICT (warehouse_id, item_id) DO UPDATE SET quantity = EXCLUDED.quantity""", tid, g, r["item_id"], q)
            n += 1
    print(f"[siapkan] {n} draf disetel stoknya (HANYA di klon)")


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if os.environ.get("SIAPKAN") == "1":
            async with conn.transaction():
                await siapkan(conn)
        owner = {tid: await pemilik(conn, tid) for tid in TENANT}
        draf = {tid: await draf_berstok(conn, tid) for tid in TENANT}
    kurang = cukup = 0
    for tid in TENANT:
        rq = req(tid, owner[tid])
        ctx = SI.get_user_context(rq)
        for iid in draf[tid]:
            pj = await SI.preview_journal(rq, {"invoice_id": str(iid)})
            OUT["jurnal"][f"{tid}|{iid}"] = json.loads(json.dumps(pj.get("journal_lines"), default=str)) if pj.get("ok", True) else None
            if not BARU:
                continue
            try:
                await SI._pratinjau_dari_posting(pool, ctx, iid)
                tolak = None
            except HTTPException as e:
                tolak = e.detail if isinstance(e.detail, str) else (e.detail or {}).get("message")
            async with pool.acquire() as conn:
                async with conn.transaction(readonly=True):
                    r = await FTR.rencana_terbit_ro(conn, ctx, iid)
            stok_ro = [b["message"] for b in r["blocks"] if b["code"] == "STOK_KURANG"]
            stok_tulis = tolak if tolak and tolak.startswith("Stok ") else None
            cek(f"E {tid} {iid} pratinjau==penulis", (stok_ro[:1] == [stok_tulis]) if stok_tulis else not stok_ro,
                f"ro {stok_ro} tulis {tolak}")
            if tolak:
                ok = (pj["ok"] is False and pj["journal_lines"] == [] and pj["blocks"] and pj["blocks"][0]["message"] == tolak
                      and "Pelanggan belum dipilih" not in json.dumps(pj))
                kurang += bool(stok_tulis)
            else:
                ok = pj["ok"] is True and pj["blocks"] == [] and len(pj["journal_lines"]) > 0 and any(
                    (x["debit"] or x["credit"]) for x in pj["journal_lines"])
                cukup += 1
            cek(f"J {tid} {iid} preview-journal", ok, json.dumps(pj, default=str)[:500])
            OUT["draf"][f"{tid}|{iid}"] = {"tolak": tolak, "blok_ro": [b["code"] for b in r["blocks"]]}
    if BARU:
        cek(f"K cakupan kurang={kurang} cukup={cukup}", kurang > 0 and cukup > 0, "cakupan kurang")


asyncio.run(utama())
json.dump({"cek": CEK, **OUT}, open(os.environ["KELUAR"], "w"), default=str)
print(f"[{'baru' if BARU else 'lama'}] cek {sum(1 for c in CEK if c[1])}/{len(CEK)}, draf {len(OUT['jurnal'])}")
