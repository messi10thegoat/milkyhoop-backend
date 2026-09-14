"""GERBANG (A) pensiun X-Source — bagian PERILAKU (AuthMiddleware nyata, self-contained, in-container).
argv: <path auth_middleware.py> <mode: baru|lama> [path unified_chat.py]
Blok bypass ada di awal dispatch (sebelum decode token) -> tak perlu DB/JWT. Cek statis (0 ref, file dihapus,
is_direct utuh, compose) dijalankan sebagai grep host di langkah verifikasi, bukan di sini.
"""
import importlib
import importlib.util
import sys

sys.path.insert(0, "/app/backend/api_gateway")
import httpx  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

PATH, MODE = sys.argv[1], sys.argv[2]
UC = sys.argv[3] if len(sys.argv) > 3 else None
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:160]))


async def main():
    import app.middleware  # noqa: F401
    spec = importlib.util.spec_from_file_location("app.middleware.am_gerbang", PATH)
    am = importlib.util.module_from_spec(spec); sys.modules[spec.name] = am
    spec.loader.exec_module(am)

    async def sampai(request):
        return JSONResponse({"sampai": True})

    aplikasi = Starlette(routes=[Route("/{j:path}", sampai, methods=["GET", "POST"])])
    aplikasi.add_middleware(am.AuthMiddleware)

    async def minta(headers):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=aplikasi), base_url="http://g") as c:
            r = await c.post("/api/expenses", headers=headers, json={})
            return r.status_code, r.text[:100]

    OWNER_UID = "0bccdb25-fdf0-4e99-9024-b9a20846f76c"
    k, b = await minta({"X-Source": "action_executor", "X-Tenant-ID": "kaos-biru-konveksi", "X-User-ID": OWNER_UID})
    catat("BYPASS", "X-Source+X-Tenant+X-User-ID(owner) TANPA Bearer -> 401 (bypass mati)", k == 401, (k, b))
    k2, b2 = await minta({})
    catat("BASELINE", "tanpa auth apa pun -> 401", k2 == 401, (k2, b2))
    if UC:
        uc = open(UC, encoding="utf-8").read()
        catat("REF", "unified_chat: 0 referensi get_action_executor_client", "get_action_executor_client" not in uc, uc.count("get_action_executor_client"))
        catat("JWT-PATH", "jalur is_direct utuh: confirm_action -> _confirm_direct_action", "_confirm_direct_action(" in uc and "if is_direct:" in uc, "")
        catat("JWT-PATH", "penerus JWT ke kernel utuh (Authorization: auth_header + X-Tenant-ID: tenant_id)",
              '"Authorization": auth_header' in uc and '"X-Tenant-ID": tenant_id' in uc, "")

    for s_, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s_:9} {u}  | {k}")
    g = [h[1] for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)}")
    if MODE in ("baru", "hidup"):
        sys.exit(0 if not g else 1)
    ok = any(x.startswith("X-Source") for x in g)
    print("[lama] ->", "BYPASS HIDUP (200) TERBUKTI" if ok else "TAK SESUAI", g); sys.exit(0 if ok else 1)


import asyncio  # noqa: E402
asyncio.run(main())
