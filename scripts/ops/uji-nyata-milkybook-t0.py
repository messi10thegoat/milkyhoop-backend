"""UJI NYATA Milkybook T0 (24 Okt 2026): rute NYATA, ROLLBACK-ONLY di kaos (nama "TES E2E"). Mode: baru [merah_idem|merah_penentu]
Pelanggan & barang: pratinjau bersih -> buat ber-kunci -> ulang kunci sama = id SAMA + X-Idempotent-Replay -> kunci sama isi beda = 409
IDEMPOTENCY_KEY_REUSED -> pratinjau nama sama = blok NAMA_*_DIPAKAI == galat tulis tanpa kunci (kode SAMA) -> saldo awal / jasa dikirim.
Penawaran draf kaos: PATCH ber-kunci + ulang = replay; isi beda = 409; void ber-kunci + ulang = replay (bukan 400 'sudah batal').
Kontrol merah (WAJIB FAIL): merah_idem = kunci dibuang (kunci_dari -> None) -> ulang membuat pelanggan KEDUA / galat;
merah_penentu = penentu pelanggan dilumpuhkan -> pratinjau tak memblok nama kembar (pratinjau != tulis)."""
import asyncio, json, os, sys
from uuid import uuid4
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

T = "kaos-biru-konveksi"
MERAH = sys.argv[2] if len(sys.argv) > 2 else ""
gagal = []
CONTOH = {}


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok:
        gagal.append(nama)


async def main():
    from app.routers import customers as RC, items as RI, quotes as RQ
    from app.schemas.customers import CreateCustomerRequest
    from app.schemas.items import CreateItemRequest
    from app.schemas.quotes import UpdateQuoteRequest, VoidQuoteRequest
    from app.services import idem_buat
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()
        async def close(s): return None  # koneksi harness dipakai ulang; ROLLBACK luar di akhir
    k = Kon()

    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False

    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    async def dbc(): return k
    for m in (RC, RQ):
        m.get_pool = gp
    RI.get_db_connection = dbc
    if MERAH == "merah_idem":
        idem_buat.kunci_dari = lambda request, kunci_body=None: None
    if MERAH == "merah_penentu":
        async def lumpuh(conn, tid, body): return None
        RC._siapkan_buat_pelanggan = lumpuh
    uid = await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id = $1 AND created_by IS NOT NULL LIMIT 1", T)

    def req(kunci=None):
        async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
        hdr = [(b"x-idempotency-key", kunci.encode())] if kunci else []
        r = Request({"type": "http", "method": "POST", "path": "/", "headers": hdr, "query_string": b""}, receive)
        r.state.user = {"tenant_id": T, "user_id": str(uid), "role": "OWNER", "username": "TES E2E"}
        return r

    async def galat(coro):
        try:
            return None, await coro
        except HTTPException as e:
            return e, None

    luar = c.transaction()
    await luar.start()
    try:
        # ───────── pelanggan ─────────
        nama = f"TES E2E MB {uuid4().hex[:6]}"
        badan = {"name": nama, "payment_terms_days": 14}
        pv = (await RC.preview_create_customer(req(), dict(badan)))["data"]
        CONTOH["customers_preview_bersih"] = pv
        cek("pelanggan: pratinjau bersih ok, blocks [], payload == badan", pv["ok"] and pv["blocks"] == [] and pv["payload"] == badan, pv)
        k1 = f"TES-E2E-{uuid4()}"
        r1h, r2h = Response(), Response()
        _, r1 = await galat(RC.create_customer(req(k1), CreateCustomerRequest(**badan), r1h))
        _, r2 = await galat(RC.create_customer(req(k1), CreateCustomerRequest(**badan), r2h))
        n_nama = await c.fetchval("SELECT count(*) FROM customers WHERE tenant_id = $1 AND nama = $2", T, nama)
        cek("pelanggan: kunci sama 2x = id SAMA + X-Idempotent-Replay, 1 baris",
            r1 and r2 and r1["data"]["id"] == r2["data"]["id"] and r2h.headers.get("X-Idempotent-Replay") == "true" and n_nama == 1,
            (r1, r2, dict(r2h.headers), n_nama))
        e, _ = await galat(RC.create_customer(req(k1), CreateCustomerRequest(**{**badan, "payment_terms_days": 30}), Response()))
        cek("pelanggan: kunci sama isi beda = 409 IDEMPOTENCY_KEY_REUSED", e is not None and e.status_code == 409
            and e.detail.get("code") == "IDEMPOTENCY_KEY_REUSED", e and e.detail)
        pv = (await RC.preview_create_customer(req(), dict(badan)))["data"]
        e, _ = await galat(RC.create_customer(req(), CreateCustomerRequest(**badan), Response()))
        CONTOH["customers_preview_blok"] = pv
        cek("pelanggan: pratinjau nama kembar = blok NAMA_PELANGGAN_DIPAKAI == galat tulis (kode SAMA)",
            [b["code"] for b in pv["blocks"]] == ["NAMA_PELANGGAN_DIPAKAI"] and e is not None and e.status_code == 409
            and e.detail.get("code") == "NAMA_PELANGGAN_DIPAKAI", (pv, e and e.detail))
        sa = {"name": f"TES E2E MB SA {uuid4().hex[:6]}", "ar_opening_balance": 1000}
        pv = (await RC.preview_create_customer(req(), dict(sa)))["data"]
        e, _ = await galat(RC.create_customer(req(), CreateCustomerRequest(**sa), Response()))
        cek("pelanggan: saldo awal = blok SALDO_AWAL_SAAT_BUAT; tulis 422 pesan SAMA",
            [b["code"] for b in pv["blocks"]] == ["SALDO_AWAL_SAAT_BUAT"] and e is not None and e.status_code == 422
            and e.detail == pv["blocks"][0]["message"], (pv, e and e.detail))

        # ───────── barang ─────────
        nb = f"TES E2E MB BRG {uuid4().hex[:6]}"
        bb = {"name": nb, "item_type": "non_inventory", "track_inventory": False, "base_unit": "pcs", "sales_price": 15000}
        pv = (await RI.preview_create_item(req(), dict(bb)))["data"]
        CONTOH["items_preview_bersih"] = pv
        cek("barang: pratinjau bersih ok + rencana.item_code terisi", pv["ok"] and pv["rencana"]["item_code"], pv)
        k2 = f"TES-E2E-{uuid4()}"
        h2 = Response()
        _, i1 = await galat(RI.create_item(req(k2), CreateItemRequest(**bb), Response()))
        _, i2 = await galat(RI.create_item(req(k2), CreateItemRequest(**bb), h2))
        n_b = await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND nama_produk = $2", T, nb)
        cek("barang: kunci sama 2x = id SAMA + replay, 1 baris (bukan 409 nama dipakai)",
            i1 and i2 and i1["data"]["id"] == i2["data"]["id"] and h2.headers.get("X-Idempotent-Replay") == "true" and n_b == 1,
            (i1, i2, n_b))
        pv = (await RI.preview_create_item(req(), dict(bb)))["data"]
        e, _ = await galat(RI.create_item(req(), CreateItemRequest(**bb), Response()))
        cek("barang: pratinjau nama kembar = NAMA_BARANG_DIPAKAI == galat tulis",
            [b["code"] for b in pv["blocks"]] == ["NAMA_BARANG_DIPAKAI"] and e is not None and e.detail.get("code") == "NAMA_BARANG_DIPAKAI",
            (pv, e and e.detail))
        pv = (await RI.preview_create_item(req(), {"name": "TES E2E jasa", "item_type": "service", "base_unit": "jam", "bisa_dikirim": True}))["data"]
        CONTOH["items_preview_data_tidak_sah"] = pv
        cek("barang: jasa + bisa_dikirim = 200 blok DATA_TIDAK_SAH field bisa_dikirim (bukan 422)",
            not pv["ok"] and any(b["code"] == "DATA_TIDAK_SAH" and b["field"] == "bisa_dikirim" for b in pv["blocks"]), pv)

        # ───────── penawaran (draf kaos yang ada; diubah & dibatalkan DI DALAM rollback) ─────────
        qid = await c.fetchval("SELECT id::text FROM quotes WHERE tenant_id = $1 AND status = 'draft' ORDER BY created_at DESC LIMIT 1", T)
        cek("penawaran: ada draf kaos untuk diuji", qid is not None, qid)
        if qid:
            k3 = f"TES-E2E-{uuid4()}"
            h3 = Response()
            e1, u1 = await galat(RQ.update_quote(req(k3), qid, UpdateQuoteRequest(notes="TES E2E MB catatan"), Response()))
            e2, u2 = await galat(RQ.update_quote(req(k3), qid, UpdateQuoteRequest(notes="TES E2E MB catatan"), h3))
            ub = lambda u: u.model_dump(mode="json") if hasattr(u, "model_dump") else u  # noqa: E731
            cek("penawaran: PATCH kunci sama 2x = respons SAMA + replay", u1 is not None and u2 is not None
                and json.dumps(ub(u1), sort_keys=True, default=str) == json.dumps(ub(u2), sort_keys=True, default=str)
                and h3.headers.get("X-Idempotent-Replay") == "true", (e1 and e1.detail, e2 and e2.detail, dict(h3.headers)))
            e, _ = await galat(RQ.update_quote(req(k3), qid, UpdateQuoteRequest(notes="TES E2E MB lain"), Response()))
            cek("penawaran: PATCH kunci sama isi beda = 409", e is not None and e.status_code == 409, e and e.detail)
            k4 = f"TES-E2E-{uuid4()}"
            h4 = Response()
            e1, v1 = await galat(RQ.void_quote(req(k4), qid, VoidQuoteRequest(reason="TES E2E MB"), Response()))
            e2, v2 = await galat(RQ.void_quote(req(k4), qid, VoidQuoteRequest(reason="TES E2E MB"), h4))
            cek("penawaran: void kunci sama 2x = replay (bukan galat 'sudah batal')",
                v1 is not None and v2 is not None and h4.headers.get("X-Idempotent-Replay") == "true",
                (e1 and e1.detail, e2 and e2.detail))
    finally:
        await luar.rollback()
        sisa = await c.fetchval("SELECT count(*) FROM customers WHERE tenant_id = $1 AND nama LIKE 'TES E2E MB%'", T)
        sisa_b = await c.fetchval("SELECT count(*) FROM products WHERE tenant_id = $1 AND nama_produk LIKE 'TES E2E MB%'", T)
        cek("rollback: nol pelanggan/barang TES E2E MB", sisa == 0 and sisa_b == 0, (sisa, sisa_b))
        await c.close()
    print("CONTOH " + json.dumps(CONTOH, default=str))
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
