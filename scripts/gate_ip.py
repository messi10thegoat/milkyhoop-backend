"""Gerbang IP klien tepercaya -- DUA SISI, NOL PENULISAN ke basis data.

Dijalankan DI DALAM image api_gateway dengan pohon kode yang dipasang di
/app/backend:
  SISI=lama -> pohon produksi   harus MERAH pada [S], HIJAU pada [K]
  SISI=baru -> worktree cabang  harus HIJAU semua

Menguji lockout sungguhan di produksi berarti benar-benar mengunci sebuah IP.
Jadi keenam penurun IP dipanggil LANGSUNG dengan permintaan tiruan berheader.

  [S] PALSU   -- permintaan membawa XFF pilihan PENYERANG + X-Real-IP dari
                 nginx. Sisi baru WAJIB mengembalikan nilai nginx; sisi lama
                 mengembalikan pilihan penyerang (itulah cacatnya).
  [K] KONTROL -- tanpa XFF dan tanpa X-Real-IP (akses langsung ke :8001):
                 KEDUA sisi wajib jatuh ke client.host. Kalau ini merah,
                 perubahannya terlalu lebar dan gerbang tak membuktikan apa pun.
  [A] AUDIT   -- nol `client.host` tersisa di situs audit auth/google_auth/mfa,
                 dan nol `request.client.host` di waf_middleware.py.
  [W] WAF     -- SITUS KEPUTUSAN: nilai yang benar-benar sampai ke
                 `_is_ip_blocked()` (yang menjawab 403). Diamati dengan
                 mengganti metode itu dengan perekam -- jadi yang diuji adalah
                 nilai yang MEMUTUSKAN, bukan nilai yang kebetulan dihitung.
                 Sisi lama mengembalikan IP proksi (daftar blokir mustahil
                 cocok); sisi baru mengembalikan IP klien sebenarnya.
                 BATAS YANG HARUS DISEBUT: `IPReputationMiddleware` TIDAK
                 terpasang di main.py (terukur 12 Sep 2026) -- lengan ini
                 menjaga kode yang hari ini TIDAK JALAN. Ia ada supaya cacat
                 tak hidup kembali kalau kelas itu kelak dipasang. Yang HIDUP
                 di berkas itu adalah tujuh situs catatan `WAFMiddleware`,
                 dijaga lengan [A].
"""
import asyncio
import os
import re
import sys
from types import SimpleNamespace

SISI = os.environ["SISI"]
assert SISI in ("lama", "baru"), SISI
sys.path.insert(0, "/app")

PENYERANG = "203.0.113.66"   # TEST-NET-3: dipilih penyerang lewat XFF
NGINX = "198.51.100.7"       # TEST-NET-2: di-set nginx, tak bisa dipalsukan
SOKET = "172.18.0.1"         # jembatan Docker: yang selama ini masuk audit_logs


def permintaan(headers, peer=SOKET):
    h = {k.lower(): v for k, v in headers.items()}
    return SimpleNamespace(
        headers=SimpleNamespace(get=lambda n, d=None: h.get(n.lower(), d)),
        client=SimpleNamespace(host=peer),
        state=SimpleNamespace(user=None),
        url=SimpleNamespace(path="/api/auth/login"),
    )


def penurun():
    """Keenam penurun IP, dipanggil lewat permukaannya masing-masing."""
    from backend.api_gateway.app.middleware.account_lockout_middleware import (
        AccountLockoutMiddleware,
    )
    from backend.api_gateway.app.middleware.request_id_middleware import (
        RequestIDMiddleware,
    )
    from backend.api_gateway.app.middleware.rate_limit_middleware import (
        RateLimitMiddleware,
    )
    from backend.api_gateway.app.routers import auth, google_auth, qr_auth

    lock = AccountLockoutMiddleware.__new__(AccountLockoutMiddleware)
    rid = RequestIDMiddleware.__new__(RequestIDMiddleware)
    rl = RateLimitMiddleware.__new__(RateLimitMiddleware)
    return {
        "lockout": lambda r: lock._get_client_ip(r),
        "request_id": lambda r: rid._get_client_ip(r),
        # rate-limiter memakai kunci; tanpa user ia berbentuk "ip:<ip>"
        "rate_limit": lambda r: rl._get_client_key(r).removeprefix("ip:"),
        "auth": lambda r: auth.get_client_ip(r),
        "google_auth": lambda r: google_auth.get_client_ip(r),
        "qr_auth": lambda r: qr_auth.get_client_ip(r),
    }


async def waf_ip_keputusan(req):
    """Nilai yang SAMPAI ke _is_ip_blocked() -- situs yang menjawab 403."""
    from backend.api_gateway.app.middleware.waf_middleware import (
        IPReputationMiddleware,
    )

    m = IPReputationMiddleware.__new__(IPReputationMiddleware)
    m.enabled = True
    terlihat = {}

    def perekam(ip):
        terlihat["ip"] = ip
        return False

    m._is_ip_blocked = perekam

    async def lanjut(_):
        return SimpleNamespace(status_code=200)

    await m.dispatch(req, lanjut)
    return terlihat.get("ip")


def main():
    fn = penurun()

    # [S] penyerang memilih XFF; nginx menaruh kebenaran di X-Real-IP
    req_palsu = permintaan({
        "X-Forwarded-For": f"{PENYERANG}, {NGINX}",
        "X-Real-IP": NGINX,
    })
    tertipu = {n: f(req_palsu) for n, f in fn.items()}
    salah = {n: v for n, v in tertipu.items() if v != NGINX}
    ok_s = not salah

    # [K] akses langsung tanpa header proxy -> client.host di KEDUA sisi
    req_polos = permintaan({})
    jatuh = {n: f(req_polos) for n, f in fn.items()}
    salah_k = {n: v for n, v in jatuh.items() if v != SOKET}
    ok_k = not salah_k

    # [W] situs keputusan WAF
    waf_terlihat = asyncio.run(waf_ip_keputusan(permintaan({
        "X-Forwarded-For": f"{PENYERANG}, {NGINX}",
        "X-Real-IP": NGINX,
    })))
    ok_w = waf_terlihat == NGINX

    # [A] nol client.host tersisa di situs audit + seluruh waf_middleware
    sisa = []
    for berkas in ("routers/auth.py", "routers/google_auth.py", "routers/mfa.py"):
        isi = open("/app/backend/api_gateway/app/" + berkas).read()
        for m in re.finditer(r"^.*(ip_address|ipAddress).*client\.host.*$", isi, re.M):
            sisa.append(f"{berkas}:{isi[:m.start()].count(chr(10)) + 1}")
    isi_waf = open("/app/backend/api_gateway/app/middleware/waf_middleware.py").read()
    for m in re.finditer(r"^.*request\.client\.host.*$", isi_waf, re.M):
        sisa.append(f"waf_middleware.py:{isi_waf[:m.start()].count(chr(10)) + 1}")
    ok_a = not sisa

    print(f"=== {SISI.upper()}")
    print(f"  [S] {'HIJAU' if ok_s else 'MERAH'}  XFF penyerang={PENYERANG} "
          f"X-Real-IP={NGINX} -> tertipu: {salah or 'nihil'}")
    print(f"  [K] {'HIJAU' if ok_k else 'MERAH'}  tanpa header proxy -> "
          f"bukan client.host: {salah_k or 'nihil'}")
    print(f"  [A] {'HIJAU' if ok_a else 'MERAH'}  client.host tersisa di situs "
          f"audit/waf: {sisa or 'nihil'}")
    print(f"  [W] {'HIJAU' if ok_w else 'MERAH'}  IP yang sampai ke _is_ip_blocked() "
          f"= {waf_terlihat} (harap {NGINX})")

    if SISI == "baru":
        lulus = ok_s and ok_k and ok_a and ok_w
        print("PUTUSAN:", "LULUS" if lulus else "GAGAL")
        return 0 if lulus else 1
    # Sisi lama: [S] dan [A] WAJIB merah, [K] WAJIB hijau (kontrol dua sisi).
    sah = (not ok_s) and (not ok_a) and (not ok_w) and ok_k
    print("PUTUSAN:", "KONTROL MERAH SAH" if sah else
          "KONTROL TIDAK SAH -- gerbang tak membuktikan apa pun")
    return 0 if sah else 1


sys.exit(main())
