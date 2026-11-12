"""BUG-189 uji DB di SALINAN (bukan prod): handler ASLI bom.py + production.py atas klon busana (produk uji BHN-BENANG tanpa BOM lain) + kaos (rapikan produk tanpa utama). Mutasi HANYA di klon.
1 BOM v1 aktif = UTAMA otomatis · 2 BOM v2 aktif: v1 TETAP utama + aktif, v2 aktif non-utama (tak ada yang usang) · 3 daftar (status=active): 2 item, label 'v1 · Rp … · utama' / 'v2 · Rp …', utama true/false
· 4 jadikan-utama v2: v1 turun, v2 naik, tepat satu utama; ulang = no-op · 5 WO dari BOM aktif NON-utama DITERIMA, dari draf ditolak · 6 usangkan utama (v2): v1 jadi utama, teks menyebut BOM · 7 usangkan satu-satunya aktif: tak ada utama, teks 'tidak punya BOM aktif lagi'
· 8 resolver produk = utama · 9 draf baru + duplikat is_current=false (bukan utama) · 10 kaos: produk ber-BOM aktif tanpa utama -> jadikan-utama merapikan (tepat satu)."""
import asyncio
import sys
import uuid
from decimal import Decimal

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bom as B  # noqa: E402
from app.routers import production as P  # noqa: E402
from app.schemas import bom as SB  # noqa: E402
from app.schemas import production as SP  # noqa: E402

BS, KA = "pt-busana-indah-semesta", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c, tid):
    r = Request({"type": "http", "method": "POST", "path": "/x", "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        return e


async def utama_set(c, tid, pid):
    return [(r["bom_code"], r["status"]) for r in await c.fetch("SELECT bom_code, status FROM bill_of_materials WHERE tenant_id = $1 AND product_id = $2 AND status = 'active' AND is_current ORDER BY bom_code", tid, pid)]


async def baris(c, tid, bid):
    r = await c.fetchrow("SELECT status, is_current FROM bill_of_materials WHERE tenant_id = $1 AND id = $2", tid, bid)
    return (r["status"], r["is_current"])


async def main():
    pool = await B.get_pool()
    async with pool.acquire() as c:
        rq = await req(c, BS)
        produk = await c.fetchval("SELECT id FROM products WHERE tenant_id = $1 AND item_code = 'BHN-BENANG'", BS)
        cek("prasyarat: produk uji tanpa BOM aktif", produk is not None and not await c.fetchval("SELECT 1 FROM bill_of_materials WHERE tenant_id = $1 AND product_id = $2", BS, produk))

        async def buat(kode):
            r = await B.create_bom(rq, SB.CreateBOMRequest(product_id=produk, bom_code=f"UJI189-{kode}-" + uuid.uuid4().hex[:5], output_quantity=Decimal("1")))
            return uuid.UUID(str(r["data"]["id"]))
        v1, v2 = await buat("V1"), await buat("V2")
        cek("9 BOM baru (draf): is_current=false", await baris(c, BS, v1) == ("draft", False) and await baris(c, BS, v2) == ("draft", False), str(await baris(c, BS, v1)))
        dup = uuid.UUID(str((await B.duplicate_bom(rq, v1, "UJI189-DUP-" + uuid.uuid4().hex[:5]))["data"]["id"]))
        cek("9 duplikat (draf): is_current=false", await baris(c, BS, dup) == ("draft", False), str(await baris(c, BS, dup)))
        r1 = await B.activate_bom(rq, v1)
        cek("1 aktivasi pertama: v1 aktif + UTAMA otomatis", await baris(c, BS, v1) == ("active", True) and r1["data"]["utama"] is True, str(r1))
        r2 = await B.activate_bom(rq, v2)
        cek("2 aktivasi kedua: v1 TETAP utama+aktif; v2 aktif non-utama; tak ada usang", await baris(c, BS, v1) == ("active", True) and await baris(c, BS, v2) == ("active", False) and r2["data"]["utama"] is False, f"{await baris(c, BS, v1)} {await baris(c, BS, v2)}")
        dl = await B.list_boms(rq, 0, 100, None, produk, "active", None, "bom_code", "asc")
        items = {x["id"]: x for x in dl["items"]}
        cek("3 daftar status=active: 2 item; label 'vN · Rp … · utama' hanya utk utama; utama true/false; status_text 'Aktif'",
            len(items) == 2 and items[str(v1)]["utama"] is True and items[str(v1)]["label"].endswith("· utama") and items[str(v2)]["utama"] is False
            and "utama" not in items[str(v2)]["label"] and items[str(v1)]["status_text"] == "Aktif", str([(x["label"], x["utama"]) for x in dl["items"]]))
        det1, det2 = (await B.get_bom(rq, v1))["data"], (await B.get_bom(rq, v2))["data"]
        cek("3 detail: v1 utama true/'Aktif', v2 utama false/'Aktif'", (det1["utama"], det1["status_text"], det2["utama"], det2["status_text"]) == (True, "Aktif", False, "Aktif"), str((det1["utama"], det2["utama"])))
        # 4 jadikan-utama
        j = await B.jadikan_utama(rq, v2)
        cek("4 jadikan-utama v2: v1 turun, v2 naik, TEPAT SATU utama", await utama_set(c, BS, produk) == [(await c.fetchval("SELECT bom_code FROM bill_of_materials WHERE id = $1 AND tenant_id = $2", v2, BS), "active")] and j["data"]["utama_sebelumnya"] == str(v1), str(j))
        j2 = await B.jadikan_utama(rq, v2)
        cek("4 jadikan-utama ulang: no-op", j2["data"]["utama"] is True and "sudah menjadi BOM utama" in j2["message"] and len(await utama_set(c, BS, produk)) == 1, str(j2))
        gd = await galat(B.jadikan_utama(rq, dup))
        cek("4 jadikan-utama pada DRAF ditolak BOM_BUKAN_AKTIF", isinstance(gd, HTTPException) and getattr(gd, "code", None) == "BOM_BUKAN_AKTIF", str(gd)[:100])
        # 5 WO
        wh = await c.fetchval("SELECT warehouse_id FROM warehouse_stock WHERE tenant_id = $1 ORDER BY quantity DESC LIMIT 1", BS)
        nonutama_wo = await galat(P.create_production_order(rq, SP.CreateProductionOrderRequest(product_id=produk, bom_id=v1, planned_quantity=Decimal("1"), warehouse_id=wh)))
        cek("5 WO dari BOM aktif NON-utama (v1) DITERIMA", isinstance(nonutama_wo, dict), str(nonutama_wo)[:140])
        draf_wo = await galat(P.create_production_order(rq, SP.CreateProductionOrderRequest(product_id=produk, bom_id=dup, planned_quantity=Decimal("1"), warehouse_id=wh)))
        cek("5 WO dari BOM draf DITOLAK", isinstance(draf_wo, HTTPException) and draf_wo.status_code == 400, str(draf_wo)[:100])
        # 8 resolver
        res = await B.get_product_bom(rq, produk)
        cek("8 resolver produk = utama (v2)", res["data"] is not None and str(res["data"]["id"]) == str(v2), str(res)[:160])
        # 6 usangkan utama
        o = await B.obsolete_bom(rq, v2)
        cek("6 usangkan UTAMA (v2): v1 otomatis utama + teks menyebut BOM", await baris(c, BS, v1) == ("active", True) and await baris(c, BS, v2) == ("obsolete", False) and o["data"]["utama_baru"] == str(v1) and "BOM utama sekarang" in o["message"], o["message"])
        # 7 usangkan satu-satunya
        o2 = await B.obsolete_bom(rq, v1)
        cek("7 usangkan satu-satunya aktif: tak ada utama, teks jelas", await utama_set(c, BS, produk) == [] and "tidak punya BOM aktif lagi" in o2["message"], o2["message"])
        # 10 kaos
        rk = await req(c, KA)
        tanpa = await c.fetch("""SELECT product_id FROM bill_of_materials WHERE tenant_id = $1 GROUP BY product_id
                                 HAVING count(*) FILTER (WHERE status = 'active') > 0 AND count(*) FILTER (WHERE status = 'active' AND is_current) = 0""", KA)
        cek("10 kaos: ada produk ber-BOM aktif TANPA utama (kondisi awal terukur)", len(tanpa) >= 1, str(len(tanpa)))
        for t in tanpa:
            pilih = await c.fetchval("SELECT id FROM bill_of_materials WHERE tenant_id = $1 AND product_id = $2 AND status = 'active' ORDER BY created_at DESC LIMIT 1", KA, t["product_id"])
            await B.jadikan_utama(rk, pilih)
            cek("10 kaos: sesudah jadikan-utama produk itu tepat satu utama", len(await utama_set(c, KA, t["product_id"])) == 1)
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
