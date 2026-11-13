"""BUG-204 uji DB di SALINAN (bukan prod): handler ASLI stock_adjustments (preview buat/ubah, POST, PATCH, post) atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
A tiap pelanggaran (tanda tak cocok per jenis, selisih 0, hitung ulang tanpa fisik / fisik negatif / fisik = stok, barang ganda, barang non-stok): PRATINJAU buat memblok dgn kode X;
POST menolak dgn kode + kalimat SAMA; nol dokumen baru · B dokumen sah (increase +, decrease −, recount fisik) lolos pratinjau dan tulis · C PATCH items melanggar: edit/preview memblok, PATCH menolak sama,
baris dokumen tak berubah · D harga pokok 0: isyarat di pratinjau, boleh lanjut; INFO ukur POSTING nilai 0 · E INFO COUNT dokumen LAMA yang melanggar (tenant uji; tak diubah)."""
import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal as D

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import stock_adjustments as SA  # noqa: E402
from app.schemas import stock_adjustments as S  # noqa: E402
from app.services import stok_sesuai as SS  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []
SQL_LAMA = """
WITH baris AS (
  SELECT sa.tenant_id, sa.id AS sa_id, sa.status, sa.adjustment_type AS jenis, i.product_id, i.quantity_adjustment AS qty
  FROM stock_adjustments sa JOIN stock_adjustment_items i ON i.stock_adjustment_id = sa.id WHERE sa.tenant_id = $1
), ganda AS (SELECT sa_id FROM baris GROUP BY sa_id, product_id HAVING count(*) > 1),
nonstok AS (SELECT DISTINCT b.sa_id FROM baris b JOIN products p ON p.id = b.product_id AND p.tenant_id = b.tenant_id WHERE NOT (p.item_type = 'goods' AND COALESCE(p.track_inventory, true))),
tanda AS (SELECT DISTINCT sa_id FROM baris WHERE (jenis = 'increase' AND qty < 0) OR (jenis IN ('decrease', 'damaged', 'expired') AND qty > 0)),
nol AS (SELECT DISTINCT sa_id FROM baris WHERE qty = 0)
SELECT (SELECT count(DISTINCT sa_id) FROM baris) AS total, (SELECT count(*) FROM tanda) AS tanda, (SELECT count(*) FROM nol) AS nol,
       (SELECT count(*) FROM ganda) AS ganda, (SELECT count(*) FROM nonstok) AS nonstok"""


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


def it(pid, qty, fisik=None):
    return S.StockAdjustmentItemCreate(product_id=str(pid), quantity_adjustment=D(str(qty)), physical_quantity=None if fisik is None else D(str(fisik)), reason_detail="uji204")


def iu(pid, qty, fisik=None):
    return S.StockAdjustmentItemUpdate(product_id=str(pid), quantity_adjustment=D(str(qty)), physical_quantity=None if fisik is None else D(str(fisik)), reason_detail="uji204")


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        return e


async def main():
    pool = await SA.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            uid = await c.fetchval("SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            prods = await c.fetch("SELECT id, nama_produk FROM products WHERE tenant_id = $1 AND item_type = 'goods' AND COALESCE(track_inventory, true) ORDER BY created_at LIMIT 3", tid)
            gud = await c.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC, created_at LIMIT 1", tid)
            cek(f"prasyarat {tid}: pemilik, >= 2 barang ber-stok, gudang", uid is not None and len(prods) >= 2 and gud is not None)
            if uid is None or len(prods) < 2 or gud is None:
                continue
            rq = req(tid, uid)
            lama = await c.fetchrow(SQL_LAMA, tid)
            print(f"INFO E {tid} dokumen LAMA (tak diubah): total={lama['total']} tanda_tak_cocok={lama['tanda']} selisih_nol={lama['nol']} ganda={lama['ganda']} non_stok={lama['nonstok']}", flush=True)
            p1, p2 = prods[0]["id"], prods[1]["id"]
            stok = D(str(await SS.stok_gudang(c, tid, p1, gud)))
            n_dok = lambda: c.fetchval("SELECT count(*) FROM stock_adjustments WHERE tenant_id = $1", tid)

            async def buat_body(tipe, items):
                return S.CreateStockAdjustmentRequest(adjustment_date=date.today(), adjustment_type=tipe, notes="UJI204", items=items)
            # non-stok: p2 dijadikan tidak melacak stok DI KLON
            await c.execute("UPDATE products SET track_inventory = false WHERE tenant_id = $1 AND id = $2", tid, p2)
            kasus = [("increase bertanda −", "increase", [it(p1, -2)], "PENYESUAIAN_TANDA_TIDAK_COCOK"), ("decrease bertanda +", "decrease", [it(p1, 2)], "PENYESUAIAN_TANDA_TIDAK_COCOK"),
                     ("damaged bertanda +", "damaged", [it(p1, 1)], "PENYESUAIAN_TANDA_TIDAK_COCOK"), ("expired bertanda +", "expired", [it(p1, 1)], "PENYESUAIAN_TANDA_TIDAK_COCOK"),
                     ("selisih 0", "increase", [it(p1, 0)], "PENYESUAIAN_SELISIH_NOL"), ("hitung ulang tanpa fisik", "recount", [it(p1, 0)], "PENYESUAIAN_FISIK_WAJIB"),
                     ("hitung ulang fisik negatif", "recount", [it(p1, 0, -1)], "PENYESUAIAN_FISIK_NEGATIF"), ("hitung ulang fisik = stok sistem", "recount", [it(p1, 0, stok)], "PENYESUAIAN_SELISIH_NOL"),
                     ("barang ganda", "increase", [it(p1, 1), it(p1, 2)], "PENYESUAIAN_BARANG_GANDA"), ("barang non-stok", "increase", [it(p2, 1)], "PENYESUAIAN_BARANG_NON_STOK")]
            for nama, tipe, items, kode in kasus:
                body = await buat_body(tipe, items)
                pr = (await SA.preview_create_stock_adjustment(rq, body))["data"]
                sebelum = await n_dok()
                g = await galat(SA.create_stock_adjustment(rq, body))
                cocok = isinstance(g, HTTPException) and getattr(g, "code", None) == kode and pr["blocks"] and pr["blocks"][0]["code"] == kode and g.detail == pr["blocks"][0]["message"]
                cek(f"A {tid} {nama}: pratinjau blok {kode}; POST menolak kode + kalimat sama; nol dokumen baru", pr["ok"] is False and cocok and await n_dok() == sebelum,
                    f"blocks={[b['code'] for b in pr['blocks']]} galat={getattr(g, 'code', g)}")
            # B dokumen sah
            sah = [("increase +", "increase", [it(p1, 2)]), ("decrease −", "decrease", [it(p1, -1)]), ("recount fisik", "recount", [it(p1, 0, stok + 3)])]
            ids = []
            for nama, tipe, items in sah:
                body = await buat_body(tipe, items)
                pr = (await SA.preview_create_stock_adjustment(rq, body))["data"]
                r = await galat(SA.create_stock_adjustment(rq, body))
                ok = pr["ok"] is True and not isinstance(r, HTTPException)
                if ok:
                    ids.append(uuid.UUID(str(r["data"]["id"])))
                cek(f"B {tid} dokumen sah ({nama}): pratinjau ok, tulis berhasil", ok, f"{pr['blocks']} {r if isinstance(r, HTTPException) else ''}")
            # C PATCH melanggar
            if ids:
                sid = ids[0]
                baris_sblm = [dict(x) for x in await c.fetch("SELECT i.product_id, i.quantity_adjustment FROM stock_adjustment_items i JOIN stock_adjustments sa ON sa.id = i.stock_adjustment_id AND sa.tenant_id = $2 WHERE i.stock_adjustment_id = $1 ORDER BY i.line_number", sid, tid)]
                ub = S.UpdateStockAdjustmentRequest(items=[iu(p1, -5)])        # dokumen jenis increase
                pe = (await SA.preview_update_stock_adjustment(rq, sid, ub))["data"]
                gp = await galat(SA.update_stock_adjustment(rq, sid, ub))
                baris_ssd = [dict(x) for x in await c.fetch("SELECT i.product_id, i.quantity_adjustment FROM stock_adjustment_items i JOIN stock_adjustments sa ON sa.id = i.stock_adjustment_id AND sa.tenant_id = $2 WHERE i.stock_adjustment_id = $1 ORDER BY i.line_number", sid, tid)]
                cek(f"C {tid} PATCH tanda salah: edit/preview blok, PATCH menolak sama, baris tak berubah", pe["ok"] is False and isinstance(gp, HTTPException) and getattr(gp, "code", None) == pe["blocks"][0]["code"] == "PENYESUAIAN_TANDA_TIDAK_COCOK"
                    and gp.detail == pe["blocks"][0]["message"] and baris_sblm == baris_ssd, f"{pe['blocks']} {getattr(gp, 'code', gp)}")
                ok_patch = await galat(SA.update_stock_adjustment(rq, sid, S.UpdateStockAdjustmentRequest(items=[iu(p1, 4)])))
                cek(f"C {tid} PATCH items sah: berhasil", not isinstance(ok_patch, HTTPException), str(ok_patch))
            # D harga pokok 0
            nol = await c.fetchval("SELECT p.id FROM products p WHERE p.tenant_id = $1 AND p.item_type = 'goods' AND COALESCE(p.track_inventory, true) AND NOT EXISTS (SELECT 1 FROM inventory_ledger l WHERE l.tenant_id = p.tenant_id AND l.product_id = p.id) ORDER BY p.created_at LIMIT 1", tid)
            if nol is None:
                print(f"INFO D {tid}: tak ada barang tanpa riwayat ledger di klon; isyarat harga 0 hanya diuji unit; posting nilai 0 tak terukur", flush=True)
            else:
                await c.execute("UPDATE products SET purchase_price = 0 WHERE tenant_id = $1 AND id = $2", tid, nol)
                body = await buat_body("increase", [it(nol, 3)])
                pr = (await SA.preview_create_stock_adjustment(rq, body))["data"]
                r = await galat(SA.create_stock_adjustment(rq, body))
                cek(f"D {tid} harga pokok 0: pratinjau ok + isyarat 'belum punya harga pokok', tulis boleh", pr["ok"] is True and any("belum punya harga pokok" in x for x in pr["isyarat"]) and not isinstance(r, HTTPException), f"{pr['isyarat']} {pr['blocks']}")
                if not isinstance(r, HTTPException):
                    sid0 = uuid.UUID(str(r["data"]["id"]))
                    po = await galat(SA.post_stock_adjustment(rq, sid0))
                    jr = await c.fetchrow("SELECT je.id, (SELECT count(*) FROM journal_lines jl WHERE jl.journal_id = je.id) AS n FROM stock_adjustments sa LEFT JOIN journal_entries je ON je.id = sa.journal_id AND je.tenant_id = sa.tenant_id WHERE sa.tenant_id = $1 AND sa.id = $2", tid, sid0)
                    print(f"INFO D {tid} POSTING nilai 0: hasil={'GALAT ' + str(getattr(po, 'status_code', '')) + ' ' + str(getattr(po, 'detail', '')) if isinstance(po, HTTPException) else 'berhasil'}; jurnal={dict(jr) if jr else None}", flush=True)
            await c.execute("UPDATE products SET track_inventory = true WHERE tenant_id = $1 AND id = $2", tid, p2)
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
