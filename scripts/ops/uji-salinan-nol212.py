"""BUG-212 uji DB di SALINAN (bukan prod): handler ASLI stock_adjustments atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
A dokumen nilai 0 (barang tanpa harga pokok): pratinjau posting menyatakan tanpa jurnal + isyarat harga pokok; POST = tanpa jurnal (journal_id NULL, nol baris journal_entries), gerak stok TETAP tertulis ·
B pratinjau batal menyatakan tanpa jurnal pembalik + gerak cermin; void membalik gerak stok (ledger _VOID) TANPA jurnal pembalik, stok bersih kembali ·
C kontrol: dokumen bernilai > 0 tetap berjurnal (2 baris) dan batalnya tetap berjurnal pembalik + gerak terbalik."""
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


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        return e


def item(pid, qty):
    return S.StockAdjustmentItemCreate(product_id=str(pid), quantity_adjustment=D(str(qty)), physical_quantity=None, reason_detail="uji212")


async def siklus(c, rq, tid, pid, qty, berilai):
    """buat -> pratinjau posting -> post -> pratinjau batal -> void; kembalikan ringkasan keadaan DB."""
    ket = "bernilai" if berilai else "nilai 0"
    body = S.CreateStockAdjustmentRequest(adjustment_date=date.today(), adjustment_type="increase", notes="UJI212", items=[item(pid, qty)])
    r = await galat(SA.create_stock_adjustment(rq, body))
    if isinstance(r, HTTPException):
        cek(f"{ket} {tid}: buat draf", False, f"{r.status_code} {r.detail}")
        return
    sid = uuid.UUID(str(r["data"]["id"]))
    total = D(str(await c.fetchval("SELECT total_value FROM stock_adjustments WHERE tenant_id = $1 AND id = $2", tid, sid)))
    cek(f"{ket} {tid}: total dokumen {'> 0' if berilai else '== 0'}", (total > 0) == berilai, f"total={total}")
    pp = (await SA.preview_post_stock_adjustment(rq, sid))["data"]
    if berilai:
        cek(f"{ket} {tid}: pratinjau posting ok, ada jurnal, TANPA pernyataan tanpa jurnal", pp["ok"] is True and "tanpa_jurnal" not in pp and "jurnal_catatan" not in pp, f"{pp.get('blocks')}")
    else:
        cek(f"{ket} {tid}: pratinjau posting ok, tanpa_jurnal + 'Tanpa jurnal (nilai Rp 0).' + isyarat harga pokok",
            pp["ok"] is True and pp.get("tanpa_jurnal") is True and pp.get("jurnal_catatan") == "Tanpa jurnal (nilai Rp 0)."
            and any("belum punya harga pokok" in x for x in pp["isyarat"]) and pp["gerak"], f"{pp.get('blocks')} {pp.get('isyarat')}")
    po = await galat(SA.post_stock_adjustment(rq, sid))
    if isinstance(po, HTTPException):
        cek(f"{ket} {tid}: POST", False, f"{po.status_code} {po.detail}")
        return
    sa = await c.fetchrow("SELECT status, journal_id FROM stock_adjustments WHERE tenant_id = $1 AND id = $2", tid, sid)
    n_je = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_type = 'STOCK_ADJUSTMENT' AND source_id = $2", tid, sid)
    n_jl = await c.fetchval("SELECT count(*) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id WHERE je.tenant_id = $1 AND je.source_type = 'STOCK_ADJUSTMENT' AND je.source_id = $2", tid, sid)
    masuk = await c.fetchval("SELECT COALESCE(SUM(quantity_in), 0) FROM inventory_ledger WHERE tenant_id = $1 AND source_type = 'STOCK_ADJUSTMENT' AND source_id = $2", tid, sid)
    if berilai:
        cek(f"{ket} {tid}: POST berjurnal (2 baris), gerak stok tertulis", sa["status"] == "posted" and sa["journal_id"] is not None and po["data"]["journal_id"] and n_je == 1 and n_jl == 2 and D(str(masuk)) == D(str(qty)),
            f"je={n_je} jl={n_jl} masuk={masuk}")
    else:
        cek(f"{ket} {tid}: POST TANPA jurnal (journal_id NULL, respons None, nol journal_entries), gerak stok TETAP tertulis",
            sa["status"] == "posted" and sa["journal_id"] is None and po["data"]["journal_id"] is None and po["data"]["journal_number"] is None and n_je == 0 and D(str(masuk)) == D(str(qty)),
            f"status={sa['status']} jid={sa['journal_id']} je={n_je} masuk={masuk}")
    pv = (await SA.preview_void_stock_adjustment(rq, sid, S.VoidStockAdjustmentPreviewRequest(reason="uji212")))["data"]
    if berilai:
        cek(f"{ket} {tid}: pratinjau batal ada jurnal pembalik, tanpa pernyataan tanpa jurnal", pv["ok"] is True and "tanpa_jurnal" not in pv and pv["gerak"], f"{pv.get('blocks')}")
    else:
        cek(f"{ket} {tid}: pratinjau batal menyatakan tanpa jurnal pembalik + gerak cermin ada",
            pv["ok"] is True and pv.get("tanpa_jurnal") is True and pv.get("jurnal_catatan") == SS.TEKS_TANPA_JURNAL_BATAL and len(pv["gerak"]) == 1 and D(str(pv["gerak"][0]["keluar"])) == D(str(qty)),
            f"{pv.get('blocks')} {pv.get('gerak')}")
    vo = await galat(SA.void_stock_adjustment(rq, sid, S.VoidStockAdjustmentRequest(reason="uji212")))
    if isinstance(vo, HTTPException):
        cek(f"{ket} {tid}: void", False, f"{vo.status_code} {vo.detail}")
        return
    st = await c.fetchval("SELECT status FROM stock_adjustments WHERE tenant_id = $1 AND id = $2", tid, sid)
    n_je2 = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_type = 'STOCK_ADJUSTMENT' AND source_id = $2", tid, sid)
    keluar = await c.fetchval("SELECT COALESCE(SUM(quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND source_type = 'STOCK_ADJUSTMENT_VOID' AND source_id = $2", tid, sid)
    bersih = await c.fetchval("SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND source_id = $2", tid, sid)
    if berilai:
        rev = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND reversal_of_id = $2", tid, sa["journal_id"])
        cek(f"{ket} {tid}: void berjurnal pembalik + gerak stok terbalik (bersih 0)", st == "void" and vo["data"]["reversal_journal_id"] and rev == 1 and D(str(bersih)) == 0, f"rev={rev} bersih={bersih}")
    else:
        cek(f"{ket} {tid}: void TANPA jurnal pembalik, gerak stok TERBALIK (ledger _VOID keluar = qty, bersih 0)",
            st == "void" and vo["data"]["reversal_journal_id"] is None and n_je2 == 0 and D(str(keluar)) == D(str(qty)) and D(str(bersih)) == 0,
            f"status={st} je={n_je2} keluar_void={keluar} bersih={bersih}")


async def main():
    pool = await SA.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            uid = await c.fetchval("SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            gud = await c.fetchval("SELECT id FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC, created_at LIMIT 1", tid)
            nol = await c.fetchval("SELECT p.id FROM products p WHERE p.tenant_id = $1 AND p.item_type = 'goods' AND COALESCE(p.track_inventory, true) AND NOT EXISTS (SELECT 1 FROM inventory_ledger l WHERE l.tenant_id = p.tenant_id AND l.product_id = p.id) ORDER BY p.created_at LIMIT 1", tid)
            bernilai = await c.fetchval("SELECT p.id FROM products p JOIN inventory_ledger l ON l.product_id = p.id AND l.tenant_id = p.tenant_id WHERE p.tenant_id = $1 AND p.item_type = 'goods' AND COALESCE(p.track_inventory, true) AND l.average_cost > 0 ORDER BY p.created_at LIMIT 1", tid)
            cek(f"prasyarat {tid}: pemilik, gudang, barang tanpa riwayat ledger", uid is not None and gud is not None and nol is not None)
            if uid is None or gud is None:
                continue
            rq = req(tid, uid)
            if nol is not None:
                await c.execute("UPDATE products SET purchase_price = 0 WHERE tenant_id = $1 AND id = $2", tid, nol)
                await siklus(c, rq, tid, nol, 3, False)
            if bernilai is not None:
                await siklus(c, rq, tid, bernilai, 1, True)
            else:
                print(f"CATATAN {tid}: tak ada barang ber-WAC > 0 di klon; kontrol bernilai tak terukur", flush=True)
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
