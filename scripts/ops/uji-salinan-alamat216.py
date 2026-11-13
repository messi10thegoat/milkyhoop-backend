"""BUG-216 uji DB di SALINAN (bukan prod): handler ASLI buat/ubah/detail PO + penentu gudang GRN atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
A buat tanpa alamat/gudang -> snapshot gudang UTAMA beralamat, purchase_orders.warehouse_id TETAP NULL; gudang dipilih tanpa alamat -> alamat usaha, kolom = gudang dipilih; manual menang
B detail: alamat_kirim_sumber + warehouse_name; edit/preview ganti gudang: perubahan memuat gudang + alamat; PATCH menulis sama; PO Terkirim ganti gudang ditolak PO_GUDANG_HANYA_DRAF
C GRN (grn_rencana._gudang): PO tanpa gudang mengikuti bawaan tenant TERKINI (ganti gudang utama di klon -> ikut), PO dengan gudang memakai gudang PO"""
import asyncio
import sys
from datetime import date

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import purchase_orders as PO  # noqa: E402
from app.schemas.purchase_orders import CreatePurchaseOrderRequest, PurchaseOrderItemCreate, UpdatePurchaseOrderRequest  # noqa: E402
from app.services import grn_rencana as G  # noqa: E402
from app.services import po_alamat_kirim as PAK  # noqa: E402

HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid, uid):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": "OWNER"}
    return r


def buat(**kw):
    return CreatePurchaseOrderRequest(vendor_name="UJI216", po_date=date.today(), items=[PurchaseOrderItemCreate(description="uji216", quantity=1, unit_price=1000)], **kw)


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        return e


async def main():
    pool = await PO.get_pool()
    async with pool.acquire() as c:
        for tid in ("kaos-biru-konveksi", "pt-kita-nyawit-bersama"):
            uid = await c.fetchval("/* tenant_id */ SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)
            gud = await c.fetch("/* tenant_id */ SELECT id, name, is_default FROM warehouses WHERE tenant_id = $1 AND is_active ORDER BY is_default DESC, created_at", tid)
            cek(f"prasyarat {tid}: pemilik + gudang utama aktif + >= 2 gudang aktif", uid is not None and len(gud) >= 2 and gud[0]["is_default"])
            if uid is None or len(gud) < 2 or not gud[0]["is_default"]:
                continue
            rq, utama, lain = req(tid, uid), gud[0], gud[1]
            await c.execute("UPDATE warehouses SET address = 'Jl. Utama Uji 216', city = 'Kota Utama' WHERE tenant_id = $1 AND id = $2", tid, utama["id"])
            await c.execute("UPDATE warehouses SET address = NULL, city = NULL WHERE tenant_id = $1 AND id = $2", tid, lain["id"])
            await c.execute('/* tenant_id */ UPDATE "Tenant" SET address = $2 WHERE id = $1', tid, "Alamat Usaha Uji 216")
            # A
            r1 = await galat(PO.create_purchase_order(rq, buat()))
            id1 = r1["data"]["id"]
            wid1 = await c.fetchval("/* tenant_id */ SELECT warehouse_id FROM purchase_orders WHERE tenant_id = $1 AND id = $2::uuid", tid, id1)
            cek(f"A {tid} buat tanpa alamat/gudang: snapshot gudang UTAMA ({r1['data']['ship_to_address']!r}), sumber '{r1['data']['alamat_kirim_sumber']}', kolom gudang TETAP NULL",
                r1["data"]["ship_to_address"] == "Jl. Utama Uji 216\nKota Utama" and r1["data"]["alamat_kirim_sumber"] == f"Gudang {utama['name']}" and wid1 is None, f"wid={wid1}")
            r2 = await galat(PO.create_purchase_order(rq, buat(warehouse_id=str(lain["id"]))))
            id2 = r2["data"]["id"]
            wid2 = await c.fetchval("/* tenant_id */ SELECT warehouse_id FROM purchase_orders WHERE tenant_id = $1 AND id = $2::uuid", tid, id2)
            cek(f"A {tid} gudang DIPILIH tanpa alamat: alamat usaha, sumber 'Alamat usaha', kolom = gudang dipilih", r2["data"]["ship_to_address"] == "Alamat Usaha Uji 216"
                and r2["data"]["alamat_kirim_sumber"] == "Alamat usaha" and str(wid2) == str(lain["id"]), f"{r2['data']} wid={wid2}")
            r3 = await galat(PO.create_purchase_order(rq, buat(ship_to_address="Jl. Manual 216", warehouse_id=str(utama["id"]))))
            cek(f"A {tid} manual menang (gudang dipilih tetap disimpan)", r3["data"]["ship_to_address"] == "Jl. Manual 216" and r3["data"]["alamat_kirim_sumber"] == "Diisi manual")
            e = await galat(PO.create_purchase_order(rq, buat(warehouse_id="00000000-0000-0000-0000-000000000000")))
            cek(f"A {tid} gudang tak ada -> 422 GUDANG_TIDAK_DITEMUKAN", getattr(e, "status_code", None) == 422 and getattr(e, "code", None) == "GUDANG_TIDAK_DITEMUKAN", f"{e}")
            # B
            d2 = (await PO.get_purchase_order(rq, __import__("uuid").UUID(id2)))["data"]
            cek(f"B {tid} detail: warehouse_name + alamat_kirim_sumber", d2["warehouse_name"] == lain["name"] and d2["alamat_kirim_sumber"] == "Alamat usaha" and d2["warehouse_id"] == str(lain["id"]), f"{d2.get('warehouse_name')} {d2.get('alamat_kirim_sumber')}")
            body = UpdatePurchaseOrderRequest(warehouse_id=str(utama["id"]))
            pv = (await PO.preview_edit_purchase_order(rq, __import__("uuid").UUID(id2), body))["data"]
            campur = {p["field"] for p in pv["perubahan"]}
            cek(f"B {tid} edit/preview ganti gudang: perubahan memuat gudang + alamat, sumber 'Gudang {utama['name']}'", {"warehouse_id", "ship_to_address"} <= campur and pv.get("alamat_kirim_sumber") == f"Gudang {utama['name']}", f"{pv['perubahan']}")
            await PO.update_purchase_order(rq, __import__("uuid").UUID(id2), body)
            d3 = (await PO.get_purchase_order(rq, __import__("uuid").UUID(id2)))["data"]
            cek(f"B {tid} PATCH menulis sama dgn pratinjau", d3["warehouse_id"] == str(utama["id"]) and d3["ship_to_address"] == "Jl. Utama Uji 216\nKota Utama" and d3["alamat_kirim_sumber"] == f"Gudang {utama['name']}")
            await c.execute("UPDATE purchase_orders SET status = 'sent' WHERE tenant_id = $1 AND id = $2::uuid", tid, id2)
            t = await galat(PO.update_purchase_order(rq, __import__("uuid").UUID(id2), UpdatePurchaseOrderRequest(warehouse_id=str(lain["id"]))))
            cek(f"B {tid} PO Terkirim ganti gudang ditolak PO_GUDANG_HANYA_DRAF", getattr(t, "status_code", None) in (400, 409, 422) and (getattr(t, "code", None) == "PO_GUDANG_HANYA_DRAF" or "hanya bisa diubah saat" in str(getattr(t, "detail", ""))), f"{t}")
            # C GRN
            po_tanpa = await c.fetchrow("/* tenant_id */ SELECT * FROM purchase_orders WHERE tenant_id = $1 AND id = $2::uuid", tid, id1)
            g1 = await G._gudang(c, tid, None, po_tanpa["warehouse_id"])
            cek(f"C {tid} GRN PO tanpa gudang = bawaan tenant ({g1['name'] if g1 else None})", g1 is not None and g1["id"] == utama["id"])
            await c.execute("UPDATE warehouses SET is_default = false WHERE tenant_id = $1 AND id = $2", tid, utama["id"])
            await c.execute("UPDATE warehouses SET is_default = true WHERE tenant_id = $1 AND id = $2", tid, lain["id"])
            g2 = await G._gudang(c, tid, None, po_tanpa["warehouse_id"])
            cek(f"C {tid} gudang utama tenant diganti: GRN PO tanpa gudang IKUT bawaan terkini ({g2['name'] if g2 else None})", g2 is not None and g2["id"] == lain["id"])
            po_dengan = await c.fetchrow("/* tenant_id */ SELECT * FROM purchase_orders WHERE tenant_id = $1 AND id = $2::uuid", tid, id2)
            g3 = await G._gudang(c, tid, None, po_dengan["warehouse_id"])
            cek(f"C {tid} GRN PO dengan gudang dipilih memakai gudang PO", g3 is not None and g3["id"] == utama["id"])
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
