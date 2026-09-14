"""GERBANG tahap 3. argv: <path permission_middleware.py> <mode: baru|lama|sabotase_hapus_pola> [baseline.json]
CAKUPAN (tabel rute hidup): tiap WRITE = mapped OR write_exempt OR di baseline -> selain itu MERAH (rute baru tak tercakup).
PERILAKU (middleware+engine nyata, identitas dari user_tenant_roles): owner unmapped write -> 200; non-owner unmapped
write -> 403 PERMISSION_UNMAPPED; non-owner exempt write (chat/user) -> lolos gate; READ unmapped non-owner -> lolos.
"""
import asyncio
import importlib
import importlib.util
import json
import os
import re
import sys

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/app")
import asyncpg  # noqa: E402
import httpx  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

PATH, MODE = sys.argv[1], sys.argv[2]
BASELINE = sys.argv[3] if len(sys.argv) > 3 else "/tmp/izin_write_baseline.json"
T = "kaos-biru-konveksi"
U0 = "00000000-0000-4000-8000-000000000001"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:200]))


async def main():
    src = open(PATH, encoding="utf-8").read()
    if MODE == "sabotase_hapus_pola":
        A = '    (r"^/api/cheques/issue$", ["POST"], "kas_bank", "C"),\n'
        if src.count(A) != 1:
            print("ALAT: jangkar sabotase"); sys.exit(3)
        src = src.replace(A, "")
    open("/tmp/pm3_gerbang.py", "w", encoding="utf-8").write(src)
    import app.middleware  # noqa: F401
    spec = importlib.util.spec_from_file_location("app.middleware.pm3_gerbang", "/tmp/pm3_gerbang.py")
    pm = importlib.util.module_from_spec(spec); sys.modules[spec.name] = pm; spec.loader.exec_module(pm)
    from app.main import app

    # ---- CAKUPAN
    TULIS = {"POST", "PUT", "PATCH", "DELETE"}
    pola = [(re.compile(p), ms) for p, ms, *_ in pm.ROUTE_PERMISSIONS]
    skip = [re.compile(p) for p in pm.SKIP_PATTERNS]
    wex = [re.compile(p) for p, _ in getattr(pm, "WRITE_EXEMPT", [])]
    contoh = lambda p: re.sub(r"\{[^}]+\}", U0, p)  # noqa: E731
    base = set(json.load(open(BASELINE)))
    if MODE == "sabotase_baseline":
        base.add("POST /api/palsu/entri-baseline-baru")   # baseline BERTAMBAH -> ratchet wajib merah
    tak_tercakup = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        for m in sorted(r.methods & TULIS):
            p = contoh(r.path)
            if any(s.match(p) for s in skip) or any(x.match(p) for x in wex):
                continue
            if any(m in ms and c.match(p) for c, ms in pola):
                continue
            if f"{m} {r.path}" not in base:
                tak_tercakup.append(f"{m} {r.path}")
    catat("CAKUPAN", "tiap WRITE hidup = pola OR write_exempt OR baseline (tak ada yang baru tak tercakup)", not tak_tercakup, tak_tercakup[:8])

    # RATCHET (syarat MASTER): baseline hanya boleh MENYUSUT. Hitung himpunan unmapped-non-exempt HIDUP sekarang;
    # entri baseline yang BUKAN lagi unmapped (sudah dipola/dihapus) = penyusutan (OK). Entri unmapped hidup yang TAK
    # ada di baseline = pertumbuhan (MERAH — sudah ketangkap 'tak tercakup' di atas). Cacah baseline tak boleh > 121.
    hidup_unmapped = set()
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        for m in sorted(r.methods & TULIS):
            p = contoh(r.path)
            if any(s.match(p) for s in skip) or any(x.match(p) for x in wex):
                continue
            if any(m in ms and c.match(p) for c, ms in pola):
                continue
            hidup_unmapped.add(f"{m} {r.path}")
    tumbuh = hidup_unmapped - base           # write baru tak tercakup (== tak_tercakup)
    catat("RATCHET", "baseline TIDAK bertambah: nol entri unmapped hidup di luar baseline, cacah baseline <= 121",
          not tumbuh and len(base) <= 121, (len(base), sorted(tumbuh)[:5]))

    # ---- PERILAKU
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    import backend.api_gateway.app.services.policy_engine_client as pec
    pec.init_policy_engine(pool)
    owner = await pool.fetchval("SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id=utr.role_id WHERE utr.tenant_id=$1 AND r.code='OWNER' AND utr.status='ACTIVE' LIMIT 1", T)
    kolab = await pool.fetchval("SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id=utr.role_id WHERE utr.tenant_id=$1 AND r.code='COLLABORATOR' AND utr.status='ACTIVE' LIMIT 1", T)
    identitas = {"who": None}

    class Suntik(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if identitas["who"]:
                request.state.user = {"user_id": str(identitas["who"]), "tenant_id": T, "role": "ADMIN"}
            return await call_next(request)

    async def sampai(request):
        return JSONResponse({"sampai": True})
    aplikasi = Starlette(routes=[Route("/{j:path}", sampai, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])])
    aplikasi.add_middleware(pm.PermissionMiddleware)
    aplikasi.add_middleware(Suntik)

    async def minta(method, path, who):
        identitas["who"] = who
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=aplikasi), base_url="http://g") as c:
            r = await c.request(method, path, json={})
            return r.status_code, r.text[:400]

    UNMAP = "/api/financial-ratios/snapshot"  # write tak terpetakan, tak exempt (baseline owner-only; recipes kini dipetakan V258)
    catat("OWNER", "owner: WRITE tak terpetakan -> 200 (bypass)", (await minta("POST", UNMAP, owner))[0] == 200)
    HARIAN = ["/api/sales-invoices", "/api/receive-payments", "/api/bills", "/api/expenses"]
    go_codes = []
    for h in HARIAN:
        go_codes.append((await minta("POST", h, owner))[0])
    go_codes.append((await minta("POST", f"/api/bank-transactions/{U0}/void", owner))[0])
    catat("OWNER", "owner: 5 alur harian -> 200", all(c == 200 for c in go_codes), go_codes)
    k, b = await minta("POST", UNMAP, kolab)
    catat("NONOWNER", "non-owner: WRITE tak terpetakan -> 403 PERMISSION_UNMAPPED", k == 403 and "PERMISSION_UNMAPPED" in b, (k, b[:70]))
    k2, b2 = await minta("PUT", "/api/user/favorites", kolab)
    catat("EXEMPT", "non-owner: WRITE exempt (/api/user/favorites) -> lolos gate (200)", k2 == 200, (k2, b2[:50]))
    k3, b3 = await minta("POST", "/api/v3/chat/message", kolab)
    catat("EXEMPT", "non-owner: chat message -> lolos gate (aksi dicek di modul tujuan)", k3 == 200, k3)
    k4, b4 = await minta("GET", "/api/recipes/recipes", kolab)
    catat("READ", "non-owner: READ tak terpetakan -> tetap terbuka (200)", k4 == 200, k4)
    await pool.close()

    for s_, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s_:9} {u}  | {k}")
    g = [h[1] for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)}")
    if MODE == "baru":
        sys.exit(0 if not g else 1)
    if MODE == "sabotase_hapus_pola":
        ok = any(x.startswith("tiap WRITE") for x in g)   # cheques/issue jatuh dari mapped, bukan di baseline -> cakupan MERAH
        print("[sabotase] ->", "TERTANGKAP (cakupan merah)" if ok else "LOLOS", g); sys.exit(0 if ok else 1)
    if MODE == "sabotase_baseline":
        ok = any(x.startswith("baseline TIDAK bertambah") for x in g)   # cacah 122 > 121 -> ratchet MERAH
        print("[sabotase_baseline] ->", "TERTANGKAP (ratchet merah)" if ok else "LOLOS", g); sys.exit(0 if ok else 1)
    # lama: default terbuka -> non-owner unmapped 200 (bukan 403)
    ok = any(x.startswith("non-owner: WRITE tak terpetakan") for x in g)
    print("[lama] ->", "DEFAULT TERBUKA TERBUKTI" if ok else "TAK SESUAI", g); sys.exit(0 if ok else 1)


asyncio.run(main())
