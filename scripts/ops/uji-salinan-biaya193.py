"""BUG-193 uji DB di SALINAN (bukan prod): handler ASLI expenses (buat draf, pratinjau post/void, post, void, detail) atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
1 pratinjau post draf: can_proceed, sesudah == 'dibayar' + teks posisi; baris biaya TETAP draf, tanpa jurnal (nol tulis) · 2 post nyata -> detail (biaya_keadaan, position_text) == sesudah pratinjau
· 3 post ulang: galat BIAYA_BUKAN_DRAF == blok pratinjau (kode + kalimat) · 4 pratinjau void dgn alasan: sesudah 'dibatalkan'; void nyata -> detail == sesudah · 5 void ulang: BIAYA_SUDAH_BATAL == pratinjau
· 6 void draf: BIAYA_BUKAN_TERBIT == pratinjau · 7 id acak + id tenant lain: EXPENSE_TIDAK_DITEMUKAN (404) di post/void/pratinjau, sesudah null."""
import asyncio
import sys
import uuid

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import expenses as EX  # noqa: E402
from app.schemas import expenses as S  # noqa: E402

NY, KA = "pt-kita-nyawit-bersama", "kaos-biru-konveksi"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


async def req(c, tid, method="POST"):
    r = Request({"type": "http", "method": method, "path": "/x", "headers": []})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def galat(coro):
    try:
        await coro
        return None
    except HTTPException as e:
        return e


async def baris(c, tid, eid):
    return await c.fetchrow("SELECT status, journal_id FROM expenses WHERE tenant_id = $1 AND id = $2::uuid", tid, str(eid))


async def detail(c, tid, eid):
    return (await EX.get_expense_detail(await req(c, tid, "GET"), eid))["data"]


def blok_sama(g, pratinjau):
    b = pratinjau["data"]["blocks"]
    return g is not None and b == [{"code": getattr(g, "code", None), "message": g.detail}] and pratinjau["data"]["can_proceed"] is False


async def main():
    pool = await EX.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            e0 = await c.fetchrow("SELECT paid_through_id, account_id FROM expenses WHERE tenant_id = $1 AND status = 'posted' AND journal_id IS NOT NULL AND NOT is_itemized ORDER BY created_at DESC LIMIT 1", tid)
            cek(f"prasyarat {tid}: biaya terbit templat ada", e0 is not None)
            if e0 is None:
                continue
            hari = await c.fetchval("SELECT CURRENT_DATE")
            b = S.CreateExpenseRequest(expense_date=hari, paid_through_id=e0["paid_through_id"], account_id=e0["account_id"], amount=100000, status="draft")
            eid = uuid.UUID(str((await EX.create_expense(await req(c, tid), b))["data"]["id"]))
            # 6 void draf (sebelum diterbitkan)
            g6 = await galat(EX.void_expense(await req(c, tid), eid, EX.VoidExpenseRequest(reason="uji")))
            p6 = await EX.preview_void_expense(await req(c, tid), eid, EX.VoidExpensePreviewRequest(reason="uji"))
            cek(f"6 {tid} void draf: BIAYA_BUKAN_TERBIT == blok pratinjau, sesudah null", g6 is not None and g6.code == "BIAYA_BUKAN_TERBIT" and blok_sama(g6, p6)
                and p6["data"]["biaya_keadaan_sesudah"] is None, str(p6["data"]["blocks"]))
            # 1 pratinjau post (nol tulis)
            p1 = await EX.preview_post_expense(await req(c, tid), eid)
            b1 = await baris(c, tid, eid)
            d1 = p1["data"]
            cek(f"1 {tid} pratinjau post: can_proceed, sesudah 'dibayar', baris TETAP draf tanpa jurnal", d1["can_proceed"] is True and d1["biaya_keadaan_sesudah"] == "dibayar"
                and bool(d1["position_text_sesudah"]) and b1["status"] == "draft" and b1["journal_id"] is None, f"{d1['biaya_keadaan_sesudah']} | {d1['position_text_sesudah']} | {dict(b1)}")
            # 2 post nyata -> detail
            await EX.post_expense(await req(c, tid), eid)
            dt = await detail(c, tid, eid)
            cek(f"2 {tid} post nyata: detail (biaya_keadaan, position_text) == sesudah pratinjau", dt["biaya_keadaan"] == d1["biaya_keadaan_sesudah"] and dt["position_text"] == d1["position_text_sesudah"],
                f"{dt['biaya_keadaan']} | {dt['position_text']}")
            # 3 post ulang
            g3 = await galat(EX.post_expense(await req(c, tid), eid))
            p3 = await EX.preview_post_expense(await req(c, tid), eid)
            cek(f"3 {tid} post ulang: BIAYA_BUKAN_DRAF == blok pratinjau", g3 is not None and g3.code == "BIAYA_BUKAN_DRAF" and g3.detail == "Hanya biaya draf yang bisa diterbitkan." and blok_sama(g3, p3), str(p3["data"]["blocks"]))
            # 4 pratinjau void + void nyata
            p4 = await EX.preview_void_expense(await req(c, tid), eid, EX.VoidExpensePreviewRequest(reason="uji batal"))
            d4 = p4["data"]
            cek(f"4 {tid} pratinjau void: sesudah 'dibatalkan' / 'Dibatalkan'", d4["can_proceed"] is True and d4["biaya_keadaan_sesudah"] == "dibatalkan" and d4["position_text_sesudah"] == "Dibatalkan", str(d4["blocks"]))
            await EX.void_expense(await req(c, tid), eid, EX.VoidExpenseRequest(reason="uji batal"))
            dt4 = await detail(c, tid, eid)
            cek(f"4 {tid} void nyata: detail == sesudah pratinjau", dt4["biaya_keadaan"] == d4["biaya_keadaan_sesudah"] and dt4["position_text"] == d4["position_text_sesudah"], f"{dt4['biaya_keadaan']} | {dt4['position_text']}")
            # 5 void ulang
            g5 = await galat(EX.void_expense(await req(c, tid), eid, EX.VoidExpenseRequest(reason="uji lagi")))
            p5 = await EX.preview_void_expense(await req(c, tid), eid, EX.VoidExpensePreviewRequest(reason="uji lagi"))
            cek(f"5 {tid} void ulang: BIAYA_SUDAH_BATAL == blok pratinjau", g5 is not None and g5.code == "BIAYA_SUDAH_BATAL" and blok_sama(g5, p5), str(p5["data"]["blocks"]))
            # 7 id acak + tenant lain
            lain = NY if tid == KA else KA
            for nama, tujuan, e_id in (("id acak", tid, uuid.uuid4()), (f"id tenant {lain}", tid, None)):
                if e_id is None:
                    e_id = uuid.UUID(str(await c.fetchval("SELECT id FROM expenses WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT 1", lain)))
                gp = await galat(EX.post_expense(await req(c, tujuan), e_id))
                gv = await galat(EX.void_expense(await req(c, tujuan), e_id, EX.VoidExpenseRequest(reason="x")))
                pp = await EX.preview_post_expense(await req(c, tujuan), e_id)
                pv = await EX.preview_void_expense(await req(c, tujuan), e_id, EX.VoidExpensePreviewRequest(reason="x"))
                cek(f"7 {tid} {nama}: 404 EXPENSE_TIDAK_DITEMUKAN di post/void/pratinjau, sesudah null",
                    gp is not None and gp.status_code == 404 and gp.code == "EXPENSE_TIDAK_DITEMUKAN" and gv is not None and gv.code == "EXPENSE_TIDAK_DITEMUKAN"
                    and blok_sama(gp, pp) and blok_sama(gv, pv) and pp["data"]["biaya_keadaan_sesudah"] is None and pv["data"]["position_text_sesudah"] is None, str(pp["data"]["blocks"]))
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
