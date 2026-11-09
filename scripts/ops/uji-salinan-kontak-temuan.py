"""Temuan TESTER Kontak 9 Nov (A HP vendor, C teks riwayat pelanggan) -- uji di SALINAN: SATU dump, DUA klon, handler ASLI pohon LAMA
(master) dan BARU (cabang). Tulis HANYA di salinan (trap). JSON per pohon -> banding-kontak-temuan.py.
 A daftar + detail vendor/pelanggan: identik selain kontak_teks vendor ber-HP (mobile_phone) tanpa phone
 R riwayat /history SEMUA kontak sebelum tulis: jumlah kejadian identik; BARU tanpa teks mentah (" -> ", "Merged customer", UUID)
 C PATCH 3 pelanggan (alamat, termin, nonaktif) + gabung 1 pasang: baris aktivitas baru per pohon (lama: details; baru: per medan)"""
import asyncio
import json
import os
import re
import sys

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import customers as cu, vendors as ve  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def req(tid, method="GET"):
    r = Request({"type": "http", "method": method, "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-kontak-temuan"}
    return r


class JReq(Request):
    def __init__(self, tid, badan):
        super().__init__({"type": "http", "method": "POST", "path": "/", "headers": []})
        self.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-kontak-temuan"}
        self._badan = badan

    async def json(self):
        return self._badan


def j(v):
    return json.loads(json.dumps(v, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


async def boleh(*a, **k):
    return True


async def aktivitas_baru(conn, tid, cid, sebelum):
    return [j({k: r[k] for k in ("type", "description", "details", "field_name", "old_value", "new_value")}) for r in await conn.fetch(
        "SELECT id, type, description, details, field_name, old_value, new_value FROM customer_activities "
        "WHERE customer_id = $1 AND tenant_id = $2 ORDER BY field_name NULLS FIRST, description", cid, tid) if r["id"] not in sebelum]


async def id_aktivitas(conn, tid, cid):
    return {r["id"] for r in await conn.fetch("SELECT id FROM customer_activities WHERE customer_id = $1 AND tenant_id = $2", cid, tid)}


async def utama():
    import app.services.dashboard_izin as DI
    DI.boleh_baca = boleh  # riwayat: semua modul boleh dibaca (pembanding isi, bukan izin)
    pool = await cu.get_pool()
    out = {}
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_ktemuan_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            o = out[tid] = {}
            cs = await conn.fetch("SELECT id, nama FROM customers WHERE tenant_id = $1 AND deleted_at IS NULL ORDER BY nama, id", tid)
            vs = await conn.fetch("SELECT id, name, phone, mobile_phone FROM vendors WHERE tenant_id = $1 ORDER BY name, id", tid)
            o["A_vl"] = {str(x["id"]): j(x) for x in (await ve.list_vendors(req(tid), search=None, is_active=None, has_balance=None, is_pkp=None, has_overdue=None, skip=0,
                                                                              limit=200, sort_by="name", sort_order="asc", with_totals=False,
                                                                              aksi=None))["items"]}
            o["A_vhp"] = [str(v["id"]) for v in vs if (v["mobile_phone"] or "").strip() and not (v["phone"] or "").strip()]
            for v in vs:
                o[f"A_vd:{v['id']}"] = j((await ve.get_vendor(req(tid), v["id"]))["data"])
                o[f"R_v:{v['id']}"] = [e["ringkas"] for e in (await ve.get_vendor_history(req(tid), v["id"], limit=200))["data"]["events"]]
            for c in cs:
                o[f"A_cd:{c['id']}"] = j((await cu.get_customer(req(tid), str(c["id"])))["data"])
                o[f"R_c:{c['id']}"] = [e["ringkas"] for e in (await cu.get_customer_history(req(tid), c["id"], limit=200))["data"]["events"]]
            # C: tulis di klon
            for i, c in enumerate(cs[:3]):
                sb = await id_aktivitas(conn, tid, c["id"])
                await cu.update_customer(req(tid, "PATCH"), str(c["id"]), cu.UpdateCustomerRequest(
                    address=f"Jl. UJI Salinan {i}", payment_terms_days=14 + i, is_active=(i != 2)))
                o[f"C_ca:{c['nama']}"] = await aktivitas_baru(conn, tid, c["id"], sb)
            if len(cs) >= 6:
                tgt, src = cs[4], cs[5]
                sb_t, sb_s = await id_aktivitas(conn, tid, tgt["id"]), await id_aktivitas(conn, tid, src["id"])
                try:
                    await cu.merge_customers(JReq(tid, {"target_id": str(tgt["id"]), "source_ids": [str(src["id"])]}))
                    o["C_g"] = {"ok": True, "target": await aktivitas_baru(conn, tid, tgt["id"], sb_t),
                                "source": await aktivitas_baru(conn, tid, src["id"], sb_s), "nama": [tgt["nama"], src["nama"]]}
                except Exception as e:  # noqa: BLE001 -- blok gabung (dokumen bentrok) sama di dua pohon
                    o["C_g"] = {"ok": False, "galat": str(getattr(e, "status_code", type(e).__name__))}
    json.dump(out, open(os.environ["KELUAR"], "w"), sort_keys=True, indent=1)
    print("[info] " + ", ".join(f"{t}: {sum(k.startswith('A_cd:') for k in out[t])} pelanggan, {sum(k.startswith('A_vd:') for k in out[t])} "
                                f"vendor, HP-tanpa-telepon {len(out[t]['A_vhp'])}" for t in TENANT), flush=True)

asyncio.run(utama())
