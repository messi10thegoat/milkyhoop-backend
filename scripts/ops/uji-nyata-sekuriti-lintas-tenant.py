"""UJI NYATA SEKURITI lintas tenant (24 Okt 2026). Pemanggil = OWNER kaos; "korban" = grapgrap-manado (HANYA dibaca: hitungan, tak
pernah mencetak isi). SATU transaksi luar SELALU di-ROLLBACK (get_pool/get_db_connection tertambal; tiap skenario di savepoint).
  A GET /products/recent-sales ?tenant_id=korban -> produk milik tenant lain di hasil (LAMA >0, BARU 0; BARU = parameter tak ada lagi)
  B POST /consolidation/groups/{g}/entities entity_tenant_id=korban (pemanggil bukan anggota korban) -> LAMA 201 + invarian 1, BARU 403 + 0
  C POST /intercompany/transactions from=korban -> LAMA 201 + baris "atas nama" korban, BARU 403 + 0;
    C+ from=kaos to=tenant-tak-ada -> BARU 404; C++ from=kaos to=korban (jalur SAH) -> 201 di kedua pohon (kontrol positif)
Rekaman per pohon -> /out/<NAMA>.json; BANDING menilai keduanya. NET 0 diukur proses terpisah sesudah selesai.
MERAH=1: harapan A pohon BARU dibalik (harus >0) -> WAJIB gagal."""
import asyncio, inspect, json, os, sys, uuid
from datetime import date
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from starlette.requests import Request

T, KORBAN, TAK_ADA = "kaos-biru-konveksi", "grapgrap-manado", "tenant-tak-ada-uji-sekuriti"
MODE, NAMA, MERAH = os.environ["MODE"], os.environ.get("NAMA", ""), os.environ.get("MERAH") == "1"
gagal = []


def cek(ok, l, i=""):
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else ""), flush=True)
    if not ok: gagal.append(l)


SQL_JEPRET = {
    "grup": "SELECT count(*) FROM consolidation_groups WHERE tenant_id = $1",
    "entitas": "SELECT count(*) FROM consolidation_entities",
    "ic": "SELECT count(*) FROM intercompany_transactions WHERE tenant_id = $1 OR from_entity_tenant_id = $1 OR to_entity_tenant_id = $1",
    "coa": "SELECT count(*) FROM chart_of_accounts WHERE tenant_id = $1",
    "jurnal": "SELECT count(*), COALESCE(sum(total_debit),0)::text FROM journal_entries WHERE tenant_id = $1",
    "seq": "SELECT json_agg(json_build_object('p', prefix, 'n', last_number) ORDER BY prefix, year, month)::text FROM journal_number_sequences WHERE tenant_id = $1",
}
INVARIAN_B = """SELECT count(*) FROM consolidation_entities ce JOIN consolidation_groups cg ON cg.id = ce.group_id
  WHERE ce.entity_tenant_id <> cg.tenant_id AND NOT EXISTS (
    SELECT 1 FROM user_tenant_roles a JOIN user_tenant_roles b ON a.user_id = b.user_id
    WHERE a.tenant_id = cg.tenant_id AND b.tenant_id = ce.entity_tenant_id AND upper(a.status) = 'ACTIVE' AND upper(b.status) = 'ACTIVE')"""


def req(uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": T, "user_id": str(uid)}; return r


async def tulis():
    from fastapi import HTTPException
    from app.routers import products as P, consolidation as CO, intercompany as IC
    from app.schemas.consolidation import CreateConsolidationEntityRequest as ER
    from app.schemas.intercompany import CreateIntercompanyTransactionRequest as IR
    from app.services.role_resolution import has_active_membership
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    for t in (T, KORBAN):
        assert await c.fetchval('SELECT 1 FROM "Tenant" WHERE id = $1', t), f"tenant {t} tak ada -- harness menolak"
    uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId" = $1 ORDER BY "createdAt" LIMIT 1', T)
    assert not await has_active_membership(c, str(uid), KORBAN), "prasyarat: pemanggil BUKAN anggota korban"

    class Kon:
        def __getattr__(s, n): return getattr(c, n)
        def transaction(s, **kw): return c.transaction()
        async def close(s): return None  # get_db_connection lama memanggil close(): jangan tutup koneksi harness
    k = Kon()
    class CM:
        async def __aenter__(s): return k
        async def __aexit__(s, *a): return False
    class Pool:
        def acquire(s): return CM()
    async def gp(): return Pool()
    async def gdc(): return k
    CO.get_pool = gp; IC.get_pool = gp; P.get_db_connection = gdc

    async def coba(fn):
        sp = c.transaction(); await sp.start()
        try:
            await fn(); out = "OK"
        except HTTPException as e:
            out = str(e.status_code)
        except Exception as e:  # noqa: BLE001
            out = f"GALAT {type(e).__name__}: {str(e)[:100]}"
        return out, sp

    h = {}
    tx = c.transaction(); await tx.start()
    try:
        # A
        punya_param = "tenant_id" in inspect.signature(P.get_recent_sales_products).parameters
        kw = {"tenant_id": KORBAN} if punya_param else {}  # BARU: ?tenant_id= diabaikan FastAPI = panggilan tanpa argumen itu
        r = await P.get_recent_sales_products(req(uid), limit=20, **kw)
        ids = [uuid.UUID(p.id) for p in r.products]
        asing = await c.fetchval("SELECT count(*) FROM products WHERE id = ANY($1::uuid[]) AND tenant_id <> $2", ids, T)
        h["A"] = {"param_tenant_id": punya_param, "n": len(ids), "asing": asing}
        # B
        g = await c.fetchval("INSERT INTO consolidation_groups (tenant_id, code, name) VALUES ($1, 'TES-SEK', 'TES E2E sekuriti') RETURNING id", T)
        wajib = {kk: "X" for kk, v in ER.model_fields.items() if v.is_required()}
        wajib["entity_tenant_id"] = KORBAN
        st, sp = await coba(lambda: CO.add_entity(req(uid), g, ER(**wajib)))
        h["B"] = {"status": st, "invarian": await c.fetchval(INVARIAN_B)}
        await sp.rollback()
        # C -- jalur tulis IC butuh akun 1-10900 yang TAK ADA di tenant mana pun (diukur 24 Okt: 0 tenant, 0 transaksi IC sepanjang
        # masa = rute 500 untuk semua orang hari ini, celah LATEN). Akun disisipkan di dalam transaksi ROLLBACK agar kedua pohon
        # mencapai penjaga yang sebenarnya; tanpa itu kontrol merah C tak bermakna.
        await c.execute("""INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, is_detail)
                           SELECT $1, '1-10900', 'TES E2E Piutang antar-perusahaan', 'ASSET', 'DEBIT', true
                           WHERE NOT EXISTS (SELECT 1 FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '1-10900')""", T)
        def ic(frm, to):
            return IR(transaction_date=date.today(), from_entity_tenant_id=frm, to_entity_tenant_id=to, transaction_type="sale",
                      amount=1000, description="TES E2E sekuriti")
        st, sp = await coba(lambda: IC.create_transaction(req(uid), ic(KORBAN, T)))
        h["C"] = {"status": st, "atas_nama_korban": await c.fetchval(
            "SELECT count(*) FROM intercompany_transactions WHERE tenant_id = $1 AND from_entity_tenant_id = $2", T, KORBAN)}
        await sp.rollback()
        st, sp = await coba(lambda: IC.create_transaction(req(uid), ic(T, TAK_ADA)))
        h["C+"] = st; await sp.rollback()
        st, sp = await coba(lambda: IC.create_transaction(req(uid), ic(T, KORBAN)))
        h["C++"] = st; await sp.rollback()
        print(f"REKAM {NAMA} {json.dumps(h)}", flush=True)
    finally:
        await tx.rollback(); await c.close()
    json.dump(h, open(f"/out/{NAMA}.json", "w"))


async def main():
    if MODE == "JEPRET":
        c = await asyncpg.connect(os.environ["DATABASE_URL"], server_settings={"default_transaction_read_only": "on"})
        out = {}
        for kk, q in SQL_JEPRET.items():
            out[kk] = [str(v) for v in (await c.fetchrow(q, T) if "$1" in q else await c.fetchrow(q)).values()]
        json.dump(out, open(f"/out/{NAMA}.json", "w")); await c.close(); print(f"JEPRET {NAMA} ok"); return
    if MODE == "TULIS":
        return await tulis()
    lama, baru = json.load(open("/out/lama.json")), json.load(open("/out/baru.json"))
    print(f"LAMA {lama}\nBARU {baru}")
    cek(lama["A"]["param_tenant_id"] and lama["A"]["asing"] > 0, "(A) LAMA: ?tenant_id=korban MEMBOCORKAN produk tenant lain (celah nyata)", str(lama["A"]))
    harap_a = (lambda a: a > 0) if MERAH else (lambda a: a == 0)
    cek(not baru["A"]["param_tenant_id"] and harap_a(baru["A"]["asing"]) and baru["A"]["n"] > 0,
        "(A) BARU: parameter dicabut, hasil HANYA produk sendiri (n>0 = kontrol positif)", str(baru["A"]))
    cek(lama["B"] == {"status": "OK", "invarian": 1}, "(B) LAMA: entitas korban tertulis tanpa cek keanggotaan (celah nyata)", str(lama["B"]))
    cek(baru["B"] == {"status": "403", "invarian": 0}, "(B) BARU: 403, tak tertulis", str(baru["B"]))
    cek(lama["C"] == {"status": "OK", "atas_nama_korban": 1}, "(C) LAMA: transaksi IC atas nama korban tertulis (celah nyata)", str(lama["C"]))
    cek(baru["C"] == {"status": "403", "atas_nama_korban": 0}, "(C) BARU: 403, tak tertulis", str(baru["C"]))
    cek(lama["C+"] == "OK" and baru["C+"] == "404", "(C+) tujuan tak ada: LAMA 201, BARU 404", f"{lama['C+']} / {baru['C+']}")
    cek(lama["C++"] == "OK" and baru["C++"] == "OK", "(C++) jalur SAH from=sendiri: 201 di kedua pohon (kontrol positif)", f"{lama['C++']} / {baru['C++']}")
    s, e = json.load(open("/out/sebelum.json")), json.load(open("/out/sesudah.json"))
    for kk in SQL_JEPRET:
        cek(s[kk] == e[kk], f"NET 0 (sesudah proses selesai): {kk}", "" if s[kk] == e[kk] else f"{s[kk]} / {e[kk]}")
    print(f"RINGKAS gagal={len(gagal)} {gagal[:6]}"); sys.exit(1 if gagal else 0)

asyncio.run(main())
