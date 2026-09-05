#!/usr/bin/env python3
"""Gerbang template faktur A/B — lapis LAYANAN.

⚠️ CAKUPAN YANG JUJUR: gerbang ini membangun konteks faktur SENDIRI dari basis
data, memakai nama medan yang sama dengan endpoint. Jadi ia menguji RESOLVER
dan KEDUA TEMPLATE dengan data nyata + WeasyPrint nyata — TAPI ia BUKAN uji
endpoint. Jalur HTTP `?template=` tak bisa kutembak karena rute PDF menuntut
izin `sales_invoice` aksi `E` (Export) yang tidak dimiliki akun uji
(Collaborator); menaikkan perannya adalah keputusan pemilik.
"""
import base64
import os
import re
import sys

sys.path.insert(0, "/app/backend/api_gateway")

import asyncio  # noqa: E402

import asyncpg  # noqa: E402

from app.services.pdf_service import (  # noqa: E402
    TemplateTidakDikenal,
    get_pdf_service,
    pilih_template,
)

TENANT = "kaos-biru-konveksi"


def halaman_html(svc, ctx, template="b") -> int:
    """Hitung halaman lewat MESIN yang sama, bukan dengan menebak dari byte.

    ⚠️ Percobaan pertama menghitung `/Type /Page` di dalam byte PDF dan SELALU
    mengembalikan 0 — WeasyPrint menaruh objeknya di object stream terkompresi,
    jadi polanya tak pernah cocok. Penghitung yang selalu 0 membuat gerbang
    "0 halaman" merah untuk alasan yang keliru.
    """
    from weasyprint import CSS, HTML

    from app.services.pdf_service import TEMPLATE_DIR, TEMPLATE_FAKTUR

    html = svc.jinja_env.get_template(TEMPLATE_FAKTUR[template]).render(
        **svc._konteks_faktur(ctx)
    )
    css_nama = "invoice.css" if template == "a" else "invoice_b.css"
    css_path = TEMPLATE_DIR / css_nama
    sheets = [CSS(filename=str(css_path))] if css_path.exists() else []
    return len(HTML(string=html).render(stylesheets=sheets).pages)


def _logo_data(nama):
    """Salinan sengaja dari logika rute PDF (routers/sales_invoices.py).

    Kalau berkasnya tak ada, kembalikan None DAN berteriak: logo yang diam-diam
    hilang persis kelas kegagalan yang membuat contoh pertama menyesatkan.
    """
    if not nama:
        return None
    for d in ("/app/backend/api_gateway/app/static/logos", "/logos"):
        p = os.path.join(d, nama)
        if os.path.exists(p):
            with open(p, "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    print(f"  ! PERINGATAN: logo {nama} tak ditemukan; uji logo akan MERAH")
    return None


async def konteks(conn, invoice_id):
    inv = await conn.fetchrow(
        """SELECT si.*, c.nama AS customer_nama, c.alamat AS customer_alamat,
                  c.telepon AS customer_telepon, c.tax_id AS customer_tax_id
             FROM sales_invoices si
             LEFT JOIN customers c ON c.id = si.customer_id
            WHERE si.id = $1""",
        invoice_id,
    )
    items = await conn.fetch(
        "SELECT * FROM sales_invoice_items WHERE invoice_id = $1 ORDER BY line_number",
        invoice_id,
    )
    # Tenant diambil DARI FAKTURNYA, bukan dari tetapan TENANT.
    #
    # CACAT YANG DIPERBAIKI: harness ini mematok kaos-biru-konveksi, jadi saat
    # dipakai untuk faktur tenant LAIN ia merender kop dan bendera is_pkp milik
    # tenant yang salah -- terukur: faktur adhita-ariyani (PKP) dirender dengan
    # is_pkp=False milik kaos-biru, sehingga baris Tax hilang tanpa alasan.
    # Ini kelas yang sama dengan logo dulu: harness yang menyimpang dari jalur
    # nyata akan memfitnah kode yang sehat, atau menyembunyikan cacatnya.
    t = await conn.fetchrow(
        'SELECT display_name, address, phone, logo_url, tax_id, is_pkp, pdf_template '
        'FROM "Tenant" WHERE id = $1',
        inv["tenant_id"],
    )
    return {
        "invoice_number": inv["invoice_number"],
        "invoice_date": inv["invoice_date"],
        "due_date": inv["due_date"],
        "customer_name": inv["customer_nama"],
        "customer_address": inv["customer_alamat"],
        "customer_phone": inv["customer_telepon"],
        "customer_npwp": inv["customer_npwp"] or inv["customer_tax_id"],
        "ref_no": inv["ref_no"] if "ref_no" in inv else None,
        "payment_terms": inv["payment_terms"] if "payment_terms" in inv else None,
        "payment_bank_name": inv["payment_bank_name"] if "payment_bank_name" in inv else None,
        "payment_account_number": inv["payment_account_number"] if "payment_account_number" in inv else None,
        "payment_account_holder": inv["payment_account_holder"] if "payment_account_holder" in inv else None,
        "subtotal": inv["subtotal"],
        "tax_amount": inv["tax_amount"],
        "total_amount": inv["total_amount"],
        "status": inv["status"],
        "tenant": {
            "name": t["display_name"],
            "address": t["address"],
            "phone": t["phone"],
            "logo_url": t["logo_url"],
            # Rute PDF sungguhan mengubah logo_url jadi data URI sebelum
            # merender. Harness versi pertama TIDAK, jadi contoh yang kukirim
            # ke pemilik tampil tanpa logo dan terbaca sebagai cacat template
            # padahal produksi baik-baik saja. Harness yang lebih miskin dari
            # jalur nyata akan MEMFITNAH kodenya sendiri.
            "logo_data": _logo_data(t["logo_url"]),
            "tax_id": t["tax_id"],
            "is_pkp": bool(t["is_pkp"]),
        },
        "items": [
            {
                "item_code": it["item_code"],
                "description": it["description"],
                "quantity": float(it["quantity"]),
                "unit": it["unit"],
                "unit_price": it["unit_price"],
                # KEDUANYA dibawa: `subtotal` pra-pajak (yang dicetak di
                # kolom AMOUNT) dan `total` yang sudah termasuk pajak.
                "subtotal": it["subtotal"],
                "total": it["total"],
                "line_number": it["line_number"],
            }
            for it in items
        ],
        "_pdf_template_tenant": t["pdf_template"],
    }


async def utama():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    svc = get_pdf_service()
    gagal = []

    row = await conn.fetchrow(
        """SELECT si.id, si.invoice_number, count(sii.id) AS n
             FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id = si.id
            WHERE si.tenant_id=$1 AND si.status <> 'void'
         GROUP BY si.id, si.invoice_number ORDER BY count(sii.id) DESC LIMIT 1""",
        TENANT,
    )
    ctx = await konteks(conn, row["id"])
    print(f"faktur uji: {row['invoice_number']}  ({row['n']} item)")

    # ── 1. dua PDF BERBEDA, keduanya PDF sungguhan, 1 halaman
    a = svc.generate_sales_invoice_pdf(ctx, template="a")
    b = svc.generate_sales_invoice_pdf(ctx, template="b")
    for nama, p in (("A", a), ("B", b)):
        if not p.startswith(b"%PDF"):
            gagal.append(f"{nama} bukan PDF")
        print(f"  {nama}: {len(p)} byte, magic={p[:4]!r}")
    if a == b:
        gagal.append("A dan B menghasilkan byte IDENTIK")
    else:
        print("  OK: A dan B berbeda")
    hb = halaman_html(svc, ctx, "b")
    print(f"  B ({row['n']} item): {hb} halaman")
    if hb != 1:
        gagal.append(f"B {hb} halaman untuk {row['n']} item, harusnya 1")

    # ── 2. tinggi tabel TETAP: 2 item vs 5 item -> tetap 1 halaman
    ctx5 = dict(ctx)
    ctx5["items"] = (ctx["items"] * 3)[:5]
    h5 = halaman_html(svc, ctx5, "b")
    print(f"  B dengan 5 item: {h5} halaman")
    if h5 != 1:
        gagal.append("B dengan 5 item bukan 1 halaman")

    # ── 3. luber ke halaman 2, header tabel berulang
    ctxN = dict(ctx)
    ctxN["items"] = (ctx["items"] * 14)[:24]  # BARIS_TETAP=21; terukur luber pada >=23 item
    hN = halaman_html(svc, ctxN, "b")
    print(f"  B dengan 24 item: {hN} halaman")
    if hN < 2:
        gagal.append("24 item tidak meluber ke halaman 2")

    # ── 4. Tax HANYA bila PKP — dua arah
    html_env = svc.jinja_env.get_template("sales_invoice_b.html")
    # Faktur uji ini berpajak NOL, jadi tanpa suntikan kontrolnya tak bermakna:
    # "tidak ada baris Tax" akan benar di KEDUA arah dan tak membuktikan apa pun.
    ctx_non = dict(ctx, tax_amount=1_000_000)
    ctx_non["tenant"] = dict(ctx["tenant"], is_pkp=False)
    ctx_pkp = dict(ctx, tax_amount=1_000_000)
    ctx_pkp["tenant"] = dict(ctx["tenant"], is_pkp=True)
    h_non = html_env.render(**svc._konteks_faktur(ctx_non))
    h_pkp = html_env.render(**svc._konteks_faktur(ctx_pkp))
    print(f"  non-PKP memuat 'Tax 11%': {'Tax 11%' in h_non}")
    print(f"  PKP     memuat 'Tax 11%': {'Tax 11%' in h_pkp}")
    if "Tax 11%" in h_non:
        gagal.append("non-PKP menampilkan baris Tax")
    if "Tax 11%" not in h_pkp:
        gagal.append("PKP TIDAK menampilkan baris Tax padahal tax_amount ada")

    # ── 5. angka SAMA di A dan B
    h_a = svc.jinja_env.get_template("sales_invoice.html").render(**svc._konteks_faktur(ctx))
    def angka(h):
        return set(re.findall(r"[\d][\d.]*,\d{2}", h))
    for nilai in (ctx["subtotal"], ctx["total_amount"]):
        s = svc.format_currency(nilai)
        ok_a, ok_b = s in h_a, s in h_pkp
        print(f"  nilai {s!r}: di A={ok_a} di B={ok_b}")
        if not (ok_a and ok_b):
            gagal.append(f"nilai {s} tidak muncul di kedua template")

    # ── 6. resolver + KONTROL MERAH
    print("  resolver:")
    for bawaan, override, harap in (("a", None, "a"), ("b", None, "b"),
                                    ("a", "b", "b"), ("b", "a", "a"),
                                    ("a", "", "a"), ("a", "  B ", "b")):
        got = pilih_template(bawaan, override)
        tanda = "OK " if got == harap else "GAGAL"
        print(f"    {tanda} bawaan={bawaan!r} override={override!r} -> {got!r} (harap {harap!r})")
        if got != harap:
            gagal.append(f"resolver: {bawaan}/{override} -> {got}, harap {harap}")
    try:
        pilih_template("a", "x")
        gagal.append("KONTROL MERAH GAGAL: 'x' tidak ditolak")
        print("    GAGAL kontrol: 'x' diterima")
    except TemplateTidakDikenal as e:
        print(f"    OK  KONTROL MERAH: 'x' ditolak -> {e}")

    # ── 7. tiga unsur visual acuan: logo, blok bank, satuan
    #
    # Ketiganya BUKAN perubahan template -- ketiganya sudah didukung. Yang
    # sempat hilang di contoh pertama disebabkan DATA (faktur uji tanpa
    # rekening, baris tanpa satuan) dan HARNESS (logo tak diubah jadi data
    # URI). Gerbang ini mengunci ketiganya supaya "hilang" berikutnya merah.
    print("  unsur visual:")
    bank = await conn.fetchrow(
        """SELECT id FROM sales_invoices
            WHERE tenant_id=$1 AND payment_bank_name IS NOT NULL AND status<>'void'
            ORDER BY created_at DESC LIMIT 1""",
        TENANT,
    )
    if not bank:
        gagal.append("tak ada faktur ber-rekening untuk menguji blok bank")
    else:
        ctxb = await konteks(conn, bank["id"])
        hb2 = html_env.render(**svc._konteks_faktur(ctxb))
        ada_logo = "data:image/png;base64," in hb2
        print(f"    logo tertanam: {ada_logo}")
        if not ada_logo:
            gagal.append("logo tenant tidak tertanam di B padahal tenant punya logo")
        ada_bank = (ctxb.get("payment_bank_name") or "") in hb2 and "Account no" in hb2
        print(f"    blok bank ({ctxb.get('payment_bank_name')!r}): {ada_bank}")
        if not ada_bank:
            gagal.append("blok rekening tidak muncul padahal faktur punya rekening")
        # KONTROL: faktur TANPA rekening tidak boleh memunculkan blok itu
        ctx_tanpa = dict(ctxb, payment_bank_name=None, payment_account_number=None,
                         payment_account_holder=None)
        h_tanpa = html_env.render(**svc._konteks_faktur(ctx_tanpa))
        print(f"    KONTROL tanpa rekening -> 'Account no' hilang: {'Account no' not in h_tanpa}")
        if "Account no" in h_tanpa:
            gagal.append("blok rekening muncul padahal faktur tak punya rekening")
        # CACAT GERBANG YANG DIPERBAIKI: mencari "pcs" di SELURUH HTML selalu
        # cocok -- string itu ada di dalam base64 logo. Kontrolnya merah, dan
        # itu berarti uji positifnya pun hijau karena alasan yang keliru.
        # Cari HANYA di dalam sel QTY.
        def sel_qty(h):
            return re.findall(r'<td class="c-qty">(.*?)</td>', h)

        satuan = [i for i in ctxb["items"] if i.get("unit")]
        if satuan:
            u = satuan[0]["unit"]
            # Bandingkan TANPA memandang besar-kecil huruf: acuan menulis
            # "Pcs", data menyimpan "pcs", dan kapitalisasi itu DISENGAJA.
            isi = sel_qty(hb2)
            ada = any(u.lower() in x.lower() for x in isi)
            print(f"    satuan {u!r} ikut di sel QTY {isi[:2]}: {ada}")
            if not ada:
                gagal.append(f"satuan {u} tidak tercetak di kolom QTY")
        ctx_nounit = dict(ctxb, items=[dict(i, unit=None) for i in ctxb["items"]])
        h_nounit = html_env.render(**svc._konteks_faktur(ctx_nounit))
        isi_n = sel_qty(h_nounit)
        bersih = not any("pcs" in x.lower() for x in isi_n)
        print(f"    KONTROL tanpa satuan -> sel QTY {isi_n[:2]} tanpa satuan: {bersih}")
        if not bersih:
            gagal.append("satuan tercetak padahal data tak punya satuan")

    await conn.close()
    if gagal:
        print("\nGAGAL:")
        for g in gagal:
            print("  - " + g)
        return 1
    print("\nOK: semua gerbang template hijau.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(utama()))
