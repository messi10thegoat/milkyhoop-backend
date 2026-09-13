"""GERBANG sweep izin TAHAP 1. PermissionMiddleware NYATA + policy engine NYATA (pool DB nyata), dirangkai di aplikasi
Starlette kecil: penyuntik identitas (request.state.user seperti AuthMiddleware) -> PermissionMiddleware -> titik akhir
'SAMPAI' (200). TANPA login, TANPA JWT: identitas diambil dari user_tenant_roles (baris OWNER / COLLABORATOR).
argv: <path permission_middleware.py> <mode: baru|lama|sabotase_failopen>
Tabel rute: argv[3] opsional 'rute' -> hitung rute app.main (dipanggil terpisah untuk hidup sesudah deploy).
"""
import asyncio
import importlib.util
import os
import re
import sys

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
import httpx  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

PATH, MODE = sys.argv[1], sys.argv[2]
T = "kaos-biru-konveksi"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:220]))


async def main():
    src = open(PATH, encoding="utf-8").read()
    if MODE == "sabotase_failopen":
        A = "                return JSONResponse(\n                    status_code=403,\n                    content={\n                        \"error\": \"Permission check failed\","
        if src.count(A) != 1:
            print("ALAT: jangkar sabotase"); sys.exit(3)
        src = src.replace(A, "                return await call_next(request)\n                return JSONResponse(\n                    status_code=403,\n                    content={\n                        \"error\": \"Permission check failed\",")
    open("/tmp/pm_gerbang.py", "w", encoding="utf-8").write(src)
    import app.middleware  # noqa: F401
    spec = importlib.util.spec_from_file_location("app.middleware.pm_gerbang", "/tmp/pm_gerbang.py")
    pm = importlib.util.module_from_spec(spec); sys.modules[spec.name] = pm; spec.loader.exec_module(pm)

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    # Middleware mengimpor get_policy_engine lewat jalur ABSOLUT backend.api_gateway.app.services... (produksi menjalankan
    # uvicorn backend.api_gateway.app.main:app, jadi sama). Run pertama gerbang meng-init jalur 'app.services...' = objek
    # modul LAIN -> semua cek galat "not initialized" -> hasil run itu artefak alat, dibuang.
    sys.path.insert(0, "/app")
    import backend.api_gateway.app.services.policy_engine_client as pec
    pec.init_policy_engine(pool)
    owner = await pool.fetchval("""SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id=utr.role_id
        WHERE utr.tenant_id=$1 AND r.code='OWNER' AND utr.status='ACTIVE' LIMIT 1""", T)
    kolab = await pool.fetchval("""SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id=utr.role_id
        WHERE utr.tenant_id=$1 AND r.code='COLLABORATOR' AND utr.status='ACTIVE' LIMIT 1""", T)
    if not (owner and kolab):
        print("TAK SAH: identitas owner/collaborator", owner, kolab); sys.exit(2)

    identitas = {"who": None}

    class Suntik(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if identitas["who"]:
                request.state.user = {"user_id": str(identitas["who"]), "tenant_id": T, "role": "ADMIN"}
            return await call_next(request)

    async def sampai(request):
        return JSONResponse({"sampai": True})

    aplikasi = Starlette(routes=[Route("/{jalan:path}", sampai, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])])
    aplikasi.add_middleware(pm.PermissionMiddleware)
    aplikasi.add_middleware(Suntik)   # ditambahkan terakhir = paling luar (seperti AuthMiddleware di main.py)

    async def minta(method, path, who):
        identitas["who"] = who
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=aplikasi), base_url="http://g") as c:
            r = await c.request(method, path, json={})
            return r.status_code, r.text[:400]   # dulu [:120] memotong "PERMISSION_CHECK_ERROR" -> merah palsu (alat)

    U0 = "00000000-0000-4000-8000-000000000001"
    ALUR = [("POST", "/api/sales-invoices", "faktur"), ("POST", "/api/receive-payments", "pembayaran"), ("POST", "/api/bills", "tagihan"),
            ("POST", "/api/expenses", "beban"), ("POST", f"/api/bank-transactions/{U0}/void", "bank")]
    for meth, path, nama in ALUR:
        terpeta = pm.PermissionMiddleware(None)._find_permission(path, meth)
        k, body = await minta(meth, path, owner)
        catat("OWNER", f"{nama}: {meth} {path} terpetakan ({terpeta}) & owner SAMPAI (200)", terpeta is not None and k == 200, (terpeta, k, body))

    # kontrol: pemeriksa masih MENOLAK peran yang tak berhak (bukan sekadar semua 200)
    # Subjek kontrol pertama (POST faktur) SALAH PREMIS: collaborator punya override Kelola Akses INVOICE {C,R,U,P,E}
    # (7 Sep) -> engine benar mengizinkan. Kini modul yang terukur can()=False untuknya (EXPENSE, tanpa override).
    ada_override = await pool.fetchval("SELECT count(*) FROM user_permission_overrides WHERE user_id=$1 AND tenant_id=$2 AND module='EXPENSE'", str(kolab), T)
    k, body = await minta("POST", "/api/expenses", kolab)
    catat("KONTROL", "COLLABORATOR (EXPENSE tanpa hak, tanpa override) POST beban -> 403 PERMISSION_DENIED",
          ada_override == 0 and k == 403 and "PERMISSION_DENIED" in body, (ada_override, k, body))

    # (i) galat engine -> tolak
    asli = pec.policy_engine
    class Rusak:
        async def get_user_context(self, **kw):
            raise RuntimeError("engine rusak (gerbang)")
    pec.policy_engine = Rusak()
    try:
        k, body = await minta("POST", "/api/sales-invoices", owner)
    finally:
        pec.policy_engine = asli
    catat("FAILCLOSED", "engine galat -> 403 PERMISSION_CHECK_ERROR (dulu 200 lolos)", k == 403 and "PERMISSION_CHECK_ERROR" in body, (k, body))

    # (iii) skip dipatok
    pmi = pm.PermissionMiddleware(None)
    lewat = lambda p: any(x.match(p) for x in pmi._compiled_skip)  # noqa: E731
    catat("SKIP", "/api/team-members/roles/list DILEWATI", lewat("/api/team-members/roles/list"), "")
    catat("SKIP", "prefiks lain /api/team-members/roles/{id}/permissions TIDAK dilewati (patokan persis)",
          not lewat(f"/api/team-members/roles/{U0}/permissions") and not lewat("/api/team-members/roles"), "")
    k, body = await minta("GET", "/api/team-members/roles/list", owner)
    catat("SKIP", "owner GET roles/list SAMPAI 200", k == 200, (k, body))
    await pool.close()

    badan = open(__file__, encoding="utf-8").read().split("async def main():", 1)[1]
    harap = len(re.findall(r"^\s+catat\(", badan, re.M)) + 5 - 1   # situs OWNER di dalam loop 5 alur
    for s_, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s_:10} {u}  | {k}")
    g = [h[1] for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)} harap={harap}")
    if MODE == "baru":
        sys.exit(0 if not g and len(hasil) == harap else 1)
    wajib = {"lama": ["engine galat", "/api/team-members/roles/list DILEWATI"], "sabotase_failopen": ["engine galat"]}[MODE]
    ok = all(any(x.startswith(w) for x in g) for w in wajib) and not any(x.startswith(("faktur", "pembayaran", "tagihan", "beban", "bank")) for x in g)
    print(f"[{MODE}] ->", "SESUAI HARAPAN" if ok else "TAK SESUAI", g); sys.exit(0 if ok else 1)


asyncio.run(main())
