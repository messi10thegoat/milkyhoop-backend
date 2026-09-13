"""G2 in-process — pola izin void transaksi bank, lewat _find_permission milik middleware SENDIRI.

Tanpa regex salinan: kalau pola di middleware salah ketik, gerbang ini ikut merah.
can() dipanggil dengan konteks non-owner TIRUAN; pembaca izin (_get_user_overrides /
_get_role_permissions) ditambal -> NOL data izin disentuh.

Argumen: path berkas permission_middleware.py yang diuji (lama/baru), dan harapan
pola ('ada' | 'tiada').
"""
import asyncio
import importlib.util
import sys

sys.path.insert(0, "/app/backend/api_gateway")

MW_PATH, HARAP = sys.argv[1], sys.argv[2]
hasil = []


def catat(nama, ok, ket=""):
    hasil.append((nama, bool(ok), ket))


spec = importlib.util.spec_from_file_location("app.middleware.pm_uji", MW_PATH)
pm = importlib.util.module_from_spec(spec)
sys.modules["app.middleware.pm_uji"] = pm
spec.loader.exec_module(pm)


async def dummy_app(scope, receive, send):
    pass


mw = pm.PermissionMiddleware(dummy_app)
JALUR = "/api/bank-transactions/3f0c7a52-0000-4000-8000-000000000001/void"

got = mw._find_permission(JALUR, "POST")
if HARAP == "ada":
    catat("jalur void cocok -> (kas_bank, V)", got == ("kas_bank", "V"), str(got))
else:
    catat("KODE LAMA: jalur void TAK cocok pola apa pun (tanpa pagar)", got is None, str(got))

if HARAP == "ada":
    catat("kontrol: GET jalur yang sama tak cocok pola POST ini", mw._find_permission(JALUR, "GET") != ("kas_bank", "V"), str(mw._find_permission(JALUR, "GET")))
    catat("kontrol: jalur karangan tak cocok", mw._find_permission("/api/bank-transactions-karangan/x/void", "POST") is None, str(mw._find_permission("/api/bank-transactions-karangan/x/void", "POST")))
    catat("kontrol: sub-jalur lebih panjang tak cocok", mw._find_permission(JALUR + "/lagi", "POST") != ("kas_bank", "V"), str(mw._find_permission(JALUR + "/lagi", "POST")))

    from app.services.policy_engine_client import PolicyEngineClient as PE, UserContext  # noqa: E402

    async def uji_can():
        pe = PE.__new__(PE)
        async def no_over(user_id, tenant_id):
            return {}
        pe._get_user_overrides = no_over
        tanpa_v = UserContext(user_id="u1", tenant_id="t1", subscription_role="ADMIN",
                              business_role_id="r-staf", business_role_code="STAFF")
        dengan_v = UserContext(user_id="u2", tenant_id="t1", subscription_role="ADMIN",
                               business_role_id="r-kasir", business_role_code="STAFF")
        mod_db = pm_mod = got[0]
        from app.services.policy_engine_client import normalize_module_name
        mod_db = normalize_module_name(pm_mod)
        async def perms(role_id):
            return {mod_db: ["R", "C"]} if role_id == "r-staf" else {mod_db: ["R", "C", "V"]}
        pe._get_role_permissions = perms
        catat("non-owner TANPA V -> can() menolak", await pe.can(tanpa_v, got[1], got[0]) is False, mod_db)
        catat("non-owner DENGAN V -> can() mengizinkan", await pe.can(dengan_v, got[1], got[0]) is True, mod_db)
    asyncio.run(uji_can())

for n, ok, k in hasil:
    print(("[H] " if ok else "[X] ") + n + "  | " + k)
harap = 6 if HARAP == "ada" else 1
g = sum(1 for h in hasil if not h[1])
print(f"gagal={g} total={len(hasil)} harap={harap} -> {'LENGKAP' if len(hasil) == harap else 'TAK SAH'}")
sys.exit(0 if g == 0 and len(hasil) == harap else 1)
