"""Strip X-Source & X-User-ID dari permintaan yang di-proxy ke gateway (defense-in-depth). X-Tenant-ID TIDAK di-strip.
Backup .bak lebih dulu. Jangkar tepat, NOL bila tak cocok."""
import io
import shutil
import sys

P = "/etc/nginx/sites-available/milkyhoop.conf"
shutil.copy(P, P + ".bak-20260914")
t = io.open(P, encoding="utf-8").read()
A = '''    location /api {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
'''
if t.count(A) != 1:
    print("GAGAL — jangkar location /api cocok %dx" % t.count(A)); sys.exit(1)
B = A + '''        # 14 Sep 2026: buang header kepercayaan-internal dari klien luar (defense-in-depth pasca pensiun bypass
        # X-Source). FE tak mengirim ini; hanya panggilan backend-ke-backend internal (langsung ke :8000, bukan
        # lewat nginx) yang memakainya. X-Tenant-ID TIDAK dibuang (tak terbukti tak dipakai klien luar).
        proxy_set_header X-Source "";
        proxy_set_header X-User-ID "";
'''
t = t.replace(A, B)
io.open(P, "w", encoding="utf-8").write(t)
print("nginx: X-Source & X-User-ID di-strip di location /api (backup .bak-20260914)")
