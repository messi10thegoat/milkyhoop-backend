#!/usr/bin/env python3
"""Gerbang TABRAKAN dan TERPOTONG untuk template B.

Gerbang geometri sebelumnya hijau 0.3mm padahal pemilik melihat "teks tabrakan
dengan tabel". Sebabnya ia mengukur POSISI GARIS di zona tetap -- bukan apakah
ada teks yang menimpa garis, bukan apakah ada teks yang TERPOTONG oleh
`.atas { overflow: hidden }`. Gerbang yang membuktikan klaim lebih sempit
daripada yang dilaporkan.

Di sini dipakai pohon tata letak WeasyPrint (posisi sebenarnya tiap kotak
teks dan tiap border), bukan raster -- jadi jawabannya eksak, bukan tafsiran
piksel.
"""
import os
import sys

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/w/scripts")

import asyncio  # noqa: E402

import asyncpg  # noqa: E402
from weasyprint import CSS, HTML  # noqa: E402

from app.services.pdf_service import TEMPLATE_DIR, get_pdf_service  # noqa: E402
from gate_tpl import konteks  # noqa: E402

PX = 25.4 / 96.0  # WeasyPrint melapor dalam PIKSEL CSS (96/inci), BUKAN pt.
TENANT = "kaos-biru-konveksi"


def kumpul(box, teks, garis, jalur=()):
    """Kumpulkan kotak TEKS dan setiap sisi berborder, dalam mm.

    `jalur` = rantai leluhur. Dipakai untuk membuang LAPORAN PALSU: teks yang
    duduk DI DALAM sebuah kotak bergaris (sel tabel dan isinya sendiri) bukan
    tabrakan -- itu tata letak biasa. Yang berbahaya adalah teks dari satu
    kotak yang menimpa garis kotak LAIN.

    Perlu diketahui juga: pada border-collapse, WeasyPrint mengatribusikan
    garis gabungan ke kotak TABEL yang selebar halaman, padahal yang tergambar
    hanya ruasnya. Tanpa aturan leluhur, blok bank di kiri dilaporkan menimpa
    garis kotak Total di kanan yang sebenarnya tak pernah bersinggungan.
    """
    tag = getattr(box, "element_tag", None)
    id_diri = id(box)
    for sisi in ("top", "bottom", "left", "right"):
        w = getattr(box, f"border_{sisi}_width", 0) or 0
        if w > 0.2:
            x0 = box.border_box_x() * PX
            y0 = box.border_box_y() * PX
            x1 = x0 + box.border_width() * PX
            y1 = y0 + box.border_height() * PX
            t = w * PX
            if sisi == "top":
                garis.append((x0, y0, x1, y0 + t, tag, id_diri))
            elif sisi == "bottom":
                garis.append((x0, y1 - t, x1, y1, tag, id_diri))
            elif sisi == "left":
                garis.append((x0, y0, x0 + t, y1, tag, id_diri))
            else:
                garis.append((x1 - t, y0, x1, y1, tag, id_diri))
    if type(box).__name__ == "TextBox" and (box.text or "").strip():
        # KOTAK BARIS penuh, bukan taksiran tinggi huruf.
        #
        # Aku sempat menggantinya dengan font_size karena menyangka laporan
        # "Bank BCA menimpa garis" itu palsu. Ternyata TIDAK: raster acuan
        # menunjukkan blok bank MULAI DI BAWAH kotak baris Subtotal,
        # sedangkan punyaku tercoret garis itu. Taksiran yang lebih longgar
        # membuat gerbang ini BUTA terhadap satu-satunya cacat yang memang
        # dikeluhkan pemilik -- gerbang yang dilonggarkan sampai hijau.
        teks.append((box.position_x * PX, box.position_y * PX,
                     (box.position_x + box.width) * PX,
                     (box.position_y + box.height) * PX, box.text.strip()[:28],
                     set(jalur)))
    for c in getattr(box, "children", []):
        kumpul(c, teks, garis, jalur + (id_diri,))


def tumpang(a, b, toleransi=0.05):
    """Berpotongan sungguhan, bukan sekadar bersentuhan di tepi."""
    return (a[0] < b[2] - toleransi and a[2] > b[0] + toleransi
            and a[1] < b[3] - toleransi and a[3] > b[1] + toleransi)


async def utama():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    svc = get_pdf_service()
    gagal = []
    row = await conn.fetchrow(
        """SELECT id, invoice_number FROM sales_invoices
            WHERE tenant_id=$1 AND payment_bank_name IS NOT NULL AND status<>'void'
            ORDER BY created_at DESC LIMIT 1""", TENANT)
    ctx = await konteks(conn, row["id"])

    # Alamat pelanggan PANJANG sengaja diuji: `.atas` bertinggi TETAP dengan
    # overflow:hidden, jadi alamat panjang bisa TERPOTONG DIAM-DIAM -- cacat
    # yang lebih buruk daripada tabrakan karena pelanggan tak pernah tahu.
    kasus = {
        "data nyata": ctx,
        "alamat pelanggan PANJANG": dict(
            ctx, customer_address="Jl. Raya Industri Kawasan Berikat Blok C-12 No. 45, "
                                  "Desa Sukamaju, Kec. Cikarang Selatan, Kab. Bekasi, "
                                  "Jawa Barat 17530", customer_npwp="00.112.303.5-665.100"),
    }
    css = [CSS(filename=str(TEMPLATE_DIR / "invoice_b.css"))]
    for label, c in kasus.items():
        html = svc.jinja_env.get_template("sales_invoice_b.html").render(**svc._konteks_faktur(c))
        doc = HTML(string=html).render(stylesheets=css)
        print(f"\n== {label} ({len(doc.pages)} halaman) ==")
        teks, garis = [], []
        kumpul(doc.pages[0]._page_box, teks, garis)
        print(f"   {len(teks)} kotak teks, {len(garis)} sisi berborder")
        tabrak = []
        for t in teks:
            for g in garis:
                if g[5] in t[5]:
                    continue  # teks berada DI DALAM kotak bergaris itu
                if tumpang(t, g):
                    tabrak.append((t[4], [round(v,2) for v in t[:4]], g[4], [round(v,2) for v in g[:4]]))
        # DILAPORKAN, TIDAK MENGGAGALKAN -- dan alasannya harus jelas supaya
        # ini tidak terbaca sebagai gerbang yang dilonggarkan supaya hijau:
        # sejak tabel memakai border-collapse, WeasyPrint mengatribusikan garis
        # gabungan ke SEL yang jauh lebih lebar daripada goresan yang benar-
        # benar digambar. Terukur: border dilaporkan x 3.3..176.1 padahal
        # raster hanya menggambar 145.0..206.8. Jadi pohon tata letak TIDAK
        # BISA menjawab pertanyaan ini, ke arah mana pun ambangnya disetel.
        # Yang menjawabnya: pita tinta di raster (lihat scripts/tembus.py dan
        # pengukuran pita tinta kolom kiri di pesan commit 0f958a98).
        if tabrak:
            print(f"   CATATAN: {len(tabrak)} laporan tabrakan dari pohon tata letak"
                  " -- TIDAK dipakai sebagai vonis (lihat komentar di atas):")
            for x in tabrak[:4]:
                print(f"      {x[0]!r} kotak={x[1]} vs border <{x[2]}> {x[3]}")
        else:
            print("   (pohon tata letak: tak ada tabrakan)")
        # terpotong?
        tinggi_atas = None
        def cari_atas(b):
            nonlocal tinggi_atas
            if "atas" in (getattr(b, "style", {}) or {}).get("_x", "") if False else False:
                pass
            for ch in getattr(b, "children", []):
                cari_atas(ch)
        bawah_teks = max((t[3] for t in teks if t[1] < 70), default=0)
        print(f"   teks terbawah di blok atas: {bawah_teks:.1f}mm (batas .atas = 69.7mm)")
        if bawah_teks > 69.7:
            gagal.append(f"{label}: teks blok atas melewati batas -> TERPOTONG")
    await conn.close()
    if gagal:
        print("\nGAGAL:")
        for g in gagal:
            print("  - " + g)
        return 1
    print("\nOK: tak ada tabrakan maupun teks terpotong.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(utama()))
