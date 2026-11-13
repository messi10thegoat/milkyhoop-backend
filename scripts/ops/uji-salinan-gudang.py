"""Gudang K1/K5/K7 + Barang P5 uji DB di SALINAN (bukan prod): handler ASLI list/detail gudang + detail Barang atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
A daftar ?aksi=nonaktif = tepat gudang nonaktif (== /summary nonaktif_count); urutan stabil dua kali sama, terurut (utama, nama, id)
B detail == daftar: nilai_stok, aksi_semua, aksi, ada_stok_minus sama untuk SETIAP gudang; barang_minus tiap saldo < 0 dan jumlahnya == hitungan SQL ledger independen
C buat minus nyata di klon (penyesuaian pengurangan barang tanpa riwayat): pill stok_minus memuat gudangnya, detail memuat barangnya, detail Barang stok_per_gudang menandai minus + Σ saldo == current_stock"""
import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal as D

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as IT  # noqa: E402
from app.routers import stock_adjustments as SA  # noqa: E402
from app.routers import warehouses as WH  # noqa: E402
from app.schemas import stock_adjustments as S  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


async def daftar(rq, **kw):
    p = dict(skip=0, limit=100, search=None, is_active=None, is_branch=None, aksi=None, with_totals=False)
    p.update(kw)
    return (await WH.list_warehouses(rq, **p)).data


async def main():
    pool = await WH.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            gud = await c.fetch("/* tenant_id */ SELECT id, is_default, is_active FROM warehouses WHERE tenant_id = $1 ORDER BY created_at", tid)
            cek(f"prasyarat {tid}: pemilik + >= 1 gudang", uid is not None and len(gud) >= 1)
            if uid is None or not gud:
                continue
            rq = req(tid, uid)
            lain = [g for g in gud if not g["is_default"]]
            if lain:  # jadikan satu gudang NONAKTIF di klon (tak ada transaksi aktif di klon yang berlaku)
                await c.execute("UPDATE warehouses SET is_active = false WHERE tenant_id = $1 AND id = $2", tid, lain[0]["id"])
            # A
            nonaktif = await daftar(rq, aksi="nonaktif")
            n_sql = await c.fetchval("/* tenant_id */ SELECT count(*) FROM warehouses WHERE tenant_id = $1 AND NOT is_active", tid)
            ringkas = (await WH.warehouses_summary(rq))["data"]
            cek(f"A {tid} ?aksi=nonaktif = tepat gudang nonaktif ({len(nonaktif)}), == SQL ({n_sql}) == /summary nonaktif_count ({ringkas['nonaktif_count']})",
                len(nonaktif) == n_sql == ringkas["nonaktif_count"] and all(not g.is_active and g.aksi_semua and "nonaktif" in g.aksi_semua for g in nonaktif))
            a1, a2 = await daftar(rq), await daftar(rq)
            urut = [(not g.is_default, g.name, str(g.id)) for g in a1]
            cek(f"A {tid} urutan daftar stabil (dua panggilan sama) dan terurut (utama, nama, id)", [str(g.id) for g in a1] == [str(g.id) for g in a2] and urut == sorted(urut))
            # B
            for g in a1:
                d = (await WH.get_warehouse(rq, g.id)).data
                tmp = (d.nilai_stok, d.aksi, d.aksi_semua, d.ada_stok_minus) == (g.nilai_stok, g.aksi, g.aksi_semua, g.ada_stok_minus)
                n_min = await c.fetchval("/* tenant_id */ SELECT count(*) FROM (SELECT il.product_id FROM inventory_ledger il WHERE il.tenant_id = $1 AND il.warehouse_id = $2 GROUP BY il.product_id HAVING SUM(il.quantity_in) - SUM(il.quantity_out) < 0) x", tid, g.id)
                ok_min = (d.barang_minus is not None and len(d.barang_minus) == min(n_min, 100) and all(b["saldo"] < 0 for b in d.barang_minus)
                          and d.ada_stok_minus == (n_min > 0))
                cek(f"B {tid} detail gudang {g.code} == daftar (nilai_stok, aksi, aksi_semua, ada_stok_minus); barang_minus {len(d.barang_minus or [])} == SQL {n_min}", tmp and ok_min,
                    f"detail={(d.nilai_stok, d.aksi, d.aksi_semua, d.ada_stok_minus)} daftar={(g.nilai_stok, g.aksi, g.aksi_semua, g.ada_stok_minus)}")
            # C minus nyata. DB menolak gerak keluar yang membuat saldo gudang negatif (trigger cache tak memotong; CHECK chk_ws_quantity "anti-oversell"), jadi minus per gudang
            # HANYA ada sebagai data lama. Di KLON disimulasikan: trigger cache dinonaktifkan sesaat, satu baris ledger KELUAR melebihi saldo disisipkan, trigger diaktifkan lagi.
            utama = next((g for g in await daftar(rq) if g.is_default), None) or (await daftar(rq))[0]
            cand = await c.fetchrow("""/* tenant_id */ SELECT il.product_id, p.item_code, p.nama_produk, SUM(il.quantity_in - il.quantity_out) AS led FROM inventory_ledger il
                JOIN products p ON p.id = il.product_id AND p.tenant_id = il.tenant_id WHERE il.tenant_id = $1 AND il.warehouse_id = $2 AND p.item_type = 'goods'
                GROUP BY il.product_id, p.item_code, p.nama_produk HAVING SUM(il.quantity_in - il.quantity_out) >= 0 ORDER BY 4 LIMIT 1""", tid, utama.id)
            if cand is None:
                print(f"CATATAN C {tid}: tak ada barang berjejak ledger di gudang utama (klon); minus nyata tak terukur (unit memakai tiruan)", flush=True)
                continue
            nol, led = cand["product_id"], D(str(cand["led"]))
            sesudah = D("-2")
            keluar = led + 2
            await c.execute("/* tenant_id */ ALTER TABLE inventory_ledger DISABLE TRIGGER trg_update_warehouse_stock")
            try:
                await c.execute("""INSERT INTO inventory_ledger (tenant_id, product_id, product_code, product_name, movement_type, movement_date, source_type, source_id, source_number,
                                   quantity_in, quantity_out, quantity_balance, unit_cost, total_cost, average_cost, warehouse_id, created_by, notes)
                                   VALUES ($1, $2, $3, $4, 'UJI_MINUS', $5, 'UJI_MINUS', $6, 'UJI-MINUS', 0, $7, 0, 0, 0, 0, $8, $9, 'uji gudang minus (data lama disimulasikan)')""",
                                tid, nol, cand["item_code"], cand["nama_produk"], date.today(), uuid.uuid4(), keluar, utama.id, uid)
            finally:
                await c.execute("/* tenant_id */ ALTER TABLE inventory_ledger ENABLE TRIGGER trg_update_warehouse_stock")
            minus_pill = await daftar(rq, aksi="stok_minus")
            dd = (await WH.get_warehouse(rq, utama.id)).data
            ada = any(b["item_id"] == str(nol) and D(str(b["saldo"])) == sesudah for b in (dd.barang_minus or []))
            cek(f"C {tid} minus (data lama): pill stok_minus memuat gudang, detail gudang memuat barangnya (saldo ledger {sesudah})", any(g.id == utama.id for g in minus_pill) and dd.ada_stok_minus is True and ada,
                f"pill={[g.code for g in minus_pill]} barang_minus={dd.barang_minus}")
            item = (await IT.get_item(rq, nol)).data
            spg = item["stok_per_gudang"]
            tot = sum(D(str(x["saldo"])) for x in spg)
            cek(f"C {tid} detail Barang: stok_per_gudang menandai minus ({sesudah}), Σ saldo == current_stock, ada_stok_minus_gudang", item["ada_stok_minus_gudang"] is True
                and any(x["minus"] and D(str(x["saldo"])) == sesudah for x in spg) and tot == D(str(item["current_stock"])), f"spg={spg} stok={item['current_stock']}")
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
