#!/usr/bin/env python3
"""Gerbang FONT: faktur memakai Liberation Sans dari BERKAS REPO, bukan sistem.

Dijalankan DI DALAM kontainer (docker run ... python3 scripts/gate_font.py).
Menguji DUA ARAH dalam satu jalan:
  hijau  = berkas .ttf ada  -> PDF menanam Liberation
  merah  = berkas disembunyikan -> PDF jatuh ke DejaVu
Tanpa arah kedua, hijaunya tak membuktikan font datang dari repo -- bisa saja
kebetulan image itu punya paket fonts-liberation.

Kelas kegagalan yang ditutup: WeasyPrint TIDAK PERNAH mengeluh saat font yang
diminta tak ada; ia diam-diam memakai penggantinya. Dan ia juga mengabaikan
seluruh @font-face kalau FontConfiguration tidak diberikan -- juga tanpa
galat. Dua diam yang menghasilkan faktur berhuruf salah.
"""
import os
import re
import sys
import zlib

sys.path.insert(0, "/app/backend/api_gateway")

from weasyprint import CSS, HTML  # noqa: E402
from weasyprint.text.fonts import FontConfiguration  # noqa: E402

from app.services.pdf_service import TEMPLATE_DIR  # noqa: E402

CONTOH = "<p style=\"font-family: 'Liberation Sans', Arial, sans-serif\">Faktur 12.500,00</p>"


def font_tertanam():
    fc = FontConfiguration()
    css = CSS(filename=str(TEMPLATE_DIR / "invoice_b.css"), font_config=fc)
    pdf = HTML(string=CONTOH).write_pdf(stylesheets=[css], font_config=fc)
    nama = set(re.findall(rb"/BaseFont\s*/([A-Za-z0-9+\-_]+)", pdf))
    if not nama:
        for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
            try:
                nama |= set(re.findall(rb"/BaseFont\s*/([A-Za-z0-9+\-_]+)",
                                       zlib.decompress(m.group(1))))
            except Exception:
                pass
    return sorted({n.decode().split("+")[-1] for n in nama})


def utama():
    d = TEMPLATE_DIR / "fonts"
    ttf = sorted(d.glob("LiberationSans-*.ttf"))
    print(f"berkas font di repo: {[f.name for f in ttf] or '(TIDAK ADA)'}")
    if not ttf:
        print("GAGAL: berkas font tidak ada di repo")
        return 1

    hijau = font_tertanam()
    print(f"  dengan berkas font : {hijau}")
    ok = any("Liberation" in f for f in hijau)

    # KONTROL MERAH: sembunyikan, ukur lagi, kembalikan.
    tersembunyi = []
    try:
        for f in ttf:
            b = f.with_suffix(".ttf.uji")
            os.rename(f, b)
            tersembunyi.append((b, f))
        merah = font_tertanam()
    finally:
        for b, f in tersembunyi:
            os.rename(b, f)
    print(f"  KONTROL tanpa berkas: {merah}")
    kontrol = any("DejaVu" in f for f in merah) and not any("Liberation" in f for f in merah)

    print(f"\nfont datang dari REPO      : {ok}")
    print(f"KONTROL MERAH menyala      : {kontrol}")
    return 0 if (ok and kontrol) else 1


if __name__ == "__main__":
    sys.exit(utama())
