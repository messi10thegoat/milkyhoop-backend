"""BUG-261 uji DB di SALINAN (DRAF, belum pernah dijalankan; perbaiki di slot): V503 sudah diterapkan di klon (oleh pembungkus .sh, 2x idempoten). Semua penulisan di SATU transaksi yang di-ROLLBACK.
Skenario per tenant (kaos + nyawit) dengan PENULIS ASLI: pemindahan 4 unit satu barang (PB.buat_draf -> D2.kirim -> D2.terima 3) -> tertunda 1 di Transit;
A tertunda (item_baris) == 1, stok Transit bertambah 1 (relatif); daftar tertunda memuat pemindahan itu
B tarik_pemindahan: ok, 1 baris, qty -1, gudang Transit terkunci, alasan 'Hilang (selisih terima <no>)'
C buat draf penyesuaian (RA._buat_draf, penentu SS.rencana_buat) baris bertaut: kolom warehouse_id=Transit + source_transfer_id TERSIMPAN (SQL independen)
D posting (SS.rencana_posting kunci=True + RA._tulis_posting): ledger STOCK_ADJUSTMENT keluar 1 di gudang TRANSIT (bukan gudang dokumen); stok Transit kembali ke awal; tertunda == 0; teks daftar 'Diterima · selisih diselesaikan'; daftar tertunda tak memuatnya
E draf kedua qty 1 (tertunda 0) -> blok PENYESUAIAN_MELEBIHI_TERTUNDA (pratinjau); Terima 1 lagi -> blok PB_TERIMA_QTY_MELEBIHI; void kirim -> blok PB_SUDAH_ADA_PENYELESAIAN
F status penyesuaian 'void' (disimulasikan SQL) -> tertunda kembali 1 (void tak terhitung); kembali 'posted' -> 0
G gudang per baris: baris biasa dgn warehouse_id gudang lain -> stok sebelum dihitung per gudang itu (SQL independen)"""
import asyncio
import sys
from decimal import Decimal as D

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import stock_adjustments as RA  # noqa: E402
from app.schemas.stock_adjustments import CreateStockAdjustmentRequest, StockAdjustmentItemCreate  # noqa: E402
from app.services import gudang_transit as GT  # noqa: E402
from app.services import pemindahan_barang as PB  # noqa: E402
from app.services import pemindahan_dua_langkah as D2  # noqa: E402
from app.services import stok_sesuai as SS  # noqa: E402
from app.services.bayar_keluar_rencana import Blok  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def stok(c, tid, pid, gid):
    return D(str(await c.fetchval("/* tenant_id */ SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 AND warehouse_id = $3", tid, pid, gid)))


async def tertunda(c, tid, trf, pid):
    return [i["quantity_pending"] for i in await D2.item_baris(c, tid, trf) if i["item_id"] == pid][0]


async def skenario(c, tid):
    uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.name ILIKE '%owner%' LIMIT 1", tid)
    ctx = {"tenant_id": tid, "user_id": str(uid)}
    gs = await c.fetch("/* tenant_id */ SELECT id, name FROM warehouses WHERE tenant_id = $1 AND is_active AND NOT is_system ORDER BY is_default DESC, created_at LIMIT 2", tid)
    cand = await c.fetchrow(
        "/* tenant_id */ SELECT il.product_id, il.warehouse_id, SUM(il.quantity_in - il.quantity_out) AS s FROM inventory_ledger il JOIN products p ON p.id = il.product_id AND p.tenant_id = il.tenant_id "
        "WHERE il.tenant_id = $1 AND p.item_type = 'goods' AND COALESCE(p.track_inventory, true) AND p.deleted_at IS NULL AND il.warehouse_id = ANY($2::uuid[]) "
        "GROUP BY il.product_id, il.warehouse_id HAVING SUM(il.quantity_in - il.quantity_out) >= 5 ORDER BY 3 DESC LIMIT 1", tid, [g["id"] for g in gs])
    if len(gs) < 2 or not cand:
        print(f"CATATAN {tid}: butuh 2 gudang aktif + barang ber-stok >= 5; dilewati", flush=True)
        return
    pid, asal = cand["product_id"], cand["warehouse_id"]
    tujuan = [g["id"] for g in gs if g["id"] != asal][0]
    transit = await GT.pastikan_transit(c, tid)
    hari = await tanggal_dokumen(c, tid)
    t0 = await stok(c, tid, pid, transit)
    body = PB.PemindahanIn(from_warehouse_id=asal, to_warehouse_id=tujuan, items=[PB.BarangIn(item_id=pid, quantity_requested=D("4"))])
    trf = await PB.buat_draf(c, ctx, body)
    await D2.kirim(c, ctx, trf)
    await D2.terima(c, ctx, trf, [{"item_id": pid, "qty": D("3")}])
    nomor = await c.fetchval("/* tenant_id */ SELECT transfer_number FROM stock_transfers WHERE tenant_id = $1 AND id = $2", tid, trf)
    # A
    p0, t1 = await tertunda(c, tid, trf, pid), await stok(c, tid, pid, transit)
    daftar = await SS.pemindahan_tertunda(c, tid, 500)
    cek(f"A {tid}: tertunda {p0} == 1, stok Transit +1 ({t0} -> {t1}), daftar tertunda memuat {nomor}", p0 == 1 and t1 - t0 == 1 and any(d["transfer_number"] == nomor for d in daftar))
    # B
    tarik = await SS.tarik_pemindahan(c, tid, trf)
    b = tarik["baris"][0] if tarik and tarik["baris"] else {}
    cek(f"B {tid}: tarik ok, 1 baris qty -1, Transit terkunci, alasan Hilang", tarik["ok"] and len(tarik["baris"]) == 1 and b.get("quantity_adjustment") == "-1" and b.get("gudang_terkunci") is True
        and b.get("warehouse_id") == str(transit) and b.get("reason_detail") == f"Hilang (selisih terima {nomor})")
    # C
    req = CreateStockAdjustmentRequest(adjustment_date=hari, adjustment_type="decrease",
                                       items=[StockAdjustmentItemCreate(product_id=str(pid), quantity_adjustment=D("-1"), source_transfer_id=str(trf))])
    r = await SS.rencana_buat(c, tid, req, Blok(False))
    sa_id, sa_no, _ = await RA._buat_draf(c, ctx, req, r)
    row = await c.fetchrow("SELECT i.warehouse_id, i.source_transfer_id, i.reason_detail FROM stock_adjustment_items i JOIN stock_adjustments s ON s.id = i.stock_adjustment_id AND s.tenant_id = $2 WHERE i.stock_adjustment_id = $1", sa_id, tid)
    cek(f"C {tid}: draf {sa_no} menyimpan warehouse_id Transit + source_transfer_id", row["warehouse_id"] == transit and row["source_transfer_id"] == trf and row["reason_detail"] == f"Hilang (selisih terima {nomor})")
    # draf belum menghitung (hanya POSTED)
    cek(f"C2 {tid}: draf TIDAK mengurangi tertunda ({await tertunda(c, tid, trf, pid)} == 1)", await tertunda(c, tid, trf, pid) == 1)
    # D
    bl = Blok(True)
    rp = await SS.rencana_posting(c, tid, sa_id, bl, kunci=True)
    if bl.daftar:
        print(f"CATATAN {tid}: posting diblok ({[b['code'] for b in bl.daftar]}); D-F dilewati", flush=True)
        return
    await RA._tulis_posting(c, ctx, sa_id, rp)
    led = await c.fetch("/* tenant_id */ SELECT warehouse_id, quantity_out, quantity_in FROM inventory_ledger WHERE tenant_id = $1 AND source_type = 'STOCK_ADJUSTMENT' AND source_id = $2", tid, sa_id)
    t2, p1 = await stok(c, tid, pid, transit), await tertunda(c, tid, trf, pid)
    teks = (await D2.teks_daftar(c, tid, [{"id": trf, "status": "in_transit"}]))[trf][0]
    sisa = await SS.pemindahan_tertunda(c, tid, 500)
    cek(f"D {tid}: ledger keluar 1 di Transit ({[(str(x['warehouse_id']) == str(transit), x['quantity_out']) for x in led]}), stok Transit kembali ({t0} == {t2}), tertunda {p1} == 0, teks '{teks}', daftar tak memuat",
        len(led) == 1 and led[0]["warehouse_id"] == transit and led[0]["quantity_out"] == 1 and t2 == t0 and p1 == 0 and teks == "Diterima · selisih diselesaikan" and not any(d["transfer_number"] == nomor for d in sisa))
    # E
    bl2 = Blok(True)
    await SS.rencana_buat(c, tid, req, bl2)
    pr = await D2.pratinjau_terima(c, ctx, trf, [{"item_id": pid, "qty": D("1")}])
    h = await PB._header(c, tid, trf)
    vk = await D2.rencana_void_kirim(c, tid, h, False)
    cek(f"E {tid}: draf kedua diblok {[b['code'] for b in bl2.daftar]}; Terima 1 lagi {[b['code'] for b in pr['blocks']]}; void kirim {[b['code'] for b in vk['blocks']]}",
        [b["code"] for b in bl2.daftar] == ["PENYESUAIAN_MELEBIHI_TERTUNDA"] and [b["code"] for b in pr["blocks"]] == ["PB_TERIMA_QTY_MELEBIHI"] and [b["code"] for b in vk["blocks"]] == ["PB_SUDAH_ADA_PENYELESAIAN"])
    # F (void disimulasikan di SQL: yang diukur adalah definisi tertunda, bukan jalur void router)
    await c.execute("/* tenant_id */ UPDATE stock_adjustments SET status = 'void' WHERE tenant_id = $1 AND id = $2", tid, sa_id)
    pv = await tertunda(c, tid, trf, pid)
    await c.execute("/* tenant_id */ UPDATE stock_adjustments SET status = 'posted' WHERE tenant_id = $1 AND id = $2", tid, sa_id)
    cek(f"F {tid}: status void -> tertunda kembali {pv} == 1; posted -> {await tertunda(c, tid, trf, pid)} == 0", pv == 1 and await tertunda(c, tid, trf, pid) == 0)
    # G
    req2 = CreateStockAdjustmentRequest(adjustment_date=hari, adjustment_type="increase", warehouse_id=str(asal),
                                        items=[StockAdjustmentItemCreate(product_id=str(pid), quantity_adjustment=D("1"), warehouse_id=str(tujuan))])
    r2 = await SS.rencana_buat(c, tid, req2, Blok(False))
    sebelum_sql = await stok(c, tid, pid, tujuan)
    cek(f"G {tid}: gudang per baris -> stok sebelum {r2['baris_draf'][0]['quantity_before']} == SQL gudang baris {sebelum_sql}, warehouse_id tersimpan gudang baris",
        r2["baris_draf"][0]["quantity_before"] == sebelum_sql and r2["baris_draf"][0]["warehouse_id"] == tujuan)


async def main():
    pool = await RA.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            tr = c.transaction()
            await tr.start()
            try:
                await skenario(c, tid)
            finally:
                await tr.rollback()
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if HASIL and all(HASIL) else 1)


asyncio.run(main())
