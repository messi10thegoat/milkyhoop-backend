"""GERBANG sweep izin TAHAP 2. Middleware NYATA + policy engine NYATA; identitas dari user_tenant_roles (tanpa login/JWT).
argv: <path permission_middleware.py> <mode: baru|lama|sabotase_hapus_pola>
Cakupan dihitung dari TABEL RUTE HIDUP: setiap tulis di router pemindah uang yang disetujui punya pola; 16 rute chat &
document_intake SENGAJA tanpa pola dan dicatat TERBUKA (bukan himpunan pengecualian).
"""
import asyncio
import importlib.util
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
T = "kaos-biru-konveksi"
U0 = "00000000-0000-4000-8000-000000000001"
# router yang DISETUJUI dipola (putusan pemilik a) — daftar nama router, bukan salinan pola
DISETUJUI = {"bank_reconciliation", "cheques", "bank_transfers", "fixed_assets", "opening_balance", "consolidation", "intercompany",
             "budgets", "periods", "fiscal_years", "production_costing", "expense_extended", "expenses", "bills", "recurring_bills",
             "purchase_orders", "production", "stock_transfers", "items", "stock_adjustments", "customers", "vendors",
             "sales_invoices", "customer_deposits", "sales_receipts", "tax_invoices", "nsfp", "efaktur", "payroll_runs"}
TERBUKA = {"unified_chat", "document_intake"}
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:220]))


async def main():
    src = open(PATH, encoding="utf-8").read()
    if MODE == "sabotase_hapus_pola":
        A = '    (r"^/api/cheques/issue$", ["POST"], "kas_bank", "C"),\n'
        if src.count(A) != 1:
            print("ALAT: jangkar sabotase"); sys.exit(3)
        src = src.replace(A, "")
    open("/tmp/pm2_gerbang.py", "w", encoding="utf-8").write(src)
    import app.middleware  # noqa: F401
    spec = importlib.util.spec_from_file_location("app.middleware.pm2_gerbang", "/tmp/pm2_gerbang.py")
    pm = importlib.util.module_from_spec(spec); sys.modules[spec.name] = pm; spec.loader.exec_module(pm)
    from app.main import app

    # ---- cakupan dari tabel rute hidup
    pola = [(re.compile(p), ms, mod, act, p) for p, ms, mod, act in pm.ROUTE_PERMISSIONS]
    skip = [re.compile(p) for p in pm.SKIP_PATTERNS]
    contoh = lambda p: re.sub(r"\{[^}]+\}", U0, p)  # noqa: E731
    tanpa, terbuka_tanpa, aksi_salah = [], [], []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        rt = r.endpoint.__module__.split(".")[-1]
        for m in sorted(r.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            p = contoh(r.path)
            if any(s.match(p) for s in skip):
                continue
            kena = next((x for x in pola if m in x[1] and x[0].match(p)), None)
            if rt in TERBUKA:
                if kena is None:
                    terbuka_tanpa.append(f"{m} {r.path}")
                continue
            if rt not in DISETUJUI:
                continue
            if kena is None:
                tanpa.append(f"{m} {r.path}")
                continue
            # pemeriksaan aksi INDEPENDEN (subset aturan yang tak ambigu): tanpa verba di ujung jalur
            ujung = r.path.rstrip("/").split("/")[-1]
            polos = ujung.startswith("{") or re.fullmatch(r"[a-z-]+", ujung) and m != "POST" or (m == "POST" and not re.search(
                r"(void|cancel|reject|bounce|approve|confirm|post|complete|process|process-due|activate|release|start|generate|execute|"
                r"reimburse|submit|send|ship|receive|clear|deposit|issue-materials|report-output|labor|dispose|sell|to-bill|record-actual|"
                r"allocate-overhead|auto-map|auto-match|match|import|categorize|pause|resume|toggle|mark-paid|reactivate|duplicate|"
                r"stock-adjustment|stock-transfer|maintenance|intercompany|export|calculate|validate|preview)$", r.path) and "calculate-from-bom" not in r.path)
            # jalur ber-id yang segmen sebelumnya VERBA (mis. calculate-from-bom/{bom_id}) bukan "ber-id tanpa verba":
            # run pertama pemeriksa menganggapnya C, aturan memberi P (benar) -> kekurangan pemeriksa, dibetulkan & dilabeli.
            segmen = r.path.rstrip("/").split("/")
            verba_sebelum = len(segmen) >= 2 and segmen[-1].startswith("{") and "-" in segmen[-2] and segmen[-2].split("-")[0] in (
                "calculate", "record", "allocate", "report", "issue", "auto", "mark", "to", "stock", "retry", "execute")
            if polos and ujung.startswith("{") and not verba_sebelum:
                harap = {"POST": "C", "PUT": "U", "PATCH": "U", "DELETE": "D"}[m]
                if kena[3] != harap and kena[4].count("[^/]+") >= 1 and kena[4].endswith("[^/]+$"):
                    aksi_salah.append((m, r.path, kena[3], harap))
    catat("CAKUPAN", "setiap TULIS di router pemindah uang yang disetujui punya pola (dari tabel rute hidup)", not tanpa, (len(tanpa), tanpa[:6]))
    catat("CAKUPAN", "16 rute chat & document_intake TETAP tanpa pola (terbuka, bukan pengecualian)", len(terbuka_tanpa) == 16, len(terbuka_tanpa))
    catat("CAKUPAN", "aksi pola pada jalur ber-id tanpa verba == aturan metode (C/U/D) — pemeriksa independen", not aksi_salah, aksi_salah[:5])

    # ---- perilaku: middleware + engine nyata
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    import backend.api_gateway.app.services.policy_engine_client as pec
    pec.init_policy_engine(pool)
    owner = await pool.fetchval("""SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id=utr.role_id
        WHERE utr.tenant_id=$1 AND r.code='OWNER' AND utr.status='ACTIVE' LIMIT 1""", T)
    kolab = await pool.fetchval("""SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id=utr.role_id
        WHERE utr.tenant_id=$1 AND r.code='COLLABORATOR' AND utr.status='ACTIVE' LIMIT 1""", T)
    ov = {r["module"]: r["actions"] for r in await pool.fetch("SELECT module, actions FROM user_permission_overrides WHERE user_id=$1 AND tenant_id=$2", str(kolab), T)}
    if not (owner and kolab):
        print("TAK SAH: identitas"); sys.exit(2)
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
    aplikasi.add_middleware(Suntik)

    async def minta(method, path, who):
        identitas["who"] = who
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=aplikasi), base_url="http://g") as c:
            r = await c.request(method, path, json={})
            return r.status_code, r.text[:400]

    HARIAN = [("POST", "/api/sales-invoices"), ("POST", "/api/receive-payments"), ("POST", "/api/bills"), ("POST", "/api/expenses"),
              ("POST", f"/api/bank-transactions/{U0}/void")]
    CONTOH_BARU = [("POST", "/api/cheques/issue"), ("POST", "/api/fixed-assets"), ("POST", "/api/bank-reconciliation/sessions"),
                   ("POST", "/api/consolidation/groups"), ("POST", "/api/production"), ("POST", "/api/purchase-orders"),
                   ("POST", "/api/customers/merge"), ("POST", "/api/opening-balance"), ("POST", "/api/stock-transfers")]
    gagal_owner = []
    for m, p in HARIAN + CONTOH_BARU:
        k, b = await minta(m, p, owner)
        if k != 200:
            gagal_owner.append((m, p, k))
    catat("OWNER", f"owner SAMPAI 200 di 5 alur harian + {len(CONTOH_BARU)} contoh modul baru", not gagal_owner, gagal_owner)

    # akun uji non-owner: BANK hanya R, CUSTOMER hanya R (tanpa override modul itu) -> tulis modul baru ditolak
    k1, b1 = await minta("POST", "/api/cheques/issue", kolab)
    catat("NONOWNER", "akun uji COLLABORATOR POST cheques/issue (BANK hanya R) -> 403 PERMISSION_DENIED",
          "BANK" not in ov and k1 == 403 and "PERMISSION_DENIED" in b1, (ov.get("BANK"), k1, b1[:80]))
    k2, b2 = await minta("POST", "/api/customers/merge", kolab)
    catat("NONOWNER", "akun uji COLLABORATOR POST customers/merge (CUSTOMER hanya R) -> 403", "CUSTOMER" not in ov and k2 == 403, (k2, b2[:80]))
    k3, b3 = await minta("POST", "/api/v3/chat/message", kolab)
    catat("TERBUKA", "chat masih TANPA pola: akun uji lolos (dicatat terbuka, bukan hijau keamanan)", k3 == 200, k3)
    await pool.close()

    badan = open(__file__, encoding="utf-8").read().split("async def main():", 1)[1]
    harap = len(re.findall(r"^\s+catat\(", badan, re.M))
    for s_, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s_:9} {u}  | {k}")
    g = [h[1] for h in hasil if not h[2]]
    print(f"\nMODE={MODE} gagal={len(g)} total={len(hasil)} harap={harap}")
    if MODE == "baru":
        sys.exit(0 if not g and len(hasil) == harap else 1)
    wajib = {"lama": ["setiap TULIS", "akun uji COLLABORATOR POST cheques", "akun uji COLLABORATOR POST customers"],
             "sabotase_hapus_pola": ["setiap TULIS", "akun uji COLLABORATOR POST cheques"]}[MODE]
    ok = all(any(x.startswith(w) for x in g) for w in wajib) and not any(x.startswith("owner SAMPAI") for x in g)
    print(f"[{MODE}] ->", "SESUAI HARAPAN" if ok else "TAK SESUAI", g); sys.exit(0 if ok else 1)


asyncio.run(main())
