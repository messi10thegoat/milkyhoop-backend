"""rekon-lama-pagar uji di SALINAN, handler ASLI. Untuk SETIAP sesi rekonsiliasi in_progress (semua tenant, di klon) pohon BARU menjalankan
tiga keadaan pada sesi yang sama (saldo rekening koran akhir disetel HANYA di klon, lalu dipulihkan):
  A  keadaan asli (selisih hitung ulang apa adanya): tanpa & dengan penyesuaian -> 409 bila selisih != 0
  B  selisih Rp50 (dulu lolos toleransi 100): tanpa penyesuaian -> 409 SELISIH_REKONSILIASI
  C  selisih 0 + penyesuaian -> 409 REKON_PENYESUAIAN_DITOLAK; selisih 0 tanpa penyesuaian -> 200 completed
Tiap langkah: jurnal RECONCILIATION_ADJUSTMENT & reconciliation_adjustments tak bertambah. Sesudahnya status + saldo dipulihkan agar pohon
LAMA (dicatat saja: bukti lubang) membaca keadaan asli. Semua tulis terjadi di KLON."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

from fastapi import HTTPException

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bank_reconciliation as BR  # noqa: E402
from app.schemas.bank_reconciliation import AdjustmentItem, CompleteSessionRequest  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

BARU = hasattr(BR, "PENANDA_REKON_LAMA_PAGAR")
CEK, OUT = [], {"lama": {}}
SQL_TERCOCOK = """SELECT COALESCE(SUM(l.amount) FILTER (WHERE l.match_status = 'matched'), 0) FROM bank_statement_lines_v2 l
                  JOIN reconciliation_sessions s ON s.id = l.session_id WHERE l.session_id = $1 AND s.tenant_id = $2"""


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:600]])


async def hitung(conn, tid):
    return (await conn.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_type = 'RECONCILIATION_ADJUSTMENT'", tid),
            await conn.fetchval("SELECT count(*) FROM reconciliation_adjustments WHERE tenant_id = $1", tid))


async def selesai(rq, sid, adj):
    try:
        r = await BR.complete_session(rq, sid, CompleteSessionRequest(adjustments=adj))
        return [200, json.loads(r.model_dump_json()) if hasattr(r, "model_dump_json") else str(r)]
    except HTTPException as e:
        return [e.status_code, e.detail]


def kode(h):
    return h[1].get("code") if isinstance(h[1], dict) else None


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        sesi = await conn.fetch("""SELECT s.id, s.tenant_id, s.status, s.statement_ending_balance, s.statement_beginning_balance, s.difference,
                                          (SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id
                                           WHERE utr.tenant_id = s.tenant_id AND r.code = 'OWNER' ORDER BY utr.user_id LIMIT 1) AS pemilik,
                                          (SELECT id FROM chart_of_accounts c WHERE c.tenant_id = s.tenant_id AND c.account_type = 'EXPENSE'
                                           AND NOT c.is_header ORDER BY c.account_code LIMIT 1) AS akun_beban
                                   FROM reconciliation_sessions s WHERE s.status = 'in_progress' ORDER BY s.id""")
    cek("ada sesi in_progress di klon", sesi, "tak ada sesi")
    for s in sesi:
        tid, sid = s["tenant_id"], s["id"]
        rq = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": tid, "user_id": str(s["pemilik"])}))
        adj = [AdjustmentItem(type="correction", amount=1, description="UJI tutup selisih", account_id=str(s["akun_beban"]))]
        if not BARU:
            async with pool.acquire() as conn:
                sebelum = await hitung(conn, tid)
            h = {"tanpa": await selesai(rq, sid, []), "dengan": await selesai(rq, sid, adj)}
            async with pool.acquire() as conn:
                OUT["lama"][str(sid)] = {"hasil": h, "jurnal": [sebelum, await hitung(conn, tid)],
                                         "status": await conn.fetchval("SELECT status FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2", sid, tid)}
            continue
        async with pool.acquire() as conn:
            awal = s["statement_beginning_balance"] or 0
            tercocok = await conn.fetchval(SQL_TERCOCOK, sid, tid)
            sebelum = await hitung(conn, tid)

        async def setel(akhir, selisih_kolom):
            async with pool.acquire() as c:
                await c.execute("UPDATE reconciliation_sessions SET statement_ending_balance = $3, difference = $4, status = 'in_progress', "
                                "completed_at = NULL WHERE id = $1 AND tenant_id = $2", sid, tid, akhir, selisih_kolom)
        k = f"{tid} {sid}"
        a1, a2 = await selesai(rq, sid, []), await selesai(rq, sid, [AdjustmentItem(type="correction", amount=1172500,
                                                                             description="UJI", account_id=str(s["akun_beban"]))])
        cek(f"A {k} asli: 409 tanpa & dengan penyesuaian", a1[0] == 409 and a2[0] == 409, [a1, a2])
        await setel(awal + tercocok + 50, None)
        b1 = await selesai(rq, sid, [])
        cek(f"B {k} selisih 50: 409 SELISIH_REKONSILIASI", b1[0] == 409 and kode(b1) == "SELISIH_REKONSILIASI", b1)
        await setel(awal + tercocok, 0)
        c1 = await selesai(rq, sid, adj)
        cek(f"C {k} selisih 0 + penyesuaian: 409 REKON_PENYESUAIAN_DITOLAK", c1[0] == 409 and kode(c1) == "REKON_PENYESUAIAN_DITOLAK", c1)
        c2 = await selesai(rq, sid, [])
        async with pool.acquire() as conn:
            st = await conn.fetchval("SELECT status FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2", sid, tid)
            sesudah = await hitung(conn, tid)
        cek(f"C {k} selisih 0 tanpa penyesuaian: 200 completed", c2[0] == 200 and st == "completed", [c2, st])
        cek(f"{k} nol jurnal/penyesuaian baru di semua langkah", sebelum == sesudah, [sebelum, sesudah])
        await setel(s["statement_ending_balance"], s["difference"])  # pulihkan untuk pohon LAMA


asyncio.run(utama())
json.dump({"cek": CEK, **OUT}, open(os.environ["KELUAR"], "w"), default=str)
print(f"[{'baru' if BARU else 'lama'}] cek {sum(1 for c in CEK if c[1])}/{len(CEK)}")
