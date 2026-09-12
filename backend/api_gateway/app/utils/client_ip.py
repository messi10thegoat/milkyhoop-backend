"""
IP Klien Tepercaya — satu sumber untuk seluruh gateway.

MENGAPA BUKAN `X-Forwarded-For`. nginx meneruskan
`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`, dan
`$proxy_add_x_forwarded_for` = header XFF yang DIKIRIM KLIEN + `$remote_addr`
DI BELAKANGNYA. Jadi elemen PERTAMA berasal dari klien, bukan dari kita.
Sampai 12 Sep 2026 enam salinan kode membaca `XFF.split(",")[0]` — elemen
pertama itu — sehingga penyerang bisa:
  (1) mengirim `X-Forwarded-For: <IP korban>` lalu gagal login 5 kali dan
      MENGUNCI IP korban (lockout tertarget), dan
  (2) memutar IP palsu untuk MENGHINDARI lockout dan rate-limit atas dirinya.

MENGAPA `X-Real-IP`. nginx men-set `proxy_set_header X-Real-IP $remote_addr`,
dan `proxy_set_header` MENIMPA header bawaan klien — nilai yang dikirim klien
dibuang. `$remote_addr` sendiri sudah IP klien yang sebenarnya berkat
`real_ip_header CF-Connecting-IP` + `set_real_ip_from` rentang Cloudflare
(milkyhoop.conf baris 11-34). Jadi X-Real-IP adalah satu-satunya nilai di
permintaan ini yang TIDAK bisa dipilih klien.

Terukur 12 Sep 2026: di seluruh `/etc/nginx`, hanya `milkyhoop.conf:69`
(`location /api`) yang mem-proxy ke gateway 127.0.0.1:8001, dan ia men-set
X-Real-IP di baris 72. Tak ada lokasi lain menuju gateway, jadi tak ada jalur
masuk yang kehilangan header ini.

Cadangan `request.client.host` dipertahankan untuk akses LANGSUNG ke :8001
(tanpa nginx) — di sana ia memang IP pemanggil yang sebenarnya.
"""
from typing import Optional


def get_client_ip(request) -> str:
    """IP klien yang tak bisa dipalsukan pemanggil. Lihat catatan modul."""
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def get_client_ip_or_none(request) -> Optional[str]:
    """Sama, tapi None (bukan "unknown") — untuk kolom audit yang nullable."""
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else None
