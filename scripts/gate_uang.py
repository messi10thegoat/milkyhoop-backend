#!/usr/bin/env python3
"""Gerbang ANGKA faktur: kolom AMOUNT harus menjumlah TEPAT ke Subtotal.

Kenapa mutlak, bukan bertoleransi: ini dokumen yang dikirim ke pelanggan, dan
pelanggan menjumlahkan kolomnya sendiri. Selisih serupiah pun adalah faktur
yang tidak bisa dipertanggungjawabkan.

Cacat yang ditutup (INV-2609-0001): kolom AMOUNT mengambil
`sales_invoice_items.total` yang SUDAH termasuk pajak baris, sementara
Subtotal memakai nilai pra-pajak. Baris 1 tercetak 22.061.250,00 padahal
1.590 x 12.500 = 19.875.000,00. Penjumlahan kolom = 25.790.850 sementara
Subtotal = 23.235.000, di kertas yang sama.
"""
import os
import re
import sys

sys.path.insert(0, "/app/backend/api_gateway")
sys.path.insert(0, "/w/scripts")

import asyncio  # noqa: E402

import asyncpg  # noqa: E402

from app.services.pdf_service import get_pdf_service  # noqa: E402
from gate_tpl import konteks  # noqa: E402

gagal = []


def cek(ok, pesan):
    print(f"  {'OK  ' if ok else 'GAGAL'} {pesan}")
    if not ok:
        gagal.append(pesan)


def angka(teks):
    """'19.875.000,00' -> 1987500000 (sen, bilangan BULAT: tanpa float)."""
    t = teks.strip().replace(".", "").replace(",", "")
    return int(t) if t.isdigit() else None


def kolom_amount(html):
    sel = re.findall(r'<td class="c-jml">(.*?)</td>', html, re.S)
    out = []
    for s in sel:
        s = re.sub(r"<[^>]+>", "", s).strip()
        n = angka(s)
        if n is not None:
            out.append(n)
    return out


async def utama():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    svc = get_pdf_service()
    tpl = svc.jinja_env.get_template("sales_invoice_b.html")

    row = await conn.fetchrow(
        """SELECT si.id, si.invoice_number, si.tenant_id
             FROM sales_invoices si JOIN sales_invoice_items sii ON sii.invoice_id=si.id
            WHERE si.tax_amount > 0 AND si.status <> 'void'
         GROUP BY si.id, si.invoice_number, si.tenant_id
           HAVING count(sii.id) >= 2 ORDER BY si.created_at DESC LIMIT 1""")
    if not row:
        print("tak ada faktur berpajak dengan >=2 baris untuk diuji")
        return 1
    print(f"faktur uji: {row['invoice_number']} (tenant {row['tenant_id']})")

    ctx = await konteks(conn, row["id"])
    ctx["tenant"] = dict(ctx["tenant"], is_pkp=True)  # supaya baris Tax tampil
    html = tpl.render(**svc._konteks_faktur(ctx))

    nilai = kolom_amount(html)
    sub = angka(svc.format_currency2(ctx["subtotal"]))
    pjk = angka(svc.format_currency2(ctx["tax_amount"]))
    tot = angka(svc.format_currency2(ctx["total_amount"]))
    # tiga nilai terakhir di kolom itu adalah Subtotal/Tax/Total
    baris = nilai[:-3]
    print(f"  baris AMOUNT : {[f'{x/100:,.2f}' for x in baris]}")
    print(f"  Subtotal={sub/100:,.2f}  Tax={pjk/100:,.2f}  Total={tot/100:,.2f}")

    cek(sum(baris) == sub,
        f"jumlah kolom AMOUNT {sum(baris)/100:,.2f} == Subtotal {sub/100:,.2f}")
    cek(sub + pjk == tot,
        f"Subtotal + Tax {(sub+pjk)/100:,.2f} == Total {tot/100:,.2f}")

    # KONTROL MERAH: pakai `total` baris (yang sudah termasuk pajak)
    ctx_bruto = dict(ctx, items=[dict(i, subtotal=i["total"]) for i in ctx["items"]])
    html_b = tpl.render(**svc._konteks_faktur(ctx_bruto))
    baris_b = kolom_amount(html_b)[:-3]
    merah = sum(baris_b) != sub
    print(f"  KONTROL: pakai nilai bruto -> jumlah {sum(baris_b)/100:,.2f} vs Subtotal {sub/100:,.2f}")
    cek(merah, "KONTROL MERAH menyala saat nilai bruto dipakai")

    await conn.close()
    if gagal:
        print("\nGAGAL:")
        for g in gagal:
            print("  - " + g)
        return 1
    print("\nOK: angka faktur konsisten.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(utama()))
