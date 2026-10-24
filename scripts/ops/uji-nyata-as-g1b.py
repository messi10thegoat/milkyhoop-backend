"""UJI NYATA AS-G1b (24 Okt 2026) PATCH penawaran If-Match x idem QUOTE_UPDATE, ROLLBACK-ONLY di kaos (TES E2E). Mode: baru [merah_ifmatch]
Penawaran draf TES E2E -> updated_at dimundurkan 1 jam DALAM rollback. Pemicu trg_quotes_updated_at (BEFORE UPDATE) menulis NOW() yang TETAP
sepanjang transaksi -> UPDATE biasa ditimpa pemicu; maka SATU pernyataan itu di bawah SET LOCAL session_replication_role = replica (lalu origin).
(1) PATCH kunci K + If-Match v1 = berhasil, updated_at maju; (2) ulang K + v1 basi = jawaban PERTAMA + X-Idempotent-Replay;
(3) kunci baru + v1 basi = 412; (4) kontrol positif: kunci baru + If-Match terkini = berhasil;
(5) pola Milkybook (tanpa If-Match, CONVERSATIONAL 24 Okt): kunci sama 2x = replay + riwayat QUOTE_UPDATED SEKALI; kunci sama isi beda = 409.
If-Match membaca lewat db_pool sendiri -> diarahkan ke koneksi transaksi YANG SAMA (tanpa itu baris draf tak terlihat = no-op diam).
Kontrol merah (WAJIB FAIL): merah_ifmatch = kode NYATA dari SALINAN pohon dengan If-Match kembali SEBELUM replay -> (2) jadi 412."""
import asyncio, os, sys
from decimal import Decimal as D
from uuid import UUID, uuid4
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

T = "kaos-biru-konveksi"
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok:
        gagal.append(nama)


async def main():
    from app.routers import quotes as RQ
    from app.schemas.quotes import CreateQuoteRequest, UpdateQuoteRequest
    from app.utils.tanggal_tenant import tanggal_dokumen
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")
    if await c.fetchval("SHOW is_superuser") != "on":
        sys.exit("BUTUH superuser untuk SET LOCAL session_replication_role (pemundur updated_at); tak dijalankan")
    uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()

    class CMA:
        async def __aenter__(s): return Kon()
        async def __aexit__(s, *a): return False

    class PA:
        def acquire(s): return CMA()
    async def gpa(): return PA()
    RQ.get_pool = gpa
    import app.services.db_pool as DBP
    DBP.get_db_pool = gpa

    def req(kunci=None, if_match=None):
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        hdr = ([(b"x-idempotency-key", kunci.encode())] if kunci else []) + ([(b"if-match", if_match.encode())] if if_match else [])
        r = Request({"type": "http", "method": "PATCH", "path": "/", "headers": hdr, "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
        return r

    async def galat(coro):
        try:
            return None, await coro
        except HTTPException as e:
            return e, None

    def dt(x): return x.model_dump(mode="json") if hasattr(x, "model_dump") else x
    ua = lambda q: c.fetchval("SELECT updated_at FROM quotes WHERE id = $1", q)  # noqa: E731
    n_riwayat = lambda q: c.fetchval("""SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2 AND "eventType" = 'QUOTE_UPDATED'""", T, q)  # noqa: E731
    luar = c.transaction()
    await luar.start()
    try:
        hari = await tanggal_dokumen(c, T)
        cust = await c.fetchrow("SELECT id::text, nama FROM customers WHERE tenant_id = $1 AND is_active ORDER BY created_at LIMIT 1", T)

        async def penawaran():
            r = dt(await RQ.create_quote(req(), CreateQuoteRequest(quote_date=hari, customer_id=cust["id"], customer_name=cust["nama"],
                                                                   subject="TES E2E AS-G1b", notes="TES E2E AS-G1b",
                                                                   items=[{"description": "TES E2E barang", "quantity": D("1"), "unit_price": D("1000")}])))
            return UUID(str(r["data"]["id"] if "id" in r["data"] else r["data"]["quote_id"]))
        q = await penawaran()
        # Syarat MASTER 24 Okt: (a) DALAM transaksi rollback, (b) TEPAT SATU UPDATE updated_at baris TES E2E di antara replica..origin,
        # (c) session_replication_role == 'origin' DIPASTIKAN sebelum tulisan lain apa pun (tak ada penjaga Law/rantai hash terlewati).
        assert c.is_in_transaction()
        await c.execute("SET LOCAL session_replication_role = replica")
        n_mundur = await c.execute("""UPDATE quotes SET updated_at = now() - interval '1 hour'
                                      WHERE id = $1 AND tenant_id = $2 AND notes = 'TES E2E AS-G1b'""", q, T)
        await c.execute("SET LOCAL session_replication_role = origin")
        peran = await c.fetchval("SHOW session_replication_role")
        cek(f"(0) pemundur updated_at: tepat 1 baris TES E2E ({n_mundur}), peran kembali origin ({peran}) sebelum tulisan lain",
            n_mundur == "UPDATE 1" and peran == "origin", (n_mundur, peran))
        if peran != "origin":
            raise SystemExit("session_replication_role bukan origin -- berhenti sebelum tulisan lain")
        v1 = (await ua(q)).isoformat()
        k, h = f"TES-E2E-{uuid4()}", Response()
        e1, r1 = await galat(RQ.update_quote(req(k, v1), str(q), UpdateQuoteRequest(subject="TES E2E AS-G1b ubah"), Response()))
        v2 = (await ua(q)).isoformat()
        cek(f"(1) PATCH K + If-Match v1 = berhasil; updated_at maju ({v1} -> {v2})", r1 is not None and v2 != v1, (e1 and e1.detail, v1, v2))
        e2, r2 = await galat(RQ.update_quote(req(k, v1), str(q), UpdateQuoteRequest(subject="TES E2E AS-G1b ubah"), h))
        cek("(2) ulang K + If-Match v1 BASI = jawaban PERTAMA + X-Idempotent-Replay (bukan 412)",
            r2 is not None and dt(r2) == dt(r1) and h.headers.get("X-Idempotent-Replay") == "true", e2 and (e2.status_code, e2.detail))
        e3, _ = await galat(RQ.update_quote(req(f"TES-E2E-{uuid4()}", v1), str(q), UpdateQuoteRequest(subject="TES E2E AS-G1b lain"), Response()))
        cek("(3) kunci baru + If-Match v1 basi = 412 STALE_WRITE", e3 is not None and e3.status_code == 412, e3 and (e3.status_code, e3.detail))
        e4, r4 = await galat(RQ.update_quote(req(f"TES-E2E-{uuid4()}", v2), str(q), UpdateQuoteRequest(subject="TES E2E AS-G1b terkini"), Response()))
        cek("(4) kontrol positif: kunci baru + If-Match terkini = berhasil", r4 is not None, e4 and (e4.status_code, e4.detail))
        # (5) pola Milkybook: tanpa If-Match
        q2 = await penawaran()
        n0 = await n_riwayat(q2)
        k5, h5 = f"TES-E2E-{uuid4()}", Response()
        e5a, r5a = await galat(RQ.update_quote(req(k5), str(q2), UpdateQuoteRequest(subject="TES E2E AS-G1b milkybook"), Response()))
        e5b, r5b = await galat(RQ.update_quote(req(k5), str(q2), UpdateQuoteRequest(subject="TES E2E AS-G1b milkybook"), h5))
        e5c, _ = await galat(RQ.update_quote(req(k5), str(q2), UpdateQuoteRequest(subject="TES E2E AS-G1b isi beda"), Response()))
        n1 = await n_riwayat(q2)
        cek(f"(5) Milkybook tanpa If-Match: kunci sama 2x = replay + QUOTE_UPDATED SEKALI ({n0}->{n1}); isi beda = 409",
            r5a is not None and dt(r5b) == dt(r5a) and h5.headers.get("X-Idempotent-Replay") == "true" and n1 == n0 + 1
            and e5c is not None and e5c.status_code == 409, (e5a and e5a.detail, e5b and e5b.detail, n0, n1, e5c and e5c.status_code))
    finally:
        await luar.rollback()
        sisa = await c.fetchval("SELECT count(*) FROM quotes WHERE tenant_id = $1 AND notes = 'TES E2E AS-G1b'", T)
        cek("rollback: nol penawaran TES E2E AS-G1b", sisa == 0, sisa)
        await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
