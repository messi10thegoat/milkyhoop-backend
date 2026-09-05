#!/usr/bin/env python3
"""Gerbang FONT pasca-deploy: apakah image yang berjalan benar-benar punya
font metrik-Arial, atau diam-diam jatuh ke DejaVu.

Ini menutup kelas kegagalan "exit 0, kontainer hidup, konten salah": mengubah
Dockerfile lalu men-deploy dengan bind-mount + restart TIDAK memasang paket
apa pun. Tanpa gerbang ini, faktur tercetak dengan font yang salah dan tak
ada satu galat pun yang memberi tahu.

Dipakai DUA ARAH: image baru harus Liberation, image lama harus DejaVu.
"""
import re
import subprocess
import sys

SKRIP = r'''
import sys
sys.path.insert(0, "/app/backend/api_gateway")
from weasyprint import CSS, HTML
HTMLS = "<p style=\"font-family: 'Liberation Sans', Arimo, Arial, Helvetica, sans-serif\">Faktur 12.500,00</p>"
pdf = HTML(string=HTMLS).write_pdf()
sys.stdout.buffer.write(pdf)
'''


def font_terpakai(image):
    p = subprocess.run(
        ["ssh", "root@159.89.202.160",
         f"docker run --rm -i {image} python3 - <<'EOF'\n{SKRIP}\nEOF"],
        capture_output=True)
    pdf = p.stdout
    nama = set(re.findall(rb"/BaseFont\s*/([A-Za-z0-9+\-_]+)", pdf))
    if not nama:  # font di object stream terkompresi
        import zlib
        for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
            try:
                nama |= set(re.findall(rb"/BaseFont\s*/([A-Za-z0-9+\-_]+)",
                                       zlib.decompress(m.group(1))))
            except Exception:
                pass
    return {n.decode().split("+")[-1] for n in nama}


if __name__ == "__main__":
    baru, lama = sys.argv[1], sys.argv[2]
    fb, fl = font_terpakai(baru), font_terpakai(lama)
    print(f"image BARU  {baru}: {sorted(fb) or '(tak terbaca)'}")
    print(f"image LAMA  {lama}: {sorted(fl) or '(tak terbaca)'}")
    ok = any("Liberation" in f for f in fb) and not any("DejaVu" in f for f in fb)
    kontrol = any("DejaVu" in f for f in fl) and not any("Liberation" in f for f in fl)
    print(f"\nimage baru pakai Liberation, bukan DejaVu : {ok}")
    print(f"KONTROL MERAH image lama jatuh ke DejaVu  : {kontrol}")
    sys.exit(0 if (ok and kontrol) else 1)
