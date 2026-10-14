"""Harness NYATA W-20261014-QUOTE-EXPIRY-TURUNAN (V421): kedaluwarsa penawaran = turunan tanggal.
- DB: trg_quote_expiry + check_quote_expiry TIDAK ada (sesudah V421) dan status='expired' = 0 baris (semua tenant).
- kaos (boleh ditulis uji), transaksi ROLLBACK: /send pada sent lewat masa -> 409 QUOTE_NOT_SHAREABLE, status tetap;
  ubah catatan pada sent lewat masa -> status TETAP sent TANPA pemulih (kode baru). Sesudahnya nol jejak.
- kaos + grapgrap READ ONLY: ?status=expired total == summary expired_count == expired_pending_count == lewat_masa_count
  == hitungan SQL turunan (tanggal bisnis), dan id daftar == id SQL.
Dijalankan SEBELUM V421 = KONTROL MERAH (trigger ada; ubah lewat masa -> 'expired' karena pemulih sudah dicabut)."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, "/app")
import asyncpg  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from backend.api_gateway.app.routers import quotes as Q  # noqa: E402
from backend.api_gateway.app.schemas.quotes import UpdateQuoteRequest  # noqa: E402
from backend.api_gateway.app.services import penawaran_kedaluwarsa as PK  # noqa: E402

GG, KAOS = "grapgrap-manado", "kaos-biru-konveksi"
gagal = 0


class Rollback(Exception):
    pass


def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    if not ok:
        gagal += 1


def req(user_id, tenant):
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": user_id, "tenant_id": tenant, "role": "ADMIN"}))


async def pemilik(c, t):
    return str(await c.fetchval(
        "SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id "
        "WHERE utr.tenant_id = $1 AND r.code = 'OWNER' AND r.is_active ORDER BY utr.is_primary DESC LIMIT 1", t))


async def jejak(c, qid):
    q = await c.fetchrow("SELECT status, notes, sent_at, updated_at, expiry_date FROM quotes WHERE id = $1", qid)
    n = await c.fetchval("SELECT count(*) FROM audit_logs WHERE entity_id = $1::uuid AND entity_type = 'quotes'", str(qid))
    return dict(q), n


async def main():
    tulis = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    baca = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2,
                                     server_settings={"default_transaction_read_only": "on"})

    # 0) DB: trigger dicabut, nol baris 'expired'
    async with baca.acquire() as c:
        try:
            async with c.transaction():
                await c.execute("CREATE TEMP TABLE _x(i int)")
            cek(False, "pool baca menolak tulis")
        except asyncpg.exceptions.ReadOnlySQLTransactionError:
            cek(True, "pool baca menolak tulis (READ ONLY)")
        trg = await c.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_quote_expiry'")
        fn = await c.fetchval("SELECT count(*) FROM pg_proc WHERE proname = 'check_quote_expiry'")
        cek(trg == 0 and fn == 0, "V421: trg_quote_expiry + check_quote_expiry tidak ada", f"trigger {trg} fungsi {fn}")
        ex = await c.fetchval("SELECT count(*) FROM quotes WHERE status = 'expired'")
        cek(ex == 0, "status='expired' tersimpan = 0 baris (semua tenant)", ex)

    # 1) kaos TULIS di ROLLBACK
    async with tulis.acquire() as c:
        own = await pemilik(c, KAOS)
        ctx = {"tenant_id": KAOS, "user_id": own}
        hari = await Q.tanggal_dokumen(c, KAOS)
        x = await c.fetchrow("""SELECT q.id, q.quote_number, q.expiry_date FROM quotes q WHERE q.tenant_id = $1
                                AND q.status = 'sent' AND q.expiry_date < $2 AND NOT EXISTS (SELECT 1 FROM sales_orders s
                                WHERE s.tenant_id = q.tenant_id AND s.quote_id = q.id) ORDER BY q.quote_number LIMIT 1""",
                             KAOS, hari)
        cek(x is not None, "kaos punya penawaran sent yang sudah lewat masa", x and x["quote_number"])
        awal = await jejak(c, x["id"])
        try:
            async with c.transaction():
                try:
                    await Q.kirim_penawaran_core(c, ctx, x["id"])
                    cek(False, "kirim lewat masa harus 409")
                except HTTPException as e:
                    d = e.detail if isinstance(e.detail, dict) else {}
                    cek(e.status_code == 409 and d.get("code") == "QUOTE_NOT_SHAREABLE"
                        and "Ubah tanggal berlaku dulu." in d.get("message", "")
                        and x["expiry_date"].strftime("%d/%m/%Y") in d.get("message", ""),
                        "kirim ulang lewat masa -> 409 QUOTE_NOT_SHAREABLE + tanggal + 'Ubah tanggal berlaku'",
                        f"{e.status_code} {d}")
                st = await c.fetchval("SELECT status FROM quotes WHERE id = $1", x["id"])
                cek(st == "sent", "sesudah kirim ditolak: status tetap sent", st)
                await Q._ubah_penawaran(c, ctx, str(x["id"]), UpdateQuoteRequest(notes="TES E2E V421 lewat masa"))
                st = await c.fetchval("SELECT status FROM quotes WHERE id = $1", x["id"])
                cek(st == "sent", "ubah catatan sent lewat masa -> status TETAP sent tanpa pemulih", st)
                raise Rollback
        except Rollback:
            pass
        cek(await jejak(c, x["id"]) == awal, "kaos: sesudah ROLLBACK nol jejak (kepala + audit)")

    # 2) READ ONLY: ?status=expired == summary == SQL turunan
    async def gpb():
        return baca
    Q.get_pool = gpb
    async with baca.acquire() as c:
        for t in (KAOS, GG):
            hari = await Q.tanggal_dokumen(c, t)
            own_t = await pemilik(c, t)
            sq = {str(r["id"]) for r in await c.fetch(
                f"SELECT id FROM quotes WHERE tenant_id = $1 AND {PK.sql_kedaluwarsa('$2')}", t, hari)}
            s = (await Q.get_quote_summary(req(own_t, t))).data
            lst = await Q.list_quotes(req(own_t, t), status="expired", aksi=None, customer_id=None, start_date=None,
                                      end_date=None, search=None, skip=0, limit=100, page=1, offset=0)
            ids = {str(i.id) for i in lst.items}
            ok = (ids == sq and lst.total == len(sq) and s.expired_count == len(sq)
                  and s.expired_pending_count == len(sq) and s.lewat_masa_count == len(sq)
                  and all(i.is_expired for i in lst.items))
            cek(ok, f"{t}: ?status=expired == expired_count == expired_pending == lewat_masa == SQL",
                f"sql {len(sq)} daftar {lst.total} expired_count {s.expired_count} lewat_masa {s.lewat_masa_count}")

    await tulis.close()
    await baca.close()
    print(f"RINGKAS gagal={gagal}")


asyncio.run(main())
