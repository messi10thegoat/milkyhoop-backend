"""Void transaksi bank TEREKONSILIASI/DICOCOKKAN ditolak (27 Sep 2026, BACKEND; tiket FE Law 34, pola Xero/QBO).
Di salinan: V320 dipasang dulu (idempoten). Per dokumen nyata kaos (RCV posted; DP posted) yang jurnalnya punya
mutasi bank belum dibalik:
  R: bank_transactions.is_reconciled=true -> void 409 BANK_TX_RECONCILED, nol jurnal baru, jurnal asli tak dibalik.
  M: matched_statement_line_id diisi      -> void 409 BANK_TX_MATCHED (idem).
  D: lapis DB — UPDATE reversed_by_id langsung (jalur tanpa pemeriksaan kode) -> EXCEPTION BANK_TX_MATCHED.
  N: keadaan dilepas -> void 200 normal, jurnal asli DIBALIK (trigger tak memblok void biasa)."""
import os
import uuid

from journey_lib import T

MIG = "backend/migrations/V320__cegah_void_bank_terekonsiliasi.sql"


async def _pasang_v320(J):
    for akar in ("/wt", "/app"):
        p = os.path.join(akar, MIG)
        if os.path.exists(p):
            async with J.pool.acquire() as c:
                await c.execute(open(p).read())
            return True
    J.gagal("v320_ada", "berkas migrasi V320 tak ditemukan di /wt atau /app")
    return False


async def _dok(J, tabel):
    async with J.pool.acquire() as c:
        return await c.fetchval(f"""
            SELECT d.id FROM {tabel} d WHERE d.tenant_id = $1 AND d.status = 'posted'
              AND EXISTS (SELECT 1 FROM bank_transactions bt JOIN journal_entries je ON je.id = bt.journal_id
                          WHERE je.source_id = d.id AND je.reversed_by_id IS NULL AND bt.tenant_id = $1)
            ORDER BY d.created_at DESC LIMIT 1""", T)


async def _keadaan(J, dok):
    async with J.pool.acquire() as c:
        return (await c.fetchval("select count(*) from journal_entries where tenant_id=$1", T),
                await c.fetchval("""select count(*) from journal_entries where tenant_id=$1 and source_id=$2
                                    and reversed_by_id is not null""", T, dok))


async def _set(J, dok, rekon, cocok):
    async with J.pool.acquire() as c:
        await c.execute("""UPDATE bank_transactions bt SET is_reconciled = $3, matched_statement_line_id = $4
                           FROM journal_entries je WHERE je.id = bt.journal_id AND je.source_id = $2
                             AND je.reversed_by_id IS NULL AND bt.tenant_id = $1""",
                        T, dok, rekon, uuid.uuid4() if cocok else None)


async def _satu(J, nama, tabel, jalur, body):
    dok = await _dok(J, tabel)
    if not dok:
        return J.gagal(f"{nama}_dok", f"tak ada {tabel} posted ber-mutasi bank di salinan")
    awal = await _keadaan(J, dok)
    for kasus, rekon, cocok, kode in (("R", True, False, "BANK_TX_RECONCILED"), ("M", False, True, "BANK_TX_MATCHED")):
        await _set(J, dok, rekon, cocok)
        _, r = await J.langkah(f"{nama}_{kasus}_void", "POST", jalur.format(dok), body, harap=(409,))
        d = (r or {}).get("detail") or {}
        if not isinstance(d, dict) or d.get("code") != kode:
            J.gagal(f"{nama}_{kasus}_kode", f"harap {kode}: {str(r)[:200]}")
        if await _keadaan(J, dok) != awal:
            J.gagal(f"{nama}_{kasus}_nol_tulisan", f"jurnal berubah {awal} -> {await _keadaan(J, dok)}")
    # D: lapis DB (keadaan M masih terpasang)
    async with J.pool.acquire() as c:
        jid = await c.fetchval("""select je.id from journal_entries je join bank_transactions bt on bt.journal_id=je.id
                                  where je.source_id=$1 and je.tenant_id=$2 and je.reversed_by_id is null limit 1""", dok, T)
        lain = await c.fetchval("""select id from journal_entries where tenant_id=$1 and id<>$2
                                   order by created_at desc limit 1""", T, jid)   # jurnal NYATA: lolos FK tanpa trigger

        class _Batal(Exception):
            pass
        try:
            async with c.transaction():
                n = await c.execute("update journal_entries set reversed_by_id=$1 where id=$2", lain, jid)
                raise _Batal(n)   # lolos -> tetap dibatalkan (salinan tetap bersih)
        except _Batal as b:
            J.gagal(f"{nama}_D_trigger", f"UPDATE reversed_by_id langsung LOLOS ({b}) padahal mutasi bank dicocokkan")
        except Exception as e:
            if "BANK_TX_MATCHED" not in str(e):
                J.gagal(f"{nama}_D_trigger", f"galat lain: {e}")
            else:
                J.catatan = getattr(J, "catatan", []) + [f"{nama} D ditolak trigger: {str(e)[:100]}"]
                print(f"{nama} D trigger menolak: {str(e)[:120]}", flush=True)
    # N: lepas -> void normal
    await _set(J, dok, False, False)
    await J.langkah(f"{nama}_N_void", "POST", jalur.format(dok), body)
    if (await _keadaan(J, dok))[1] < 1:
        J.gagal(f"{nama}_N_dibalik", "void normal tak membalik jurnal asli")


async def jalankan(J):
    if not await _pasang_v320(J):
        return
    await _satu(J, "RCV", "receive_payments", "/api/receive-payments/{}/void", {"void_reason": "TES E2E rekon"})
    await _satu(J, "DP", "customer_deposits", "/api/customer-deposits/{}/void", {"reason": "TES E2E rekon"})
