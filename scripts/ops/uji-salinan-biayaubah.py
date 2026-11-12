"""BUG-187 uji DB di SALINAN (bukan prod): handler ASLI expenses (buat draf, PATCH, pratinjau ubah, riwayat) atas klon, tenant kaos + nyawit. Mutasi HANYA di klon.
1 buat draf -> riwayat: hanya 'dibuat' · 2 pratinjau ubah (POST /edit/preview): NOL baris audit, riwayat tak berubah · 3 PATCH nyata (jumlah+tanggal): riwayat memuat 1 baris 'Biaya … diubah: jumlah, tanggal · total … → …',
beraktor (user), jenis EXPENSE_UPDATED, medan/total_sebelum/total_sesudah di audit_logs · 4 PATCH kedua (catatan): 2 baris (urut waktu) · 5 PATCH dgn kunci idempotensi SAMA diulang: replay, TIDAK menambah baris
· 6 PATCH ditolak (biaya terbit): tak ada baris audit baru · 7 lintas tenant: riwayat biaya tenant lain 404 dan audit tenant lain tak bocor."""
import asyncio
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

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


async def req(c, tid, kunci=None, method="POST"):
    h = [(b"x-idempotency-key", kunci.encode())] if kunci else []
    r = Request({"type": "http", "method": method, "path": "/x", "headers": h})
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY (role::text = \'OWNER\') DESC LIMIT 1', tid)
    r.state.user = {"tenant_id": tid, "user_id": str(uid), "username": "uji-salinan"}
    return r


async def n_audit(c, tid, eid):
    return await c.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2::uuid AND \"eventType\" = 'EXPENSE_UPDATED'", tid, str(eid))


async def riwayat(c, tid, eid):
    return (await EX.get_expense_history(await req(c, tid, method="GET"), eid, 200))["data"]["events"]


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        return e


async def main():
    pool = await EX.get_pool()
    async with pool.acquire() as c:
        for tid in (KA, NY):
            e0 = await c.fetchrow("SELECT paid_through_id, account_id FROM expenses WHERE tenant_id = $1 AND status = 'posted' AND journal_id IS NOT NULL AND NOT is_itemized ORDER BY created_at DESC LIMIT 1", tid)
            cek(f"prasyarat {tid}: biaya terbit templat ada", e0 is not None)
            if e0 is None:
                continue
            hari = (await c.fetchval("SELECT CURRENT_DATE"))
            b = S.CreateExpenseRequest(expense_date=hari, paid_through_id=e0["paid_through_id"], account_id=e0["account_id"], amount=100000, status="draft")
            eid = uuid.UUID(str((await EX.create_expense(await req(c, tid), b))["data"]["id"]))
            ev = await riwayat(c, tid, eid)
            cek(f"1 {tid} draf baru: riwayat hanya 'dibuat'", [x["jenis"] for x in ev] == ["BIAYA_DIBUAT"] and await n_audit(c, tid, eid) == 0, str([x["jenis"] for x in ev]))
            # 2 pratinjau
            await EX.preview_update_expense(await req(c, tid), eid, S.UpdateExpenseRequest(amount=120000))
            cek(f"2 {tid} pratinjau ubah: NOL audit, riwayat tak berubah", await n_audit(c, tid, eid) == 0 and len(await riwayat(c, tid, eid)) == 1)
            # 3 PATCH nyata
            await EX.update_expense(await req(c, tid, method="PATCH"), eid, S.UpdateExpenseRequest(amount=120000, expense_date=hari - timedelta(days=1)))
            ev = await riwayat(c, tid, eid)
            diubah = [x for x in ev if x["jenis"] == "EXPENSE_UPDATED"]
            cek(f"3 {tid} PATCH: 1 baris EXPENSE_UPDATED, ringkas berisi 'diubah: jumlah' dan total 100.000 → 120.000", len(diubah) == 1 and "diubah" in diubah[0]["ringkas"] and "jumlah" in diubah[0]["ringkas"]
                and "100.000" in diubah[0]["ringkas"] and "120.000" in diubah[0]["ringkas"], str(diubah)[:300])
            meta = await c.fetchval("SELECT metadata FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2::uuid AND \"eventType\" = 'EXPENSE_UPDATED' LIMIT 1", tid, str(eid))
            cek(f"3 {tid} audit_logs: medan + total_sebelum/sesudah + user_id", meta is not None and all(k in str(meta) for k in ("medan", "total_sebelum", "total_sesudah", "user_id")), str(meta)[:300])
            cek(f"3 {tid} baris beraktor (aktor terisi: dict ber-id)", bool(diubah) and isinstance(diubah[0].get("aktor"), dict) and bool(diubah[0]["aktor"].get("id")), str(diubah[0])[:300] if diubah else "")
            # 4 PATCH kedua
            await EX.update_expense(await req(c, tid, method="PATCH"), eid, S.UpdateExpenseRequest(notes="uji catatan"))
            cek(f"4 {tid} PATCH kedua: 2 baris audit", await n_audit(c, tid, eid) == 2 and len([x for x in await riwayat(c, tid, eid) if x["jenis"] == "EXPENSE_UPDATED"]) == 2)
            # 5 idempotensi
            kunci = "uji-187-" + uuid.uuid4().hex[:8]
            sblm = await n_audit(c, tid, eid)
            await EX.update_expense(await req(c, tid, kunci, "PATCH"), eid, S.UpdateExpenseRequest(notes="catatan idem"))
            await EX.update_expense(await req(c, tid, kunci, "PATCH"), eid, S.UpdateExpenseRequest(notes="catatan idem"))
            cek(f"5 {tid} kunci idempotensi sama diulang: hanya +1 baris", await n_audit(c, tid, eid) == sblm + 1, f"{sblm} -> {await n_audit(c, tid, eid)}")
            # 6 ditolak (terbit)
            terbit = await c.fetchval("SELECT id FROM expenses WHERE tenant_id = $1 AND status = 'posted' ORDER BY created_at DESC LIMIT 1", tid)
            sblm_t = await n_audit(c, tid, terbit)
            g = await galat(EX.update_expense(await req(c, tid, method="PATCH"), terbit, S.UpdateExpenseRequest(notes="x")))
            cek(f"6 {tid} PATCH biaya terbit ditolak: tak ada baris audit baru", g is not None and await n_audit(c, tid, terbit) == sblm_t, str(g)[:120])
            # 7 lintas tenant
            lain = NY if tid == KA else KA
            g7 = await galat(EX.get_expense_history(await req(c, lain, method="GET"), eid, 200))
            cek(f"7 {tid} riwayat biaya dibuka tenant {lain}: 404", isinstance(g7, HTTPException) and g7.status_code == 404, str(g7)[:120])
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if all(HASIL) else 1)


asyncio.run(main())
