"""UJI NYATA AS-G4 (24 Okt 2026): rute NYATA, ROLLBACK-ONLY di kaos. Mode: baru [merah_idem|merah_penentu]
SJ terposting BERJURNAL (HPP + pendapatan; asal faktur -- kaos tak punya SJ-pesanan aktif): pratinjau batal ok -> batal ber-kunci -> ulang kunci
sama = replay + JUMLAH JURNAL PEMBALIK TETAP (syarat MASTER: dihitung di DB, bukan hanya respons) -> kunci sama alasan beda = 409.
Serah: pratinjau == PUT (sesudah sama; resi dobel = blok SERAH_TIDAK_SAH == galat PUT 422) -> PUT ber-kunci 2x = replay -> isi beda 409;
SJ batal -> pratinjau blok SJ_VOID == PUT 409.
Kontrol merah (WAJIB FAIL): merah_idem = kunci dibuang -> ulang batal = 'sudah batal' bukan replay / serah tertulis dua kali;
merah_penentu = penentu serah dilumpuhkan di pratinjau -> pratinjau tak memblok (pratinjau != tulis)."""
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
    from app.routers import deliveries as RD, sj_so as RS
    from app.services import idem_buat, pengiriman_serah as PS
    c = await asyncpg.connect(os.environ["URL"])
    if not await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', T):
        sys.exit(f"TENANT TAK ADA: {T}")

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()
    k = Kon()

    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False

    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    RD.get_pool = gp
    RS._pool = gp
    import app.services.db_pool as DBP
    DBP.get_db_pool = gp
    if MERAH == "merah_idem":
        idem_buat.kunci_dari = lambda request, kunci_body=None: None
    if MERAH == "merah_penentu":
        asli = RD._rencana_serah

        async def lumpuh(conn, tid, fid, body):
            r = await asli(conn, tid, fid, PS.SerahIn())  # badan diabaikan -> validasi badan lolos
            return r
        RD._rencana_serah = lumpuh
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
        # ───────── serah (SJ aktif mana pun) ─────────
        sj = await c.fetchrow("""SELECT id, fulfillment_number, journal_id, revenue_journal_id FROM invoice_fulfillments
                                 WHERE tenant_id = $1 AND status = 'posted' AND journal_id IS NOT NULL ORDER BY created_at DESC""", T)
        cek("ada SJ terposting berjurnal di kaos", sj is not None, sj)
        fid = str(sj["id"])
        badan = {"kirim_ke": "TES E2E alamat kirim"}
        pv = (await RD.preview_ubah_serah_surat_jalan(fid, req(), dict(badan)))["data"]
        k1 = f"TES-E2E-{uuid4()}"
        h1, h2 = Response(), Response()
        e1, w1 = await galat(RD.ubah_serah_surat_jalan(fid, req(k1), PS.SerahIn(**badan), h1))
        e2, w2 = await galat(RD.ubah_serah_surat_jalan(fid, req(k1), PS.SerahIn(**badan), h2))
        CONTOH["serah_preview"] = pv
        cek("serah: pratinjau ok dan 'sesudah' == hasil PUT (pratinjau == tulis)",
            pv["ok"] and w1 is not None and {kk: pv["sesudah"][kk] for kk in pv["sesudah"]} == {kk: w1["data"][kk] for kk in pv["sesudah"]},
            (pv, e1 and e1.detail, w1))
        n_tulis = await c.fetchval("""SELECT count(*) FROM audit_logs WHERE tenant_id = $1 AND entity_id = $2 AND "eventType" = 'SJ_SERAH_CHANGED'
                                      AND "createdAt" > now() - interval '5 minutes'""", T, sj["id"])
        cek("serah: PUT kunci sama 2x = replay + riwayat tertulis SEKALI", w2 is not None and w2 == w1
            and h2.headers.get("X-Idempotent-Replay") == "true" and n_tulis == 1, (e2 and e2.detail, dict(h2.headers), n_tulis))
        e, _ = await galat(RD.ubah_serah_surat_jalan(fid, req(k1), PS.SerahIn(kirim_ke="TES E2E lain"), Response()))
        cek("serah: kunci sama isi beda = 409", e is not None and e.status_code == 409, e and e.detail)
        dobel = {"resi": [{"nomor": "TESE2E-D"}, {"nomor": "TESE2E-D"}]}
        pv = (await RD.preview_ubah_serah_surat_jalan(fid, req(), dict(dobel)))["data"]
        e, _ = await galat(RD.ubah_serah_surat_jalan(fid, req(), PS.SerahIn(**dobel), Response()))
        CONTOH["serah_preview_blok"] = pv
        cek("serah: resi dobel = blok SERAH_TIDAK_SAH == galat PUT 422 (kode SAMA)",
            [b["code"] for b in pv["blocks"]] == ["SERAH_TIDAK_SAH"] and e is not None and e.status_code == 422
            and e.detail.get("code") == "SERAH_TIDAK_SAH", (pv, e and e.detail))

        # ───────── batal SJ berjurnal ─────────
        calon = await c.fetch("""SELECT id, fulfillment_number, journal_id, revenue_journal_id FROM invoice_fulfillments
                                 WHERE tenant_id = $1 AND status = 'posted' AND journal_id IS NOT NULL ORDER BY created_at DESC""", T)
        pilih = None
        for x in calon:
            p = (await RS.preview_void_sj(req(), str(x["id"]), RS.AlasanIn(reason="TES E2E")))["data"]
            if p["ok"]:
                pilih = x
                break
        cek("ada SJ berjurnal yang boleh dibatalkan (pratinjau ok)", pilih is not None, len(calon))
        if pilih:
            jur = [j for j in (pilih["journal_id"], pilih["revenue_journal_id"]) if j]
            hitung = lambda: c.fetchval("SELECT count(*) FROM journal_entries WHERE reversal_of_id = ANY($1::uuid[])", jur)  # noqa: E731
            n0 = await hitung()
            k2 = f"TES-E2E-{uuid4()}"
            hv = Response()
            e1, v1 = await galat(RS.void_sj(req(k2), str(pilih["id"]), RS.AlasanIn(reason="TES E2E AS-G4"), Response()))
            n1 = await hitung()
            e2, v2 = await galat(RS.void_sj(req(k2), str(pilih["id"]), RS.AlasanIn(reason="TES E2E AS-G4"), hv))
            n2 = await hitung()
            CONTOH["void"] = {"pertama": v1, "ulang": v2, "pembalik": [n0, n1, n2]}
            cek(f"batal SJ {pilih['fulfillment_number']}: pembalik lahir sekali ({n0}->{n1}) dan ulang kunci sama = replay ulang False, pembalik TETAP {n2}",
                v1 is not None and v1["ulang"] is False and n1 > n0 and v2 == v1 and hv.headers.get("X-Idempotent-Replay") == "true" and n2 == n1,
                (e1 and e1.detail, v1, e2 and e2.detail, v2, n0, n1, n2))
            e, _ = await galat(RS.void_sj(req(k2), str(pilih["id"]), RS.AlasanIn(reason="TES E2E alasan lain"), Response()))
            cek("batal SJ: kunci sama alasan beda = 409", e is not None and e.status_code == 409, e and e.detail)
            pv = (await RD.preview_ubah_serah_surat_jalan(str(pilih["id"]), req(), {"kirim_ke": "x"}))["data"]
            e, _ = await galat(RD.ubah_serah_surat_jalan(str(pilih["id"]), req(), PS.SerahIn(kirim_ke="x"), Response()))
            cek("serah atas SJ batal: blok SJ_VOID == galat PUT 409", [b["code"] for b in pv["blocks"]] == ["SJ_VOID"]
                and e is not None and e.status_code == 409 and e.detail.get("code") == "SJ_VOID", (pv, e and e.detail))
        # ───────── jalur ASAL PESANAN (syarat MASTER): SJ TES E2E dari SO kaos di rollback -> batal ber-kunci 2x ─────────
        so_calon = await c.fetch("""SELECT id, order_number FROM sales_orders WHERE tenant_id = $1 AND status IN ('confirmed', 'partially_shipped',
                                    'partial', 'in_progress') ORDER BY created_at DESC LIMIT 40""", T)
        so_ok = None
        for so in so_calon:
            e, p = await galat(RS.preview_sj_so(req(), str(so["id"]), RS.S.SJSOIn(notes="TES E2E AS-G4")))
            if p and p["data"]["ok"] and p["data"]["lines"]:
                so_ok = so
                break
        if so_ok is None:
            print(f"INFO jalur asal pesanan: tak ada SO kaos yang bisa dikirim ({len(so_calon)} calon) -- diliput tes unit saja", flush=True)
        else:
            r = await RS.create_sj_so(req(f"TES-E2E-{uuid4()}"), Response(), str(so_ok["id"]), RS.S.SJSOIn(notes="TES E2E AS-G4"))
            from uuid import UUID as _U
            fr = await c.fetchrow("SELECT id, journal_id, revenue_journal_id, asal FROM invoice_fulfillments WHERE id = $1", _U(r["data"]["id"]))
            jur = [j for j in (fr["journal_id"], fr["revenue_journal_id"]) if j]
            hitung2 = lambda: c.fetchval("SELECT count(*) FROM journal_entries WHERE reversal_of_id = ANY($1::uuid[])", jur)  # noqa: E731
            k5 = f"TES-E2E-{uuid4()}"
            hv2 = Response()
            e1, v1 = await galat(RS.void_sj(req(k5), str(fr["id"]), RS.AlasanIn(reason="TES E2E AS-G4 SO"), Response()))
            m1 = await hitung2()
            e2, v2 = await galat(RS.void_sj(req(k5), str(fr["id"]), RS.AlasanIn(reason="TES E2E AS-G4 SO"), hv2))
            m2 = await hitung2()
            print(f"INFO jalur asal pesanan NYATA: SJ dari {so_ok['order_number']} asal={fr['asal']} berjurnal={len(jur)}", flush=True)
            cek(f"batal SJ asal PESANAN: ulang kunci sama = replay, pembalik TETAP ({m1} -> {m2})",
                v1 is not None and v1["ulang"] is False and v2 == v1 and hv2.headers.get("X-Idempotent-Replay") == "true" and m2 == m1
                and (m1 >= 1 or not jur), (e1 and e1.detail, v1, e2 and e2.detail, v2, m1, m2))
    finally:
        await luar.rollback()
        sisa = await c.fetchval("SELECT count(*) FROM pengiriman_serah WHERE tenant_id = $1 AND kirim_ke LIKE 'TES E2E%'", T)
        cek("rollback: nol serah TES E2E", sisa == 0, sisa)
        await c.close()
    print("CONTOH " + json.dumps(CONTOH, default=str))
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
