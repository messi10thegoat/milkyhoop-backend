"""KONTAK standar SO -- uji di SALINAN (klon milkydb, SATU klon per pohon dari dump yang SAMA): handler ASLI Pelanggan + Vendor dari pohon
LAMA dan BARU. Tulis HANYA di salinan (dihapus trap). JSON per pohon -> banding-kontak.py.
 A detail semua kontak (baru = lama + aksi_tersedia)          B rename-impact pelanggan (identik)
 C PATCH pelanggan/vendor: baris + aktivitas sesudahnya (identik); BARU: edit/preview sebelum PATCH, perubahan == aktivitas
 D nonaktifkan lalu aktifkan (pohon lama: PATCH is_active + /reactivate; baru: alias /status) -> baris identik; BARU: pratinjau status
 E pratinjau hapus == hasil DELETE                            F pratinjau buat vendor == hasil POST
 G sidik tabel kontak sebelum/sesudah semua pratinjau (nol tulis)
 H gabung pelanggan (badan lama) + vendor: pratinjau + tulis identik lama vs baru (selain medan aditif); BARU: data == preview lama"""
import asyncio
import json
import os
import sys
import uuid

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import customers as cu, vendors as ve  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
BARU = hasattr(ve, "preview_create_vendor")


def req(tid, method="GET"):
    r = Request({"type": "http", "method": method, "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-kontak"}
    return r


class JReq(Request):
    """Request ber-badan JSON (rute gabung pelanggan membaca request.json())."""
    def __init__(self, tid, badan):
        super().__init__({"type": "http", "method": "POST", "path": "/", "headers": []})
        self.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-kontak"}
        self._badan = badan

    async def json(self):
        return self._badan


def j(v):
    return json.loads(json.dumps(v, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


async def coba(f):
    try:
        return {"ok": True, "hasil": j(await f)}
    except HTTPException as e:
        return {"ok": False, "status": e.status_code, "detail": j(e.detail), "code": getattr(e, "code", None)}


async def sidik(conn, tid):
    return {t: await conn.fetchval(f"SELECT md5(COALESCE(string_agg(x::text, '|' ORDER BY x.id), '')) FROM {t} x WHERE x.tenant_id = $1", tid)
            for t in ("customers", "vendors", "customer_activities", "vendor_activities")}


async def utama():
    pool = await cu.get_pool()
    out = {"baru": BARU}
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_kontak_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            o = out[tid] = {}
            cs = await conn.fetch("SELECT id, nama FROM customers WHERE tenant_id = $1 ORDER BY nama, id", tid)
            vs = await conn.fetch("SELECT id, name FROM vendors WHERE tenant_id = $1 ORDER BY name, id", tid)
            # A + B
            for c in cs:
                o[f"A_c:{c['nama']}"] = j((await cu.get_customer(req(tid), str(c["id"])))["data"])
                o[f"B_c:{c['nama']}"] = j((await cu.get_customer_rename_impact(req(tid), c["id"]))["data"])
            for v in vs:
                o[f"A_v:{v['name']}"] = j((await ve.get_vendor(req(tid), v["id"]))["data"])
            # G (awal) + pratinjau baru
            s0 = await sidik(conn, tid)
            if BARU:
                for c in cs:
                    o[f"E_pc:{c['nama']}"] = j((await cu.preview_delete_customer(req(tid, "POST"), c["id"]))["data"])
                    o[f"D_pc:{c['nama']}"] = j((await cu.preview_customer_status(req(tid, "POST"), c["id"], status="inactive"))["data"])
                for v in vs:
                    o[f"E_pv:{v['name']}"] = j((await ve.preview_delete_vendor(req(tid, "POST"), v["id"]))["data"])
                    o[f"B_v:{v['name']}"] = j((await ve.get_vendor_rename_impact(req(tid), v["id"]))["data"])
                o["F_p_baru"] = j((await ve.preview_create_vendor(req(tid, "POST"), {"name": "UJI Salinan Vendor K"}))["data"])
                if vs:
                    o["F_p_ganda"] = j((await ve.preview_create_vendor(req(tid, "POST"), {"name": vs[0]["name"]}))["data"])
                for c in cs[:3]:
                    o[f"C_pc:{c['nama']}"] = j((await cu.preview_update_customer(
                        req(tid, "POST"), c["id"], {"notes": "UJI salinan K", "credit_limit": 7500000, "phone": "0812 UJI"}))["data"])
                for v in vs[:3]:
                    o[f"C_pv:{v['name']}"] = j((await ve.preview_update_vendor(
                        req(tid, "POST"), v["id"], {"notes": "UJI salinan K", "phone": "0812 UJI", "payment_terms_days": 21}))["data"])
            o["G_nol_tulis"] = s0 == await sidik(conn, tid)
            # C tulis
            for c in cs[:3]:
                o[f"C_c:{c['nama']}"] = await coba(cu.update_customer(req(tid, "PATCH"), str(c["id"]), cu.UpdateCustomerRequest(
                    notes="UJI salinan K", credit_limit=7500000, phone="0812 UJI")))
                o[f"C_cr:{c['nama']}"] = j(dict(await conn.fetchrow(
                    "SELECT nama, telepon, notes, credit_limit, is_active FROM customers WHERE id = $1 AND tenant_id = $2", c["id"], tid)))
                o[f"C_ca:{c['nama']}"] = await conn.fetchval(
                    "SELECT details FROM customer_activities WHERE customer_id = $1 AND tenant_id = $2 AND type = 'updated' "
                    "ORDER BY timestamp DESC LIMIT 1", str(c["id"]), tid)
            for v in vs[:3]:
                lama_ids = {r["id"] for r in await conn.fetch(
                    "SELECT id FROM vendor_activities WHERE vendor_id = $1 AND tenant_id = $2", v["id"], tid)}
                o[f"C_v:{v['name']}"] = await coba(ve.update_vendor(req(tid, "PATCH"), v["id"], ve.UpdateVendorRequest(
                    notes="UJI salinan K", phone="0812 UJI", payment_terms_days=21)))
                o[f"C_vr:{v['name']}"] = j(dict(await conn.fetchrow(
                    "SELECT name, phone, notes, payment_terms_days, is_active FROM vendors WHERE id = $1 AND tenant_id = $2", v["id"], tid)))
                o[f"C_va:{v['name']}"] = [j({k: r[k] for k in ("description", "field_name", "old_value", "new_value")}) for r in await conn.fetch(
                    "SELECT id, description, field_name, old_value, new_value FROM vendor_activities WHERE vendor_id = $1 AND tenant_id = $2 "
                    "ORDER BY field_name", v["id"], tid) if r["id"] not in lama_ids]
            # D: nonaktif lalu aktif (pelanggan ke-4..5)
            for c in cs[3:5]:
                k = c["nama"]
                if BARU:
                    o[f"D_c1:{k}"] = await coba(cu.set_customer_status(req(tid, "PATCH"), c["id"], status="inactive"))
                    o[f"D_pa:{k}"] = j((await cu.preview_customer_status(req(tid, "POST"), c["id"], status="active"))["data"])
                    o[f"D_c2:{k}"] = await coba(cu.set_customer_status(req(tid, "PATCH"), c["id"], status="active"))
                else:
                    o[f"D_c1:{k}"] = await coba(cu.update_customer(req(tid, "PATCH"), str(c["id"]), cu.UpdateCustomerRequest(is_active=False)))
                    o[f"D_c2:{k}"] = await coba(cu.reactivate_customer(req(tid, "PATCH"), str(c["id"])))
                o[f"D_cr:{k}"] = j(dict(await conn.fetchrow(
                    "SELECT is_active, deleted_at IS NULL AS hidup FROM customers WHERE id = $1 AND tenant_id = $2", c["id"], tid)))
            # H: gabung (pelanggan ke-6 -> ke-7, vendor ke-4 -> ke-5); badan LAMA pelanggan (dua pohon sama)
            if len(cs) >= 7:
                bc = {"target_id": str(cs[6]["id"]), "source_ids": [str(cs[5]["id"])]}
                o["H_sumber"] = [cs[5]["nama"], vs[3]["name"] if len(vs) >= 5 else None]
                o["H_pc"] = j(await cu.preview_customer_merge(JReq(tid, bc)))
                o["H_c"] = await coba(cu.merge_customers(JReq(tid, bc)))
            if len(vs) >= 5:
                bv = ve.MergeVendorRequest(source_vendor_id=str(vs[3]["id"]), target_vendor_id=str(vs[4]["id"]))
                o["H_pv"] = j(await ve.preview_merge_vendors(req(tid, "POST"), bv))
                o["H_v"] = await coba(ve.merge_vendors(req(tid, "POST"), bv))
            # F: buat vendor (sebelum hapus: nama ganda = vendor pertama yang masih ada)
            o["F_baru"] = await coba(ve.create_vendor(req(tid, "POST"), ve.CreateVendorRequest(name="UJI Salinan Vendor K")))
            if vs:
                o["F_ganda"] = await coba(ve.create_vendor(req(tid, "POST"), ve.CreateVendorRequest(name=vs[0]["name"])))
            # E: DELETE semua kontak (salinan) -> bandingkan dengan pratinjau
            for c in cs:
                o[f"E_c:{c['nama']}"] = await coba(cu.delete_customer(req(tid, "DELETE"), str(c["id"])))
            for v in vs:
                o[f"E_v:{v['name']}"] = await coba(ve.delete_vendor(req(tid, "DELETE"), v["id"]))
    for t in TENANT:
        o = out[t]
        if "F_baru" in o and o["F_baru"]["ok"]:
            o["F_baru"]["hasil"]["data"].pop("id", None)
    json.dump(out, open(os.environ["KELUAR"], "w"), sort_keys=True, indent=1)
    print("[info] " + ", ".join(f"{t}: {sum(k.startswith('A_c:') for k in out[t])} pelanggan, "
                                f"{sum(k.startswith('A_v:') for k in out[t])} vendor" for t in TENANT), flush=True)

asyncio.run(utama())
