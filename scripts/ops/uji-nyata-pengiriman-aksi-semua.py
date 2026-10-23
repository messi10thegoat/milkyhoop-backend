"""UJI NYATA Pengiriman aksi_semua (23 Okt 2026, opsi A aditif; putusan MASTER). MODE (env), tiap mode proses terpisah:
  TULIS  (pohon ini, kaos + grapgrap BACA; sintetis HANYA kaos di transaksi luar yang SELALU di-ROLLBACK):
     (1) tiap baris daftar + detail: aksi_semua[0] == aksi; aksi ∈ id ?aksi=aksi; tiap kunci aksi_semua memenuhi predikat MENTAH
         (perlu_resi/belum_dicatat = sql_aksi tanpa partisi, perlu_difaktur = sql_perlu_difaktur bila tagih sesudah kirim); detail == baris
     (2) SINTETIS tumpang (data nyata 0 baris perlu_difaktur): satu SJ asal 'so' kaos yang batal di-UN-void + baris serah kurir tanpa resi,
         di dalam rollback -> baris itu aksi = perlu_resi (partisi), aksi_semua = [perlu_resi, perlu_difaktur], TIDAK ada di ?aksi=perlu_difaktur
         (partisi utuh). Bila data tak cukup untuk tumpang -> FAIL (bukan lewat diam).
  JEPRET (pohon LAMA dan BARU): id ?aksi=k per kunci + summary {k}_count per tenant -> /out/<NAMA>.json; BANDING: lama == baru (pill tak berubah).
MERAH=1 : aksi_semua disabotase (urutan dibalik) -> (1)/(2) WAJIB gagal."""
import asyncio, inspect, json, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
import asyncpg
from starlette.requests import Request
from app.routers import deliveries as DL
from app.services import pengiriman_daftar as PG

TEN = ["kaos-biru-konveksi", "grapgrap-manado"]
MODE, NAMA = os.environ["MODE"], os.environ.get("NAMA", "")
gagal = []


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"), flush=True)
    if not ok: gagal.append(nama)


def req(t, uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": t, "user_id": uid}; return r


def kw(fn, r, **ov):
    o = {}
    for n, p in inspect.signature(fn).parameters.items():
        if n == "request": o[n] = r
        elif n in ov: o[n] = ov[n]
        elif p.default is not inspect.Parameter.empty:
            d = p.default
            o[n] = getattr(d, "default", d)
    return o


async def siap(c):
    class CM:
        async def __aenter__(s): return c
        async def __aexit__(s, *a): return False
    class P:
        def acquire(s): return CM()
    async def gp(): return P()
    DL.get_pool = gp
    if hasattr(DL, "get_db_pool"):
        DL.get_db_pool = gp


async def semua(t, uid, **ov):
    rows, skip = [], 0
    while True:
        r = await DL.list_deliveries(**kw(DL.list_deliveries, req(t, uid), skip=skip, limit=100, **ov))
        items = r["items"] if isinstance(r, dict) else r.items
        rows += items; skip += 100
        if not (r.get("has_more") if isinstance(r, dict) else False): return rows


async def predikat_mentah(c, t, fid, k) -> bool:
    sql = PG.sql_perlu_difaktur("f") if k == "perlu_difaktur" else PG.sql_aksi(k, "f")
    return bool(await c.fetchval(f"SELECT 1 FROM invoice_fulfillments f WHERE f.tenant_id = $1 AND f.id = $2 AND {sql}", t, fid))


async def periksa_baris(c, t, uid, label):
    rows = await semua(t, uid)
    per_aksi = {k: {str(x["id"]) for x in await semua(t, uid, aksi=k)} for k in PG.AKSI}
    tagih = await PG.tagih_sesudah_kirim(c, t)
    for x in rows:
        sm = x.get("aksi_semua")
        ok = isinstance(sm, list) and (sm[0] if sm else None) == x["aksi"]
        ok = ok and (x["aksi"] is None or str(x["id"]) in per_aksi[x["aksi"]])
        for k in (sm or []):
            ok = ok and await predikat_mentah(c, t, x["id"], k) and (k != "perlu_difaktur" or tagih)
        ok = ok and sm == [k for k in PG.AKSI if k in (sm or [])]
        if not ok:
            cek(f"{label} {t} baris {x.get('fulfillment_number') or x['id']}", False, f"aksi={x['aksi']} semua={sm}")
            return rows, per_aksi
    cek(f"{label} {t}: {len(rows)} baris -- aksi_semua[0]==aksi, aksi ∈ ?aksi=, tiap kunci memenuhi predikat mentah, urut AKSI", True)
    for x in rows[:20]:
        d = await DL.get_delivery_detail(**kw(DL.get_delivery_detail, req(t, uid), delivery_id=str(x["id"])))
        if d.get("aksi") != x["aksi"] or d.get("aksi_semua") != x["aksi_semua"]:
            cek(f"{label} {t} detail == baris", False, f"{x['id']}: {d.get('aksi_semua')} / {x['aksi_semua']}")
            break
    else:
        cek(f"{label} {t} detail == baris (aksi, aksi_semua; {min(20, len(rows))} baris)", True)
    return rows, per_aksi


async def tulis():
    if os.environ.get("MERAH") == "1":
        _a = PG.aksi_semua
        PG.aksi_semua = lambda s, m: list(reversed(_a(s, m)))
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    await siap(c)
    tx = c.transaction(); await tx.start()
    try:
        for t in TEN:
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', t)
            await periksa_baris(c, t, str(uid), "(1)")
        # (2) sintetis tumpang -- kaos saja, di dalam rollback
        t = "kaos-biru-konveksi"
        uid = str(await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', t))
        cand = await c.fetch("SELECT id FROM invoice_fulfillments WHERE tenant_id = $1 AND asal = 'so' AND status = 'voided' ORDER BY created_at", t)
        dapat = None
        for r in cand:
            sp = c.transaction(); await sp.start()
            await c.execute("UPDATE invoice_fulfillments SET status = 'posted', voided_at = NULL WHERE id = $1", r["id"])
            if await predikat_mentah(c, t, r["id"], "perlu_difaktur"):
                dapat = r["id"]
                break
            await sp.rollback()
        cek("(2) sintetis: ada SJ asal so kaos yang jadi perlu_difaktur sesudah di-un-void (rollback)", dapat is not None, f"{len(cand)} kandidat")
        if dapat:
            await c.execute("DELETE FROM pengiriman_resi WHERE tenant_id = $1 AND fulfillment_id = $2", t, dapat)
            if await c.fetchval("SELECT 1 FROM pengiriman_serah WHERE tenant_id = $1 AND fulfillment_id = $2", t, dapat):
                await c.execute("UPDATE pengiriman_serah SET jenis_serah = 'kurir', kirim_ke = 'TES E2E aksi_semua (rollback)' "
                                "WHERE tenant_id = $1 AND fulfillment_id = $2", t, dapat)
            else:
                await c.execute("INSERT INTO pengiriman_serah (tenant_id, fulfillment_id, jenis_serah, kirim_ke) "
                                "VALUES ($1, $2, 'kurir', 'TES E2E aksi_semua (rollback)')", t, dapat)
            rows, per = await periksa_baris(c, t, uid, "(2)")
            x = next((r for r in rows if str(r["id"]) == str(dapat)), None)
            cek("(2) SJ tumpang: aksi = perlu_resi (partisi), aksi_semua = [perlu_resi, perlu_difaktur]",
                x is not None and x["aksi"] == "perlu_resi" and x["aksi_semua"] == ["perlu_resi", "perlu_difaktur"], x and (x["aksi"], x["aksi_semua"]))
            cek("(2) SJ tumpang TIDAK di ?aksi=perlu_difaktur (partisi utuh), ADA di ?aksi=perlu_resi",
                str(dapat) not in per["perlu_difaktur"] and str(dapat) in per["perlu_resi"])
    finally:
        await tx.rollback(); await c.close()
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)


async def jepret():
    c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
    await siap(c)
    out = {}
    for t in TEN:
        uid = str(await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', t))
        sm = await DL.get_deliveries_summary(**kw(DL.get_deliveries_summary, req(t, uid)))
        out[t] = {"summary": {k: v for k, v in (sm or {}).items() if k.endswith("_count")},
                  "aksi": {k: sorted(str(x["id"]) for x in await semua(t, uid, aksi=k)) for k in PG.AKSI},
                  "aksi_baris": sorted((str(x["id"]), x["aksi"]) for x in await semua(t, uid))}
    await c.close()
    json.dump(out, open(f"/out/{NAMA}.json", "w"), default=str); print(f"JEPRET {NAMA} ok")


async def main():
    if MODE == "TULIS":
        return await tulis()
    if MODE == "JEPRET":
        return await jepret()
    if MODE == "BANDING":
        a, b = json.load(open("/out/lama.json")), json.load(open("/out/baru.json"))
        for t in TEN:
            for k in ("summary", "aksi", "aksi_baris"):
                cek(f"pill tak berubah LAMA == BARU {t}: {k}", a[t][k] == b[t][k], f"{a[t][k]} / {b[t][k]}")
        print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
