"""rekon-lama-pagar uji di SALINAN, handler ASLI: setiap sesi rekonsiliasi in_progress tenant (semua tenant di klon) diselesaikan lewat
complete_session (a) tanpa penyesuaian dan (b) dengan penyesuaian sebesar selisih (perilaku FE lama). Cek (pohon BARU): bila selisih hitung
ulang != 0 -> 409 kedua-duanya; jurnal RECONCILIATION_ADJUSTMENT & reconciliation_adjustments tak bertambah; sesi tetap in_progress.
Pohon LAMA dicatat saja (bukti lubang lama). Semua tulis terjadi di KLON."""
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


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:600]])


async def hitung(conn, tid):
    return (await conn.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1 AND source_type = 'RECONCILIATION_ADJUSTMENT'", tid),
            await conn.fetchval("SELECT count(*) FROM reconciliation_adjustments WHERE tenant_id = $1", tid))


async def utama():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        sesi = await conn.fetch("""SELECT s.id, s.tenant_id, s.statement_ending_balance, s.statement_beginning_balance, ba.coa_id,
                                          (SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id
                                           WHERE utr.tenant_id = s.tenant_id AND r.code = 'OWNER' ORDER BY utr.user_id LIMIT 1) AS pemilik,
                                          (SELECT id FROM chart_of_accounts c WHERE c.tenant_id = s.tenant_id AND c.account_type = 'EXPENSE'
                                           AND NOT c.is_header ORDER BY c.account_code LIMIT 1) AS akun_beban
                                   FROM reconciliation_sessions s JOIN bank_accounts ba ON ba.id = s.account_id AND ba.tenant_id = s.tenant_id
                                   WHERE s.status = 'in_progress' ORDER BY s.id""")
    cek("ada sesi in_progress di klon", sesi, "tak ada sesi")
    for s in sesi:
        tid = s["tenant_id"]
        rq = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": tid, "user_id": str(s["pemilik"])}))
        async with pool.acquire() as conn:
            tercocok = await conn.fetchval(BR.SQL_TERCOCOK_SESI, s["id"], tid) if BARU else None
            sebelum = await hitung(conn, tid)
        selisih = int((s["statement_ending_balance"] or 0) - (s["statement_beginning_balance"] or 0) - (tercocok or 0))
        hasil = {}
        for nama, adj in (("tanpa", []), ("dengan", [AdjustmentItem(type="correction", amount=abs(selisih) or 1, description="UJI tutup selisih",
                                                                    account_id=str(s["akun_beban"]))])):
            try:
                r = await BR.complete_session(rq, s["id"], CompleteSessionRequest(adjustments=adj))
                hasil[nama] = [200, json.loads(r.model_dump_json()) if hasattr(r, "model_dump_json") else str(r)]
            except HTTPException as e:
                hasil[nama] = [e.status_code, e.detail]
        async with pool.acquire() as conn:
            sesudah = await hitung(conn, tid)
            status = await conn.fetchval("SELECT status FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2", s["id"], tid)
        if not BARU:
            OUT["lama"][str(s["id"])] = {"hasil": hasil, "jurnal_sebelum_sesudah": [sebelum, sesudah], "status": status}
            continue
        if selisih != 0:
            cek(f"{tid} {s['id']} selisih {selisih}: 409 dua-duanya", hasil["tanpa"][0] == 409 and hasil["dengan"][0] == 409, hasil)
            cek(f"{tid} {s['id']} tanpa jurnal/penyesuaian baru", sebelum == sesudah, [sebelum, sesudah])
            cek(f"{tid} {s['id']} tetap in_progress", status == "in_progress", status)
        else:
            cek(f"{tid} {s['id']} selisih 0: dengan penyesuaian 409", hasil["dengan"][0] == 409, hasil)
            cek(f"{tid} {s['id']} tanpa jurnal baru", sebelum[0] == sesudah[0], [sebelum, sesudah])


asyncio.run(utama())
json.dump({"cek": CEK, **OUT}, open(os.environ["KELUAR"], "w"), default=str)
print(f"[{'baru' if BARU else 'lama'}] cek {sum(1 for c in CEK if c[1])}/{len(CEK)}")
