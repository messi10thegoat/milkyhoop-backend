"""BUG-268b uji DB di SALINAN (DRAF, belum pernah dijalankan; perbaiki di slot). Fungsi ASLI inventory_helpers (record_inventory_inbound + record_inventory_reversal) atas klon; SEMUA tulis di
transaksi yang di-rollback. Tenant kaos + nyawit; satu barang dilacak per tenant yang punya saldo > 0 dan gudang.
A (KONTROL MERAH, kode lama disimulasikan): terima 10 @ (WAC lama x 2 + 1) lalu void penerimaan itu dengan hitung_wac_sesudah -> cadangan (perilaku lama) => snapshot terakhir != nilai/qty (bug direproduksi)
B (kode BARU): ulangi di savepoint terpisah dgn fungsi asli: snapshot pembalik == ROUND(nilai/qty, 2) == get_weighted_average_cost == WAC sebelum terima (WAC pulih)
C paritas guard: verify_inventory_wac_reconciliation_all() sesudah terima+void (kode baru): kontribusi barang uji = qty x snapshot terakhir == nilai bersih ledger (selisih <= 0,005 x |qty|), dan drift tenant TIDAK memburuk
   dibanding sebelum (|drift sesudah| <= |drift sebelum| + toleransi barang uji)
(D void sesudah ada keluar -> cadangan: dicakup unit test dua sisi; tak diulang di klon karena butuh jurnal HPP)
E kolom lain baris pembalik tak berubah: quantity_in/out, unit_cost, total_cost, warehouse sama dengan pola lama (hanya average_cost berbeda)"""
import asyncio
import sys
import uuid
from decimal import Decimal as D

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as RI  # noqa: E402
from app.services import inventory_helpers as IH  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def keadaan(c, tid, pid):
    r = await c.fetchrow("/* tenant_id */ SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) AS qty, COALESCE(SUM(quantity_in * unit_cost) - SUM(quantity_out * unit_cost), 0) AS nilai FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2", tid, pid)
    last = await c.fetchrow("/* tenant_id */ SELECT average_cost FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2 ORDER BY created_at DESC, id DESC LIMIT 1", tid, pid)
    return D(str(r["qty"])), D(str(r["nilai"])), (D(str(last["average_cost"])) if last and last["average_cost"] is not None else None)


async def drift(c, tid):
    r = await c.fetchrow("/* tenant_id */ SELECT drift, tolerance FROM verify_inventory_wac_reconciliation_all() WHERE tenant_id = $1", tid)
    return (D(str(r["drift"])), D(str(r["tolerance"]))) if r else (D(0), D(0))


async def terima_dan_void(c, tid, p, wh, qty, uc, sebelum_void=None):
    sid = uuid.uuid4()
    await IH.record_inventory_inbound(c, tid, p["id"], p["item_code"], p["nama_produk"], wh, qty, uc, "BILL", sid, "UJI-268B", None, "uji 268b")
    if sebelum_void:
        await sebelum_void()
    return sid, await IH.record_inventory_reversal(c, tid, "BILL", sid, uuid.uuid4())


async def main():
    pool = await RI.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            p = await c.fetchrow("/* tenant_id */ SELECT p.id, p.item_code, p.nama_produk FROM products p WHERE p.tenant_id = $1 AND p.deleted_at IS NULL AND p.item_type = 'goods' AND p.track_inventory = true AND EXISTS (SELECT 1 FROM inventory_ledger il WHERE il.tenant_id = p.tenant_id AND il.product_id = p.id) ORDER BY p.created_at LIMIT 1", tid)
            wh = await c.fetchval("/* tenant_id */ SELECT id FROM warehouses WHERE tenant_id = $1 AND NOT is_system AND is_active ORDER BY is_default DESC, created_at LIMIT 1", tid)
            cek(f"prasyarat {tid}: barang dilacak ber-ledger + gudang", p is not None and wh is not None)
            if p is None or wh is None:
                continue
            q0, v0, s0 = await keadaan(c, tid, p["id"])
            wac0 = (v0 / q0).quantize(D("0.01")) if q0 > 0 and v0 >= 0 else (s0 or D(0))
            uc = max(wac0 * 2 + 1, D("1"))
            d_awal, tol_awal = await drift(c, tid)
            # A kontrol merah: kode lama
            tr = c.transaction()
            await tr.start()
            asli = IH.hitung_wac_sesudah
            IH.hitung_wac_sesudah = lambda nilai, qty, masuk, keluar, ucx, cad: cad     # perilaku lama: snapshot sebelum efek
            try:
                await terima_dan_void(c, tid, p, wh, D("10"), uc)
                q, v, s = await keadaan(c, tid, p["id"])
                bug = q > 0 and s is not None and abs(s - (v / q).quantize(D("0.01"))) > D("0.01")
                cek(f"A {tid}: KONTROL MERAH kode lama: snapshot {s} != nilai/qty {(v / q).quantize(D('0.01')) if q > 0 else '-'} (bug direproduksi)", bug)
            finally:
                IH.hitung_wac_sesudah = asli
                await tr.rollback()
            # B/C/E kode baru
            tr = c.transaction()
            await tr.start()
            try:
                n_sebelum = await c.fetchval("/* tenant_id */ SELECT count(*) FROM inventory_ledger WHERE tenant_id = $1 AND product_id = $2", tid, p["id"])
                sid, hasil = await terima_dan_void(c, tid, p, wh, D("10"), uc)
                q, v, s = await keadaan(c, tid, p["id"])
                harap = (v / q).quantize(D("0.01")) if q > 0 and v >= 0 else None
                wac_fn = D(str(await c.fetchval("SELECT get_weighted_average_cost($1, $2)", tid, p["id"])))
                cek(f"B {tid}: snapshot pembalik {s} == nilai/qty {harap} == get_weighted_average_cost {wac_fn}", harap is not None and s == harap == wac_fn)
                cek(f"B {tid}: WAC sesudah void ({s}) pulih ke WAC sebelum terima ({wac0})", harap is not None and abs(s - wac0) <= D("0.01") * (1 + abs(q0) / 100))
                lg = await c.fetch("/* tenant_id */ SELECT source_type, quantity_in, quantity_out, unit_cost, total_cost, warehouse_id FROM inventory_ledger WHERE tenant_id = $1 AND source_id = $2 ORDER BY created_at, id", tid, sid)
                cek(f"E {tid}: pembalik = mirror persis (masuk<->keluar, unit_cost sama, gudang sama), {len(lg)} baris", len(lg) == 2 and lg[1]["source_type"] == "BILL_VOID" and lg[1]["quantity_out"] == lg[0]["quantity_in"] and lg[1]["unit_cost"] == lg[0]["unit_cost"] and lg[1]["warehouse_id"] == lg[0]["warehouse_id"])
                d_sesudah, tol = await drift(c, tid)
                nilai_wac = q * s
                cek(f"C {tid}: kontribusi barang uji qty x snapshot {nilai_wac:.2f} == nilai bersih {v:.2f}; drift tenant {d_awal} -> {d_sesudah} tak memburuk",
                    abs(nilai_wac - v) <= D("0.005") * abs(q) + D("0.01") and abs(d_sesudah) <= abs(d_awal) + D("0.005") * abs(q) + D("0.02"))
            finally:
                await tr.rollback()
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
