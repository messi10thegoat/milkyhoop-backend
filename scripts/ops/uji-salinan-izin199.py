"""BUG-199 uji DB di SALINAN (bukan prod): handler ASLI GET /api/stock-adjustments/{id} + PolicyEngine NYATA atas klon, tenant kaos + nyawit. Mutasi HANYA di klon
(peran + anggota uji dibuat di klon). aksi_tersedia{tersedia, alasan} per peran (draf: posting/ubah/hapus; terposting: batalkan):
A SA {C,R} -> semua tertutup + kalimat izin · B SA {C,R,P} -> hanya posting · C SA {C,R,P,V,U,D} -> semua terbuka sesuai keadaan · D SA {R} tapi PRODUCT {U,D} -> ubah/hapus TETAP tertutup (tak lagi lewat PRODUCT)
· E peran C + override pengguna STOCK_ADJUST {P} -> hanya posting (override menang) · F OWNER -> hanya keadaan (bypass) · G middleware: huruf rute == izin yang dinilai aksi_tersedia."""
import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal

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


def req(tid, uid, role="USER"):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan", "role": role}
    return r


async def main():
    from backend.api_gateway.app.services import policy_engine_client as PEC
    pool = await SA.get_pool()
    PEC.init_policy_engine(pool)                    # singleton yang SAMA dipakai izin_rute_pengguna (impor mutlak)
    async with pool.acquire() as c:
        for tid in (KA, NY):
            owner = await c.fetchval("SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id WHERE utr.tenant_id = $1 AND utr.status = 'ACTIVE' AND r.code = 'OWNER' ORDER BY utr.is_primary DESC LIMIT 1", tid)   # pemilik = peran bisnis OWNER (bukan kolom User.role)
            p = await c.fetchrow("SELECT id FROM products WHERE tenant_id = $1 AND item_type = 'goods' ORDER BY created_at LIMIT 1", tid)
            cek(f"prasyarat {tid}: pemilik + barang goods ada", owner is not None and p is not None)
            if owner is None or p is None:
                continue
            rq = req(tid, owner)

            async def buat():
                b = S.CreateStockAdjustmentRequest(adjustment_date=date.today(), adjustment_type="increase", notes="UJI199",
                                                   items=[S.StockAdjustmentItemCreate(product_id=str(p["id"]), quantity_adjustment=Decimal("2"), reason_detail="uji")])
                return uuid.UUID(str((await SA.create_stock_adjustment(rq, b))["data"]["id"]))
            s_draf, s_post = await buat(), await buat()
            await SA.post_stock_adjustment(rq, s_post)

            async def peran(kode, sa_huruf, pr_huruf=None, override=None):
                rid, uid = uuid.uuid4(), uuid.uuid4()
                await c.execute("INSERT INTO roles (id, tenant_id, code, name, is_active) VALUES ($1, $2, $3, $3, true)", rid, tid, kode)
                for modul, huruf in (("STOCK_ADJUST", sa_huruf), ("PRODUCT", pr_huruf)):
                    if huruf is not None:
                        await c.execute("INSERT INTO role_permissions (id, role_id, module, actions) SELECT gen_random_uuid(), r.id, $3, $4::char(1)[] FROM roles r "
                                        "WHERE r.tenant_id = $1 AND r.id = $2", tid, rid, modul, huruf)
                await c.execute("INSERT INTO user_tenant_roles (id, user_id, tenant_id, role_id, is_primary, status) VALUES (gen_random_uuid(), $1, $2, $3, true, 'ACTIVE')", uid, tid, rid)
                if override is not None:
                    await c.execute("INSERT INTO user_permission_overrides (id, user_id, tenant_id, module, actions) VALUES (gen_random_uuid(), $1, $2, 'STOCK_ADJUST', $3::char(1)[])", str(uid), tid, override)
                return uid

            async def aksi(uid, sid, role="USER"):
                return (await SA.get_stock_adjustment(req(tid, uid, role), sid))["data"]["aksi_tersedia"]

            def baca(a, k):
                return a[k]["tersedia"], a[k]["alasan"]
            kal = SS.ALASAN_TAK_BERIZIN
            uA = await peran("UJI199A", ["C", "R"])
            ad, ap = await aksi(uA, s_draf), await aksi(uA, s_post)
            cek(f"A {tid} SA {{C,R}}: posting/ubah/hapus/batalkan semua tertutup + kalimat izin", baca(ad, "posting") == (False, kal["posting"]) and baca(ad, "ubah") == (False, kal["ubah"])
                and baca(ad, "hapus") == (False, kal["hapus"]) and baca(ap, "batalkan") == (False, kal["batalkan"]), str((ad, ap))[:300])
            uB = await peran("UJI199B", ["C", "R", "P"])
            bd, bp = await aksi(uB, s_draf), await aksi(uB, s_post)
            cek(f"B {tid} SA {{C,R,P}}: hanya posting terbuka", bd["posting"]["tersedia"] is True and bd["ubah"]["tersedia"] is False and bd["hapus"]["tersedia"] is False and bp["batalkan"]["tersedia"] is False, str((bd, bp))[:300])
            uC = await peran("UJI199C", ["C", "R", "P", "V", "U", "D"])
            cd, cp = await aksi(uC, s_draf), await aksi(uC, s_post)
            cek(f"C {tid} SA {{C,R,P,V,U,D}}: terbuka sesuai keadaan (draf: posting/ubah/hapus; terposting: batalkan)", all(cd[k]["tersedia"] for k in ("posting", "ubah", "hapus")) and cp["batalkan"]["tersedia"] is True
                and cd["batalkan"]["tersedia"] is False and cp["posting"]["tersedia"] is False, str((cd, cp))[:300])
            uD = await peran("UJI199D", ["R"], pr_huruf=["R", "U", "D"])
            dd = await aksi(uD, s_draf)
            cek(f"D {tid} SA {{R}} + PRODUCT {{R,U,D}}: ubah/hapus TETAP tertutup (tak lagi lewat PRODUCT)", baca(dd, "ubah") == (False, kal["ubah"]) and baca(dd, "hapus") == (False, kal["hapus"]), str(dd)[:300])
            uE = await peran("UJI199E", ["C", "R", "P", "V", "U", "D"], override=["R", "P"])
            ed, ep = await aksi(uE, s_draf), await aksi(uE, s_post)
            cek(f"E {tid} override pengguna STOCK_ADJUST {{R,P}} menang atas peran: hanya posting terbuka", ed["posting"]["tersedia"] is True and ed["ubah"]["tersedia"] is False and ed["hapus"]["tersedia"] is False and ep["batalkan"]["tersedia"] is False, str((ed, ep))[:300])
            od, op = await aksi(owner, s_draf, "OWNER"), await aksi(owner, s_post, "OWNER")
            cek(f"F {tid} OWNER: hanya keadaan dokumen (bypass izin)", all(od[k]["tersedia"] for k in ("posting", "ubah", "hapus")) and op["batalkan"]["tersedia"] is True and od["posting"]["alasan"] is None, str((od, op))[:300])
            from app.middleware.permission_middleware import izin_untuk_jalur
            peta = {k: izin_untuk_jalur(j.replace("{id}", str(s_draf)), m) for k, (m, j) in SS.JALUR_IZIN.items()}
            cek(f"G {tid} huruf rute (middleware) == yang dinilai aksi_tersedia", peta == {"posting": ("stock_adjust", "P"), "batalkan": ("stock_adjust", "V"), "ubah": ("stock_adjust", "U"), "hapus": ("stock_adjust", "D")}, str(peta))
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
