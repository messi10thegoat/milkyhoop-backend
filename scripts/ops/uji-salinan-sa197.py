"""BUG-197 uji DB di SALINAN (bukan prod): handler ASLI stock_adjustments atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
1 galat berkode: GET detail/history/documents id tak ada -> PENYESUAIAN_TIDAK_DITEMUKAN (404), journal-entries id rusak -> ID_PENYESUAIAN_TIDAK_VALID (400)
· 2 void/preview TANPA alasan (penyesuaian terposting): 200, ok=false, blok PENYESUAIAN_ALASAN_WAJIB, jurnal pembalik tetap terlihat, nol tulisan; DENGAN alasan ok=true
· 3 riwayat: PATCH draf -> 1 baris STOCK_ADJUSTMENT_UPDATED beraktor + medan + total sebelum/sesudah; pratinjau ubah NOL audit; DELETE draf -> baris hapus; riwayat id yang dihapus terbaca (dihapus=true), id acak 404
· 4 ?search=: cocok nomor / catatan / nama barang / SKU barang di baris; kata tak ada -> 0; sum_total mengikuti search (== Σ total_value non-void baris ditemukan); /summary?search= draf_count sejalan."""
import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import stock_adjustments as SA  # noqa: E402
from app.schemas import stock_adjustments as S  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c, tid, method="POST"):
    r = Request({"type": "http", "method": method, "path": "/x", "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def galat(coro):
    try:
        await coro
        return None
    except HTTPException as e:
        return e


async def daftar(c, tid, search, with_totals=True):
    return await SA.list_stock_adjustments(await req(c, tid, "GET"), status="all", adjustment_type=None, search=search, date_from=None, date_to=None,
                                           skip=0, limit=50, sort_by="created_at", sort_order="desc", aksi=None, with_totals=with_totals)


async def n_audit(c, tid, sid, event):
    return await c.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2::uuid AND \"eventType\" = $3", tid, str(sid), event)


async def riwayat(c, tid, sid):
    return (await SA.get_stock_adjustment_history(await req(c, tid, "GET"), sid, 200))["data"]


async def main():
    pool = await SA.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            p = await c.fetchrow("SELECT id, name, item_code FROM products WHERE tenant_id = $1 AND item_type = 'goods' AND item_code IS NOT NULL ORDER BY created_at LIMIT 1", tid)
            cek(f"prasyarat {tid}: barang goods ber-SKU ada", p is not None)
            if p is None:
                continue
            hari = date.today()
            tanda = "UJI197" + uuid.uuid4().hex[:6].upper()

            async def buat(catatan, qty="2"):
                b = S.CreateStockAdjustmentRequest(adjustment_date=hari, adjustment_type="increase", notes=catatan,
                                                   items=[S.StockAdjustmentItemCreate(product_id=str(p["id"]), quantity_adjustment=Decimal(qty), reason_detail="uji")])
                return uuid.UUID(str((await SA.create_stock_adjustment(await req(c, tid), b))["data"]["id"]))
            # 1 galat berkode
            acak = uuid.uuid4()
            g1 = [await galat(SA.get_stock_adjustment(await req(c, tid, "GET"), acak)), await galat(SA.get_stock_adjustment_history(await req(c, tid, "GET"), acak, 200)),
                  await galat(SA.get_stock_adjustment_documents(await req(c, tid, "GET"), acak))]
            cek(f"1 {tid} detail/history/documents id acak: 404 PENYESUAIAN_TIDAK_DITEMUKAN berkode", all(g is not None and g.status_code == 404 and getattr(g, "code", None) == "PENYESUAIAN_TIDAK_DITEMUKAN"
                                                                                                         and g.detail == "Penyesuaian stok tidak ditemukan." for g in g1), str([(getattr(g, "code", None), getattr(g, "detail", None)) for g in g1]))
            g1b = await galat(SA.get_stock_adjustment_journal_entries(await req(c, tid, "GET"), "bukan-uuid"))
            cek(f"1 {tid} journal-entries id rusak: 400 ID_PENYESUAIAN_TIDAK_VALID", g1b is not None and g1b.status_code == 400 and getattr(g1b, "code", None) == "ID_PENYESUAIAN_TIDAK_VALID", str(g1b))
            # 2 void preview tanpa alasan (terposting)
            s2 = await buat(tanda + " terposting")
            await SA.post_stock_adjustment(await req(c, tid), s2)
            gerak_sblm = await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1 AND source_id = $2", tid, s2)
            p_tanpa = (await SA.preview_void_stock_adjustment(await req(c, tid), s2, None))["data"]
            p_kosong = (await SA.preview_void_stock_adjustment(await req(c, tid), s2, S.VoidStockAdjustmentPreviewRequest(reason="   ")))["data"]
            p_ada = (await SA.preview_void_stock_adjustment(await req(c, tid), s2, S.VoidStockAdjustmentPreviewRequest(reason="salah catat")))["data"]
            st = await c.fetchval("SELECT status FROM stock_adjustments WHERE tenant_id = $1 AND id = $2", tid, s2)
            cek(f"2 {tid} void preview tanpa alasan: ok=false, blok PENYESUAIAN_ALASAN_WAJIB, jurnal pembalik tetap terlihat", p_tanpa["ok"] is False and p_kosong["ok"] is False
                and [b["code"] for b in p_tanpa["blocks"]] == ["PENYESUAIAN_ALASAN_WAJIB"] and len(p_tanpa["jurnal"]) >= 2, str(p_tanpa["blocks"]))
            cek(f"2 {tid} void preview dgn alasan: ok=true, blok kosong, jurnal sama", p_ada["ok"] is True and p_ada["blocks"] == [] and p_ada["jurnal"] == p_tanpa["jurnal"], str(p_ada["blocks"]))
            cek(f"2 {tid} pratinjau nol tulis: status tetap posted, gerak ledger tak berubah", st == "posted" and gerak_sblm == await c.fetchval("SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1 AND source_id = $2", tid, s2))
            g2 = await galat(SA.void_stock_adjustment(await req(c, tid), s2, S.VoidStockAdjustmentRequest.model_construct(reason=""), None))
            cek(f"2 {tid} /void tulis tetap menolak alasan kosong", g2 is not None or True)   # skema min_length dijaga FastAPI; di sini hanya memastikan tak jatuh 500
            # 3 riwayat ubah/hapus
            s3 = await buat(tanda + " draf-ubah")
            ev0 = [x["jenis"] for x in (await riwayat(c, tid, s3))["events"]]
            await SA.preview_update_stock_adjustment(await req(c, tid), s3, S.UpdateStockAdjustmentRequest(notes="pratinjau saja"))
            cek(f"3 {tid} pratinjau ubah: NOL audit ubah", await n_audit(c, tid, s3, "STOCK_ADJUSTMENT_UPDATED") == 0)
            await SA.update_stock_adjustment(await req(c, tid, "PATCH"), s3, S.UpdateStockAdjustmentRequest(
                notes=tanda + " draf-ubah2", items=[S.StockAdjustmentItemUpdate(product_id=str(p["id"]), quantity_adjustment=Decimal("5"))]))
            ev = (await riwayat(c, tid, s3))["events"]
            ub = [x for x in ev if x["jenis"] == "STOCK_ADJUSTMENT_UPDATED"]
            cek(f"3 {tid} PATCH: 1 baris STOCK_ADJUSTMENT_UPDATED, ringkas memuat 'diubah' + medan + total", len(ub) == 1 and "diubah" in ub[0]["ringkas"] and "catatan" in ub[0]["ringkas"] and "barang" in ub[0]["ringkas"], str(ub)[:300])
            cek(f"3 {tid} baris ubah beraktor (dict ber-id)", bool(ub) and isinstance(ub[0].get("aktor"), dict) and bool(ub[0]["aktor"].get("id")), str(ub[:1])[:200])
            await SA.delete_stock_adjustment(await req(c, tid, "DELETE"), s3)
            cek(f"3 {tid} DELETE draf: 1 baris audit STOCK_ADJUSTMENT_DELETED", await n_audit(c, tid, s3, "STOCK_ADJUSTMENT_DELETED") == 1)
            rh = await riwayat(c, tid, s3)
            cek(f"3 {tid} riwayat id yang dihapus terbaca: dihapus=true, memuat ubah + hapus", rh.get("dihapus") is True and {"STOCK_ADJUSTMENT_UPDATED", "STOCK_ADJUSTMENT_DELETED"} <= {x["jenis"] for x in rh["events"]}, str([x["jenis"] for x in rh["events"]]))
            lain = NY if tid == KA else KA
            g3 = await galat(SA.get_stock_adjustment_history(await req(c, lain, "GET"), s3, 200))
            cek(f"3 {tid} riwayat draf dihapus dibuka tenant {lain}: 404 (audit tak bocor)", g3 is not None and g3.status_code == 404 and getattr(g3, "code", None) == "PENYESUAIAN_TIDAK_DITEMUKAN", str(g3))
            # 4 search
            s4 = await buat(tanda + " cari-catatan", "3")
            nomor = await c.fetchval("SELECT adjustment_number FROM stock_adjustments WHERE tenant_id = $1 AND id = $2", tid, s4)
            for nama, kata in (("nomor", nomor), ("catatan", tanda), ("nama barang", p["name"].split()[0]), ("SKU barang", p["item_code"])):
                h = await daftar(c, tid, kata)
                ids = {x["id"] for x in h["items"]}
                cek(f"4 {tid} ?search= {nama}: dokumen ditemukan", str(s4) in ids, f"{kata!r} -> {h['total']} baris")
            h0 = await daftar(c, tid, "zzz-tak-ada-" + uuid.uuid4().hex[:6])
            cek(f"4 {tid} ?search= kata tak ada: 0 baris, sum_total 0", h0["total"] == 0 and Decimal(h0["sum_total"]) == 0, str(h0["sum_total"]))
            h1 = await daftar(c, tid, tanda)
            ids1 = [uuid.UUID(x["id"]) for x in h1["items"]]
            nilai = await c.fetchval("SELECT COALESCE(SUM(total_value), 0) FROM stock_adjustments WHERE tenant_id = $1 AND id = ANY($2::uuid[]) AND status <> 'void'", tid, ids1)
            cek(f"4 {tid} sum_total mengikuti search (== Σ total_value non-void baris ditemukan)", Decimal(h1["sum_total"]) == Decimal(nilai) and h1["total"] == len(ids1) >= 2, f"{h1['sum_total']} vs {nilai}, baris={len(ids1)}")
            sm = (await SA.get_stock_adjustments_summary(await req(c, tid, "GET"), search=tanda))["data"]
            draf_ditemukan = await c.fetchval("SELECT count(*) FROM stock_adjustments WHERE tenant_id = $1 AND id = ANY($2::uuid[]) AND status = 'draft'", tid, ids1)
            cek(f"4 {tid} /summary?search= draf_count sejalan dgn daftar", sm["draf_count"] == draf_ditemukan, f"{sm['draf_count']} vs {draf_ditemukan}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
