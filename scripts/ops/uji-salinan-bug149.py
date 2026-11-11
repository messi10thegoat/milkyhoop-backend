"""BUG-149 -- uji di SALINAN (klon milkydb), per tenant (kaos, nyawit), kelima dokumen (rute & service ASLI). Tulis HANYA di salinan,
semuanya di transaksi yang di-ROLLBACK kecuali T5 (dipulihkan).
T1 pratinjau == nomor yang benar-benar diterbitkan generator; GET /next-number == pratinjau (estimated).
T2 pratinjau MELOMPATI nomor terpakai (nomor sudah dipakai dokumen lain) dan tetap == generator.
T3 nomor manual: tak menggeser seri (last_number sama); duplikat di tenant yang sama -> 409 {code,message}; nomor milik TENANT LAIN bukan konflik.
T4 pratinjau nol tulis: seri sama sebelum/sesudah.
T5 BALAPAN: dua transaksi serentak memakai nomor manual sama -> tepat satu sukses + satu pelanggaran UNIQUE yang dipetakan dari_pelanggaran_unik
   -> 409 berisi nomornya (pesan asyncpg NYATA: constraint_name + detail)."""
import asyncio
import os
import sys

import asyncpg
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import bills as RB, purchase_orders as RPO, quotes as RQ, sales_invoices as RSI, sales_orders as RSO  # noqa: E402
from app.services import nomor_dokumen as ND  # noqa: E402

HASIL, COVER = [], {}
TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
ROUTE = {"sales_invoice": RSI.nomor_faktur_berikut, "sales_order": RSO.nomor_so_berikut, "quote": RQ.nomor_penawaran_berikut,
         "bill": RB.pratinjau_nomor_tagihan, "purchase_order": RPO.nomor_po_berikut}


class _Batal(Exception):
    pass


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/api/x/next-number", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-bug149"}
    return r


async def dalam_batal(conn, fn):
    """Jalankan fn di transaksi yang SELALU di-rollback (trigger dimatikan di sesi ini: salinan scratch)."""
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL session_replication_role = replica")
            raise _Batal(await fn())
    except _Batal as b:
        return b.args[0]


async def seri(conn, tid, j):
    ym = await conn.fetchval("SELECT TO_CHAR(tanggal_bisnis($1), 'YYYY-MM')", tid)
    return await conn.fetchval(f"SELECT last_number FROM {j.seri} WHERE tenant_id = $1 AND year_month = $2", tid, ym)


async def dokumen(conn, tid, j, n=2):
    return await conn.fetch(f"SELECT id, {j.kolom} AS nomor FROM {j.tabel} WHERE tenant_id = $1 ORDER BY id LIMIT {n}", tid)


async def periksa(pool, conn, tid, jenis):
    j = ND.JENIS[jenis]
    t = f"{tid} {jenis}"
    s0 = await seri(conn, tid, j)
    p = await ND.pratinjau(conn, tid, jenis)
    cek(f"{t} T4 pratinjau nol tulis (seri sama)", await seri(conn, tid, j) == s0, f"last={s0} pratinjau={p}")
    g = await dalam_batal(conn, lambda: ND.tetapkan_nomor(conn, tid, jenis, None))
    cek(f"{t} T1 pratinjau == nomor generator", p == g, f"{p} vs {g}")
    r = (await ROUTE[jenis](req(tid)))["data"]
    cek(f"{t} T1 GET /next-number == pratinjau", r == {"number": p, "estimated": True}, str(r))

    docs = await dokumen(conn, tid, j)
    if not docs:
        print(f"[info] {t}: tak ada dokumen (T2/T3-duplikat/T5 dilewati)", flush=True)
    else:
        async def lompat():
            await conn.execute(f"UPDATE {j.tabel} SET {j.kolom} = $1 WHERE id = $2 AND tenant_id = $3", p, docs[0]["id"], tid)
            p2 = await ND.pratinjau(conn, tid, jenis)
            g2 = await ND.tetapkan_nomor(conn, tid, jenis, None)
            return p2, g2
        p2, g2 = await dalam_batal(conn, lompat)
        cek(f"{t} T2 pratinjau melompati nomor terpakai & == generator", p2 != p and p2 == g2, f"terpakai={p} -> {p2}/{g2}")
        try:
            await ND.tetapkan_nomor(conn, tid, jenis, docs[0]["nomor"])
            cek(f"{t} T3 duplikat di tenant yang sama -> 409", False, "tak ditolak")
        except Exception as e:  # noqa: BLE001
            d = getattr(e, "detail", None)
            cek(f"{t} T3 duplikat di tenant yang sama -> 409 {{code,message}}",
                getattr(e, "status_code", None) == 409 and isinstance(d, dict) and d["code"] == "NOMOR_DOKUMEN_TERPAKAI"
                and docs[0]["nomor"] in d["message"], str(d))

    lain = [x for x in TENANT if x != tid]
    if lain:
        ada = await conn.fetchrow(f"SELECT {j.kolom} AS nomor FROM {j.tabel} WHERE tenant_id = $1 ORDER BY id LIMIT 1", lain[0])
        if ada and not await conn.fetchval(f"SELECT 1 FROM {j.tabel} WHERE tenant_id = $1 AND {j.kolom} = $2", tid, ada["nomor"]):
            cek(f"{t} T3 nomor milik tenant lain BUKAN konflik", await ND.tetapkan_nomor(conn, tid, jenis, ada["nomor"]) == ada["nomor"])
    manual = await ND.tetapkan_nomor(conn, tid, jenis, "UJI-149-MANUAL")
    cek(f"{t} T3 manual tak menggeser seri", manual == "UJI-149-MANUAL" and await seri(conn, tid, j) == s0, f"last={s0}")

    if len(docs) >= 2:
        z = f"UJI-149-RACE-{jenis}"
        asli = {d["id"]: d["nomor"] for d in docs}
        c1, c2 = await pool.acquire(), await pool.acquire()
        try:
            t1 = c1.transaction()
            await t1.start()
            await c1.execute("SET LOCAL session_replication_role = replica")
            await c1.execute(f"UPDATE {j.tabel} SET {j.kolom} = $1 WHERE id = $2 AND tenant_id = $3", z, docs[0]["id"], tid)

            async def lawan():
                t2 = c2.transaction()
                await t2.start()
                try:
                    await c2.execute("SET LOCAL session_replication_role = replica")
                    await c2.execute(f"UPDATE {j.tabel} SET {j.kolom} = $1 WHERE id = $2 AND tenant_id = $3", z, docs[1]["id"], tid)
                    await t2.commit()
                    return None
                except asyncpg.UniqueViolationError as e:
                    await t2.rollback()
                    return e
            tugas = asyncio.create_task(lawan())
            await asyncio.sleep(1.0)
            await t1.commit()
            e = await tugas
            galat = ND.dari_pelanggaran_unik(e) if e else None
            cek(f"{t} T5 balapan: satu sukses, satu UNIQUE -> 409 berisi nomor",
                galat is not None and galat.status_code == 409 and z in galat.detail["message"],
                f"constraint={getattr(e, 'constraint_name', None)} detail={getattr(e, 'detail', None)}")
            COVER[jenis] = True
        finally:
            await c1.execute("SET session_replication_role = replica")
            for i, n in asli.items():
                await c1.execute(f"UPDATE {j.tabel} SET {j.kolom} = $1 WHERE id = $2 AND tenant_id = $3", n, i, tid)
            await c1.execute("SET session_replication_role = DEFAULT")
            await pool.release(c1)
            await pool.release(c2)


async def utama():
    pool = await RB.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_b149"):
            print(f"TOLAK: bukan salinan ({db})")
            return 2
        for tid in TENANT:
            for jenis in ND.JENIS:
                await periksa(pool, conn, tid, jenis)
    for jenis in ND.JENIS:
        cek(f"T5 balapan tercakup untuk {jenis} (min. satu tenant)", COVER.get(jenis, False))
    print(f"HASIL {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    return 0 if HASIL and all(HASIL) else 1


sys.exit(asyncio.run(utama()))
